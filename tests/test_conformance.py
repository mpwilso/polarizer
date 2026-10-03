"""Both verifiers agree with expected.json on status, line and seq for every fixture."""

import pytest
import reference_verify
from conftest import expected, fixture_paths

from polarizer import ledger

NAMES = sorted(expected()["fixtures"])


def want(name):
    w = expected()["fixtures"][name]
    return {k: w[k] for k in ("status", "line", "seq", "check")}


@pytest.mark.parametrize("name", NAMES)
def test_reference_verifier(name):
    path, head = fixture_paths(name)
    assert reference_verify.verify(path, head) == want(name)


@pytest.mark.parametrize("name", NAMES)
def test_polarizer_verifier(name):
    path, head = fixture_paths(name)
    result = ledger.verify_bytes(path.read_bytes(), head.read_bytes() if head else None)
    got = {"status": result.status, "line": result.line, "seq": result.seq, "check": result.check}
    assert got == want(name)


def test_every_status_is_pinned():
    statuses = {expected()["fixtures"][n]["status"] for n in NAMES}
    assert statuses == {"intact", "tampered", "invalid", "not canonical", "torn tail", "truncated"}


def test_order_fixtures_exist():
    for name in [
        "broken/extra_key_and_bad_hash",
        "broken/bad_seq_and_bad_hash",
        "broken/torn_tail_and_truncated",
        "broken/tampered_line_and_head_mismatch",
    ]:
        assert name in NAMES


def test_v0_fixture_source_is_recorded():
    source = expected()["v0_source"]
    assert source["file"] == "parallax/ledger.py"
    assert len(source["commit"]) == 40


def test_reference_verifier_is_small_and_standalone():
    from conftest import CONFORMANCE

    text = (CONFORMANCE / "reference_verify.py").read_text(encoding="utf-8")
    assert len(text.splitlines()) < 80
    imports = {
        line.split()[1] for line in text.splitlines() if line.startswith(("import ", "from "))
    }
    assert imports == {"hashlib", "json", "re", "sys", "rfc8785"}


def test_blank_line_in_v0_is_invalid_in_both_verifiers(tmp_path):
    """Deliberately unlike Parallax's own verify, which skips blank lines: the frozen v0 check
    is byte for byte on line form (LEDGER-SPEC.md, v0)."""
    from conftest import CONFORMANCE

    lines = (CONFORMANCE / "valid" / "v0-parallax.jsonl").read_bytes().split(b"\n")
    data = b"\n".join(lines[:2] + [b""] + lines[2:])
    path = tmp_path / "blank.jsonl"
    path.write_bytes(data)
    assert reference_verify.verify(path) == {
        "status": "invalid",
        "line": 3,
        "seq": None,
        "check": "structure",
    }
    result = ledger.verify_bytes(data, None)
    assert (result.status, result.line, result.seq) == ("invalid", 3, None)
    assert result.message == "invalid: line 3: not valid JSON"
