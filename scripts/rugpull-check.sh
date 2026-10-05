#!/usr/bin/env bash
# Manual only: it calls a model three times, so it costs money and never runs in CI.
#
# The rug pull end to end, headless, with no questions: three `claude -p` runs (small model)
# with polarizer serve as the only MCP server and the probe as Polarizer's only upstream.
#   A  the probe's original definitions, approved: the model's call reaches the probe.
#   B  the probe serves wait with a changed description (the rug pull), not approved:
#      Polarizer records tool.drift and hides it, so no call reaches the probe.
#   then the new definition is approved through the library, as `polarizer approve` does.
#   C  the same changed definition, now approved: the call reaches the probe again.
# Each run's Claude Code is given its own mcp config, and the two configs differ only in
# PROBE_PHASE (original or changed), which Polarizer passes to the probe. So nothing depends on
# how many times the probe started (docs/dev/MANUAL-CHECK.md, M1a).
#
# Before run A it primes the ledger (live_check.py's priming: a serve run with stdin closed,
# then the group approval, with no model). Pass or fail is read only from the ledger and the
# probe's log; the model's replies are printed for information. Parsing and the checks are in
# scripts/rugpull_check.py.
#
# Everything it writes goes in a new temporary directory, removed only if every check passed;
# otherwise it is kept and its path printed. The output also goes to
# /tmp/rugpull-check-results.txt. It reads nothing under ~/.claude, and only stats
# ~/.claude.json, which Claude Code updates on every run. It never touches polarizer.toml,
# manual/mcp.json or any other ledger.
#
# Testing only: with POLARIZER_CHECK_TESTING=1, CLAUDE_BIN names the claude to run (a stub) and
# POLARIZER_CHECK_RESULTS the results file; both are required then, and ignored otherwise.
set -euo pipefail
umask 077

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
HELPER="$REPO/scripts/rugpull_check.py"
NAME=polarizer-rugpull-check

