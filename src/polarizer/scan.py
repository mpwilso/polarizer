"""The scanner: named rules over a tool's served definition (docs/SCAN-SPEC.md).

The rules are data: RULES is a table of Rule, each with an id, a family, a why line, and what
it matches (regular expressions, a set of characters, or a special check). scan() reads every
string of one stored copy, keys included, and returns the flags; it reads nothing else and
writes nothing. A flag quotes what matched through quote(), which escapes as printable()
and cuts as safe() does, and names its field through printable(), so no flag line can carry
an invisible or control character.

The rules point a person at text worth reading. They are patterns, not judgments: a paraphrase
matches none of them, and ordinary text can match one (docs/SCAN-SPEC.md, section 10).
"""

import re
import unicodedata
from dataclasses import dataclass

from polarizer.text import CUT_MARK, printable

INSTRUCTIONS = ":instructions"  # the reserved tool name of an upstream's instructions
SPAN_LIMIT = 80  # characters of a quoted span, after escaping
FIELD_LIMIT = 120  # characters of a field path, after escaping
CONTEXT = 12  # characters on each side of a character rule's match
LONG_TEXT = 1024  # a string longer than this is flagged by long-text
LONG_QUOTE = 60  # characters of a long string quoted
SHOWN = 10  # flag lines printed per definition
FAMILIES = ("hidden instructions", "shadowing", "exfiltration", "characters", "length")

# A sentence ends at a newline, or at ".", "!" or "?" (with any closing quotes or brackets)
# followed by whitespace or the end of the text, except the period of an abbreviation such
# as "e.g." (docs/SCAN-SPEC.md, section 3). A period inside a file name, host or version
# ("package.json", "v1.2") ends nothing. Every rule that keeps a match inside one sentence,
# and shadow-other-tool's sentence, use this one definition.
ABBREVIATIONS = ("e.g", "i.e", "cf", "vs", "viz", "approx", "incl", "esp")
_END = (
    r"[.!?]" + "".join(rf"(?<!\b{re.escape(a)}\.)" for a in ABBREVIATIONS) + r"[\"')\]]*(?=\s|$)|\n"
)
_S = rf"(?:(?!(?:{_END})).)"  # a character that stays inside one sentence


def _chars(*ranges) -> frozenset:
    found = set()
    for item in ranges:
        low, high = item if isinstance(item, tuple) else (item, item)
        found.update(chr(c) for c in range(low, high + 1))
    return frozenset(found)


@dataclass(frozen=True)
class Rule:
    id: str
    family: str
    why: str
    patterns: tuple[str, ...] = ()
    chars: frozenset = frozenset()
    special: str | None = None  # "names", "confusable" or "length"
    # A character rule only: one character of the set directly after a character outside
    # ASCII that is not in the set is how ordinary text uses it (an emoji's presentation
    # selector), and is not flagged.
    one_after_non_ascii: bool = False


DIRECTIVE = (
    r"\b(?:must|always|never|instead|before|after|whenever|do not|don't|should|only|replace|"
    r"override|redirect|rather than)\b"
)
# Top-level domains exfil-send takes as a host after one label or more ("evil.tk").
TLDS = (
    "com|net|org|io|dev|app|ai|co|info|biz|xyz|ru|cn|site|online|top|example|test|invalid|me|"
    "us|uk|de|edu|gov|mil|eu|fr|nl|be|ch|se|no|fi|dk|es|pt|it|ua|by|kz|tr|gr|ro|hu|cz|sk|bg|"
    "jp|kr|kp|tw|hk|sg|vn|th|au|nz|ca|mx|br|ar|cl|ir|pk|ng|za|ke|tk|ly|gg|ga|gq|ws|su|pw|tv|"
    "fm|gl|li|lt|lv|nu|vc|cx|to|club|click|cloud|shop|tech|fun|icu|vip|pro|mobi|win|bid|"
    "website|digital|xxx|onion"
)
# Top-level domains that are also common file extensions ("run.sh", "README.md"): taken as a
# host only after two labels or more ("drop.evil.sh"), so a file name is not a place.
FILE_TLDS = "sh|md|py|pl|pm|rs|so|ps|in|am|cc|ml|cf"
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
# Where exfil-send says something goes: a URL, an e-mail address, an IPv4 or bracketed IPv6
# literal, or a host name.
TARGET = (
    r"(?:https?://[^\s\"'<>]+"
    r"|(?:mailto:)?[\w.+-]+@(?:[a-z0-9-]+\.)+[a-z]{2,}\b"
    rf"|(?:{_OCTET}\.){{3}}{_OCTET}(?!\.?\d)"
    r"|\[[0-9a-f.]*:[0-9a-f:.]*\]"
    r"|(?:[a-z0-9-]+\.)+(?:" + TLDS + r")\b"
    r"|(?:[a-z0-9-]+\.){2,}(?:" + FILE_TLDS + r")\b)"
)

