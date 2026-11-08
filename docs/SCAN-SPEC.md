# Scan spec (M1b)

This is the behavior contract for M1b: scanning and review. It adds to the pins of PIN-SPEC.md, which says in section 1 that scan rules, the review UI with diffs, group approval of unflagged tools, and tool `_meta` and upstream `instructions` are M1b's. Pins, `pending`, `approve` and the hash are in PIN-SPEC.md, the card in CARD-SPEC.md, the ledger format in LEDGER-SPEC.md, and `serve` in PROXY-SPEC.md. Everything they say stays as written, except where this file changes it. The owner's brief is in section 13, and every guess this file had to make is a numbered deviation in section 14.

## 1. What M1b is and is not

**In M1b:**
- **The scanner.** Named rules, kept as data, read every string of a tool's served definition: its description, its title, the text and keys of its schemas, its annotations, and any `_meta` it serves. Each flag names its rule and quotes what matched (sections 2 to 4).
- **Flags in `pending`.** Each new or changed definition lists its flags. A flagged definition is never in a group: it is approved by name, one at a time (section 5).
- **The review view on the card.** `polarizer card` gains a section for definitions waiting for a decision: each with its flags, a diff against the approved (or rejected) definition, and Approve; and one button, `Approve all unflagged`, which records exactly what `approve --group` records (section 6).
- **Upstream instructions,** the `instructions` an upstream returns when Polarizer connects, pinned and approved like a tool, and served to the client only once approved (section 7).
- **Tool `_meta`,** served only for the keys a new config key, `meta_allowlist`, names per upstream, and then pinned with the rest of the definition under the hash prefix `POLARIZER-TOOLDEF/2` (section 8).

**Not in M1b:**
- reading results, or anything a tool returns: the scanner reads definitions only (taint, TAINT-SPEC.md, covers what an agent read);
- a model judging a definition: every rule is a fixed pattern or a count, written in code;
- rejecting on the card: `polarizer reject` stays the one way to reject;
- the practice range's poisoned and shadowing servers (M4), which will use these rules.

**What it protects.** A definition is text the model reads as instructions. The scanner points a person at the parts of that text that look like an attack before they approve it: instructions hidden from the user, a tool that tries to change how another tool is used, hints to send data out, characters a person can't see, and text too long to read. A flagged definition can't ride along in a group approval.

**What it does not protect.** Section 10, stated plainly: a pattern can be paraphrased, and a definition that is benign says nothing about what the server does.

## 2. What the scanner reads

The scanner takes one stored copy (PIN-SPEC.md, section 5): the exact object Polarizer would serve, with the upstream's own name. It never reads the live list. For an upstream's instructions (section 7) the stored copy is `{"instructions": "<text>"}`.

**Every string is read:** every string value at any depth, and every key of every object, in the order of the canonical form (keys sorted, lists in order). So a description, a parameter's name, an `enum` value, a `default`, a schema's `title`, an annotation's title and a served `_meta` value are all read. Numbers, booleans and nulls are not.

**The field** of a flag names where the string was, as a dotted path: `description`, `inputSchema.properties.note.description`, `inputSchema.required[0]`, `_meta.vendor/hint`, `instructions`. A key is named by its path with ` (a key)` after it, such as `inputSchema.properties.note (a key)`. The path goes through `printable()` (PIN-SPEC.md, section 7) and is cut to 120 characters.

**One flag per rule and field:** the first match of that rule in that string. A definition's flags are listed in field order, then in the order of section 3's table.

**The quoted span:**
- for a pattern rule, the matched text;
- for a character rule, the 12 characters on each side of the first such character, with it in the middle;
- for `long-text`, the first 60 characters, and the count in the why line.

It goes through `scan.quote()`, which escapes as `printable()` does and cuts as `safe()` does (PIN-SPEC.md, deviation 46), with a limit of 80 characters: runs of ASCII space, tab, newline and carriage return fold to one space, every other character outside printable ASCII is escaped as `\xNN` or `\uNNNN`, and a longer span ends in `...`, never inside an escape. An invisible character therefore shows as its escape. Unlike `safe()`, a Unicode space or control that Python counts as whitespace (U+0085, U+2028) is escaped rather than folded, since it may be what the rule flagged (deviation 19).

**The flag line,** as `pending` prints it and the card shows it:

    flag <rule> in <field>: "<span>" (<why>)

At most 10 flag lines are printed per definition, then `and <n> more flags`. All of them count for grouping.

**Rules are data.** `src/polarizer/scan.py` holds a table of rules, each with an id, a family, what it matches (a list of regular expressions, a set of characters, or a length), and a why line. The scanner is one function over that table, `scan(copy, names)`, which returns the flags; `names` is section 4's context. It reads nothing else and writes nothing.

**Folding** (added by the review fixes of Nov 8, 2026 (UTC)). The pattern rules and `shadow-other-tool` read each string folded, one character at a time: the characters of `zero-width`, `bidi-control`, `tag-characters` and `variation-selectors` are removed, since they show nothing; U+2018, U+2019, U+201B, U+02BC, U+2032, U+00B4 and U+FF07 become `'`; and every other character goes through NFKC, so fullwidth and mathematical letters become ASCII ones and `ignore` in either still reads as `ignore`. A match on the folded text is quoted from the original text, so the flag shows the characters that were there. The character rules, `confusable-letters` and `long-text` read the string as it is, unfolded. Folding is per character, so it does not compose a letter with a following combining mark.

