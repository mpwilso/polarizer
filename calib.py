"""ci-scratch only: which processes does the runner's orphan cleanup list when the rug-pull
script is stuck in run C's claude (a stub that sleeps), and its caller gives up without killing
anything, as pytest does after a communicate timeout?"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
base = Path(tempfile.mkdtemp())
for d in ("tmp", "home", "stub"):
    (base / d).mkdir()
stub = base / "claude"
stub.write_text(
    "#!/bin/sh\n"
    f'n=$(ls "{base}/stub" | grep -c "^run-")\n'
    f'if [ "$1" != --version ] && [ "$n" -ge 2 ]; then echo hanging >&2; exec sleep 3600; fi\n'
    f'exec "{sys.executable}" "{ROOT}/tests/helpers/fake_claude.py" "$@"\n'
)
stub.chmod(0o755)
env = {k: v for k, v in os.environ.items() if not k.startswith(("STUB_", "PROBE_", "CLAUDE_CODE_"))}
env.update(
    HOME=str(base / "home"), TMPDIR=str(base / "tmp"), STUB_DIR=str(base / "stub"),
    STUB_MODE="tamper", CLAUDE_BIN=str(stub), POLARIZER_CHECK_RESULTS=str(base / "results.txt"),
    POLARIZER_CHECK_TESTING="1",
)
proc = subprocess.Popen(
    ["bash", str(ROOT / "scripts" / "rugpull-check.sh")], env=env, stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
try:
    proc.communicate(timeout=40)
    print("finished?", proc.returncode)
except subprocess.TimeoutExpired as e:
    print("timed out; output so far:\n" + (e.output or b"").decode())
    print(f"bash pid {proc.pid}, returncode {proc.returncode}")
    print(subprocess.run(["ps", "-axww", "-o", "pid,ppid,pgid,stat,command"], capture_output=True, text=True).stdout)
# Leave everything running, as pytest did.
os._exit(0)
