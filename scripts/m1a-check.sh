#!/usr/bin/env bash
# Superseded by scripts/rugpull-check.sh, which docs/dev/MANUAL-CHECK.md's M1a section now runs.
# Kept for the record: the owner ran it interactively on 2026-10-04 (docs/verified-facts.md,
# M1a check, interactive), skipping its rugpull step.
#
# The M1a manual check (formerly docs/dev/MANUAL-CHECK.md, M1a) as one script, run from a second
# terminal while Claude Code runs in terminal A. Only starting Claude Code and looking at /mcp
# are left to the person.
#
#   scripts/m1a-check.sh reset            a fresh ledger, and polarizer.toml set up for the check
#   scripts/m1a-check.sh approve          approve the first run's group while the session is open
#   scripts/m1a-check.sh rugpull          after reconnecting polarizer in /mcp: the changed tool
#   scripts/m1a-check.sh approve-changed  approve the changed definition while the session is open
#   scripts/m1a-check.sh finish           after exiting Claude Code: verify, tidy up, guard
#   scripts/m1a-check.sh status           which step is next
#
# Each command prints its output and appends it, under a header, to /tmp/m1a-check-results.txt.
# The approvals run polarizer approve in this terminal, so its terminal check applies; this script
# never passes --allow-no-terminal. A blank answer, end of input or Ctrl+C stops it with a line
# saying what was done. Parsing is in scripts/m1a_check.py.
#
# Testing only: POLARIZER_CHECK_TOML and POLARIZER_CHECK_LEDGER_DIR replace polarizer.toml and the
# ledger directory. With POLARIZER_CHECK_TESTING=1 as well, the other files live next to that
# toml (polarizer.manual.toml, m1a-check-results.txt, polarizer-rugpull), the processes looked
# for are that directory's probe_server.py, and no guard, uv or npx runs. reset refuses either
# override without POLARIZER_CHECK_TESTING=1, and so does every other command.
set -euo pipefail
umask 077

REPO="$(cd "$(dirname "$0")/.." && pwd)"
POLARIZER="$REPO/.venv/bin/polarizer"
PY="$REPO/.venv/bin/python"
HELPER="$REPO/scripts/m1a_check.py"
M1A_LEDGER="$HOME/.local/share/polarizer-m1a-check"
# shellcheck disable=SC2016  # $PWD is for the person's shell to expand
START='Terminal A: cd ~/code/polarizer && claude --mcp-config "$PWD/manual/mcp.json" --strict-mcp-config. Type /mcp. polarizer should be connected with no tools. Then run: scripts/m1a-check.sh approve'

TESTING="${POLARIZER_CHECK_TESTING:-0}"
OVERRIDDEN=0
if [ -n "${POLARIZER_CHECK_TOML:-}" ] || [ -n "${POLARIZER_CHECK_LEDGER_DIR:-}" ]; then
  OVERRIDDEN=1
fi
# Checked before anything is written, so a test that forgets POLARIZER_CHECK_TESTING=1 can't touch
# the real results file, polarizer.toml or ledger.
if [ "$OVERRIDDEN" = 1 ] && [ "$TESTING" != 1 ]; then
  echo "m1a-check: POLARIZER_CHECK_TOML and POLARIZER_CHECK_LEDGER_DIR are for testing; reset runs only on ~/.local/share/polarizer-m1a-check unless POLARIZER_CHECK_TESTING=1 is set, and so does every other command" >&2
  exit 2