## 3. The rules

Patterns are Python regular expressions, matched case-insensitively, on the folded text (section 2). A bounded run of characters that are not a sentence end keeps a match inside one sentence. **A sentence ends** at a newline, or at `.`, `!` or `?`, with any closing quotes, parentheses or brackets after it, that is followed by whitespace or the end of the text; the period of `e.g.`, `i.e.`, `cf.`, `vs.`, `viz.`, `approx.`, `incl.` or `esp.` ends nothing. So a period inside a file name, a host or a version (`package.json`, `evil.com`, `v1.2`) does not split a sentence. The pattern rules and `shadow-other-tool` use this one definition (`scan._END`). The exact expressions are in `scan.py`; this table says what each one is for.

| Rule | Family | Matches | Why line |
|---|---|---|---|
| `ignore-instructions` | hidden instructions | `ignore`, `disregard`, `forget`, `override` or `bypass`, then within the sentence `previous`, `prior`, `above`, `all`, `system`, `your` and the like, then `instructions`, `prompt`, `rules` or `guidelines` | text telling the model to drop the instructions it has |
| `conceal-from-user` | hidden instructions | `do not` / `don't` / `never` / `without` with `tell`, `mention`, `inform`, `reveal`, `show` or `notify` and `the user` (or the human, person, operator); `the user must not know`; `keep this secret` | text telling the model to keep something from the person |
| `read-before-use` | hidden instructions | `before using this tool`, `before calling any tool` and the like; `this tool requires you to first read` | a precondition that makes the model do something before the call |
| `secret-path` | hidden instructions | `.ssh`, `.aws`, `.gnupg`, `.kube`, `.docker`, `.azure`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, a shell history or `.env` as a path (after whitespace, `/`, a backslash, a quote, `(` or `~`, so `C:\Users\me\.ssh` counts); `id_rsa` and the other key names; `authorized_keys`; `mcp_config.json`, `claude_desktop_config.json`, `.claude.json`, `credentials.json`; `/etc/passwd`, `/etc/shadow`, `/etc/sudoers` | a definition that names a place where secrets are kept |
| `instruction-tag` | hidden instructions | a tag such as `<IMPORTANT>`, `<system>`, `<instructions>`, `<secret>`, `<hidden>`, `<admin>` or `<override>` | a tag that marks text as instructions for the model |
| `shadow-other-tool` | shadowing | in one sentence, the name of a tool or server behind another upstream (section 4) and a directive word: `must`, `always`, `never`, `instead`, `before`, `after`, `whenever`, `do not`, `should`, `only`, `replace`, `override`, `redirect` | a definition that tells the model how to use another server's tool |
| `shadow-any-tool` | shadowing | `when using any other tool`, `before calling other tools`; `instead of the <name> tool`; `this rule also applies to`; `all other tools must` | a definition that reaches past its own tool to change how others are used |
| `exfil-send` | exfiltration | `send`, `post`, `upload`, `forward`, `transmit`, `submit`, `report`, `leak`, `deliver`, `email`, `copy` or `exfiltrate`, then within the sentence `to`, `at`, `via` or `into` and a place: a URL, an e-mail address, an IPv4 address, a bracketed IPv6 address, or a host name. A host name ends in one of a fixed list of top-level domains (`scan.TLDS`: the common generic ones, many country codes, and ones often used for throwaway hosts such as `.tk`, `.ly` and `.gg`), or, for the domains that are also file extensions (`scan.FILE_TLDS`: `.sh`, `.md`, `.py`, `.cc` and others), has two labels or more before it, so `drop.evil.sh` is a host and `run.sh` is not | a definition that tells the model to send something to a named place |
| `exfil-encode` | exfiltration | `encode`, `convert`, `wrap` or `transform` with `base64`, `hex` or `rot13` in the sentence; `base64-encode` | encoding is how data is smuggled past a reader |
| `exfil-contents` | exfiltration | `include`, `append`, `attach`, `embed`, `pass`, `put`, `add`, `insert`, `paste`, `send` or `provide`, then within the sentence `the contents of`, `the entire conversation`, `chat history`, `system prompt`, `API key`, `credentials`, `password`, `secret`, `token`, `private key`, `environment variables` or `previous messages` | a definition that asks for data the call does not need |
| `zero-width` | characters | U+200B to U+200D, U+2060 to U+2064, U+FEFF, U+180E, U+00AD, U+034F, U+115F, U+1160, U+3164, U+FFA0 | a character that shows nothing, so a person reads text the model reads differently |
| `bidi-control` | characters | U+202A to U+202E, U+2066 to U+2069, U+200E, U+200F, U+061C | a direction control, which can show text in another order than the model reads it |
| `tag-characters` | characters | U+E0000 to U+E007F | invisible tag characters, which can carry a whole hidden message |
| `variation-selectors` | characters | U+FE00 to U+FE0F and U+E0100 to U+E01EF, except one directly after a character outside ASCII that is not itself a selector (an emoji's presentation selector, an ideograph's variant), which is how ordinary text uses them | a variation selector, which shows nothing and can carry a hidden message |
| `control-characters` | characters | C0 controls other than tab, newline and carriage return, DEL, and C1 controls (U+0080 to U+009F) | a control character, which a terminal or page may act on instead of showing |
| `confusable-letters` | characters | a word that mixes Latin letters with Cyrillic, Greek, Armenian, Cherokee or fullwidth letters; or, in a string whose letters are mostly Latin, a word of two or more letters all from a fixed set of Cyrillic and Greek letters that look Latin; or a word of two or more letters with a fullwidth or mathematical letter in it (one whose Unicode name starts `FULLWIDTH LATIN` or `MATHEMATICAL` and that NFKC folds to an ASCII letter) | look-alike letters, which make a word read as another |
| `long-text` | length | any one string longer than 1024 characters | text too long for a person to read with care before approving |

