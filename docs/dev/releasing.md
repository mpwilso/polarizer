# Releasing

`.github/workflows/release.yml` runs when a GitHub release is published. Its one job checks that the release tag is `v` plus the version in `pyproject.toml`, builds the wheel and the sdist with `uv build`, and keeps them as the run's artifact. It has a read-only token and no secrets.

Polarizer is not on PyPI. Trusted publishing to PyPI was set up in this workflow on Oct 11, 2026, never used, and dropped on Oct 24, 2026 (the setup is in git history before that date). The README installs the wheel from the GitHub release.

## Each release

1. Set the version in `pyproject.toml` (the one version source; run `uv lock` after), turn the CHANGELOG's Unreleased heading into the version with its date, and update the install lines in README.md, docs/SETUP.md and docs/DRILL-GUIDE.md to the new tag's wheel (`tests/test_readme.py` and `tests/test_drill_guide.py` check they name the newest released heading in CHANGELOG.md).
2. Merge to `master`, build with `uv build --out-dir dist`, tag `v<version>`, and publish a GitHub release for the tag with the wheel and the sdist attached. The release's install line is the wheel's download URL.
3. The workflow's run on the published release is a check: its tag test and build pass, or the release was cut from the wrong commit.
