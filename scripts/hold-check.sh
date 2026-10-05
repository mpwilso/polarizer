#!/usr/bin/env bash
# The M2a manual check (docs/MANUAL-CHECK.md, M2a) as one script, run from a second terminal
# while Claude Code runs in terminal A. The person starts Claude Code, asks Claude for each call
# this script prints, and says what Claude Code showed. The script finds each hold itself, so no
# id or hash is ever copied by hand.
#
#   scripts/hold-check.sh reset      a fresh ledger, polarizer.toml set up, every tool approved
#   scripts/hold-check.sh deny       a write under .git/hooks is held; read it, deny it
#   scripts/hold-check.sh allow      the same write again; allow it
#   scripts/hold-check.sh long-wait  one call held for more than 60 s, then allowed
#   scripts/hold-check.sh expire     hold_timeout_seconds 30; a move_file left to time out
#   scripts/hold-check.sh finish     after exiting Claude Code: verify, tidy up, guard
#   scripts/hold-check.sh status     which step is next
#
# Each command prints its output and appends it, under a header, to /tmp/hold-check-results.txt.
# Decisions run polarizer allow and deny in this terminal, so their terminal check applies; this
# script never passes --allow-no-terminal. A blank answer, end of input or Ctrl+C stops it with a
# line saying what was done. Each answer is one line: whatever else is already waiting on the
# terminal after it (the rest of a pasted block) is thrown away, with a line saying how many
# bytes. Nothing is deleted: reset moves an earlier check's ledger aside.
# Reading the ledger is in scripts/hold_check.py.
#
# Testing only: POLARIZER_CHECK_TOML and POLARIZER_CHECK_LEDGER_DIR replace polarizer.toml and the
# ledger directory. With POLARIZER_CHECK_TESTING=1 as well, the other files live next to that toml
# (polarizer.example.toml, hold-check-results.txt, and manual/ in place of /tmp/polarizer-manual),
# the processes looked for are that directory's fs_stub.py, the long wait and the timeout are 1 s,
# and no guard, uv or npx runs. Every command refuses either override without
# POLARIZER_CHECK_TESTING=1.
set -euo pipefail
umask 077

REPO="$(cd "$(dirname "$0")/.." && pwd)"
POLARIZER="$REPO/.venv/bin/polarizer"
PY="$REPO/.venv/bin/python"
HELPER="$REPO/scripts/hold_check.py"
M2A_LEDGER="$HOME/.local/share/polarizer-m2a-check"
# shellcheck disable=SC2016  # $PWD is for the person's shell to expand
START='Terminal A: cd ~/code/polarizer && claude --mcp-config "$PWD/manual/mcp.json" --strict-mcp-config. Type /mcp: polarizer should be connected, with its tools listed. Then run: scripts/hold-check.sh deny'

TESTING="${POLARIZER_CHECK_TESTING:-0}"
OVERRIDDEN=0
if [ -n "${POLARIZER_CHECK_TOML:-}" ] || [ -n "${POLARIZER_CHECK_LEDGER_DIR:-}" ]; then
  OVERRIDDEN=1
fi
# Checked before anything is written, so a test that forgets POLARIZER_CHECK_TESTING=1 can't touch
# the real results file, polarizer.toml or ledger.
if [ "$OVERRIDDEN" = 1 ] && [ "$TESTING" != 1 ]; then
  echo "hold-check: POLARIZER_CHECK_TOML and POLARIZER_CHECK_LEDGER_DIR are for testing; every command runs only on ~/.local/share/polarizer-m2a-check unless POLARIZER_CHECK_TESTING=1 is set" >&2
  exit 2
