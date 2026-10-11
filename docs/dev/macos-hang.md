# The macOS rug-pull check hang

An investigation log, written as it goes. On macos-latest, `tests/test_rugpull_check.py::test_tampered_ledger_fails` once timed out after 300 s running `bash scripts/rugpull-check.sh` (CI run 38168770514, attempt 1, job 114556753181, commit on branch `ci-flake`). The rerun passed. No other macOS timeout is in the last 100 runs.

## What the failed job shows

- **Runner:** image macos-26-arm64, version 20260907.0351.1. Later scratch jobs on the same image name report `/bin/bash` 3.2.57, `/usr/bin/tee`, macOS 26.6.2 and 3 CPUs.
- **Where the script was is not known.** The repr of `stdout_seq` in the traceback is cut by pytest: its first chunk starts at `== conditions` and ends at the `run C arguments: ... < /dev/null` line, and the list then goes on (`, ...]`). So the output went further than the conditions block, and the line with `--allowedTools=mcp__pz__probe__wait` is only the cut point of the repr, not proof that a claude run hung.
- **bash was alive at 300 s:** the traceback shows `Popen: returncode: None`, and the test's `AtExit` thread was blocked in `proc.wait()`, which sets the return code as soon as bash exits.
- **Only tee was left at the end:** the job's cleanup printed `Terminate orphan process: pid (9885) (tee)` and nothing else, about 40 s after the timeout. Four passing macOS jobs checked (runs 38171325562, 38170604445, 38168770514 attempt 2, 38166322950) have no orphan line. So bash, the stub and serve were gone by then, but tee still had its pipe open for reading, or was blocked.
- **Time:** the failed job's pytest took 605.81 s, the passing rerun 411.44 s.

The runner finds orphans by an environment variable the job sets. The test passes the whole environment to bash, so bash, tee, the stub and serve carry it. Serve starts the probe through the MCP SDK, which gives it only a few variables, so a leftover probe would not be listed.

## Iterations

### 1. Does it reproduce on Linux? (not reproduced)

Hypothesis: a race in the script or the stub that bash 5.2 hides less often than bash 3.2, so a stress loop on Linux shows it at some rate.

- 100 runs of `test_tampered_ledger_fails` in four parallel loops (bash 5.2.21, WSL2, 24 CPUs), each run with its own `--basetemp` and a 150 s outer limit: 96 passed, 0 hung. The other 4 failed with exit -13 (SIGPIPE), all at the same moment: the first version of the scratch diagnostics (iteration 2) matched processes by name and killed those loops' tee processes. Not a finding about the script.
- The whole test file in four loops pinned to one CPU (`taskset -c 0`): 22 iterations passed, 0 hung, when the session's scratch directory was lost.

Ruled out on Linux at these counts. Nothing yet says the hang needs macOS rather than luck.

### 2. A plain repeat on macOS with diagnostics (not caught)

Hypothesis: the hang happens at some rate on macOS whenever the script runs, so repeating the test file there catches it, and the diagnostics say which process held things up.

Branch `ci-scratch/rugpull-diag` (commit 6710435): `ci.yml` runs only five macos-latest jobs, each looping `pytest tests/test_rugpull_check.py` for 40 minutes. The fixture's timeout is 120 s there, and on a timeout `tests/helpers/hangdiag.py` prints the whole output so far, `ps` and `lsof` of the test's processes and anything named tee, polarizer or probe_server, every file in the test's directory, `sample` of each process, then SIGABRT (with `PYTHONFAULTHANDLER=1`, for Python stacks in the err files) and SIGKILL to the test's own processes only.

Run 38173063214: 221 iterations passed, 0 failed (44, 42, 45, 45 and 45 per job), 46 to 71 s each. That is about 1,550 runs of the script, 221 of them in tamper mode. No hang.

So the hang is rare when the test file runs alone: under about 1 in 1,500 script runs.

### 3. The same repeat under load on macOS (not caught)

Hypothesis: the hang needs a busy machine, as in the full suite, where other tests start servers and scripts while the rug-pull check runs.

Branch `ci-scratch/rugpull-load` (commit d102480): each of five macos-latest jobs loops the rug-pull test file for 40 minutes while a second loop runs the rest of the suite beside it, each with its own `--basetemp`. After a timeout the diagnostics now also watch for 60 s, to see which processes end and which stay, as bash ended and tee stayed in the failed job.

Run 38178206865: 205 iterations passed, 0 failed (42, 39, 41, 40 and 43), about 1,435 script runs. The slowest single test call was 16.7 s, far from any timeout. The suite beside it ran 7 times per job (2 failures each time, from running beside another pytest; not looked into, since that loop was only load). No job printed an orphan process at cleanup.

### 4. Real bash 3.2 on Linux (not reproduced)

Hypothesis: the hang is in bash 3.2's job handling (the `wait` on tee, or a child's exit while the EXIT trap runs), which the emulation in `tests/helpers/slowtee.py` doesn't reproduce.

bash 3.2.57 built from the GNU tarball (sha256 3fa9daf85ebf35068f090ce51283ddeeb3c75eb5bc70b1a4a7cb05868bfe06a4) in a scratch directory outside the repo, at -O1 with gcc 13.3 (gcc crashed on subst.c at -O2). The shipped y.tab.c predates the later parse.y patches, so the build has one call fixed by hand and `parser_remaining_input` copied from parse.y; jobs.c and the wait code are 3.2.57's. A `bash` first on PATH made the test run it (checked through /proc/<pid>/exe). Six loops of the whole test file, 30 each: 180 passed, 0 failed, 0 hung, about 1,260 script runs.

Not reproduced. Apple's /bin/bash has its own patches, so this rules out only stock 3.2.57 on Linux.
