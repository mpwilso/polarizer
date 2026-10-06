#!/usr/bin/env bash
# A development guard for the author's machine, not part of Polarizer: it shows that a session
# working on Polarizer changed nothing in the author's other projects (Parallax, Loupe and ISR)
# or in Claude Code's MCP config. It needs a gitignored .guard-paths listing those clones, and is
# of no use on any other machine.
#
#   scripts/dev/guard.sh snapshot          record the state now in .guard/snapshot-<UTC timestamp>.txt
#   scripts/dev/guard.sh check [FILE]      report what changed since the newest snapshot, or since FILE
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
# Both commands also warn, and exit 1, if this repo has a .mcp.json at its root: any claude session
# started here would then spawn polarizer serve (CLAUDE.md rule 13).
#
# Exit codes, and the output that goes with each:
#   0  snapshot: recorded, last line "snapshot <file>".  check: nothing changed.
#   1  check: a change, each one printed.  Either command: the root .mcp.json warning (a snapshot
#      is still recorded).
#   2  usage error, no snapshot, or the state could not be read completely (git or python failed,
#      or an entry was unreadable): a "guard: stopped" line on stderr says so. A snapshot that stops is left as
#      .guard/partial-<UTC timestamp>.txt, never as snapshot-*.txt, so check can't use it.
# guard.sh's exit code is its own. Chained after other commands (guard.sh snapshot && wc ...),
# the shell reports the last command's code: on 2026-10-04 an exit 1 that looked like the
# snapshot's came from wc reading scripts/__pycache__.
#
# Snapshots are never deleted. check also reads the older format, where the summarized directory
# was stored line by line, by summarizing those lines the same way.
#
# It runs on macOS's bash 3.2 and BSD tools as well as on Linux: no bash 4 features, and the
# metadata (path, size, mtime) comes from Python's lstat rather than GNU find -printf, stat -c or
# date -d, in exactly the format GNU find printed it, so older snapshots still compare.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
paths_file="$root/.guard-paths"
state_dir="$root/.guard"
# A search by name on Oct 2, 2026 found no Loupe or ISR directories under ~/.local/share,
# ~/.config or ~/.cache. ~/isr-notes is listed by metadata only and never opened.
summary_paths=("$HOME/.local/share/parallax")
meta_paths=("$HOME/.config/parallax" "$HOME/isr-notes" "$HOME/.claude/settings.json")
claude_json="$HOME/.claude.json"
shown=40
tab=$'\t'  # a literal tab for sed: BSD sed has no escape for it

repos() {
  [ -f "$paths_file" ] || { echo "no .guard-paths in $root" >&2; exit 2; }
  grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$paths_file"
}

