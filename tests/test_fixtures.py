"""The committed fixtures are exactly what tools/make_fixtures.py writes, byte for byte."""

import make_fixtures
from conftest import CONFORMANCE

# Committed files the generator reads or never writes.
NOT_GENERATED = {"reference_verify.py", make_fixtures.V0_FILE}


def files(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_fixtures_regenerate_byte_for_byte(tmp_path):
    make_fixtures.main(["--out", str(tmp_path)])
    generated = files(tmp_path)
    committed = {k: v for k, v in files(CONFORMANCE).items() if k not in NOT_GENERATED}
    assert sorted(generated) == sorted(committed)
    for name, data in generated.items():
        assert data == committed[name], name


def test_each_broken_fixture_comes_from_a_named_mutation():
    source = (CONFORMANCE.parent / "tools" / "make_fixtures.py").read_text(encoding="utf-8")
    for mutation in [
        "edit_value",
        "delete_middle_line",
        "swap_lines",
        "tear_last_line",
        "reorder_keys",
        "insert_float",
        "big_integer",
        "non_ascii_key",
        "extra_top_level_key",
        "second_genesis",
        "edit_v0_entry",
        "mix_v0_v1",
    ]:
        assert f"def {mutation}(" in source
