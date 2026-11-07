# Stage 14 notes

Read after docs/dev/STAGE13-NOTES.md. This file records stage 14: three stretch milestones from docs/milestones.md, OpenTelemetry, OCSF export, and anchoring and signatures, specified together in docs/EXPORT-SPEC.md and built on Nov 7, 2026 (UTC) on branch `stretch-export` from c1982c1, master after M1b. The owner approved the three items; the Parallax and ISR adoption item stays out, since it needs work in those projects.

## Step 0

Read at c1982c1, `git status` clean, on Windows 11 (native), Python 3.12.10, uv 0.12.2, and in the WSL2 distro `parallax` with uv 0.12.19, in a worktree under `~/code/polarizer/.repro/`. The development guard's snapshot was taken in WSL2 at the start (`.guard/snapshot-20261107T230933Z.txt`); its check is under The guard, below.

What the brief asked for, checked against the code:
- **The SDK's middleware.** mcp 2.2.0, the pinned version, installs `OpenTelemetryMiddleware` in every low-level `Server` and opens a client span around each upstream request (docs/verified-facts.md, Stretch export). So "the SDK's own middleware does most of it" was true for the span itself. Its failure status carries the upstream's error message, though, and Polarizer passes a protocol error's message to the client unchanged, so a span would have held upstream text. The spec replaces the server middleware with a copy that differs only there (EXPORT-SPEC.md, deviation 2).
- **`opentelemetry-api`** was already locked at 1.45.1 through mcp; exporting needs the SDK, which was not. The SDK and the OTLP exporter went into an optional extra, pinned to 1.45.1 to match the API (deviation 3).
- **The ledger's position for a span.** `LedgerWriter.append_async` returns each entry's seq and hash, and every `tools/call` request runs in one task under its span, so `Gateway._append` can put the seq and hash of `call.returned` or `call.refused` on the current span with no other plumbing.
- **Anchors.** LEDGER-SPEC.md already said a checkpoint is (chain id, head seq, head hash) with size = head seq + 1, and reserves the `sig` key. A checkpoint signed outside the chain leaves the chain format unchanged, so neither verifier's chain rules change and every existing fixture keeps its bytes.
- **The reference verifier** is pinned below 80 lines and to five imports by `tests/test_conformance.py`, and it was at 79, so the second anchor check is a file of its own, `conformance/reference_anchor.py`.

## The spec

docs/EXPORT-SPEC.md was written first, with every guess as a numbered deviation (section 7). One spec rather than three because all three take ledger facts out of Polarizer under one rule (section 1): ledger fields and decisions leave, arguments, results and free text that can quote them never do. The guesses that change what the brief said or implied:
1. **The SDK's server middleware is replaced** (deviation 2), as above.
2. **The exporters are an optional extra** (deviation 3), and a missing extra warns instead of refusing to start (deviation 4).
3. **One OCSF event per ledger entry,** not per call (deviation 10), so each event names one hash and seq that `verify` can check.
4. **Free text is never exported** (deviation 13): a hold's reason names argument paths and hosts, so the brief's "arguments are never exported" rules out every reason, not only the side files.
5. **`verify --anchor <ref>` walks back** to the newest commit with a trailer (deviation 18), so `--anchor HEAD` reports the last anchored position.
6. **Two new exit codes,** 9 and 10 (deviation 19), added to LEDGER-SPEC.md's table.

## The build

Tests were written before the code they test, from the spec: `tests/test_telemetry.py`, `tests/test_export.py` and `tests/test_anchor.py`, then the fixtures, then the modules.

