#!/usr/bin/env bash
# The output plumbing of scripts/rugpull-check.sh, with a stand-in body.
set -euo pipefail
umask 077
DIR="$(mktemp -d "${TMPDIR:-/tmp}/synth.XXXXXX")"
RESULTS="$DIR/results.txt"
SIGNALLED=0
trap 'SIGNALLED=1' INT TERM
mkfifo "$DIR/tee"
(trap '' INT TERM; exec tee "$RESULTS") < "$DIR/tee" &
TEE_PID=$!
exec > "$DIR/tee" 2>&1
[ "${VARIANT:-orig}" = keep ] || rm -- "$DIR/tee"
end_output() {
  local i
  exec >&- 2>&-
  if [ "${VARIANT:-orig}" = fix ]; then
    i=0
    while [ "$i" -lt 100 ] && kill -0 "$TEE_PID" 2> /dev/null; do
      sleep 0.1
      i=$((i + 1))
    done
    if kill -0 "$TEE_PID" 2> /dev/null; then
      kill -KILL "$TEE_PID" 2> /dev/null || true
    fi
  fi
  wait "$TEE_PID" 2> /dev/null || true
  rm -rf -- "$DIR"
}
trap end_output EXIT
on_signal() { printf '\nstopped\n'; exit 130; }
trap on_signal INT TERM
[ "$SIGNALLED" = 0 ] || on_signal
for i in 1 2 3 4 5 6; do
  echo "== part $i"
  echo "version: $(/bin/echo 0.0.0 < /dev/null 2>&1 | head -n 1)"
  (cd "$DIR" && /bin/sh -c 'echo out; echo err >&2' < /dev/null > "$DIR/run-$i.out" 2> "$DIR/run-$i.err") || true
  echo "0" > "$DIR/run-$i.code"
  echo "left: $(for j in 1 2; do found="$(true)"; done; echo none)"
  printf 'line %s: %s\n' "$i" "$(printf 'x%.0s' $(seq 1 120))"
done
echo "Paste the output of: cat $RESULTS"
exit 1
