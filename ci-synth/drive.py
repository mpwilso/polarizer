"""Runs skeleton.sh as tests/test_rugpull_check.py runs rugpull-check.sh, until a deadline,
and reports any run that does not finish in 10 s: the output so far, ps, lsof and a sample of
tee, then kills it."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
deadline = time.monotonic() + float(sys.argv[1])
runs = hangs = slow = 0
while time.monotonic() < deadline:
    runs += 1
    proc = subprocess.Popen(["bash", str(HERE / "skeleton.sh")], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    threading.Thread(target=proc.wait, daemon=True).start()
    started = time.monotonic()
    try:
        out, err = proc.communicate(timeout=30)
        took = time.monotonic() - started
        complete = out.rstrip().split("\n")[-1].startswith("Paste the output of")
        if not complete:
            print(f"run {runs}: short output: {out[-200:]!r} {err[-200:]!r}", flush=True)
        if took > 5:
            slow += 1
            print(f"run {runs}: took {took:.1f} s, output complete: {complete}", flush=True)
    except subprocess.TimeoutExpired as e:
        hangs += 1
        so_far = (e.output or b"").decode("utf-8", "replace")
        ps = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,stat=,command="], capture_output=True, text=True).stdout
        kids = [l for l in ps.splitlines() if l.split()[1] == str(proc.pid) or l.split()[0] == str(proc.pid)]
        print(f"=== HANG run {runs}, bash {proc.pid}, returncode {proc.returncode}, output ends: {so_far[-120:]!r}")
        print("\n".join(kids))
        pids = ",".join(l.split()[0] for l in kids)
        print(subprocess.run(["lsof", "-nP", "-p", pids], capture_output=True, text=True).stdout)
        lsof_all = subprocess.run(["lsof", "-nP", "-u", str(os.getuid())], capture_output=True, text=True).stdout
        print("all FIFOs:\n" + "\n".join(l for l in lsof_all.splitlines() if " FIFO " in l))
        for l in kids:
            pid = l.split()[0]
            s = subprocess.run(["sample", pid, "1"], capture_output=True, text=True).stdout
            i = s.find("Call graph:")
            j = s.find("Total number in stack")
            print(f"--- sample {pid}:\n" + (s[i:j] if i >= 0 else s[-1500:]))
        for l in kids:
            try:
                os.kill(int(l.split()[0]), signal.SIGKILL)
            except OSError:
                pass
        proc.wait()
        sys.stdout.flush()
print(f"variant {os.environ.get('VARIANT', 'orig')}: runs {runs} hangs {hangs} over 5 s {slow}", flush=True)