**The 1024.** It was chosen before the reference servers were scanned, as the ledger's limit for free text (PIN-SPEC.md, section 3), and is not tuned to them (section 11, False flags).

## 4. The context: other tools' names

`shadow-other-tool` needs to know what else is there. Its context is built from the pins (PIN-SPEC.md, section 4) of the ledger the command reads: every (prefix, tool) the ledger has named, other than the definition's own and other than instructions. From each it takes:
- the exposed name, `<prefix>__<tool>`, for every tool, of any upstream;
- the bare tool name, for a tool of another upstream, when it has at least 4 characters and holds `_`, `-` or `.`, so `read_file` counts and `echo` doesn't;
- `<prefix> server` and `<prefix> mcp`, for another upstream's prefix.

A name matches as a whole word, case-insensitively: no letter, digit, `_`, `-` or `.` just before it, and no letter, digit, `_` or `-` just after. The context depends only on the ledger, so `pending`, `approve` and the card compute the same flags for the same ledger.

## 5. `pending` and `approve`

### `pending`

Each `new` and `changed` block whose stored copy passes its check lists its flags (section 2) after its header lines and its class line (with `--config`), before its definition. Unservable blocks and blocks whose copy fails have none.

**A flagged block is never grouped.** A `new` block of a tool with no decision and one definition waiting, which PIN-SPEC.md would group, prints on the line after its header:

    flagged by the scanner; read it, then approve it by name

A block that already prints `not in a group, because this tool has a decision; approve it by name` or `more than one definition waits for this tool; approve one by name` keeps that line, and its flags follow it.

**The group line** is unchanged when no block is flagged. When any block is flagged, it reads `group <id> covers the <k> unflagged new definitions above for tools with no decision yet and one definition waiting`. The group id is computed as PIN-SPEC.md says, over the blocks in the group, so a ledger with no flags keeps its ids.

**Instructions** (section 7) list as `new <prefix>__:instructions <def_hash>` or `changed <prefix>__:instructions <live_hash>, approved <approved_hash>`, with the stored copy as their definition and no class line.

**Diffs are not printed** by `pending`; the card shows them (section 6).

### `approve`

- **`approve --group`** recomputes the group as `pending` does, flags included, so a definition flagged between the two commands changes the id and nothing is approved.
- **`approve <prefix> <tool> <def_hash>`** of a flagged definition prints the definition and its class line as before, then its flag lines, then the `approved` line, and records the rule ids in `tool.approved`'s new `flags` field (section 9). An unflagged one prints and records exactly what M1a did.
- **`approve <prefix> :instructions <def_hash>`** approves instructions. `reject` takes them the same way.

## 6. The card's review view

The card (CARD-SPEC.md) gains one section, `Tool definitions waiting for approval`, below the held calls, and two requests. Every check of CARD-SPEC.md, section 3 applies to them as to `GET /state` and `POST /decide`.

| Request | Needs | Answer |
|---|---|---|
| `GET /review` | Host, cookie, nonce | 200, the review as JSON |
| `POST /approve` | Host, cookie, nonce, Origin, JSON | 200 for approvals recorded, 409 for one refused, 400 for a malformed body, 413 for a body over 16 KiB |

### `GET /review`

One JSON object, sorted keys, `ensure_ascii`:

```json
{
  "status": "intact",
  "problem": [],
  "definitions": [<definition>, ...],
  "group": {"id": "<group id>", "count": <k>, "names": ["<prefix>__<tool>", ...]} or null
}
```

- **`status` and `problem`** are `GET /state`'s, from the same read of the ledger. With a problem, `definitions` is empty and `group` is null.
- **A definition** is `{"upstream", "tool", "def_hash", "name", "header": [lines], "flags": [lines], "diff": [lines], "definition": [lines], "in_group": true or false, "can_approve": true or false}`, one for each `new` or `changed` block `pending` would print, in its order. `header` is the block's header lines as `pending` prints them, its grouping line included; `flags` its flag lines; `definition` the stored copy as `pending` renders it, or the one line `pending` prints for a copy that fails its check, with `can_approve` false.
- **`diff`** compares the stored copy, rendered as `pending` renders it, with the definition the tool's latest decision names: for a `changed` block the approved one, and for a `new` block after a rejection the rejected one. It is a unified diff with 3 lines of context, headed `--- approved <hash>` (or `rejected`) and `+++ waiting <hash>`, its lines escaped by the rendering. It is empty for a tool with no decision. When the decided copy fails its check, it is one line: `no diff: stored copy defs/<hash>.json <problem>`. A revert never shows here as identical: as PIN-SPEC.md, section 4 says, `pending` keeps listing the change it reverted.
- **`group`** is the group `pending` would print, or null.

