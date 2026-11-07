# Releasing

`.github/workflows/release.yml` runs when a GitHub release is published. Its one job checks that the release tag is `v` plus the version in `pyproject.toml`, builds the wheel and the sdist with `uv build`, and keeps them as the run's artifact. It has a read-only token and no secrets.

Polarizer is not on PyPI. Trusted publishing to PyPI was set up in this workflow on Oct 11, 2026, never used, and dropped on Oct 24, 2026 (the setup is in git history before that date). The README installs the wheel from the GitHub release.

## Each release

The install lines on master name the new wheel from the moment the release commit is merged, so the release must exist by the end of the same session. 0.4.0's release commit, made at 02:57 UTC on Nov 6, 2026, reached master at 03:04 UTC, and its release was published at 16:46 UTC: for those 13.7 hours master's install lines named a wheel that didn't exist.

1. On a branch, set the version in `pyproject.toml` (the one version source; run `uv lock` after), turn the CHANGELOG's Unreleased heading into `## <version> (preview, released <Mon D, YYYY> (UTC))`, and update the install lines in README.md, docs/SETUP.md and docs/DRILL-GUIDE.md to the new tag's wheel (`tests/test_readme.py` and `tests/test_drill_guide.py` check they name the newest released heading in CHANGELOG.md). Run `scripts/test.sh`, commit, push, and wait for CI to pass on all five jobs.
2. Merge the release commit to `master`.
3. Immediately, in the same session: check out the merged `master`, build with `uv build --out-dir dist` (after removing any old `dist`), tag that commit `v<version>`, push the tag, and publish a GitHub release for the tag with the wheel and the sdist attached. The release's install line is the wheel's download URL.
4. Check that the README's wheel URL resolves: `curl -sI <url>` must answer `302` (GitHub's redirect to the file). The release is not done until it does.
5. The workflow's run on the published release is a check: its tag test and build pass, or the release was cut from the wrong commit.