fi
TOML="${POLARIZER_CHECK_TOML:-$REPO/polarizer.toml}"
LEDGER="${POLARIZER_CHECK_LEDGER_DIR:-$M2A_LEDGER}"
if [ "$TESTING" = 1 ]; then
  if [ -z "${POLARIZER_CHECK_TOML:-}" ] || [ -z "${POLARIZER_CHECK_LEDGER_DIR:-}" ]; then
    echo "hold-check: POLARIZER_CHECK_TESTING=1 needs POLARIZER_CHECK_TOML and POLARIZER_CHECK_LEDGER_DIR" >&2
    exit 2
  fi
  case "$TOML$LEDGER" in
    /*/*) ;;
    *) echo "hold-check: the test paths must be absolute" >&2; exit 2 ;;
  esac
  if [ "$(basename "$LEDGER")" != polarizer-m2a-check ]; then
    echo "hold-check: a test ledger directory must be named polarizer-m2a-check" >&2
    exit 2
  fi
  BASE="$(dirname "$TOML")"
  EXAMPLE="$BASE/polarizer.example.toml"
  RESULTS="$BASE/hold-check-results.txt"
  MANUAL="$BASE/manual"
  PATTERN="$BASE/fs_stub.py"
  LEDGER_LINE="$LEDGER"
  LONG_WAIT=1
  TIMEOUT=1
  RECONNECT_WAIT=60
else
  EXAMPLE="$REPO/polarizer.example.toml"
  RESULTS=/tmp/hold-check-results.txt
  MANUAL=/tmp/polarizer-manual
  PATTERN='tests/helpers/probe_server.py|server-filesystem'
  # shellcheck disable=SC2088  # written into polarizer.toml, where Polarizer expands it
  LEDGER_LINE='~/.local/share/polarizer-m2a-check'
  LONG_WAIT=65
  TIMEOUT=30
  RECONNECT_WAIT=300
fi
HOOKS="$MANUAL/.git/hooks"

# --- output, answers and stopping

STATE="nothing was changed"
TEE_PID=""

# Everything from here on goes to the terminal and to the results file. tee ignores Ctrl+C, so
# the line saying where the script stopped is still recorded.
start_output() {
  exec > >(trap '' INT TERM; exec tee -a "$RESULTS") 2>&1
  TEE_PID=$!
  # reset prints its header once it has emptied the file.
  [ "$1" = reset ] || printf '\n== %s, %s\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

end_output() {
  if [ -n "$TEE_PID" ]; then
    exec >&- 2>&-
    wait "$TEE_PID" 2> /dev/null || true
  fi
}
trap end_output EXIT

stop() {
  printf 'stopped: %s; %s\n' "$1" "$STATE"
  exit 1
}

on_signal() {
  printf '\nstopped by Ctrl+C: %s\n' "$STATE"
  exit 130
}
trap on_signal INT TERM

# discard_input: after each answer, throws away anything else already waiting on the terminal
# (the rest of a multi-line paste), so it can't answer the next question or reach the shell as
# commands once the script ends. Never waits for input.
discard_input() {
  helper discard-input || true
}

trimmed() {
  local s="${1//$'\r'/}"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

# ask "<question>" <answer>...: sets ANSWER to one of the answers, asking again after any other
# text. A blank line or end of input stops the script.
ask() {
  local question="$1" option
  shift
  while true; do
    printf '%s ' "$question"
    ANSWER=""
    IFS= read -r ANSWER || true
    discard_input
    ANSWER="$(trimmed "$ANSWER")"
    if [ -z "$ANSWER" ]; then
      printf 'answer: none\n'
      stop "no answer given"
    fi
    for option in "$@"; do
      if [ "$ANSWER" = "$option" ]; then
        printf 'answer: %s\n' "$ANSWER"
        return 0
      fi
    done
    printf 'Please type one of: %s\n' "$(printf '"%s" ' "$@")"
  done
}

# confirm "<question>": only "yes" goes on; anything else stops with nothing done.
confirm() {
  printf '%s ' "$1"
  ANSWER=""
  IFS= read -r ANSWER || true
  discard_input
  ANSWER="$(trimmed "$ANSWER")"
  printf 'answer: %s\n' "${ANSWER:-none}"
  [ "$ANSWER" = yes ] || stop "the answer was not yes"
}

# describe "<question>": one line of free text, recorded; a blank line records "(skipped)".
describe() {
  local said=""
  printf '%s Type a short answer; do not paste multi-line text (or press Enter to skip): ' "$1"
  IFS= read -r said || true
  discard_input
  said="$(trimmed "$said")"
  printf 'answer: %s\n' "${said:-(skipped)}"
}

# --- reading the toml, the ledger and the holds

helper() {
  "$PY" "$HELPER" "$@"
}

marker() {
  [ -f "$RESULTS" ] && grep -qx -- "-- $1: complete" "$RESULTS"
}

# Prints "<pid> <command>" for each probe or Filesystem server still running.
leftovers() {
  local pids pid
  pids="$(pgrep -f -- "$PATTERN" || true)"
  for pid in $pids; do
    ps -o pid=,args= -p "$pid" 2> /dev/null || true
  done
}

stop_on_leftovers() {
  local found pids
  found="$(leftovers)"
  [ -z "$found" ] && return 0
  printf 'These probe or Filesystem server processes are running:\n%s\n' "$found"
  pids="$(printf '%s\n' "$found" | awk '{print $1}' | tr '\n' ' ')"
  stop "exit Claude Code in terminal A first. If they are still listed after that, stop them with: kill ${pids% }. Then run: scripts/hold-check.sh $1"
}

toml_ready() {
  [ -f "$TOML" ] && [ "$(helper toml "$TOML" ledger_dir 2> /dev/null)" = "$LEDGER" ]
}

check_setup() {
  [ -x "$POLARIZER" ] || stop "$POLARIZER not found. Run: cd ~/code/polarizer && uv sync --locked"
  toml_ready || stop "$TOML is not set up for the M2a check. Run: scripts/hold-check.sh reset"
  [ -s "$LEDGER/ledger.jsonl" ] || stop "there is no ledger yet. Run: scripts/hold-check.sh reset"
  TOKEN="$(helper token "$LEDGER")" || stop "cannot read the ledger's chain id"
}

# Writes polarizer.toml from the example, with the M2a check's ledger and hold timeout if $1 is
# m2a. The new file replaces the old one in one step, so it is never half written.
write_toml() {
  local tmp="$TOML.hold-check-tmp"
  if [ "$1" = m2a ]; then
    sed -e "s|/home/<you>|$HOME|g" \
      -e "s|^ledger_dir = .*|ledger_dir = \"$LEDGER_LINE\"|" \
      -e "s|^hold_timeout_seconds = .*|hold_timeout_seconds = $2|" \
      "$EXAMPLE" > "$tmp"
  else
    sed "s|/home/<you>|$HOME|g" "$EXAMPLE" > "$tmp"
  fi
  mv -f "$tmp" "$TOML"
}

# wait_for_hold "<what to ask Claude>" [<hold id to skip>]: prints the request, runs polarizer
# holds --wait --bell until a call of a running session is held, shows the listing, and sets
# HOLD to the newest open hold of a running session.
wait_for_hold() {
  local listing code=0
  printf '\nIn terminal A, ask Claude:\n\n  %s\n\n' "$1"
  echo "If Claude Code asks whether to allow the polarizer tool, allow it there: that is Claude Code's own prompt, before the call reaches Polarizer."
  echo "Waiting for the call to be held: polarizer holds --config $TOML --wait --bell"
  listing="$("$POLARIZER" holds --config "$TOML" --wait --bell)" || code=$?
  printf '%s\n' "$listing"
  [ "$code" = 0 ] || stop "polarizer holds exited $code"
  HOLD="$(helper open-hold "$LEDGER" ${2:+"$2"})" || stop "no open hold of a running session was found"
  echo "the hold this step decides: $HOLD"
}

# wait_until <seconds> <helper command> <args>...: runs the helper every second until it
# succeeds, at most <seconds>; its output goes to OUT. Returns 1 if it never succeeded.
wait_until() {
  local seconds="$1" i=0
  shift
  while [ "$i" -lt "$seconds" ]; do
    if OUT="$(helper "$@" 2> /dev/null)"; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

show_trail() {
  echo "The ledger for hold $1:"
  helper trail "$LEDGER" "$1" | sed 's/^/  /'
}

# Other open holds of a running session (Claude may retry a call it was not allowed), each with
# the complete command that ends it.
other_holds() {
  local other
  if other="$(helper open-hold "$LEDGER" "$1" 2> /dev/null)"; then
    echo "Another call is held: hold $other (Claude may have retried). It times out on its own; to end it now, run: $POLARIZER deny --config $TOML $other --reason \"retried during the hold check\""
  fi
}

# --- the commands

cmd_reset() {
  local gid code=0 pending stamp
  stop_on_leftovers reset
  : > "$RESULTS"
  printf '== reset, %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  STATE="run scripts/hold-check.sh reset again"

  if [ "$TESTING" != 1 ]; then
    local package mcp_json
    "$REPO/scripts/guard.sh" snapshot
    (cd "$REPO" && uv sync --locked) || stop "uv sync --locked failed"
    mcp_json="$(sed "s|/home/<you>|$HOME|g" "$REPO/manual/mcp.json.example")"
    if [ ! -e "$REPO/manual/mcp.json" ]; then
      printf '%s\n' "$mcp_json" > "$REPO/manual/mcp.json"
      echo "wrote manual/mcp.json from manual/mcp.json.example"
    elif [ "$(cat "$REPO/manual/mcp.json")" != "$mcp_json" ]; then
      stop "manual/mcp.json differs from manual/mcp.json.example with your home directory. To replace it, run: cd ~/code/polarizer && sed \"s|/home/<you>|\$HOME|g\" manual/mcp.json.example > manual/mcp.json"
    fi
    # The pre-warm: a first npx fetch can take longer than the server's connect timeout.
    package="$(grep -o '@modelcontextprotocol/server-filesystem@[0-9.]*' "$EXAMPLE" | head -n 1)"
    if [ -n "$package" ]; then
      mkdir -p "$MANUAL"
      npx -y "$package" "$MANUAL" < /dev/null || stop "the pre-warm of $package failed"
    fi
  fi
  mkdir -p "$HOOKS"
  printf 'hello from the manual check\n' > "$MANUAL/note.txt"
  echo "$HOOKS exists, and $MANUAL/note.txt holds: hello from the manual check"
  if [ -e "$LEDGER" ]; then
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    mv -- "$LEDGER" "$LEDGER.before-$stamp"
    echo "moved the earlier check's ledger to $LEDGER.before-$stamp"
  fi

  write_toml m2a 300
  grep -n -e '^ledger_dir' -e '^hold_timeout_seconds' -e '^workspace_roots' "$TOML"
  toml_ready || stop "the M2a lines did not apply to $TOML; compare it with $EXAMPLE"

  echo "Recording the tools' definitions, with no model: polarizer serve --config $TOML < /dev/null"
  "$POLARIZER" serve --config "$TOML" < /dev/null || code=$?
  [ "$code" = 0 ] || stop "polarizer serve exited $code"
  pending="$("$POLARIZER" pending --ledger-dir "$LEDGER")" || stop "polarizer pending failed"
  code=0
  gid="$(printf '%s\n' "$pending" | helper group)" || code=$?
  [ "$code" = 0 ] || stop "the first run's definitions are not one group"
  echo "To read every definition first, press Enter to stop here, run: $POLARIZER pending --config $TOML, then run: scripts/hold-check.sh reset"
  confirm "Type yes to approve them as group $gid:"
  STATE="polarizer approve may have recorded some approvals; run: scripts/hold-check.sh reset"
  code=0
  "$POLARIZER" approve --config "$TOML" --group "$gid" || code=$?
  [ "$code" = 0 ] || stop "polarizer approve exited $code"
  printf -- '-- reset: complete\n\n%s\n' "$START"
}

cmd_deny() {
  check_setup
  local file="$HOOKS/hold-check-$TOKEN.txt" code=0
  STATE="nothing was decided"
  wait_for_hold "Call the polarizer tool fs__write_file to write the text \"M2a hold check $TOKEN\" to $file"
  echo "Read the block above: the tool should be fs__write_file, held by write-pattern because the path matches .git/hooks/**."
  ask "Is hold $HOLD the write you asked for? (y/n)" y n
  [ "$ANSWER" = y ] || stop "it was not the expected call; it times out on its own"
  STATE="the hold may be denied; run: scripts/hold-check.sh status"
  "$POLARIZER" deny --config "$TOML" "$HOLD" --reason "M2a hold check: deny" || code=$?
  [ "$code" = 0 ] || stop "polarizer deny exited $code"
  STATE="hold $HOLD is denied and the question was not answered; to answer it, run: scripts/hold-check.sh deny"
  wait_until 30 ended "$LEDGER" "$HOLD" > /dev/null || true
  sleep 1  # serve answers within a quarter of a second; give Claude Code a moment to show it
  describe "What did Claude Code show for the call?"
  show_trail "$HOLD"
  [ -e "$file" ] && echo "$file exists: the denied call was written anyway (not expected)" \
    || echo "$file does not exist, as expected after a deny"
  other_holds "$HOLD"
  printf -- '-- deny: complete\n'
  echo "Next: scripts/hold-check.sh allow"
}

cmd_allow() {
  check_setup
  local file="$HOOKS/hold-check-$TOKEN.txt" code=0
  STATE="nothing was decided"
  wait_for_hold "Call the polarizer tool fs__write_file again, with the same arguments: write the text \"M2a hold check $TOKEN\" to $file"
  ask "Is hold $HOLD the same write? (y/n)" y n
  [ "$ANSWER" = y ] || stop "it was not the expected call; it times out on its own"
  STATE="the hold may be allowed; run: scripts/hold-check.sh status"
  "$POLARIZER" allow --config "$TOML" "$HOLD" || code=$?
  [ "$code" = 0 ] || stop "polarizer allow exited $code"
  STATE="hold $HOLD is allowed and the question was not answered; to answer it, run: scripts/hold-check.sh allow"
  if wait_until 60 returned "$LEDGER" "$HOLD"; then
    echo "the forwarded call returned: $OUT"
  else
    echo "no call.returned for the forwarded call within 60 s"
  fi
  describe "What did Claude Code show for the call?"
  show_trail "$HOLD"
  if [ "$(cat "$file" 2> /dev/null || true)" = "M2a hold check $TOKEN" ]; then
    echo "$file holds: M2a hold check $TOKEN"
  else
    echo "$file does not hold the text that was asked for (not expected)"
  fi
  other_holds "$HOLD"
  printf -- '-- allow: complete\n'
  echo "Next: scripts/hold-check.sh long-wait"
}

cmd_long_wait() {
  check_setup
  local file="$HOOKS/hold-check-long-$TOKEN.txt" code=0 waited=0
  STATE="nothing was decided"
  wait_for_hold "Call the polarizer tool fs__write_file to write the text \"M2a long wait $TOKEN\" to $file"
  ask "Is hold $HOLD that write? (y/n)" y n
  [ "$ANSWER" = y ] || stop "it was not the expected call; it times out on its own"
  echo "Leave the call waiting. Watch terminal A; this script allows nothing for $LONG_WAIT s."
  STATE="hold $HOLD is still open; it times out on its own"
  while [ "$waited" -lt "$LONG_WAIT" ]; do
    sleep 1
    waited=$((waited + 1))
    if [ $((waited % 15)) = 0 ]; then
      echo "held for $waited s"
    fi
  done
  if helper ended "$LEDGER" "$HOLD" > /dev/null 2>&1; then
    show_trail "$HOLD"
    stop "the hold ended before it was allowed"
  fi
  echo "held for $LONG_WAIT s, still open"
  describe "While it waited, what did Claude Code show for the call?"
  ask "Could you type in terminal A while the call waited? (y/n/not tried)" y n "not tried"
  confirm "Type yes to allow hold $HOLD now:"
  STATE="the hold may be allowed; run: scripts/hold-check.sh status"
  "$POLARIZER" allow --config "$TOML" "$HOLD" || code=$?
  [ "$code" = 0 ] || stop "polarizer allow exited $code"
  STATE="hold $HOLD is allowed and the question was not answered; to answer it, run: scripts/hold-check.sh long-wait"
  if wait_until 60 returned "$LEDGER" "$HOLD"; then
    echo "the forwarded call returned: $OUT"
  else
    echo "no call.returned for the forwarded call within 60 s"
  fi
  describe "What did Claude Code show once the call finished, and how long did it say the call ran?"
  show_trail "$HOLD"
  other_holds "$HOLD"
  printf -- '-- long-wait: complete\n'
  echo "Next: scripts/hold-check.sh expire"
}

cmd_expire() {
  check_setup
  local before source="$MANUAL/note.txt" destination="$MANUAL/note-$TOKEN.txt" ending
  STATE="nothing was changed"
  before="$(helper loaded "$LEDGER" "$TIMEOUT")"
  write_toml m2a "$TIMEOUT"
  grep -n '^hold_timeout_seconds' "$TOML"
  STATE="$TOML has hold_timeout_seconds = $TIMEOUT; finish writes it again from the example"
  echo "In terminal A, type /mcp, choose polarizer and reconnect it, so it starts again and reads hold_timeout_seconds = $TIMEOUT."
  echo "Waiting up to $RECONNECT_WAIT s for the new start (policy.loaded with hold_timeout_seconds $TIMEOUT)..."
  wait_until "$RECONNECT_WAIT" loaded-more "$LEDGER" "$TIMEOUT" "$before" > /dev/null \
    || stop "polarizer did not start again with the new timeout. Reconnect it from /mcp in terminal A, then run: scripts/hold-check.sh expire"
  echo "polarizer started again with hold_timeout_seconds = $TIMEOUT"
  wait_for_hold "Call the polarizer tool fs__move_file to move $source to $destination"
  ask "Is hold $HOLD that move? (y/n)" y n
  [ "$ANSWER" = y ] || stop "it was not the expected call; it times out on its own"
  echo "Do not allow or deny it. Waiting for its $TIMEOUT s timeout..."
  if ! wait_until $((TIMEOUT + 60)) ended "$LEDGER" "$HOLD"; then
    stop "hold $HOLD had no ending $((TIMEOUT + 60)) s after it was seen"
  fi
  ending="$OUT"
  echo "the hold ended: $ending"
  sleep 1  # give Claude Code a moment to show the answer
  describe "What did Claude Code show for the call?"
  describe "About how long did the call appear to run, in seconds?"
  show_trail "$HOLD"
  [ -e "$source" ] && echo "$source is still there, as expected" || echo "$source is gone (not expected)"
  [ -e "$destination" ] && echo "$destination exists (not expected)" || echo "$destination does not exist, as expected"
  other_holds "$HOLD"
  printf -- '-- expire: complete\n'
  echo "Next: exit Claude Code in terminal A, then run: scripts/hold-check.sh finish"
}

cmd_finish() {
  local code=0 found
  STATE="nothing was changed"
  stop_on_leftovers finish
  STATE="to finish, run: scripts/hold-check.sh finish"
  echo "\$ polarizer verify --ledger-dir $LEDGER"
  "$POLARIZER" verify --ledger-dir "$LEDGER" || code=$?
  echo "verify exit code: $code"
  code=0
  echo "\$ polarizer holds --ledger-dir $LEDGER"
  "$POLARIZER" holds --ledger-dir "$LEDGER" || code=$?
  echo "holds exit code: $code"
  write_toml plain
  echo "polarizer.toml written again from polarizer.example.toml, without the M2a lines"
  found="$(leftovers)"
  echo "probe or Filesystem server processes still running: ${found:-none}"
  if [ "$TESTING" = 1 ]; then
    echo "guard: not run in testing"
  else
    code=0
    "$REPO/scripts/guard.sh" check || code=$?
    echo "guard exit code: $code"
  fi
  printf -- '-- finish: complete\n'
  echo "Paste this: cat $RESULTS"
}

cmd_status() {
  local next step
  if marker finish; then
    next="nothing: the check is finished. Paste this: cat $RESULTS"
  elif ! toml_ready || ! marker reset; then
    next="scripts/hold-check.sh reset"
  else
    next="exit Claude Code in terminal A, then run: scripts/hold-check.sh finish"
    for step in expire long-wait allow deny; do
      marker "$step" || next="scripts/hold-check.sh $step"
    done
  fi
  echo "Next: $next"
}

case "${1:-}" in
  reset | deny | allow | long-wait | expire | finish | status)
    [ $# = 1 ] || { echo "usage: scripts/hold-check.sh reset|deny|allow|long-wait|expire|finish|status" >&2; exit 2; }
    [ -x "$PY" ] || { echo "hold-check: $PY not found. Run: cd ~/code/polarizer && uv sync --locked" >&2; exit 2; }
    start_output "$1"
    "cmd_${1//-/_}"
    ;;
  *)
    echo "usage: scripts/hold-check.sh reset|deny|allow|long-wait|expire|finish|status" >&2
    exit 2
    ;;
esac