Every answer of `GET /review` marks its definitions and its group id as shown.

### `POST /approve`

The body is one of `{"upstream": "<prefix>", "tool": "<tool>", "def_hash": "<64 hex>"}` or `{"group": "<64 hex>"}`, with exactly those keys. Anything else is 400, `{"ok": false, "error": "polarizer: the request is not an approval"}`.

1. **The card must have shown it:** the definition, or the group id, in an answer to `GET /review`. Otherwise 409, `polarizer: this card has not shown <prefix>__<tool> <def_hash>; reload the page` (or `group <id>`).
2. **The approval** goes through `decisions.Decider`, exactly as `polarizer approve` and `approve --group` (PIN-SPEC.md, section 7): the ledger opened as a writer, with the 2 s lock wait and full verification; the definition required to have been seen, its stored copy reread and rehashed; the group recomputed and compared with the id. One decision runs at a time in a card, holds and approvals together.
3. **The answer:** 200 with `{"ok": true, "lines": [<the lines approve prints>]}`. A refusal is 409 with `{"ok": false, "error": "<the refusal's line>"}`, and nothing is written. A group whose writer fails partway is 409 with the lines already printed and the error, as `approve --group` prints them.

**What is recorded** is exactly what `approve` records, with `terminal: false` for a card started with `--allow-no-terminal`. No `elapsed_ms` is added (deviation 9).

### The page

- Each definition is a box with its name, its header lines, its flags in a marked list beside the definition, the diff in a monospace box, and the definition in another, and one button, `Approve this definition`, disabled when `can_approve` is false.
- Above the boxes, when `group` is not null, one button: `Approve all unflagged (<k>)`, with the names it covers. A flagged definition's box says `Flagged: approve it on its own, after reading it.`
- With nothing waiting: `No tool definition waits for approval.`
- The page asks for `GET /review` with every `GET /state`, every 1.5 s, and places every line with `textContent`, as CARD-SPEC.md, section 9 requires.
- After a click, the answer's lines show in the `Last decision` box, as for a hold.

## 7. Upstream instructions

**What they are.** An MCP server may return `instructions` when a client connects: text meant for the model about how to use the server. The SDK's client keeps them (`Client.instructions`), from `initialize` on 2025-11-25 and from `server/discover` on 2026-07-28. M0 and M1a never passed them on.

**Pinned like a tool.** When an upstream connects with instructions (a string that is not empty), `serve` treats them as one more definition of that upstream, under the reserved tool name `:instructions`. No tool can have that name: the exposed-name rule admits only `[A-Za-z0-9._-]`.
- **The stored copy** is the canonical bytes (RFC 8785) of `{"instructions": "<text>"}`, and its hash is `hex(sha256(b"POLARIZER-INSTRUCTIONS/1\n" + canon))`. The size limit, the four fixed reasons for text that can't be hashed, and the stored-copy rules are PIN-SPEC.md's.
- **The records** are `tool.seen`, `tool.drift`, `tool.unservable`, `tool.approved` and `tool.rejected`, with `tool` `:instructions`. The fold, the states, the cap, sticky drift and the latest decision winning are PIN-SPEC.md's, unchanged.
- **Observed** at every successful listing, as the tools are, from the instructions of that connection. An upstream that returns none has nothing to observe, and earlier approved instructions are not served.

**Served once approved.** At the end of startup, before the client connects, `serve` sets its own instructions from every upstream, in config order, whose instructions are in the approved state:

    Instructions from upstream <prefix>, whose tools are named <prefix>__<tool>:
    <the stored copy's text>

with a blank line between upstreams, or none when no upstream's are approved. The client gets them once, when it connects (on 2026-07-28, in `server/discover`; before, in `initialize`), so a decision on instructions applies from the next start of `serve`: reconnect the server in `/mcp`. A change notice can't carry them.

`serve`'s start line counts them apart from tools: `polarizer: the instructions of <n> upstreams wait for approval; run polarizer pending`.

## 8. Tool `_meta` and the allowlist

**The key.** `[upstream.<p>]` gains `meta_allowlist`, a list of at most 32 strings, each 1 to 256 characters, naming the `_meta` keys of that upstream's tools that may be served. It defaults to `[]`, which serves none, as M1a did. Errors, exit 2, before anything starts:
- `[upstream.<p>] "meta_allowlist" must be a list of at most 32 strings of 1 to 256 characters`.

**The hashed form.** For a tool whose `_meta` holds at least one listed key:
1. Steps 1 to 3 of PIN-SPEC.md, section 2, with `_meta` set in the dumped form to an object holding exactly the listed keys the tool has, with their values. The SDK's 2026-07-28 form keeps it.
2. Step 4 as before.
3. **The prefix becomes `POLARIZER-TOOLDEF/2`:** `def_hash = hex(sha256(b"POLARIZER-TOOLDEF/2\n" + canon))`.

A tool whose `_meta` holds no listed key, and every tool of an upstream with no `meta_allowlist`, hashes exactly as in M1a, under `POLARIZER-TOOLDEF/1`. Its served form has not changed, so nothing new needs reading (deviation 4).

