# Contributing

Polarizer is a one-person portfolio project, and issues and pull requests are welcome. A report of something wrong in a doc or a test helps as much as code. Security reports go through [SECURITY.md](SECURITY.md), not the issue tracker.

## Layout

- `src/polarizer/`: the proxy, the ledger, pins, holds, drills and the command line; `src/polarizer/scenarios/` holds the drill scenarios.
- `conformance/`: the ledger fixtures and the separately written reference verifier.
- `tests/`: every test; `tests/test_readme.py` runs the commands in README.md and docs/SETUP.md, and `tests/test_readme_rules.py` holds the README to its rules (below).
- `docs/`: the five specs, [the setup](docs/SETUP.md), [the drill guide](docs/DRILL-GUIDE.md), [the evidence](docs/EVIDENCE.md), [the limits](docs/LIMITS.md), [related projects and credits](docs/related.md) and [the verified facts](docs/verified-facts.md). `docs/dev/` is the build log, and [docs/dev/releasing.md](docs/dev/releasing.md) says how a release is cut.
- `docs/brand/`: the logo, drawn by `scripts/brand.py`. The parallel lines are calls that line up with what you approved; the one turned out of line is held ([what the mark means](docs/brand/README.md)).
- `manual/`: the config for the interactive checks.
- `scripts/`: `test.sh` runs every check, as CI does; `demo.py` draws the README's terminal picture from a real run against a test server; the live and manual check scripts.

## Tests

```sh
uv sync --locked
bash scripts/test.sh
```

That runs ruff, `scripts/check_docs.py` and pytest, exactly as CI does. pytest is configured with `-q` already; a second `-q` hides the summary line. Tests never call a model or the network, and use temporary directories, never the real ledger directory. A bug found in real data gets a test before the fix.

## The README tests

`tests/test_readme.py` runs the commands in README.md and docs/SETUP.md as written, in a temporary home, with a standard-library probe in place of the Filesystem server, so the docs can't drift from the code. It also parses every `polarizer` command the two files show and checks every link. `tests/test_readme_rules.py` keeps the README under 155 shown lines, with no em or en dashes, no bare angle-bracket placeholders in prose, and none of the words only, first and best in prose. `tests/test_drill_guide.py` does the same for docs/DRILL-GUIDE.md, and `tests/test_demo.py` redraws the terminal picture from a fresh run.

## How changes land

Work on a branch, never on master. Push the branch; CI runs five jobs (Linux with Python 3.11, 3.12 and 3.13, Windows and macOS), and the owner merges when all five pass. Say in the commit message what changed and what moved where. CHANGELOG.md gets a line under the Unreleased heading for anything a user would notice.