RULES = (
    Rule(
        "ignore-instructions",
        "hidden instructions",
        "text telling the model to drop the instructions it has",
        (
            r"\b(?:ignore|disregard|forget|override|bypass)\b" + _S + r"{0,40}?"
            r"\b(?:previous|prior|earlier|above|preceding|all|any|other|system|your|the)\b"
            + _S
            + r"{0,20}?\b(?:instructions?|prompts?|rules?|directions?|guidelines?|messages?|"
            r"polic(?:y|ies))\b",
        ),
    ),
    Rule(
        "conceal-from-user",
        "hidden instructions",
        "text telling the model to keep something from the person",
        (
            r"\b(?:do not|don't|never|without)\s+(?:\w+\s+){0,3}?(?:tell(?:ing)?|mention(?:ing)?|"
            r"inform(?:ing)?|reveal(?:ing)?|show(?:ing)?|notify(?:ing)?|alert(?:ing)?|"
            r"let(?:ting)?)\b" + _S + r"{0,20}?"
            r"\b(?:the\s+)?(?:user|human|person|operator|owner)s?\b",
            r"\b(?:the\s+)?users?\s+(?:must|should|need|needs)\s+(?:not|never)\s+"
            r"(?:know|see|be told|find out|notice)\b",
            r"\b(?:keep|hide)\s+(?:this|it|these|that)\s+(?:secret|hidden|from)\b",
        ),
    ),
    Rule(
        "read-before-use",
        "hidden instructions",
        "a precondition that makes the model do something before the call",
        (
            r"\b(?:before|prior to)\s+(?:using|calling|invoking|running|executing)\s+"
            r"(?:this|any|the|each|every)\s+(?:\w+\s+)?(?:tools?|functions?)\b",
            r"\bthis tool (?:requires|needs) (?:you|the (?:agent|assistant|model)) to "
            r"(?:first )?(?:read|open|load|send|fetch|cat)\b",
        ),
    ),
    Rule(
        "secret-path",
        "hidden instructions",
        "a definition that names a place where secrets are kept",
        (
            r"(?:^|(?<=[\s/\\\"'`(~]))\.(?:ssh|aws|gnupg|kube|docker|azure|netrc|npmrc|pypirc|"
            r"git-credentials|bash_history|zsh_history|env)\b",
            r"\bid_(?:rsa|dsa|ecdsa|ed25519)\b",
            r"\bauthorized_keys\b",
            r"\b(?:mcp|claude_desktop)_config\.json\b",
            r"\.claude\.json\b",
            r"\bcredentials\.json\b",
            r"/etc/(?:passwd|shadow|sudoers)\b",
        ),
    ),
    Rule(
        "instruction-tag",
        "hidden instructions",
        "a tag that marks text as instructions for the model",
        (
            r"<\s*/?\s*(?:important|system|instructions?|secret|hidden|admin|override|critical)"
            r"\b[^>]{0,40}>",
        ),
    ),
    Rule(
        "shadow-other-tool",
        "shadowing",
        "a definition that tells the model how to use another server's tool",
        special="names",
    ),
    Rule(
        "shadow-any-tool",
        "shadowing",
        "a definition that reaches past its own tool to change how others are used",
        (
            r"\b(?:when|whenever|before|after|each time|every time|if)\b" + _S + r"{0,40}?"
            r"\b(?:using|calling|invoking|you use|you call)\b" + _S + r"{0,30}?"
            r"\b(?:any|other|another|every|all)\b" + _S + r"{0,15}?"
            r"\b(?:tools?|servers?|functions?)\b",
            r"\b(?:instead of|rather than|in place of)\b" + _S + r"{0,40}?"
            r"\b(?:the\s+)?[\w.-]+\s+(?:tool|server|function)s?\b",
            r"\b(?:this|these)\s+(?:rule|instruction|requirement)s?\s+(?:also\s+)?"
            r"appl(?:y|ies)\s+to\b",
            r"\b(?:all|every|any)\s+(?:other\s+)?(?:tools?|servers?)\s+(?:must|should|need to)\b",
        ),
    ),
    Rule(
        "exfil-send",
        "exfiltration",
        "a definition that tells the model to send something to a named place",
        (
            r"\b(?:send|post|upload|forward|transmit|submit|report|leak|deliver|e-?mail|mail|copy|"
            r"exfiltrate)\w*\b" + _S + r"{0,80}?\b(?:to|at|via|into)\s+" + TARGET,
        ),
    ),
    Rule(
        "exfil-encode",
        "exfiltration",
        "encoding is how data is smuggled past a reader",
        (
            r"\b(?:encode|convert|wrap|transform)\b" + _S + r"{0,40}?"
            r"\b(?:base-?64|b64|hex|rot-?13)\b",
            r"\bbase-?64[- ]?encodes?\b",
        ),
    ),
    Rule(
        "exfil-contents",
        "exfiltration",
        "a definition that asks for data the call does not need",
        (
            r"\b(?:include|append|attach|embed|pass|put|add|insert|paste|send|provide)\b"
            + _S
            + r"{0,60}?\b(?:(?:full |entire |complete )?contents? of|entire (?:file|conversation|"
            r"history|chat|context)|(?:conversation|chat|message) history|system prompt|"
            r"api[ _-]?keys?|credentials?|passwords?|secrets?|(?:access|auth|bearer|session) "
            r"tokens?|private keys?|environment variables?|previous messages)\b",
        ),
    ),
    Rule(
        "zero-width",
        "characters",
        "a character that shows nothing, so a person reads text the model reads differently",
        chars=_chars(
            (0x200B, 0x200D),
            (0x2060, 0x2064),
            0xFEFF,
            0x180E,
            0x00AD,
            0x034F,
            0x115F,
            0x1160,
            0x3164,
            0xFFA0,
        ),
    ),
    Rule(
        "bidi-control",
        "characters",
        "a direction control, which can show text in another order than the model reads it",
        chars=_chars((0x202A, 0x202E), (0x2066, 0x2069), 0x200E, 0x200F, 0x061C),
    ),
    Rule(
        "tag-characters",
        "characters",
        "invisible tag characters, which can carry a whole hidden message",
        chars=_chars((0xE0000, 0xE007F)),
    ),
    Rule(
        "variation-selectors",
        "characters",
        "a variation selector, which shows nothing and can carry a hidden message",
        chars=_chars((0xFE00, 0xFE0F), (0xE0100, 0xE01EF)),
        one_after_non_ascii=True,
    ),
    Rule(
        "control-characters",
        "characters",
        "a control character, which a terminal or page may act on instead of showing",
        chars=_chars((0x00, 0x08), (0x0B, 0x0C), (0x0E, 0x1F), (0x7F, 0x9F)),
    ),
    Rule(
        "confusable-letters",
        "characters",
        "look-alike letters, which make a word read as another",
        special="confusable",
    ),
    Rule(
        "long-text",
        "length",
        "text too long for a person to read with care before approving",
        special="length",
    ),
)