**The stored copy and serving.** The stored copy holds `canon`, `_meta` included, and is served as PIN-SPEC.md, section 5 says, so the client gets the allowed keys and nothing else. Rehashing a copy uses `/2` when it has a top-level `_meta`, `/1` when it doesn't, and `POLARIZER-INSTRUCTIONS/1` when it is an instructions copy; the three forms never share bytes, so each copy has one hash.

**The migration.** A tool approved under `/1` that now serves `_meta` has a new hash, so it is changed: hidden, `tool.drift` recorded, and approved again by name, as any changed definition is. The same holds the other way, when a key leaves the allowlist. At start, `serve` says so plainly, once, when it applies:

    polarizer: <n> tools changed form because meta_allowlist changed what _meta they serve (POLARIZER-TOOLDEF/2 serves _meta, /1 none); each is hidden until approved again; run polarizer pending

counting the listed tools whose approved copy and live form differ in having `_meta`.

**Scanned like the rest.** The served `_meta` is part of the stored copy, so the scanner reads its keys and values (section 2), and `pending` shows it in the definition.

## 9. Ledger

No kind is added. LEDGER-SPEC.md's rows gain:
- **`tool.approved`:** `flags`, a list of the rule ids of the flags the definition had when it was approved, sorted, written only when it is not empty. A group approval never has it, since a flagged definition is never grouped.
- **The pin kinds:** `tool` may be `:instructions` (section 7).

Both verifiers already accept these: the data stays inside the canonical subset. `tools/make_fixtures.py` adds `valid/scan`, a chain with instructions seen and approved, a `/2` definition, and a flagged approval, and `broken/scan_edit_flags`, the same chain with one rule id edited and not rehashed; both verifiers agree on them.

## 10. Limits

Added to LIMITS.md when M1b ships:
- **Scanning reads patterns, not meaning.** A paraphrase, a synonym, another language, a misspelling, text split across fields or broken by a character no rule names, or an instruction spread over several sentences matches no rule. No flag is not a statement that a definition is safe.
- **A benign definition says nothing about behavior.** A server whose descriptions are clean can still do anything when called, and return text that steers the agent. Pins and the scanner read what the model is shown before a call, not what the server does.
- **False flags.** The rules flag ordinary text too, as the reference servers show (EVIDENCE.md). A flag asks a person to read; it is not a verdict.
- **Instructions apply at the next start.** The client reads them once, when it connects, so an approval or rejection of instructions takes effect when Claude Code starts `serve` again.
- **The context is the ledger's.** `shadow-other-tool` knows the tools this ledger has seen, not servers configured directly in the client.

## 11. Tests

"Default" means the test runs in `scripts/test.sh` and CI on every platform. "Reference" means it runs only with `POLARIZER_REFERENCE=1`, locally, never in CI.

### `tests/test_scan.py`

| Test | Claim | Suite |
|---|---|---|
| `test_every_poisoned_fixture_is_flagged` | Every definition in `tests/fixtures/poisoned/` is flagged by the rule its file names, and by no rule it doesn't list | Default |
| `test_every_rule_has_a_fixture` | Every rule in the table has at least one poisoned fixture | Default |
| `test_clean_fixtures_are_not_flagged` | The definitions in `tests/fixtures/clean/` and the test suite's own probe and fake tools have no flag | Default |
| `test_rules_are_data` | Each rule has an id, a family from section 3, a why line, and a pattern list, a character set or a length; ids are unique | Default |
| `test_every_string_is_read` | A pattern in a description, a property's description, a key, an `enum` value, a default, an annotation title and a `_meta` value is flagged, with its field path | Default |
| `test_flag_line_escapes` | The span and field go through `safe()` and `printable()`: a zero-width character shows as `\u200b`, a newline folds, a long span ends in `...` | Default |
| `test_one_flag_per_rule_and_field` | Two matches of one rule in one string give one flag | Default |
| `test_context_names` | `shadow-other-tool` flags another upstream's exposed name and identifier-like bare name with a directive word, not its own upstream's bare names, not a short bare name, not without a directive word | Default |
| `test_long_text_boundary` | 1024 characters: no flag; 1025: the flag with the count | Default |
| `test_review_probes_are_flagged` | Each string the review of Nov 8, 2026 (UTC) found unflagged (a period in a file name, a version or `e.g.`; an e-mail address, an IP literal or another top-level domain; a curly apostrophe; a Windows path; variation selectors, fullwidth and mathematical letters) gets exactly the rules it names | Default |
| `test_ordinary_text_stays_unflagged` | File names such as `run.sh` and `README.md`, `user.email`, versions, `e.g.` and `etc.`, an emoji's presentation selector, an ordinal and a superscript get no flag | Default |
| `test_sentence_end_is_one_definition` | The sentence end of section 3: a period inside `package.json`, `v1.2` or after `e.g.` ends nothing; one followed by a space does | Default |
| `test_spans_quote_the_original_text` | A match on folded text quotes the original characters | Default |

### `tests/test_scan_review.py`

