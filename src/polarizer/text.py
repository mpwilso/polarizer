"""Text Polarizer prints or records but did not write itself.

printable() escapes what `pending` prints from the ledger. safe() is the one function every
piece of upstream text goes through before serve writes it to stderr or the ledger: an error
message from an upstream, an exception built from upstream data, a skipped tool name or an SDK
log line. It escapes, folds and cuts, so nothing an upstream sends can move the cursor of the
terminal that shows serve's stderr, start a line of its own, or fill the ledger.
"""

SAFE_LIMIT = 200  # characters, after escaping
CUT_MARK = "..."


def printable(text: str) -> str:
    """Every character outside printable ASCII (0x20 to 0x7e) written as an escape, \\xNN
    below 0x100 and \\uNNNN above, one per UTF-16 code unit past U+FFFF. A lone surrogate is
    one \\uNNNN escape."""
    out = []
    for ch in str(text):
        code = ord(ch)
        if 0x20 <= code <= 0x7E:
            out.append(ch)
        elif code < 0x100:
            out.append(f"{chr(0x5C)}x{code:02x}")
        elif code <= 0xFFFF:
            out.append(f"{chr(0x5C)}u{code:04x}")
        else:
            code -= 0x10000
            for unit in (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)):
                out.append(f"{chr(0x5C)}u{unit:04x}")
    return "".join(out)


def safe(text, limit: int = SAFE_LIMIT) -> str:
    """Upstream text as serve writes it to stderr or the ledger: every run of whitespace
    (newlines included) folded to one space, every other character outside printable ASCII
    escaped as printable() does, and the result cut to at most `limit` characters, ending in
    "..." when cut. An escape is never split. Applying it twice changes nothing."""
    out: list[str] = []
    size = 0
    for ch in " ".join(str(text).split()):
        piece = printable(ch)
        if size + len(piece) > limit:
            while out and size + len(CUT_MARK) > limit:
                size -= len(out.pop())
            return "".join(out) + CUT_MARK
        out.append(piece)
        size += len(piece)
    return "".join(out)