_COMPILED = {rule.id: [re.compile(p, re.IGNORECASE) for p in rule.patterns] for rule in RULES}
_DIRECTIVE = re.compile(DIRECTIVE, re.IGNORECASE)
_SENTENCE_END = re.compile(_END, re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]+")
# Folding (docs/SCAN-SPEC.md, section 2): pattern rules and shadow-other-tool read the text
# with the characters of these rules removed, since they show nothing, each other character
# through NFKC (fullwidth and mathematical letters become ASCII ones), and these look-alikes
# of the apostrophe as "'". Character rules, confusable-letters and long-text read the text
# as it is.
FOLD_AWAY = ("zero-width", "bidi-control", "tag-characters", "variation-selectors")
_AWAY = frozenset().union(*(rule.chars for rule in RULES if rule.id in FOLD_AWAY))
APOSTROPHES = _chars(0x2018, 0x2019, 0x201B, 0x02BC, 0x2032, 0x00B4, 0xFF07)
OTHER_SCRIPTS = {"CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE", "FULLWIDTH"}
# Cyrillic and Greek letters that look like Latin ones (docs/SCAN-SPEC.md, section 3).
LOOKALIKES = _chars(
    0x0430, 0x0435, 0x043E, 0x0440, 0x0441, 0x0443, 0x0445, 0x0455, 0x0456, 0x0458, 0x04BB,
    0x0501, 0x051B, 0x051D, 0x0410, 0x0412, 0x0415, 0x041A, 0x041C, 0x041D, 0x041E, 0x0420,
    0x0421, 0x0422, 0x0425, 0x0405, 0x0406, 0x0408, 0x03B1, 0x03BF, 0x03BD, 0x03C1, 0x0391,
    0x0392, 0x0395, 0x0396, 0x0397, 0x0399, 0x039A, 0x039C, 0x039D, 0x039F, 0x03A1, 0x03A4,
    0x03A5, 0x03A7,
)  # fmt: skip