| Test | Claim | Suite |
|---|---|---|
| `test_pending_shows_flags` | `pending_flagged.txt`: flag lines in new, changed and after-rejection blocks, the flagged line, and the group line's `unflagged` wording | Default |
| `test_flagged_never_grouped` | A flagged definition is left out of the group line and the group id; `approve --group` approves only the unflagged | Default |
| `test_group_bound_to_flags` | A definition that becomes flagged between `pending` and `approve --group` (its context gained a name) changes the id; nothing is approved | Default |
| `test_approve_flagged_by_name` | `approve_flagged.txt`: the flag lines after the definition, and `flags` in `tool.approved`; an unflagged approval has no `flags` key | Default |
| `test_card_review_state` | `card_review_mixed.json`: `GET /review` for `pending_flagged`'s ledger, with diffs, flags and the group | Default |
| `test_card_review_needs_cookie_and_nonce` | `GET /review` and `POST /approve` refuse without the cookie, the nonce, the Origin or JSON, with 403, and write nothing | Default |
| `test_card_approves_group_like_the_cli` | On two copies of one ledger, the card's group and `approve --group` write `tool.approved` entries whose data is identical | Default |
| `test_card_approves_one_like_the_cli` | The same for one flagged definition, with `flags`, and for `--allow-no-terminal` against a card without a terminal | Default |
| `test_card_refuses_what_it_did_not_show` | An approval of a definition or group id the card never put in a review answer is 409, nothing written; a stale group id is `approve`'s refusal line | Default |
| `test_card_diff` | A changed definition's diff against the approved copy; a new one after a rejection against the rejected copy; a revert's identical line; a decided copy that fails its check | Default |
| `test_page_has_the_review_section` | `index.html` has the section; `card.js` still has no `innerHTML`, `eval` or `Function(`; all files ASCII | Default |
| `test_scan_fixture_verifies` | `valid/scan` is intact in both verifiers and folds as section 9 says; `broken/scan_edit_flags` is tampered in both | Default |

### `tests/test_instructions.py`

| Test | Claim | Suite |
|---|---|---|
| `test_instructions_pinned_and_hidden` | An upstream's instructions get one `tool.seen` under `:instructions` and a stored copy under `POLARIZER-INSTRUCTIONS/1`, and serve's own instructions are empty until they are approved | Default |
| `test_approved_instructions_served` | After approval, a new gateway serves them, with the header line, to a 2026-07-28 client and a 2025-11-25 one | Default |
| `test_changed_instructions_hidden` | Instructions that change between starts record `tool.drift` and are not served until approved again | Default |
| `test_instructions_scanned` | Poisoned instructions are flagged and never grouped | Default |
| `test_no_instructions_nothing_recorded` | An upstream without instructions records nothing for them | Default |

### `tests/test_meta.py`