- **`src/polarizer/telemetry.py`** (new): `choose`, `build`, `start`, `flush`, `note`, `SpanMiddleware` and `install`.
- **`src/polarizer/export.py`** (new): `MAPPING`, the spec's table as data, and `Exporter`, which folds the verified entries and joins what later entries need from `call.sent`, `hold.created` and `session.client`.
- **`src/polarizer/anchor.py`** (new): `sign`, `parse_argument`, `from_git`, `read_key`, `create_key` and `check`.
- **`src/polarizer/proxy.py`:** `telemetry.install` in the gateway, and `_note` at each step of a call: routing, the verdict, `call.sent`, `hold.created`, the hold's ending, and in `_append` for the call's last entry.
- **`src/polarizer/cli.py`:** `export --ocsf`, `checkpoint`, `verify --anchor`, and in `serve` the switch at start and the bounded flush at shutdown.
- **`src/polarizer/config.py`:** `[telemetry]` with `traces`.
- **`conformance/reference_anchor.py`** (new) and **`tools/make_fixtures.py`:** the anchor ledgers `anchors/rewound`, `anchors/rewritten` and `anchors/other_chain`, the test key `anchors/checkpoint.key` (a fixed value, `40` to `5f` in hex), and seven cases under `anchors` in `conformance/expected.json`, signed with the reference's `sign`. Every existing fixture regenerates byte for byte.
- **`pyproject.toml` and `uv.lock`:** `opentelemetry-api==1.45.1` as a direct dependency, the `otel` extra, and the same two packages in the dev group; `uv lock` added 13 packages and changed none (docs/verified-facts.md, Stretch export).
- **Docs:** EXPORT-SPEC.md; pointers in LEDGER-SPEC.md (the `sig` note, two exit codes, `checkpoint.key`, and the two places that said "until anchoring"), PROXY-SPEC.md (the command line, `[telemetry]`, and what the upstream's `traceparent` is with export on), CLAUDE.md's read-first list, milestones.md, LIMITS.md, EVIDENCE.md, CHANGELOG.md, polarizer.example.toml, README.md (one sentence), verified-facts.md and this file.

**What failed while building, before any code changed for it:** the tests' own expectations, three times. `test_odd_values` had a hand-computed epoch for `2026-10-02T15:00:00.001Z` that was off by 1,036,800,000 ms; `datetime` gives 1790953200001, which the code already produced. `verify --anchor -x` never reached Polarizer, because argparse reads `-x` as an option, so the test passes `--anchor=-x`. On Windows, a span's start and end times can be equal at the clock's resolution, so the test checks `>=`. The Bash tool's heredocs turned a patch's `b"\\n"` into a real newline once, as the earlier notes warned; every later patch was written to a file first.

## Runs

- **Windows 11, native,** Python 3.12.10: `scripts/test.sh` passed ruff, the format check and the docs check, and pytest gave 1593 passed, 83 skipped.
- **WSL2,** Python 3.12.3, uv 0.12.19, in the worktree: `scripts/test.sh` passed, and pytest gave 1661 passed, 15 skipped.
- **The `otel` extra from a wheel,** in WSL2: `uv build --wheel`, then into a new venv under `.repro/`, `uv pip install -r` the lock's own pins (`uv export --frozen --extra otel --no-dev --no-emit-project --no-hashes`) with `"polarizer[otel] @ file://<the wheel>"`. It installed opentelemetry-sdk and opentelemetry-exporter-otlp-proto-http 1.45.1 with the locked protobuf 7.36.2 and requests 2.34.2, and `telemetry.build(("otlp",))` built a provider there.
- **CI** on the branch: recorded in the owner's report for this stage, since this file is part of the commit CI runs on.

## The guard

Recorded in the report for this stage: the check runs at the end of the session, after the commit.

## Not run

- **Jaeger.** The `docker run` line is pinned and documented; no test or CI job runs it, and no span has reached a real collector.
- **An OCSF validator.** Every attribute name the export writes is checked against the lists read from schema.ocsf.io on Nov 7, 2026, by name only.
- **`uv tool install` of the extra;** the extra was installed from a built wheel into a venv instead (Runs).
- **Claude Code.** No live model run was needed: spans, the export and anchors are exercised with SDK clients and the stdio serve in tests.
