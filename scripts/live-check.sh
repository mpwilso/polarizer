#!/usr/bin/env bash
# Manual only: it calls a model, so it costs money and never runs in CI.
#
# One headless Claude Code run (`claude -p`, small model) with polarizer serve as its only MCP
# server, the probe as Polarizer's only upstream, and a wiretap between Claude Code and
# Polarizer. Claude Code's own limit (MCP_TOOL_TIMEOUT=5000) cancels a 14 s wait call. It
# passes only if the probe's log shows notifications/cancelled within 2 s of Claude Code's
# cancel and the ledger has a call.returned with outcome cancelled for that call; otherwise it
# exits 1 and names the failed check (docs/m0-plan.md, build step 7).
#
# Everything it writes goes in a new temporary directory, which it prints and leaves in place.
# It reads nothing under ~/.claude, and only stats ~/.claude.json, which Claude Code updates
# on every run.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "live-check: $PY not found; run uv sync --locked first" >&2; exit 2; }
command -v claude > /dev/null || { echo "live-check: claude is not on PATH" >&2; exit 2; }

DIR="$(mktemp -d "${TMPDIR:-/tmp}/polarizer-live-check.XXXXXX")"
"$PY" "$REPO/scripts/live_check.py" setup "$DIR" "$REPO"

PROMPT="Call the tool mcp__pz__probe__wait exactly once, with seconds set to 14. Do not call any other tool and do not retry. Then reply in one line saying what happened."
ARGS=(
  -p "$PROMPT"
  --model haiku
  --strict-mcp-config
  --mcp-config "$DIR/mcp.json"
  --allowedTools=mcp__pz__probe__wait
  --permission-mode default
  --no-session-persistence
  --output-format json
)
export MCP_TOOL_TIMEOUT=5000

claude_json_stat() {
  if [ -e "$HOME/.claude.json" ]; then
    "$PY" -c 'import os, sys, time; s = os.stat(sys.argv[1]); print(s.st_size, "bytes, mtime", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s.st_mtime)))' "$HOME/.claude.json"
  else
    echo "absent"
  fi
}

echo "== conditions"
echo "claude version: $(claude --version)"
printf 'claude arguments:'; printf ' %q' "${ARGS[@]}"; printf ' < /dev/null\n'
echo "permission mode: default (passed with --permission-mode)"
echo "auto mode: not set here; user-scope settings may set it and were not read"
echo "CLAUDE_* and ANTHROPIC_* variables set (names only): $(compgen -e | grep -E '^(CLAUDE|ANTHROPIC)_' | sort | tr '\n' ' ')"
for name in MCP_TIMEOUT MCP_TOOL_TIMEOUT MCP_CONNECT_TIMEOUT_MS CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT; do
  echo "$name=${!name-<unset>}"
done
echo "working directory: $DIR"
BEFORE="$(claude_json_stat)"

echo "== running claude"
set +e
(cd "$DIR" && claude "${ARGS[@]}" < /dev/null > "$DIR/claude.json" 2> "$DIR/claude.stderr")
CODE=$?
set -e
AFTER="$(claude_json_stat)"
echo "claude exit code: $CODE"
"$PY" - "$DIR/claude.json" <<'EOF'
import json, sys
try:
    out = json.load(open(sys.argv[1], encoding="utf-8"))
except ValueError:
    print("claude output: not JSON; see claude.json and claude.stderr")
    sys.exit(0)
print(f"model reply: {out.get('result')!r}")
print(f"cost: {out.get('total_cost_usd')} USD (claude's own total_cost_usd)")
EOF
echo "~/.claude.json bookkeeping: before $BEFORE; after $AFTER"
echo "  (Claude Code updates it on every run; scripts/guard.sh check compares its MCP config)"

echo "== polarizer verify"
"$PY" -m polarizer verify --ledger-dir "$DIR/ledger" || true

echo "== checks"
"$PY" "$REPO/scripts/live_check.py" check "$DIR"
