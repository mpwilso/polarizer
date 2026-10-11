# Releasing

`.github/workflows/release.yml` runs when a GitHub release is published. Its `build` job checks that the release tag is `v` plus the version in `pyproject.toml`, then builds the wheel and the sdist with `uv build`. Its `publish` job, in the `pypi` environment and the only job with `id-token: write`, uploads them to PyPI with trusted publishing, so no PyPI token is stored anywhere.

## Before the first release to PyPI (the owner, once)

The name `polarizer` was free on PyPI on Oct 11, 2026 (`https://pypi.org/pypi/polarizer/json` answered 404). Publishing fails until PyPI knows the workflow:

1. On pypi.org, signed in, open Your account, then Publishing, and add a pending publisher under GitHub with these values:
   - **PyPI Project Name:** `polarizer`
   - **Owner:** `mpwilso`
   - **Repository name:** `polarizer`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
2. On GitHub, in the repository's Settings, Environments, create an environment named `pypi`. Adding yourself as a required reviewer makes each upload wait for your approval.

The first successful upload creates the project on PyPI and turns the pending publisher into a normal one.

## Each release

1. Set the version in `pyproject.toml` (the one version source; run `uv lock` after), date the CHANGELOG section, and update the README's install line to the new tag's wheel (`tests/test_readme.py` checks it names this version).
2. Merge to `master`, tag `v<version>`, and publish a GitHub release for the tag with the wheel attached, as before.
3. The release workflow uploads the same version to PyPI. A failed upload can be re-run from the Actions tab; PyPI refuses a version it already has.

Once a release is on PyPI, `uvx polarizer` and `uv tool install polarizer` will work, and the README's install line can change to them. Until then the README installs the wheel from the GitHub release.