fi
TOML="${POLARIZER_CHECK_TOML:-$REPO/polarizer.toml}"
LEDGER="${POLARIZER_CHECK_LEDGER_DIR:-$M1A_LEDGER}"
if [ "$TESTING" = 1 ]; then
  if [ -z "${POLARIZER_CHECK_TOML:-}" ] || [ -z "${POLARIZER_CHECK_LEDGER_DIR:-}" ]; then
    echo "m1a-check: POLARIZER_CHECK_TESTING=1 needs POLARIZER_CHECK_TOML and POLARIZER_CHECK_LEDGER_DIR" >&2
    exit 2
  fi
  case "$TOML$LEDGER" in
    /*/*) ;;
    *) echo "m1a-check: the test paths must be absolute" >&2; exit 2 ;;
  esac
  if [ "$(basename "$LEDGER")" != polarizer-m1a-check ]; then
    echo "m1a-check: a test ledger directory must be named polarizer-m1a-check" >&2
    exit 2
  fi
  BASE="$(dirname "$TOML")"
  EXAMPLE="$BASE/polarizer.manual.toml"
  RESULTS="$BASE/m1a-check-results.txt"
  RUGPULL="$BASE/polarizer-rugpull"
  PATTERN="$BASE/probe_server.py"
  LEDGER_LINE="$LEDGER"
else
  EXAMPLE="$REPO/manual/polarizer.manual.toml"
  RESULTS=/tmp/m1a-check-results.txt
  RUGPULL=/tmp/polarizer-rugpull
  PATTERN='tests/helpers/probe_server.py|server-filesystem'
  # shellcheck disable=SC2088  # written into polarizer.toml, where Polarizer expands it
  LEDGER_LINE='~/.local/share/polarizer-m1a-check'
fi

# --- output, answers and stopping

STATE="nothing was changed"
TEE_PID=""
SIGNALLED=0

# Everything from here on goes to the terminal and to the results file. tee ignores Ctrl+C, so
# the line saying where the script stopped is still recorded. tee is a job of this shell reading
# a named pipe, not a process substitution: bash 3.2 (macOS) can't wait for a process
# substitution, so the script could exit before tee had written. The pipe and its directory are
# removed once both ends are open. A Ctrl+C before then stops the script once they are.
start_output() {
  local dir
  trap 'SIGNALLED=1' INT TERM
  dir="$(mktemp -d "${TMPDIR:-/tmp}/m1a-check-output.XXXXXX")"
  mkfifo "$dir/tee"
  (trap '' INT TERM; exec tee -a "$RESULTS") < "$dir/tee" &
  TEE_PID=$!
  exec > "$dir/tee" 2>&1
  rm -- "$dir/tee"
  rmdir -- "$dir"
  trap on_signal INT TERM
  # reset prints its header once it has emptied the file.
  [ "$1" = reset ] || printf '\n== %s, %s\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  [ "$SIGNALLED" = 0 ] || on_signal
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
  ANSWER="$(trimmed "$ANSWER")"
  printf 'answer: %s\n' "${ANSWER:-none}"
  [ "$ANSWER" = yes ] || stop "the answer was not yes"
}

# --- reading the toml, pending and the ledger

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
  stop "exit Claude Code in terminal A first. If they are still listed after that, stop them with: kill ${pids% }. Then run: scripts/m1a-check.sh $1"
}

toml_ready() {
  [ -f "$TOML" ] \
    && [ "$(helper toml "$TOML" ledger_dir 2> /dev/null)" = "$LEDGER" ] \
    && [ "$(helper toml "$TOML" rugpull 2> /dev/null)" = "$RUGPULL" ]
}

check_setup() {
  [ -x "$POLARIZER" ] || stop "$POLARIZER not found. Run: cd ~/code/polarizer && uv sync --locked"
  toml_ready || stop "$TOML is not set up for the M1a check. Run: scripts/m1a-check.sh reset"
  EXPECTED="$(helper toml "$TOML" expected)" || stop "cannot tell how many tools to expect"
}

run_pending() {
  local code=0
  PENDING="$("$POLARIZER" pending --config "$TOML" 2>&1)" || code=$?
  if [ "$code" = 2 ] && [ "${PENDING#no ledger at}" != "$PENDING" ]; then
    printf '%s\n' "$PENDING"
    stop "Claude Code has not started polarizer yet. $START"
  fi
  if [ "$code" != 0 ]; then
    printf '%s\n' "$PENDING"
    stop "polarizer pending exited $code"
  fi
}

# Writes polarizer.toml from the example, with the M1a changes if $1 is m1a. The new file
# replaces the old one in one step, so it is never half written.
write_toml() {
  local tmp="$TOML.m1a-check-tmp"
  if [ "$1" = m1a ]; then
    sed -e "s|/home/<you>|$HOME|g" \
      -e "s|^ledger_dir = .*|ledger_dir = \"$LEDGER_LINE\"|" \
      -e "s|env = { PROBE_LOG = \"\\([^\"]*\\)\" }|env = { PROBE_LOG = \"\\1\", PROBE_RUGPULL = \"$RUGPULL\" }|" \
      "$EXAMPLE" > "$tmp"
  else
    sed "s|/home/<you>|$HOME|g" "$EXAMPLE" > "$tmp"
  fi
  mv -f "$tmp" "$TOML"
}

# --- the commands

cmd_reset() {
  stop_on_leftovers reset
  : > "$RESULTS"
  printf '== reset, %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  STATE="run scripts/m1a-check.sh reset again"

  if [ "$TESTING" != 1 ]; then
    local package mcp_json
    "$REPO/scripts/dev/guard.sh" snapshot
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
      mkdir -p /tmp/polarizer-manual
      npx -y "$package" /tmp/polarizer-manual < /dev/null || stop "the pre-warm of $package failed"
    fi
    rm -f /tmp/polarizer-rugpull
    rm -rf ~/.local/share/polarizer-m1a-check
  else
    rm -f -- "$RUGPULL"
    rm -rf -- "$LEDGER"
  fi
  echo "removed $RUGPULL and $LEDGER"

  write_toml m1a
  grep -n -e ledger_dir -e PROBE_RUGPULL "$TOML"
  toml_ready || stop "the M1a lines did not apply to $TOML; compare it with $EXAMPLE"
  printf -- '-- reset: complete\n\n%s\n' "$START"
}

ask_relisted() {
  ask "In terminal A, wait 5 seconds, type /mcp. Do the tools appear without reconnecting? (y/n/not sure)" y n "not sure"
  if [ "$ANSWER" = n ]; then
    echo "In terminal A, reconnect polarizer from /mcp, then type /mcp again. Reconnecting restarts the probe, which changes probe__wait (the rug pull), so expect $((EXPECTED - 1)) tools, without probe__wait."
    ask "Do the tools appear after reconnecting? (y/n/not sure)" y n "not sure"
  fi
}

cmd_approve() {
  check_setup
  run_pending
  local gid code=0
  gid="$(printf '%s\n' "$PENDING" | helper group "$EXPECTED")" || code=$?
  if [ "$code" = 3 ]; then
    if marker approve; then
      echo "approve is done. Next: in terminal A, reconnect polarizer from /mcp. Then run: scripts/m1a-check.sh rugpull"
      return 0
    fi
    [ "$(helper count "$LEDGER" group-approved)" -gt 0 ] \
      || stop "nothing is pending and nothing is approved. $START"
    echo "The group is already approved; asking about /mcp now."
  elif [ "$code" != 0 ]; then
    stop "nothing was approved. To start again from a fresh ledger, exit Claude Code and run: scripts/m1a-check.sh reset"
  else
    echo "group $gid"
    echo "To read every definition first, press Enter to stop here, run: $POLARIZER pending --config $TOML, then run: scripts/m1a-check.sh approve"
    STATE="nothing was approved"
    confirm "Type yes to approve these $EXPECTED definitions as group $gid:"
    code=0
    STATE="polarizer approve may have recorded some approvals; run: scripts/m1a-check.sh status"
    "$POLARIZER" approve --config "$TOML" --group "$gid" || code=$?
    [ "$code" = 0 ] || stop "polarizer approve exited $code"
  fi
  STATE="the group is approved and the question was not answered; to answer it, run: scripts/m1a-check.sh approve"
  ask_relisted
  printf -- '-- approve: complete\n'
  echo "Next: in terminal A, reconnect polarizer from /mcp (skip this if you just did). Then run: scripts/m1a-check.sh rugpull"
}

cmd_rugpull() {
  check_setup
  run_pending
  local hashes said
  STATE="nothing was changed"
  if ! hashes="$(printf '%s\n' "$PENDING" | helper changed)"; then
    if marker rugpull; then
      echo "rugpull is done. Next: scripts/m1a-check.sh approve-changed"
      return 0
    fi
    stop "in terminal A, reconnect polarizer from /mcp, then run: scripts/m1a-check.sh rugpull"
  fi
  echo "hashes (new, approved): $hashes"
  STATE="to answer the questions, run: scripts/m1a-check.sh rugpull"
  ask "Does /mcp now omit probe__wait and still list the other $((EXPECTED - 1))? (y/n/not sure)" y n "not sure"
  echo "In terminal A, ask Claude: Call probe__wait with 1 second."
  printf 'Type what Claude said, as one line (or press Enter to skip): '
  said=""
  IFS= read -r said || true
  said="$(trimmed "$said")"
  printf 'claude said: %s\n' "${said:-(skipped)}"
  echo "tool.drift entries in the ledger: $(helper count "$LEDGER" drift)"
  echo "last call.refused entry: $(helper last-refused "$LEDGER")"
  printf -- '-- rugpull: complete\n'
  echo "Next: scripts/m1a-check.sh approve-changed"
}

cmd_approve_changed() {
  check_setup
  run_pending
  local hashes live code=0
  if hashes="$(printf '%s\n' "$PENDING" | helper changed)"; then
    live="${hashes%% *}"
    STATE="nothing was approved"
    confirm "Type yes to approve probe__wait's new definition $live:"
    STATE="polarizer approve may have recorded the approval; run: scripts/m1a-check.sh status"
    "$POLARIZER" approve --config "$TOML" probe wait "$live" || code=$?
    [ "$code" = 0 ] || stop "polarizer approve exited $code"
  elif marker approve-changed; then
    echo "approve-changed is done. Next: exit Claude Code in terminal A, then run: scripts/m1a-check.sh finish"
    return 0
  elif [ "$(helper count "$LEDGER" wait-approved-alone)" -gt 0 ]; then
    echo "probe__wait's new definition is already approved; asking about /mcp now."
  else
    stop "no changed probe__wait is pending. Run: scripts/m1a-check.sh status"
  fi
  STATE="probe__wait's new definition is approved and the question was not answered; to answer it, run: scripts/m1a-check.sh approve-changed"
  ask "In terminal A, wait 5 seconds, type /mcp. Is probe__wait listed again without reconnecting? (y/n/not sure)" y n "not sure"
  printf -- '-- approve-changed: complete\n'
  echo "Next: exit Claude Code in terminal A, then run: scripts/m1a-check.sh finish"
}

cmd_finish() {
  local code=0 found
  STATE="nothing was changed"
  stop_on_leftovers finish
  STATE="to finish, run: scripts/m1a-check.sh finish"
  echo "\$ polarizer verify --ledger-dir $LEDGER"
  "$POLARIZER" verify --ledger-dir "$LEDGER" || code=$?
  echo "verify exit code: $code"
  write_toml plain
  echo "polarizer.toml written again from manual/polarizer.manual.toml, without the M1a lines"
  if [ "$TESTING" = 1 ]; then
    rm -f -- "$RUGPULL"
  else
    rm -f /tmp/polarizer-rugpull
  fi
  echo "removed $RUGPULL"
  found="$(leftovers)"
  echo "probe or Filesystem server processes still running: ${found:-none}"
  if [ "$TESTING" = 1 ]; then
    echo "guard: not run in testing"
  else
    code=0
    "$REPO/scripts/dev/guard.sh" check || code=$?
    echo "guard exit code: $code"
  fi
  printf -- '-- finish: complete\n'
  echo "Paste this: cat $RESULTS"
}

cmd_status() {
  local next
  if marker finish; then
    next="nothing: the check is finished. Paste this: cat $RESULTS"
  elif ! toml_ready; then
    next="scripts/m1a-check.sh reset"
  elif [ ! -s "$LEDGER/ledger.jsonl" ]; then
    next="start Claude Code. $START"
  elif [ "$(helper count "$LEDGER" group-approved)" = 0 ] || ! marker approve; then
    next="scripts/m1a-check.sh approve"
  elif [ "$(helper count "$LEDGER" drift)" = 0 ]; then
    next="in terminal A, reconnect polarizer from /mcp. Then run: scripts/m1a-check.sh rugpull"
  elif ! marker rugpull; then
    next="scripts/m1a-check.sh rugpull"
  elif ! marker approve-changed; then
    next="scripts/m1a-check.sh approve-changed"
  else
    next="exit Claude Code in terminal A, then run: scripts/m1a-check.sh finish"
  fi
  echo "Next: $next"
}

case "${1:-}" in
  reset | approve | rugpull | approve-changed | finish | status)
    [ $# = 1 ] || { echo "usage: scripts/m1a-check.sh reset|approve|rugpull|approve-changed|finish|status" >&2; exit 2; }
    [ -x "$PY" ] || { echo "m1a-check: $PY not found. Run: cd ~/code/polarizer && uv sync --locked" >&2; exit 2; }
    start_output "$1"
    "cmd_${1//-/_}"
    ;;
  *)
    echo "usage: scripts/m1a-check.sh reset|approve|rugpull|approve-changed|finish|status" >&2
    exit 2
    ;;
esac
