#!/usr/bin/env bash
# Shows that a Polarizer session changed nothing in the other projects.
#
#   scripts/guard.sh snapshot          record the state now in .guard/snapshot-<UTC timestamp>.txt
#   scripts/guard.sh check [FILE]      report what changed since the newest snapshot, or since FILE
#                                      (exit 1 if anything did)
#
# Repos come from .guard-paths (gitignored), one path per line; # starts a comment.
# For each repo: HEAD, `git status --porcelain --ignored`, and files modified since the snapshot.
# Tool directories are compared by metadata only (path, size, mtime); their contents are never
# opened. Parallax's runtime directory is recorded as a summary (file count, total size, newest
# mtime, and one sha256 over the sorted path, size and mtime lines); the others line by line.
# Git is only read, with --no-optional-locks so status doesn't refresh the index.
# ~/.claude.json changes on every claude run, so its size and mtime are informational.
# What counts there is MCP config: a sha256 of the top-level mcpServers object and of
# projects[<path>].mcpServers for each guarded repo and this repo. Only hashes are
# printed or stored, never contents.
#
# A snapshot is one line per item, "<section><TAB><fields...>", so check can diff it by section.
# Snapshots are never deleted. check also reads the older format, where the summarized directory
# was stored line by line, by summarizing those lines the same way.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
paths_file="$root/.guard-paths"
state_dir="$root/.guard"
# A search by name on Oct 2, 2026 found no Loupe or ISR directories under ~/.local/share,
# ~/.config or ~/.cache. ~/isr-notes is listed by metadata only and never opened.
summary_paths=("$HOME/.local/share/parallax")
meta_paths=("$HOME/.config/parallax" "$HOME/isr-notes" "$HOME/.claude/settings.json")
claude_json="$HOME/.claude.json"
shown=40

repos() {
  [ -f "$paths_file" ] || { echo "no .guard-paths in $root" >&2; exit 2; }
  grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$paths_file"
}

mcp_hashes() {  # "<label><TAB><sha256|absent>" per MCP config location in ~/.claude.json
  python3 - "$claude_json" "$root" $(repos) <<'EOF'
import hashlib, json, sys, time
path, wanted = sys.argv[1], sys.argv[2:]
for attempt in range(5):  # Claude Code may be rewriting the file
    try:
        with open(path, encoding="utf-8") as f: cfg = json.load(f)
        break
    except FileNotFoundError: print("file\tabsent"); sys.exit(0)
    except ValueError: time.sleep(0.2)
else: print("file\tunparseable"); sys.exit(0)
def h(v):
    if v is None: return "absent"
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
print("mcpServers (user scope)\t" + h(cfg.get("mcpServers")))
projects = cfg.get("projects") or {}
for p in wanted:
    print(f"projects[{p}].mcpServers\t" + h((projects.get(p) or {}).get("mcpServers")))
EOF
}

listing() {  # "<path><TAB><size><TAB><mtime>" for every entry under $1, sorted; never contents
  find "$1" -printf "%P\t%s\t%T@\n" 2>/dev/null | LC_ALL=C sort
}

summarize() {  # stdin: listing lines; stdout: one summary line
  python3 -c '
import hashlib, sys
lines = sys.stdin.read().splitlines()
if not lines:
    print("absent"); sys.exit(0)
size = sum(int(l.split("\t")[1]) for l in lines)
newest = max((l.split("\t")[2] for l in lines), key=float)
digest = hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()
print(f"files {len(lines)}, bytes {size}, newest {newest}, sha256 {digest}")'
}

info_of() {
  [ -e "$1" ] || { echo "absent"; return; }
  echo "$(stat -c %s "$1") bytes, mtime $(date -u -d "@$(stat -c %Y "$1")" +%Y-%m-%dT%H:%M:%SZ)"
}

state() {  # the full state, one tab-separated line per item
  while IFS= read -r repo; do
    printf 'repo:%s\thead\t%s\n' "$repo" "$(git -C "$repo" rev-parse HEAD)"
    git -C "$repo" --no-optional-locks status --porcelain --ignored | sed "s#^#repo:$repo\tstatus\t#"
  done < <(repos)
  for p in "${summary_paths[@]}"; do
    if [ -e "$p" ]; then printf 'sum:%s\t%s\n' "$p" "$(listing "$p" | summarize)"
    else printf 'sum:%s\tabsent\n' "$p"; fi
  done
  for p in "${meta_paths[@]}"; do
    if [ -e "$p" ]; then listing "$p" | sed "s#^#meta:$p\t#"
    else printf 'meta:%s\tabsent\n' "$p"; fi
  done
  mcp_hashes | sed 's#^#hash:#'
  printf 'info:%s\t%s\n' "$claude_json" "$(info_of "$claude_json")"
}

sections() {
  repos | sed 's#^#repo:#'
  printf 'sum:%s\n' "${summary_paths[@]}"
  printf 'meta:%s\n' "${meta_paths[@]}"
}

show() { awk -v n="$shown" 'NR<=n {print "    " $0} END {if (NR>n) print "    ... and " NR-n " more"}'; }

