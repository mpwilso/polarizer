"""The release workflow: it runs only for a published release, every action is pinned to a
full commit SHA with its version in a comment, only the publish job can mint an OIDC token, and
it publishes from the pypi environment with trusted publishing, never a stored token
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
    assert len(uses) >= 5
    for action, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
        assert re.fullmatch(r"  # v\d+\.\d+\.\d+", rest), (action, rest)


def test_only_publish_gets_an_id_token():
    assert WORKFLOW.count("id-token: write") == 1
    publish = job("publish")
    assert "    permissions:\n      id-token: write\n" in publish
    assert "    environment:\n      name: pypi\n" in publish
    assert "uses: pypa/gh-action-pypi-publish@" in publish
    assert "id-token" not in job("build")
    assert "password" not in WORKFLOW and "secrets." not in WORKFLOW


def test_build_checks_the_tag_and_uses_uv():
    build = job("build")
    assert "uv build --out-dir dist" in build
    assert '"$TAG" != "v$version"' in build
