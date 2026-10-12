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

### 5. Rug-pull runs after the whole suite, in the same pytest process (caught)

Hypothesis: the hang needs what the full suite leaves behind (in the pytest process, or on the machine), since the one failure came mid-suite and 3,000 runs outside it passed.

Branch `ci-scratch/rugpull-suite` (commit dd8c4c6): each of five macos-latest jobs runs `pytest --keep-duplicates tests` followed by the rug-pull file 20 times, in one pytest process, for 45 minutes (2 rounds per job, about 22 minutes each, so about 280 script runs per job).

Run 38182880180, job 114598985865, round 1: `test_claude_exiting_nonzero_fails` timed out (120 s here) at 23:29:33. The diagnostics show:

- **The script had finished.** Its output so far runs to the last line, `Paste the output of: cat .../results.txt`, and the checks are all there (6 of 9 failed, as that test expects).
- **bash was in end_output's `wait "$TEE_PID" 2> /dev/null`:** its open descriptors were 0 (/dev/null), 2 (/dev/null, the `2> /dev/null` of the wait) and 255 (the script). Descriptor 1 was closed. bash held no pipe or fifo at all.
- **tee was still reading the fifo, with nothing left to read:** tee had descriptor 0 on the fifo at offset 6251, and results.txt was 6251 bytes, so it had read and written everything. Its descriptors 1 and 2 were the pipes to pytest.
- **No process held the fifo's write end:** `lsof -u runner`, filtered to FIFO lines, listed only tee's read end.
- **Nothing changed in 60 more seconds:** bash and tee were both still there, in the same state.
- The `sample` of tee printed only its image list (the diagnostics kept the last 6,000 characters), so its stack is not known.

So the hang is not in the stub, serve or the probe: the script's work is done, the last writer of the fifo is closed, and tee never sees end of input, so `wait "$TEE_PID"` never returns. The failed run of 38168770514 fits the same picture (iteration 6).

The other four jobs passed every round. This round's other 279 or so script runs passed.

### 6. What the runner's orphan cleanup lists (calibration)

Question: does "only tee was left" in the failed job mean bash had already exited?

Branch `ci-scratch/orphan-calib` (commit 0982df9): one macOS job runs the real script with a stub claude that sleeps for an hour in run C, gives up after 40 s without killing anything, as pytest does, and ends the step.

Run 38183397952: the cleanup printed `Terminate orphan process: pid (1911) (sleep)` and then `pid (1833) (bash)`, and nothing for tee (1842) or the run's subshell (1910), which were also alive. So the runner kills from the highest pid down and names only processes still alive when it reaches them: once sleep died the subshell ended, and once bash died tee saw end of input.

Read against that, the failed job's single line `(tee)` means tee had the highest pid of the processes left, so the stub, serve and the subshells were gone, and once tee was killed, bash's `wait` returned and bash exited before the runner reached it. That is the state iteration 5 caught: bash waiting for tee at exit, tee never seeing end of input.

### 7. The output plumbing alone, many times (reproduced)

Hypothesis: the hang is in macOS's named pipes or tee, not in the check's work, so the plumbing alone hangs at some rate.

Branch `ci-scratch/fifo-synth` (commit 16ccd0e): `ci-synth/skeleton.sh` is the script's output plumbing as it is (`mkfifo`, tee as a job reading the pipe, `exec > pipe 2>&1`, the pipe removed, the same traps and `end_output`) with a stand-in body: about 1 KB of lines, command substitutions, a subshell running `/bin/sh` with redirections, files written. `ci-synth/drive.py` runs it as the test fixture does (pipes, a thread in `proc.wait()`, `communicate(timeout=10)`), and on a hang prints ps, lsof, every FIFO open by the user, and a `sample` call graph of bash and tee. Three drivers ran side by side in each of five macOS jobs for 40 minutes. The variant `keep` leaves the pipe's name in place until exit.

Run 38186702689:

| Variant | Jobs | Runs | Hangs |
| --- | --- | --- | --- |
| orig | 3 | 106,772 | 10 |
| keep | 2 | 73,002 | 4 |

Every hang is the state iteration 5 caught: the whole output written, bash in `wait4` (its call graph ends in `__wait4`), tee in `read` (its call graph is `start`, then `read`), tee's descriptor 0 on the pipe with everything read, and no process of the user holding the pipe's write end. So on this runner (macOS 26.6.2, arm64), a reader blocked on a named pipe sometimes never sees end of input after the last writer closes, about once in 10,000 runs of this plumbing. Leaving the pipe's name in place does not stop it. The kernel cause was not looked into further.

## The fix

The three scripts that use this plumbing (`scripts/rugpull-check.sh`, `scripts/hold-check.sh` and `scripts/m1a-check.sh`) now give tee 10 s to exit after the script closes its end of the pipe, polling every 0.1 s, then kill it (tee ignores INT and TERM, so with SIGKILL) and reap it. In the stuck state tee has already copied everything, so nothing is lost. A tee that is only slow has its usual milliseconds, and 10 s is far more than it needs for what can be left in a pipe. A normal exit is up to 0.1 s slower. The loop counts with `i=$((i + 1))` rather than `for i in $(seq ...)`, because m1a-check.sh changes IFS in places; the first version used `seq`, and `test_m1a_check.py::test_output_is_written_before_exit_on_bash_3_2` caught it (the loop ran once and tee was killed before its last line).

Tests: `tests/helpers/slowtee.py` gains `stuck_env`, a stand-in tee that copies everything and then never exits. Each script has a test with it: `test_rugpull_check.py::test_a_tee_that_never_exits_does_not_hang_the_script`, and `test_a_tee_that_never_exits_does_not_hang_status` in `test_hold_check.py` and `test_m1a_check.py`. Before the fix they failed: `rugpull-check.sh did not finish in 120 s` (its output ending at `rugpull check passed`), and `hold-check.sh status still running after 60 s` and `m1a-check.sh status still running after 60 s`. After it they pass in about 16, 10 and 10 s.

Local runs with the fix: the three script test files passed with bash 5.2.21 and with the bash 3.2.57 build (33 passed); four parallel loops of the rug-pull test file, 25 each, all passed (100 runs, about 800 script runs, 0 hung).

Separately, a timed-out run of the rug-pull check in its tests now reports the whole output so far and `ps` and `lsof` of its own processes, then kills them (`tests/helpers/stuck.py`, with `tests/test_stuck.py`), so a different hang would leave evidence.

### 8. The fix on macOS

Two runs at once, on the fix's code.

Branch `ci-scratch/fifo-synth` (commit 0be47b5), run 38190721824: the plumbing of iteration 7 with the fix (variant `fix`) in three jobs and without it (`orig`) in one, three drivers per job for 40 minutes; the driver's limit is now 30 s, and it reports any run over 5 s.

| Variant | Jobs | Runs | Hangs | Runs over 5 s |
| --- | --- | --- | --- | --- |
| fix | 3 | 54,264 | 0 | 4, all with the whole output |
| orig | 1 | 33,985 | 0 | 0 |

The four slow runs (13.3, 17.3, 17.9 and 18.2 s) are the bug happening and the bound ending it: 100 polls of 0.1 s take 13 to 18 s on a busy runner, as each `sleep` is a process. The fix variant runs fewer times in 40 minutes because each exit waits for at least one 0.1 s poll. The control job happened to see no hang this time; at iteration 7's rate about 3 were expected.

Branch `ci-scratch/fix-suite` (commit 00dd797, the fix commit plus a workflow), run 38190748936: one macOS job, the whole suite and then the rug-pull file 20 times in one pytest process, twice (round 1: 1606 passed in 2,059 s; round 2: 1606 passed in 1,998 s), about 340 script runs, none failed and none of the rug-pull tests other than the stuck-tee one took over 12 s.