# The snapshot's line for a summarized directory. An older snapshot stored that directory line by
# line under "meta:"; those lines are summarized exactly as a live listing is.
old_summary() {
  local file=$1 p=$2 line
  line=$(grep -F "sum:$p	" "$file" | cut -f2- || true)
  if [ -n "$line" ]; then echo "$line"; return; fi
  if grep -q -F "meta:$p	" "$file"; then
    grep -F "meta:$p	" "$file" | cut -f2- | summarize
  else
    echo "not in snapshot"
  fi
}

snapshot() {
  mkdir -p "$state_dir"
  local epoch at file
  epoch=$(date -u +%s); at=$(date -u -d "@$epoch" +%Y%m%dT%H%M%SZ)
  file="$state_dir/snapshot-$at.txt"
  [ -e "$file" ] && { echo "snapshot $file already exists; wait a second" >&2; exit 2; }
  { printf '#epoch\t%s\n' "$epoch"; state; } > "$file"
  while IFS= read -r repo; do
    dirty=$(grep -F "repo:$repo	status	" "$file" | cut -f3 | grep -vc '^!!' || true)
    head=$(grep -F "repo:$repo	head	" "$file" | cut -f3)
    if [ "$dirty" -gt 0 ]; then
      echo "repo $repo: dirty at snapshot ($dirty entries, not counting ignored files)"
      grep -F "repo:$repo	status	" "$file" | cut -f3 | grep -v '^!!' | show
    else
      echo "repo $repo: clean at snapshot, HEAD ${head:0:7}"
    fi
  done < <(repos)
  for p in "${summary_paths[@]}"; do echo "summary $p: $(grep -F "sum:$p	" "$file" | cut -f2)"; done
  for p in "${meta_paths[@]}"; do echo "meta $p: $(grep -c -F "meta:$p	" "$file") entries recorded"; done
  grep '^hash:' "$file" | while IFS=$'\t' read -r label hash; do echo "hash ${label#hash:}: ${hash:0:16}"; done
  echo "info $claude_json: $(grep '^info:' "$file" | cut -f2)"
  echo "snapshot $file"
}

check() {
  local file="${1:-}"
  if [ -z "$file" ]; then
    file=$(ls -1 "$state_dir"/snapshot-*.txt 2>/dev/null | LC_ALL=C sort | tail -1 || true)
    [ -n "$file" ] || { echo "no snapshot; run scripts/guard.sh snapshot first" >&2; exit 2; }
  fi
  [ -f "$file" ] || { echo "no such snapshot: $file" >&2; exit 2; }
  local epoch changed=0 now diffs sec d newer h p was is
  epoch=$(grep '^#epoch' "$file" | cut -f2)
  echo "since $(basename "$file")"
  now=$(state)
  # Summarized directories are compared on their own below, in either snapshot format.
  local skip=()
  for p in "${summary_paths[@]}"; do skip+=(-e "^sum:$p	" -e "^meta:$p	"); done
  diffs=$(diff <(grep -v -e '^#' -e '^info:' "${skip[@]}" "$file") <(grep -v -e '^info:' "${skip[@]}" <<< "$now") | grep '^[<>]' || true)
  while IFS= read -r sec; do
    case "$sec" in
      sum:*)
        p=${sec#sum:}; was=$(old_summary "$file" "$p"); is=$(grep -F "sum:$p	" <<< "$now" | cut -f2)
        if [ "$was" = "not in snapshot" ]; then echo "summary $p: not in this snapshot; now $is (informational)"
        elif [ "$was" = "$is" ]; then echo "summary $p: no changes"
        else echo "summary $p: changed"; echo "    was $was"; echo "    now $is"; changed=1; fi
        continue ;;
      meta:*)
        if ! grep -q -F "$sec	" "$file"; then
          echo "meta ${sec#meta:}: not in this snapshot; $(grep -c -F "$sec	" <<< "$now") entries now (informational)"; continue
        fi ;;
    esac
    d=$(grep -F "$sec	" <<< "$diffs" || true)
    newer=""
    case "$sec" in repo:*) newer=$(find "${sec#repo:}" -name .git -prune -o ! -type d -newermt "@$epoch" -print);; esac
    if [ -z "$d" ] && [ -z "$newer" ]; then echo "${sec/:/ }: no changes"; continue; fi
    changed=1
    if [ -n "$d" ]; then echo "${sec/:/ }: changed"; sed "s#$sec\t##; s#\t# #g" <<< "$d" | show; fi
    if [ -n "$newer" ]; then echo "${sec/:/ }: files modified since the snapshot"; show <<< "$newer"; fi
  done < <(sections)
  h=$(grep '^[<>] hash:' <<< "$diffs" || true)
  if [ -n "$h" ]; then
    echo "hash: MCP config in $claude_json changed"; changed=1
    sed 's#hash:##' <<< "$h" | awk -F'\t' '{print "    " $1 ": " substr($2, 1, 16)}'
  else
    echo "hash: MCP config in $claude_json unchanged ($(grep -c '^hash:' <<< "$now") locations)"
  fi
  echo "info $claude_json: was $(grep '^info:' "$file" | cut -f2); now $(info_of "$claude_json") (informational)"
  exit "$changed"
}

case "${1:-}" in
  snapshot) snapshot ;;
  check) check "${2:-}" ;;
  *) echo "usage: scripts/guard.sh snapshot | check [snapshot-file]" >&2; exit 2 ;;
esac