@dataclass(frozen=True)
class Flag:
    rule: str
    field: str  # as printed: through printable(), cut
    span: str  # as printed: through quote()
    why: str

    @property
    def line(self) -> str:
        return f'flag {self.rule} in {self.field}: "{self.span}" ({self.why})'


def strings(obj, path: str = ""):
    """(field path, text) for every string of a stored copy, keys included, in canonical
    order: an object's keys sorted, each key before its value, lists in order."""
    if isinstance(obj, dict):
        for key in sorted(obj):
            where = f"{path}.{key}" if path else str(key)
            yield f"{where} (a key)", str(key)
            yield from strings(obj[key], where)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from strings(item, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


_FOLD = re.compile("[ \t\n\r]+")


def quote(text: str, limit: int = SPAN_LIMIT) -> str:
    """A span as a flag quotes it: runs of ASCII space, tab, newline and carriage return
    folded to one space, every other character outside printable ASCII escaped by printable(),
    and the result cut to at most `limit` characters, ending in "..." when cut, never inside an
    escape. Unlike safe(), a Unicode space or a control character that str.split() counts as
    whitespace is escaped, not folded, since it may be the reason for the flag."""
    out: list[str] = []
    size = 0
    for ch in _FOLD.sub(" ", text).strip(" "):
        piece = printable(ch)
        if size + len(piece) > limit:
            while out and size + len(CUT_MARK) > limit:
                size -= len(out.pop())
            return "".join(out) + CUT_MARK
        out.append(piece)
        size += len(piece)
    return "".join(out)


def fold(text: str) -> tuple[str, list[int] | None]:
    """The text pattern rules read (FOLD_AWAY's characters removed, the rest through NFKC,
    apostrophes made ASCII), and for each of its characters the index in `text` of the
    character it came from; None when the text is ASCII and folds to itself."""
    if text.isascii():
        return text, None
    out: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        if ch in _AWAY:
            continue
        piece = "'" if ch in APOSTROPHES else unicodedata.normalize("NFKC", ch)
        out.append(piece)
        where.extend([i] * len(piece))
    return "".join(out), where


def _unfold(found, where: list[int] | None, size: int):
    """A match on the folded text, as bounds in the original text."""
    if found is None or where is None:
        return found
    start, end, why = found
    begin = where[start] if start < len(where) else size
    stop = where[end - 1] + 1 if end > start else begin
    return begin, stop, why


def _styled(ch: str) -> bool:
    """A fullwidth or mathematical letter: one that NFKC folds to an ASCII letter."""
    if ch.isascii() or not unicodedata.name(ch, "").startswith(("FULLWIDTH LATIN", "MATH")):
        return False
    folded = unicodedata.normalize("NFKC", ch)
    return folded.isascii() and folded.isalpha()


def _sentence(text: str, start: int, end: int) -> tuple[int, int]:
    """The bounds of the sentence holding text[start:end]."""
    begin = 0
    for found in _SENTENCE_END.finditer(text, 0, start):
        begin = found.end()
    after = _SENTENCE_END.search(text, end)
    return begin, after.start() if after else len(text)


def _script(ch: str) -> str:
    if ch.isascii():
        return "LATIN"
    name = unicodedata.name(ch, "")
    if name.startswith("FULLWIDTH LATIN"):
        return "FULLWIDTH"
    return name.split(" ", 1)[0]


def _names_pattern(names) -> re.Pattern | None:
    names = sorted({n for n in names if n}, key=lambda n: (-len(n), n))
    if not names:
        return None
    alternatives = "|".join(re.escape(n) for n in names)
    return re.compile(r"(?<![\w.-])(?:" + alternatives + r")(?![\w-])", re.IGNORECASE)


def _match(rule: Rule, text: str, names: re.Pattern | None, folded: tuple):
    """(start, end, why) of the rule's first match in text, or None. `folded` is fold(text):
    pattern rules and shadow-other-tool match the folded text, and their bounds are mapped
    back to the original."""
    if rule.special == "length":
        if len(text) > LONG_TEXT:
            return 0, LONG_QUOTE, f"{rule.why}; {len(text)} characters"
        return None
    if rule.special == "confusable":
        letters = [ch for ch in text if ch.isalpha()]
        latin = sum(1 for ch in letters if _script(ch) == "LATIN")
        mostly_latin = letters and latin * 2 >= len(letters)
        for word in _WORD.finditer(text):
            letters_of = word.group(0)
            scripts = {_script(ch) for ch in letters_of}
            mixed = "LATIN" in scripts and scripts & OTHER_SCRIPTS
            disguised = mostly_latin and len(letters_of) >= 2 and set(letters_of) <= LOOKALIKES
            styled = len(letters_of) >= 2 and any(_styled(ch) for ch in letters_of)
            if mixed or disguised or styled:
                return max(0, word.start() - CONTEXT), word.end() + CONTEXT, rule.why
        return None
    if rule.chars:
        for i, ch in enumerate(text):
            if ch not in rule.chars:
                continue
            before = text[i - 1] if i else ""
            if rule.one_after_non_ascii and before and not before.isascii():
                if before not in rule.chars:
                    continue
            return max(0, i - CONTEXT), i + 1 + CONTEXT, rule.why
        return None
    seen, where = folded
    if rule.special == "names":
        if names is None:
            return None
        for found in names.finditer(seen):
            begin, end = _sentence(seen, found.start(), found.end())
            if _DIRECTIVE.search(seen, begin, end):
                return _unfold((begin, end, rule.why), where, len(text))
        return None
    best = None
    for pattern in _COMPILED[rule.id]:
        found = pattern.search(seen)
        if found and (best is None or found.start() < best[0]):
            best = (found.start(), found.end(), rule.why)
    return _unfold(best, where, len(text))


def scan(copy: dict, names=()) -> list[Flag]:
    """Every flag of one stored copy: one per rule and field, the first match, in field order
    and then the order of RULES. `names` is the context of shadow-other-tool (section 4)."""
    pattern = _names_pattern(names)
    flags = []
    for field, text in strings(copy):
        shown_field = printable(field)[:FIELD_LIMIT]
        folded = fold(text)
        for rule in RULES:
            found = _match(rule, text, pattern, folded)
            if found is None:
                continue
            start, end, why = found
            flags.append(Flag(rule.id, shown_field, quote(text[start:end]), why))
    return flags


def lines(flags: list[Flag]) -> list[str]:
    """The flag lines `pending` and `approve` print: at most SHOWN, then a count of the rest."""
    shown = [f.line for f in flags[:SHOWN]]
    if len(flags) > SHOWN:
        shown.append(f"and {len(flags) - SHOWN} more flags")
    return shown


def rule_ids(flags: list[Flag]) -> list[str]:
    """The sorted rule ids of a definition's flags, as tool.approved records them."""
    return sorted({f.rule for f in flags})


def context_names(keys, upstream: str, tool: str) -> set[str]:
    """shadow-other-tool's context from the ledger's (prefix, tool) keys (section 4): every
    other tool's exposed name; another upstream's identifier-like bare tool names; and
    "<prefix> server" and "<prefix> mcp" for another upstream."""
    names = set()
    for prefix, name in keys:
        if name == INSTRUCTIONS or (prefix, name) == (upstream, tool):
            continue
        names.add(f"{prefix}__{name}")
        if prefix != upstream:
            names.update((f"{prefix} server", f"{prefix} mcp"))
            if len(name) >= 4 and any(c in name for c in "_-."):
                names.add(name)
    return names
