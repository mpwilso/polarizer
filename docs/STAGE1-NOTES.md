# Stage 1 notes

Read after docs/verified-facts.md. This file records what stage 1 decided on its own: the deviations from the specs, the Windows hazards to compare with CI, and the differential fuzz results. Where these notes and the specs differ, the specs win.

## Deviations (as reported at the end of stage 1 review round 2)

1. Repair lives in `writer.py`, not `ledger.py`; `lock.py` and `sidefiles.py` are separate modules.
2. serve's refusal line became `polarizer: <verify's first line>; run polarizer verify`, with nothing added for a torn tail.
3. Added the config error `[upstream.<p>] "command" must be a string`.
4. Message not in the spec: `polarizer.toml: not valid UTF-8`.
5. Message not in the spec: verify's `polarizer: cannot read <path>: <reason>`, exit 2.
6. Message not in the spec: repair's `polarizer: cannot repair <path>: <reason>`, exit 2, also used when the torn-file name already exists.
7. TOML errors that tomllib reports "at end of document" print without a line number.
8. expected.json: head problems have line null and the head's seq (null if the head doesn't parse); torn tails have the last complete line; it has extra `check` and `head` fields.
9. A `ledger.head` next to a v0 ledger reports "chain_id does not match the ledger".
10. verify doesn't check the format of `ts` or `kind`.
11. With duplicate JSON keys the last one wins, and the line then fails the canonical check, in both verifiers.
12. A blank line in a v0 ledger is invalid, deliberately unlike Parallax's verify, because the frozen check is byte for byte on line form.
13. An existing `ledger_dir` keeps its mode; 0700 is set only when Polarizer creates it.
14. A malformed `args_commit` counts as missing and is never used as a path.
15. Side files encode lone surrogates with `surrogatepass`.
16. The reference verifier checks `ledger.head`'s form with one regex instead of parsing it.
17. CI actions were pinned only by major tag; fixed by SHA pins.
18. ruff's line-length rule is off for `tests/`.
19. Repair's location check is step "0." in LEDGER-SPEC.md.
20. The `protected_paths` name overlapped with M2a's term; fixed by the rename to `ledger_forbidden_paths`.
21. The live-check conditions requirement went into m0-plan step 7, because `live-check.sh` doesn't exist yet.
22. The CI actions moved from v4 and v6 to v7.0.1 and v10.2.0.
23. The fuzz test adds `reformat_line` and starts 10% of cases from the v0 fixture.
24. The fuzz test runs 5,000 cases by default, above the 2,000 asked for.
25. `mcp-types` is a direct dependency, so it can be pinned.
26. **Guess:** Claude Code's auto-mode flag name is unknown, so live-check prints "not set here" unless the script sets it.
27. CLAUDE.md's clone note now says "on the Windows drive" in every commit; this is the one tree difference from the rewrite.
28. `.backup/` went into a separate commit after the rewrite.
29. History has 6 commits instead of one per stage: two spec, Stage 1, and three review commits the user asked for.
30. The fuzz test also applies the structure-level edits to `ledger.head`, prints per-mutation counts, and fails a default run over 60 s.
31. Rewritten commits keep their original dates.

## Windows hazards (as reported before the first CI run)

Nothing here has run on Windows. "Runs" means the test is expected to run there; "skipped" and "adapted" say how the test copes.

**File modes, 0700 and 0600**
- `writer.py:230-231`, `sidefiles.py:28-29` and every `os.open(..., 0o600)`: on Windows, chmod only toggles the read-only flag, so these modes protect nothing. The profile's ACLs do, as the spec says.
- `test_writer.py::test_modes` is **skipped** on Windows.
- `test_verify_readonly.py`'s read-only cases are **skipped**; its writable cases **run**.

**Symlinks**
- `test_ledgerdir.py::test_dot_dot_and_symlinks_are_resolved`: the `..` part **runs**; the symlink part is **skipped**, because creating symlinks needs privileges on Windows.
- There's no test for junctions.

**Path separators and `~`**
- `Path.home()` and `os.path.expanduser` use `USERPROFILE` on Windows. `conftest.fake_home` sets both `HOME` and `USERPROFILE`: **adapted**.
- `"/x"` isn't absolute on Windows. The config tests write TOML paths with `as_posix()` (`C:/...`): **adapted**.
- verify and repair messages contain the path with backslashes. The golden tests replace the temp path with `<dir>`: **adapted**.
- `verify --args` always prints `args/` with a forward slash, by design: **runs**.

**Case-insensitive paths**
- `ledgerdir.py:29` uses `normcase`, which lowercases on Windows, plus a `samefile` fallback at line 51.
- No test checked that a differently cased path is refused: **untested** at the time.
- The runners' temp paths may use 8.3 short names (`RUNNER~1`). `realpath` expands those to long names, and the tests compare only resolved-to-resolved or given-to-given paths: **expected to run**.

**Open files that can't be replaced or deleted**
- `writer.py:105-114` retries `os.replace` on `PermissionError`.
- `test_head.py::test_windows_replace_while_head_is_held_open` is **Windows only**; the simulated version of the same test **runs everywhere**.
- `_fsync_dir` is a no-op on Windows (`writer.py:95-96`).
- `os.ftruncate` in repair needs the `O_RDWR` handle it has: **expected to run**.
- If a test leaves a file open, pytest's temp-dir cleanup could fail. Every test closes its writers and locks: **expected to run**.

**Locks**
- `lock.py` uses `msvcrt.locking` on byte 0, which is per handle, so two writers in one process exclude each other, as on Unix.
- verify locks a read-only handle; expected to work, not confirmed.
- `O_APPEND` on Windows is the C runtime seeking to the end before each write, which is safe because the lock is held.
- The lock-wait and two-writer tests are **expected to run**.

**Subprocess quoting**
- `test_writer.py:118` passed a multi-line program through `python -c` with argument-list quoting: **expected to run**, the riskiest item.
- The git calls in `ledgerdir.py:82` and `test_ledgerdir.py` use argument lists, with no shell. git prints `C:/...` paths, and the test compares against git's own output: **expected to run**.

**Text-mode newline translation on write**
- `tools/make_fixtures.py` wrote `expected.json` in text mode, which would have written `\r\n` on Windows; fixed to write bytes.
- The golden update mode writes with `newline="\n"`, and the golden comparison reads with universal newlines: **adapted**.
- `test_config.py:27` writes TOML in text mode; CRLF is harmless to TOML: **runs**.
- `cli.py`'s `print()` would emit CRLF on a real Windows stdout. The golden tests capture output in-process, so they're unaffected: **runs**.
- For real use, a non-ASCII path in a printed message could fail on a Windows pipe that uses the cp1252 code page. No test covered it: **untested** at the time.
- The `ledger.jsonl`, `ledger.head`, side-file and torn-file writes are all `os.open` with `O_BINARY`, or `write_bytes`: **runs**.

**Parallax's location on Windows**
- `always_forbidden()` assumes Parallax uses `~/.local/share/parallax` and `~/.config/parallax` under the profile there too. Whether it does on Windows was **unverified**.

### Changed since that list (commit "stage1: windows fixes and notes")

- **stdout and stderr:** every command except serve now sets UTF-8, backslashreplace and `"\n"` newlines. `tests/test_cli_subprocess.py` runs `python -m polarizer verify` as a subprocess and compares raw stdout bytes with the golden files, including a non-ASCII directory name and a hostile `PYTHONIOENCODING`.
- **Subprocess quoting:** the multi-line `python -c` is gone; the workers run `tests/helpers/append_worker.py` with a path argument. No other test used `-c`.
- **Case:** a differently cased forbidden path is tested as refused on Windows, not refused on Linux, and following the file system on macOS (PROXY-SPEC.md, Startup step 2).
- **Parallax on Windows:** Parallax's README, read during the stage 1 review, says it needs bubblewrap and does not run on native Windows, so `always_forbidden()`'s Parallax defaults are moot there, but harmless.

## Differential fuzz (`tests/test_differential.py`)

Default run: seed 20261002, 5,000 cases, about 2 s on one core, with all mutations, including the structure-level ones. No disagreements between Polarizer's verifier and the reference verifier, on that seed or on seeds 1 to 5 at 20,000 cases each.

| Status | Cases | Share |
|---|---|---|
| intact | 872 | 17.4% |
| invalid | 1407 | 28.1% |
| not canonical | 1331 | 26.6% |
| tampered | 901 | 18.0% |
| torn tail | 303 | 6.1% |
| truncated | 186 | 3.7% |
| **Total** | **5000** | 100% |

`truncated` is the rarest. On seeds 1 to 5 it was 692 to 805 cases per 20,000.
