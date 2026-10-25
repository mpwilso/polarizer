"""The release workflow: it runs only for a published release, every action is pinned to a
full commit SHA with its version in a comment, it has one job, which builds the wheel and sdist
with a read-only token, and nothing in it can reach PyPI or any stored secret
(docs/dev/releasing.md)."""

import re

from conftest import ROOT

WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def job(name: str) -> str:
    start = WORKFLOW.index(f"\n  {name}:\n")
    following = re.search(r"\n  [a-z]+:\n", WORKFLOW[start + 1 :])
    return WORKFLOW[start : start + 1 + following.start() if following else len(WORKFLOW)]


def test_runs_only_for_a_published_release():
    assert "on:\n  release:\n    types: [published]\n" in WORKFLOW
    assert "\npermissions:\n  contents: read\n" in WORKFLOW


def test_every_action_is_pinned_by_sha():
    uses = re.findall(r"uses: (\S+)(.*)", WORKFLOW)
    assert len(uses) >= 3
    for action, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
        assert re.fullmatch(r"  # v\d+\.\d+\.\d+", rest), (action, rest)


def test_one_job_with_a_read_only_token_and_no_way_to_publish():
    assert re.findall(r"^  ([a-z]+):\n", WORKFLOW[WORKFLOW.index("\njobs:\n") :], re.M) == ["build"]
    assert "id-token" not in WORKFLOW and "environment:" not in WORKFLOW
    assert "pypa/" not in WORKFLOW and "pypi.org" not in WORKFLOW
    assert "password" not in WORKFLOW and "secrets." not in WORKFLOW


def test_build_checks_the_tag_and_uses_uv():
    build = job("build")
    assert "uv build --out-dir dist" in build
    assert '"$TAG" != "v$version"' in build
