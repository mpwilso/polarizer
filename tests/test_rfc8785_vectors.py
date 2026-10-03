"""RFC 8785 test vectors, written out by hand from the RFC text (rfc-editor.org, Oct 2, 2026).

Inside the Polarizer subset, canon.py, the rfc8785 package, the reference verifier's
canonicalization and the stdlib's sorted compact form must all give the RFC's bytes. Vectors
outside the subset must be rejected by canon.py for the right reason. JSON inputs are raw
strings, exactly as written in the RFC.
"""

import json

import pytest
import reference_verify
import rfc8785

from polarizer import canon


def stdlib_form(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# Section 3.2.2: the "string" member of the example. U+20AC stays as is, U+000F becomes
# lowercase \u000f, U+000A becomes \n, \u0042 becomes B, \u0022 becomes \", \u005c becomes \\,
# and \/ becomes /.
STRING_IN = r'{"string": "\u20ac$\u000F\u000aA' + "'" + r'\u0042\u0022\u005c\\\"\/"}'
STRING_OUT = '{"string":"\u20ac$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'

INSIDE = {
    # Section 3.2.2, the "string" member of the example (its "numbers" member is out of subset).
    "3.2.2 string": (STRING_IN, STRING_OUT),
    # Section 3.2.2.1, and the "literals" member of the 3.2.2 example.
    "3.2.2 literals": ('{"literals": [null, true, false]}', '{"literals":[null,true,false]}'),
    # Section 3.2.3: the 3.2.2 example without "numbers", sorted: literals before string.
    "3.2.3 sorted example": (
        STRING_IN[:-1] + ', "literals": [null, true, false]}',
        '{"literals":[null,true,false],' + STRING_OUT[1:],
    ),
    # Section 3.2.3: property names sort as "", "a", "aa", "ab" (the values are chosen here).
    "3.2.3 sort order": ('{"ab": 3, "aa": 2, "a": 1, "": 0}', '{"":0,"a":1,"aa":2,"ab":3}'),
    # Section 3.2.2.2: controls with short escapes, and the others in lowercase hex.
    "3.2.2.2 controls": (
        r'{"c": "\u0008\u0009\u000A\u000C\u000D\u0000\u001F"}',
        '{"c":"\\b\\t\\n\\f\\r\\u0000\\u001f"}',
    ),
    # Appendix B: zero, and minus zero (as the JSON integer -0), both serialize as 0.
    "B zero": ("[0, -0]", "[0,0]"),
    # Appendix B note (1): the integer range that Polarizer's subset keeps.
    "B note 1 range": (
        "[9007199254740991, -9007199254740991]",
        "[9007199254740991,-9007199254740991]",
    ),
}

OUTSIDE = {
    # Section 3.2.2: the full example; its first number is a float.
    "3.2.2 full example": (
        '{"numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],'
        ' "string": "x", "literals": [null, true, false]}',
        "float at /numbers/0",
    ),
    # Section 3.2.3: the sorting test data; its keys aren't ASCII (the first, in document order).
    "3.2.3 sorting test data": (
        r'{"\u20ac": "Euro Sign", "\r": "Carriage Return", "\ufb33": "Hebrew Letter Dalet With'
        r' Dagesh", "1": "One", "\ud83d\ude00": "Emoji: Grinning Face", "\u0080": "Control",'
        r' "\u00f6": "Latin Small Letter O With Diaeresis"}',
        "non-ASCII key at /\\u20ac",
    ),
    # Section 3.2.2.2 note: lone surrogates such as U+DEAD.
    "3.2.2.2 lone surrogate": (r'{"s": "\udead"}', "lone surrogate at /s"),
    # Appendix B rows outside the subset: integers past 2^53-1, and every non-integer.
    "B max pos int": ("[9007199254740992]", "integer out of range at /0"),
    "B max neg int": ("[-9007199254740992]", "integer out of range at /0"),
    "B ~2**68": ("[295147905179352830000]", "integer out of range at /0"),
    "B 999999999999999700000": ("[999999999999999700000]", "integer out of range at /0"),
    "B min pos number": ("[5e-324]", "float at /0"),
    "B max pos number": ("[1.7976931348623157e+308]", "float at /0"),
    "B 1e+23": ("[1e+23]", "float at /0"),
    "B 0.000001": ("[0.000001]", "float at /0"),
    "B round to even": ("[1424953923781206.2]", "float at /0"),
    "B NaN": ("[NaN]", "float at /0"),
    "B Infinity": ("[Infinity]", "float at /0"),
}


@pytest.mark.parametrize("name", sorted(INSIDE))
def test_vectors_inside_the_subset(name):
    text, want = INSIDE[name]
    value = json.loads(text)
    want = want.encode("utf-8")
    assert canon.subset_problem(value) is None
    assert canon.canonical(value) == want
    assert rfc8785.dumps(value) == want
    assert reference_verify.canonical(value) == want
    assert stdlib_form(value) == want


@pytest.mark.parametrize("name", sorted(OUTSIDE))
def test_vectors_outside_the_subset_are_rejected(name):
    text, rule = OUTSIDE[name]
    assert canon.subset_problem(json.loads(text)) == rule


def test_sorting_test_data_order_in_rfc8785_but_not_in_the_stdlib():
    """Why the subset needs ASCII keys: rfc8785 gives the RFC's expected order (UTF-16 code
    units), and the stdlib (code points) gives another."""
    value = json.loads(OUTSIDE["3.2.3 sorting test data"][0])
    rfc_order = [
        "Carriage Return",
        "One",
        "Control",
        "Latin Small Letter O With Diaeresis",
        "Euro Sign",
        "Emoji: Grinning Face",
        "Hebrew Letter Dalet With Dagesh",
    ]
    assert list(json.loads(rfc8785.dumps(value)).values()) == rfc_order
    assert list(json.loads(stdlib_form(value)).values()) != rfc_order