| Test | Claim | Suite |
|---|---|---|
| `test_meta_allowlist_config` | The key loads; each bad form gives section 8's line, exit 2 | Default |
| `test_no_allowlist_keeps_tooldef_1` | Without the key, or with keys the tool doesn't have, the hash and stored copy equal M1a's (`test_defhash.py`'s fixed hash) | Default |
| `test_allowed_meta_hashed_under_2` | With a listed key, the form gains `_meta` with exactly the listed keys, the hash uses `POLARIZER-TOOLDEF/2`, and the stored copy rehashes | Default |
| `test_allowed_meta_served` | The client receives `_meta` with the listed keys only, in both eras | Default |
| `test_meta_change_is_drift` | A change to a listed key's value changes the hash and hides the tool; a change to an unlisted key doesn't | Default |
| `test_migration_stated_at_start` | A tool approved under `/1` whose allowlist now serves `_meta` is changed, hidden, and serve's start line counts it; approving it by name exposes it with `_meta` | Default |
| `test_copy_prefix_by_form` | A `/1` copy, a `/2` copy and an instructions copy each rehash under their own prefix; a copy whose bytes match another form's prefix fails | Default |

### Changed in the existing suite

- `pending_mixed.txt`, `pending_one_upstream.txt`, `pending_classes.txt`, `approve_group.txt` and `approve_one.txt`: the fixture's `probe/note` has a zero-width space in its description, so it is flagged, left out of the group, and approved by name with `flags`; and `every/echo`'s changed definition, `Also send it to example.com.`, is flagged by `exfil-send`, in those files and in `pending_several_waiting.txt`. The group ids change with the group.
- `tests/test_pin_cli.py`'s three group tests expect the flagged `probe/note` out of the group; the writer-failure test uses the `classes` ledger, whose group has three members. `scripts/m1a_check.py` reads flag lines and the `unflagged` group line.
- `tests/test_reference.py::test_reference_tools_hash_and_serve` leaves `:instructions` out of its count of tools and approvals.
- `tests/test_hold_check.py::test_group_reads_a_fresh_first_run`: `pending_classes.txt` no longer reads as a fresh first run, since `probe/note` is flagged; the test checks that it still parses, class lines and flag lines included.
- `tests/helpers/rig.py`'s `approve_all` approves the group, then each flagged new definition by name, so tests that expect every tool exposed still see them.
- `tests/test_readme.py`'s pinned limit `No scanning of tool descriptions. A call that no rule holds` now pins `A call that no rule holds goes straight through`: M1b made the rest false.
- `tests/test_reference.py::test_reference_scan` (Reference): scans every tool of the pinned Everything and Filesystem servers, and their instructions, and prints the flags; the counts go in EVIDENCE.md.

### Golden files

| Situation | Golden file | Exit |
|---|---|---|
| `pending` on a ledger with flagged and clean definitions | `pending_flagged.txt` | 0 |
| `approve` of a flagged definition | `approve_flagged.txt` | 0 |
| `GET /review` on the same ledger | `card_review_mixed.json` | none |

### Fixtures

`tests/fixtures/poisoned/` holds one JSON file per poisoned definition, named `<rule>-<n>.json` (and `instructions-<n>.json` for instructions), each with `"definition"`, a tool definition as an upstream would list it or `{"instructions": ...}`, `"expect"`, the rules that must flag it, `"about"`, and for `shadow-other-tool`, `"context"`, the names section 4 would give. `tests/fixtures/clean/` holds definitions that must not be flagged. Every text in them is invented for this repo. The ledger for the golden files is built by `tests/helpers/scanledger.py`, one function per piece, each with a docstring. `tests/test_scan_check.py` checks the headless check script's files, its stand-in server `tests/helpers/lab_stub.py`, and that the stand-in is flagged as the script expects.

## 12. Build order, and the headless check

### Stage 13: scanning and review (medium)

1. **The spec** (this file), the milestones status line, and the stage notes started. Small.
2. **The tests** of section 11, written first, the fixtures and the golden files. Medium.
3. **Code:** `src/polarizer/scan.py` (new); `pins.py` (flags in blocks, grouping, rendering); `decisions.py` (`flags`); `defhash.py` (the `_meta` form, the instructions copy, the prefix by form); `config.py` (`meta_allowlist`); `upstream.py` (instructions kept); `proxy.py` (instructions observed and served, `_meta` hashed, the start lines); `card.py` and the page. Medium.
4. **The headless check** (below), and the docs. Small.

### The headless check

`scripts/scan_check.py`, manual only, on Linux or WSL with the repo's `.venv`, modelled on `scripts/taint_check.py`: `claude -p` with the pinned model `claude-haiku-4-5-20251001`, `--max-budget-usd 1`, `--strict-mcp-config --mcp-config <file>`, and a ledger in a directory it creates under the repo's gitignored `.repro/`. It uses `tests/helpers/lab_stub.py`, a stdlib MCP server with clean `add` and `echo` tools, clean instructions, and a `lookup` tool whose description is poisoned. It shows, on real Claude Code:
1. `pending` flags `lookup` and leaves it out of the group;
2. the card's review view shows the same flags, and its group covers the clean tools and the instructions;
3. `Approve all unflagged` through the card records the group as `approve --group` does;
4. in a first `claude -p` run, Claude Code lists `add` and `echo` but not `lookup`, and can call `add`;
5. after `lookup` is approved alone through the card, a second run lists it.

The interactive check is the owner's (docs/dev/MANUAL-CHECK.md, M1b).

## 13. Decided

The owner's brief for M1b, given on Nov 7, 2026 (UTC):
1. **The scanner:** named rules, kept as data (an id, what it matches, why), over a tool's served definition: description, schema text and any field the client sees; covering hidden instructions, tool shadowing, exfiltration hints, invisible or confusable characters, and unusually long descriptions. Each flag names its rule and quotes the matched span through `printable()` and `safe()`.
2. **Fixtures:** invented poisoned fixtures, at least one per rule, every one flagged.
3. **False flags:** the scanner run over the pinned reference servers' definitions, the false flags counted and reported in EVIDENCE.md, and no rule tuned just to remove one.
4. **The review UI:** in the card, a diff of a changed definition against the approved one, the flags beside it, and "approve all unflagged" as one group action recorded as `approve --group` records; flagged tools approved one at a time; `pending` shows flags too.
5. **`_meta` and instructions:** a config key lists the `_meta` keys served per upstream; the upstream's `instructions` are pinned and shown for approval like a tool; PIN-SPEC.md's prefix rule is followed, with a migration that requires re-approval, stated plainly at start; both verifiers and the conformance fixtures agree.
6. **Checks:** tests before code, golden files for new output, and a headless check on real Claude Code; the interactive check stays the owner's.

## 14. Deviations and guesses

1. **A spec file of its own,** as CARD-SPEC.md and TAINT-SPEC.md are; PIN-SPEC.md gains pointers here.
2. **The scanner reads the stored copy,** the exact served form, not the live list, so what is flagged is what would be served, `_meta` included, and `pending` and the card need no upstream.
3. **Flags are computed when shown, never recorded as observations.** A rule change changes the flags of definitions already waiting. Only an approval records them, in `flags`, so the ledger shows that a person approved a flagged definition.
4. **`POLARIZER-TOOLDEF/2` applies only to definitions that serve `_meta`.** PIN-SPEC.md, section 2 said that serving any `_meta` makes the prefix `/2` and every tool approved again. Here a tool whose served form is unchanged, because the allowlist names none of its keys, keeps its `/1` hash and its approval: the client sees nothing new, and approving unchanged text again is the habit PIN-SPEC.md, section 4 names as the one that wears down attention. Every tool that does serve `_meta` gets a new hash and is approved again, which is the migration the brief asked for, stated at start.
5. **The prefix of a stored copy is chosen by its form** (a top-level `_meta`, an instructions object, or neither), so each copy has one hash. PIN-SPEC.md, section 5 had one prefix.
6. **Instructions reuse the tool kinds** under the reserved name `:instructions`, rather than new kinds, so the fold, the cap, sticky drift, `pending`, `approve` and `reject` apply unchanged. The brief said "pinned and shown for approval like a tool".
7. **Instructions are served with a header line** naming their upstream and its prefix, because Polarizer serves several upstreams as one server, and the model must be able to tell whose instructions it reads.
8. **Instructions apply at the next start,** since the client reads them once, at connection.
9. **The card's approvals record no `elapsed_ms`.** PIN-SPEC.md, section 3 allows it as an optional field; nothing reads a definition's decision time yet, so it is left for a milestone that does.
10. **The card shows a diff against the rejected definition too,** for a new definition after a rejection, as well as against the approved one, which the brief named.
11. **The card does not reject.** The brief asked for approval; `polarizer reject`, which needs a reason, stays the way to reject.
12. **`pending` prints no diff.** The brief put diffs in the review UI; `pending`'s output for a changed block is unchanged apart from flags.
13. **The group line says `unflagged`** only when a block is flagged, so ledgers with no flags keep their output byte for byte.
14. **The existing `pending` and `approve` golden files change** where their fixture's `probe/note`, whose description holds a zero-width space, is now flagged.
15. **`shadow-other-tool` uses the ledger's names as context,** and leaves out short bare names (`echo`), which are ordinary words.
16. **Exfiltration rules need a directive:** `exfil-encode` needs a verb such as `encode` (or `base64-encode`), so `returns base64 encoded data` is not flagged, and `exfil-send` needs a URL or a host name, so `Post a short text to a URL` is not flagged. These were chosen before the reference servers were scanned.
17. **The stage is numbered 13,** its notes docs/dev/STAGE13-NOTES.md, and every exact line, rule id, test name, fixture and golden file name is this spec's choice.
18. **The headless check uses a stand-in server,** `tests/helpers/lab_stub.py`, since no reference server has a poisoned description, and reads what Claude Code lists from its `stream-json` start event.
19. **Spans are folded on ASCII whitespace only** (section 2): `safe()` folds every character Python counts as whitespace, which would turn U+0085, a control the scanner flags, into a plain space in the very quote that explains the flag. Found by `test_flag_line_escapes` while building.
20. **Flags follow the class line** in `pending --config`, rather than preceding it, so a block's header, grouping line and class line stay together as HOLD-SPEC.md, section 8 prints them, and `approve` prints flags in the same place, after the class line.
21. **A test helper approves flagged definitions too.** `tests/helpers/rig.py`'s `approve_all` approves the group, then each new definition the group left out only for its flags, by name, so the existing suite's tests, which expect every tool exposed, keep their meaning; a person would read such a definition first.
22. **`test_scan_fixture_verifies` and `tests/test_scan_check.py`** are tests beyond section 11's tables, as M2b added `test_taint_fixture_verifies` and its check's tests.

The review fixes of Nov 8, 2026 (UTC), for 0.6.1, changed the rules after a review found probe strings no rule flagged at 700498a. Each probe is a test (section 11). The reference servers were scanned again afterwards, and the false-flag count is in EVIDENCE.md.

23. **A sentence ends at punctuation followed by whitespace,** not at every period (section 3), so file names, hosts and versions stay inside their sentence, and the pattern rules now agree with `shadow-other-tool`, whose sentence already needed the whitespace. A short fixed list of abbreviations keeps `e.g.` from ending one. A sentence that ends with no space after its period (`Done.Next`) now runs on into the next; that makes a few more matches possible, not fewer.
24. **More places for `exfil-send`:** e-mail addresses, IPv4 and bracketed IPv6 literals, and a longer list of top-level domains. The list stays fixed rather than any two letters or more, and domains that are also file extensions need two labels before them, so `Copy the script to run.sh` and `Copy files to archive.zip` stay unflagged. Names such as `name`, `email`, `host`, `link`, `zip` and `mov`, which are top-level domains but also ordinary field names and file extensions, are left out. LIMITS.md says what still isn't a place.
25. **Folding before the pattern rules** (section 2): NFKC per character, apostrophe look-alikes as `'`, and the invisible characters of four character rules removed, so a word broken by an invisible character, a curly `don't` and fullwidth or mathematical letters read as plain words. The character rules still flag the invisible characters themselves. NFKC was chosen over a confusables table because it is in the standard library and folds exactly the compatibility forms; it does not fold Cyrillic or Greek look-alikes, which `confusable-letters` covers.
26. **`variation-selectors` is a new rule,** the 17th, in the characters family, rather than more characters in `zero-width`, because one selector after an emoji is ordinary text and needs its own exception. A run of selectors, one after ASCII, or one at the start of a string is flagged; the hidden-message trick needs many in a row.
27. **Styled letters in `confusable-letters`:** a word of two or more letters with a fullwidth or mathematical letter in it. Single letters (a math `x` in a formula) and Latin-1 ordinals (`1a` written with U+00AA) are left alone.
28. **The secret-path lookbehind accepts a backslash,** so Windows paths count like POSIX ones.
