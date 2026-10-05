# The build log

These files are the record of how Polarizer was built, kept as they were written. They are not user documentation: start with README.md at the repo root, and the specs in docs/. Where a file here disagrees with a spec in docs/ (LEDGER-SPEC.md, PROXY-SPEC.md, PIN-SPEC.md, HOLD-SPEC.md, MEASURE-SPEC.md) or with docs/verified-facts.md, those win.

The work went in stages: a spec round for each milestone, then implementation stages, each with tests written first, a claims table (what was claimed, the command that checks it, and whether it was run), and a review before the next stage. "The owner" in these files is the project's author, who ran the interactive checks.

- **m0-plan.md:** the plan for M0, the proxy and the ledger, with its build order, test methods and claims table, and the list of corrections to an earlier draft plan. Its M1a appendix is superseded by docs/PIN-SPEC.md.
- **STAGE1-NOTES.md to STAGE3-NOTES.md:** M0. Stage 1 is the ledger, its verifiers and the conformance fixtures; stage 2 the proxy; stage 3 result fidelity, the reference servers and the headless live check.
- **STAGE4-NOTES.md and STAGE5-NOTES.md:** M1a, pins at rest and in motion, shutdown, upstream text safety, and the rug-pull check.
- **STAGE6-NOTES.md and STAGE7-NOTES.md:** M2a, classes, path rules and holds, then session lifetime, progress and `holds --wait`, and the CI follow-ups for Windows and macOS.
- **STAGE8-NOTES.md:** the first slice of measurement: the hold-check test fix, the owner's answers to the measure spec, and drills (the statistics, the scenario set, `polarizer drill`, `drill report` and the export, and docs/DRILL-GUIDE.md), then the shortcut audit, the guided condition, the release round (the shape audit, the README, the CI install check and the public-readiness review) and the release fixes (the fence test, the CI record and the install step's PATH).
- **MANUAL-CHECK.md:** the interactive checks the owner ran with Claude Code, for M0, M1a and M2a. Their results are in docs/verified-facts.md.
- **QUICKSTART-DRAFT.md:** the draft that README.md replaced.
- **PUSHING.md:** the owner's steps for the first push to GitHub and reading CI.

File names written without a directory in these files (such as HOLD-SPEC.md) mean the file in docs/ or here, whichever has it. Two files were renamed for v0.1, and records made before then use the old names: `scripts/guard.sh` is now `scripts/dev/guard.sh`, and the manual check's config, `polarizer.example.toml`, is now `manual/polarizer.manual.toml` (`polarizer.example.toml` became the example for users).