if [ "${POLARIZER_CHECK_TESTING:-0}" = 1 ]; then
  CLAUDE="${CLAUDE_BIN:-}"
  RESULTS="${POLARIZER_CHECK_RESULTS:-}"
  case "$CLAUDE:$RESULTS" in
    /*:/*) ;;
    *) echo "rugpull-check: POLARIZER_CHECK_TESTING=1 needs CLAUDE_BIN and POLARIZER_CHECK_RESULTS as absolute paths" >&2; exit 2 ;;
  esac
else
  CLAUDE="$(command -v claude || true)"
  RESULTS=/tmp/rugpull-check-results.txt
fi
[ -x "$PY" ] || { echo "rugpull-check: $PY not found. Run: cd ~/code/polarizer && uv sync --locked" >&2; exit 2; }
[ -n "$CLAUDE" ] && [ -x "$CLAUDE" ] || { echo "rugpull-check: claude is not on PATH" >&2; exit 2; }

# The temp directory: a new one under the system temp directory. It is removed at the end only
# if its path is still exactly what mktemp made there, with this script's name.
TMPBASE="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
DIR="$(mktemp -d "$TMPBASE/$NAME.XXXXXX")"
is_our_dir() {
  [ "$TMPBASE" != / ] \
    && [ "$(dirname "$DIR")" = "$TMPBASE" ] \
    && [ -d "$DIR" ] && [ ! -L "$DIR" ] \
    && case "$(basename "$DIR")" in "$NAME".??????) true ;; *) false ;; esac
}
is_our_dir || { echo "rugpull-check: mktemp gave $DIR, not $TMPBASE/$NAME.XXXXXX" >&2; exit 2; }

# Everything from here on goes to the terminal and to the results file. tee is a job of this
# shell reading a named pipe in the temp directory, not a process substitution: bash 3.2 (macOS)
# can't wait for a process substitution, so the script could exit before tee had written. The
# pipe is removed once both ends are open. A Ctrl+C before then stops the script once they are.
SIGNALLED=0
trap 'SIGNALLED=1' INT TERM
mkfifo "$DIR/tee"
(trap '' INT TERM; exec tee "$RESULTS") < "$DIR/tee" &
TEE_PID=$!
exec > "$DIR/tee" 2>&1
rm -- "$DIR/tee"
end_output() {
  exec >&- 2>&-
  wait "$TEE_PID" 2> /dev/null || true
}
trap end_output EXIT
on_signal() {
  printf '\nstopped by Ctrl+C; the temp directory is kept: %s\n' "$DIR"
  exit 130
}
trap on_signal INT TERM
[ "$SIGNALLED" = 0 ] || on_signal

# The documented conditions are a plain terminal. Inside a Claude Code session its CLAUDE_CODE_*
# variables reach each claude run, so the output says so first. Names only, never values.
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  echo "notice: running inside a Claude Code session (CLAUDE_CODE_SESSION_ID is set). The documented conditions are a plain terminal. These CLAUDE_CODE_* variables are passed on to each claude run (names only): $(compgen -e | grep '^CLAUDE_CODE_' | sort | paste -sd ' ' -)"
fi

helper() {
  "$PY" "$HELPER" "$@"
}

claude_json_stat() {
  if [ -e "$HOME/.claude.json" ]; then
    "$PY" -c 'import os, sys, time; s = os.stat(sys.argv[1]); print(s.st_size, "bytes, mtime", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s.st_mtime)))' "$HOME/.claude.json"
  else
    echo "absent"
  fi
}

# Waits up to 10 s for the serve processes started with this check's config to exit, so each
# run's ledger entries are all in before its end mark. Prints any still running.
serve_left() {
  local i found=""
  command -v pgrep > /dev/null || { echo "pgrep not found; not checked"; return 0; }
  for i in $(seq 1 50); do
    found="$(pgrep -f -- "$DIR/polarizer.toml" || true)"
    [ -z "$found" ] && break
    sleep 0.2
  done
  echo "${found:-none}" | tr '\n' ' '
  echo
}

PROMPT="Call the tool mcp__pz__probe__wait exactly once, with seconds set to 1. Do not call any other tool and do not retry. Then reply in one line saying what happened. If that tool is not available to you, do not call anything; reply in one line saying it is not available."
args_for() {
  ARGS=(
    -p "$PROMPT"
    --model haiku
    --strict-mcp-config
    --mcp-config "$DIR/mcp-$1.json"
    --allowedTools=mcp__pz__probe__wait
    --permission-mode default
    --no-session-persistence
    --output-format json
  )
}
WAS_SET="unset"
[ -n "${MCP_TOOL_TIMEOUT+x}" ] && WAS_SET="set"
unset MCP_TOOL_TIMEOUT

helper setup "$DIR" "$REPO"

echo "== conditions"
echo "claude: $CLAUDE"
echo "claude version: $("$CLAUDE" --version < /dev/null 2>&1 | head -n 1)"
for run in A B C; do
  case "$run" in A) phase=original ;; *) phase=changed ;; esac
  args_for "$phase"
  printf 'run %s arguments:' "$run"; printf ' %q' "${ARGS[@]}"; printf ' < /dev/null\n'
done
echo "permission mode: default (passed with --permission-mode)"
echo "auto mode: not set here; user-scope settings may set it and were not read"
echo "environment: the script sets no variable for claude; it unsets MCP_TOOL_TIMEOUT (was $WAS_SET)"
echo "Polarizer gets PROBE_PHASE from the run's mcp config (env), and passes it to the probe"
echo "CLAUDE_* and ANTHROPIC_* variables set (names only): $(compgen -e | grep -E '^(CLAUDE|ANTHROPIC)_' | sort | tr '\n' ' ')"
for name in MCP_TIMEOUT MCP_TOOL_TIMEOUT MCP_CONNECT_TIMEOUT_MS CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT; do
  echo "$name=${!name-<unset>}"
done
echo "working directory: $DIR"
echo "results file: $RESULTS"
BEFORE="$(claude_json_stat)"

echo "== priming the ledger (no model)"
PRIMED=1
helper prime "$DIR" || PRIMED=0

run_claude() {
  local run="$1" phase="$2" code=0
  echo "== run $run ($phase phase)"
  helper mark "$DIR" "$run.start"
  args_for "$phase"
  (cd "$DIR" && "$CLAUDE" "${ARGS[@]}" < /dev/null > "$DIR/run-$run.out" 2> "$DIR/run-$run.err") || code=$?
  echo "$code" > "$DIR/run-$run.code"
  echo "serve processes still running for this check: $(serve_left)"
  helper mark "$DIR" "$run.end"
  helper report "$DIR" "$run"
}

if [ "$PRIMED" = 1 ]; then
  run_claude A original
  run_claude B changed
  echo "== approving probe wait's new definition (no model)"
  helper approve "$DIR" || echo "approving failed; run C goes ahead, and its checks will say what it saw"
  run_claude C changed
else
  echo "priming failed, so claude was not run"
fi
AFTER="$(claude_json_stat)"

echo "== cost"
helper costs "$DIR"
echo "~/.claude.json bookkeeping: before $BEFORE; after $AFTER"
echo "  (Claude Code updates it on every run; scripts/guard.sh check compares its MCP config)"

echo "== checks"
CODE=0
helper check "$DIR" || CODE=$?

if [ "$CODE" = 0 ] && is_our_dir; then
  rm -rf -- "$DIR"
  echo "removed the temp directory $DIR"
else
  echo "the temp directory is kept: $DIR"
fi
echo "Paste the output of: cat $RESULTS"
exit "$CODE"