root_mcp_json() {  # prints the warning and returns 1 if <root>/.mcp.json exists
  if [ -e "$root/.mcp.json" ] || [ -L "$root/.mcp.json" ]; then
    echo "warning: $root/.mcp.json exists; a claude session started here would spawn polarizer serve (CLAUDE.md rule 13)"
    return 1
  fi
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

# Metadata only, from lstat; never opens a file. Symlinks are not followed. An entry it can't read
# is reported on stderr and makes it exit 1, after the rest is listed (as find did).
#   meta list PATH          "<path><TAB><size><TAB><mtime>" for PATH ("" as its path) and every
#                           entry under it, as GNU find -printf "%P\t%s\t%T@\n" printed them
#   meta info PATH          "<size> bytes, mtime <UTC time, whole seconds>"
#   meta newer DIR EPOCH    every entry under DIR that is not a directory and is newer than EPOCH,
#                           skipping anything named .git (find -name .git -prune -o ...)
meta() {
  python3 - "$@" <<'EOF'
import os, stat, sys, time
mode, top = sys.argv[1], os.fsencode(sys.argv[2])
out, failed = sys.stdout.buffer, False

def fail(path, e):
    global failed
    failed = True
    print(f"guard: cannot read {os.fsdecode(path)}: {e.strerror}", file=sys.stderr)

def visit(path, rel, st, emit, skip=lambda name: False):  # depth first, as find lists
    emit(path, rel, st)
    if not stat.S_ISDIR(st.st_mode):
        return
    try:
        with os.scandir(path) as it:
            children = list(it)
    except OSError as e:
        return fail(path, e)
    for c in children:
        if skip(c.name):
            continue
        try:
            cst = c.stat(follow_symlinks=False)
        except OSError as e:
            fail(c.path, e)
            continue
        visit(c.path, rel + b"/" + c.name if rel else c.name, cst, emit, skip)

def listed(path, rel, st):
    ns = st.st_mtime_ns
    out.write(b"%s\t%d\t%d.%09d0\n" % (rel, st.st_size, ns // 10**9, ns % 10**9))

if mode == "list":
    visit(top, b"", os.lstat(top), listed)
elif mode == "info":
    st = os.lstat(top)
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime_ns // 10**9))
    print(f"{st.st_size} bytes, mtime {when}")
elif mode == "newer":
    since = int(sys.argv[3]) * 10**9
    def newer(path, rel, st):
        if not stat.S_ISDIR(st.st_mode) and st.st_mtime_ns > since:
            out.write(path + b"\n")
    if os.path.basename(top) != b".git":
        visit(top, b"", os.lstat(top), newer, skip=lambda name: name == b".git")
sys.exit(1 if failed else 0)
EOF
}

listing() {  # "<path><TAB><size><TAB><mtime>" for every entry under $1, sorted; never contents
  meta list "$1" | LC_ALL=C sort  # an unreadable entry stops the guard
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
  meta info "$1"
}

# The full state, one tab-separated line per item. A failing step must stop it, also when it runs
# inside $(...), where bash turns -e off: callers use $(set -e; state). Every command substitution
# in it is an assignment of its own, so its failure stops it too.
state() {
  local repo head p s info
  while IFS= read -r repo; do
    head=$(git -C "$repo" rev-parse HEAD)
    printf 'repo:%s\thead\t%s\n' "$repo" "$head"
    git -C "$repo" --no-optional-locks status --porcelain --ignored | sed "s#^#repo:$repo${tab}status${tab}#"
  done < <(repos)
  for p in "${summary_paths[@]}"; do
    if [ -e "$p" ]; then s=$(listing "$p" | summarize); printf 'sum:%s\t%s\n' "$p" "$s"
    else printf 'sum:%s\tabsent\n' "$p"; fi
  done
  for p in "${meta_paths[@]}"; do
    if [ -e "$p" ]; then listing "$p" | sed "s#^#meta:$p${tab}#"
    else printf 'meta:%s\tabsent\n' "$p"; fi
  done
  mcp_hashes | sed 's#^#hash:#'
  info=$(info_of "$claude_json")
  printf 'info:%s\t%s\n' "$claude_json" "$info"
}

sections() {
  repos | sed 's#^#repo:#'
  printf 'sum:%s\n' "${summary_paths[@]}"
  printf 'meta:%s\n' "${meta_paths[@]}"
}

stopped() {  # EXIT trap while the state is read: say so on stderr and exit 2
  echo "guard: stopped while reading the state (exit $1); $2" >&2
  exit 2
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
  local stamp epoch at file partial
  stamp=$(date -u +%s.%Y%m%dT%H%M%SZ)  # one call, so both name the same second
  epoch=${stamp%%.*}; at=${stamp#*.}
  file="$state_dir/snapshot-$at.txt"; partial="$state_dir/partial-$at.txt"
  [ -e "$file" ] && { echo "snapshot $file already exists; wait a second" >&2; exit 2; }
  trap "stopped \$? 'no snapshot recorded; what was read is in $partial'" EXIT
  { printf '#epoch\t%s\n' "$epoch"; state; } > "$partial"
  trap - EXIT
  mv -- "$partial" "$file"
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
  root_mcp_json || exit 1
}

check() {
  local file="${1:-}"
  if [ -z "$file" ]; then
    file=$(ls -1 "$state_dir"/snapshot-*.txt 2>/dev/null | LC_ALL=C sort | tail -1 || true)
    [ -n "$file" ] || { echo "no snapshot; run scripts/dev/guard.sh snapshot first" >&2; exit 2; }
  fi
  [ -f "$file" ] || { echo "no such snapshot: $file" >&2; exit 2; }
  local epoch changed=0 now diffs sec d newer h p was is
  epoch=$(grep '^#epoch' "$file" | cut -f2)
  echo "since $(basename "$file")"
  trap 'stopped $? "nothing was compared"' EXIT
  now=$(set -e; state)
  trap - EXIT
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
    case "$sec" in
      repo:*) newer=$(meta newer "${sec#repo:}" "$epoch") \
        || stopped $? "the comparison is incomplete; nothing after ${sec#repo:} was compared";;
    esac
    if [ -z "$d" ] && [ -z "$newer" ]; then echo "${sec/:/ }: no changes"; continue; fi
    changed=1
    if [ -n "$d" ]; then echo "${sec/:/ }: changed"; sed "s#$sec${tab}##; s#${tab}# #g" <<< "$d" | show; fi
    if [ -n "$newer" ]; then echo "${sec/:/ }: files modified since the snapshot"; show <<< "$newer"; fi
  done < <(sections)
  h=$(grep '^[<>] hash:' <<< "$diffs" || true)
  if [ -n "$h" ]; then
    echo "hash: MCP config in $claude_json changed"; changed=1
    sed 's#hash:##' <<< "$h" | awk -F "$tab" '{print "    " $1 ": " substr($2, 1, 16)}'
  else
    echo "hash: MCP config in $claude_json unchanged ($(grep -c '^hash:' <<< "$now") locations)"
  fi
  echo "info $claude_json: was $(grep '^info:' "$file" | cut -f2); now $(info_of "$claude_json") (informational)"
  root_mcp_json || changed=1
  exit "$changed"
}

case "${1:-}" in
  snapshot) snapshot ;;
  check) check "${2:-}" ;;
  *) echo "usage: scripts/dev/guard.sh snapshot | check [snapshot-file]" >&2; exit 2 ;;
esac
