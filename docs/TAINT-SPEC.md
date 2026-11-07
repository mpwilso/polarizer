# Taint spec (M2b)

This is the behavior contract for M2b: argument rules and taint. It adds to the rule function of HOLD-SPEC.md, section 4, whose deny, hold and allow tiers exist so these rules slot in before the holds. Holds, their endings and the commands are in HOLD-SPEC.md, the ledger format in LEDGER-SPEC.md, `serve` in PROXY-SPEC.md, the card in CARD-SPEC.md, and stats and planted calls in MEASURE-SPEC.md, sections 10 and 11. Everything they say stays as written, except where this file changes it. The owner's design is in section 14, and every guess this file had to make is a numbered deviation in section 15.

**Why a file of its own.** HOLD-SPEC.md is M2a's contract, with its own decisions, deviations and review history, and it says in section 1 that taint and argument rules are not in it. CARD-SPEC.md set the pattern of one spec per milestone that builds on HOLD-SPEC.md, so M2b gets one too, and HOLD-SPEC.md gains pointers to it (section 15, deviation 1).

## 1. What M2b is and is not

**In M2b:**
- **Domain allowlists.** Per tool, `domains` lists the hosts an egress tool may reach, and `url_args` names the arguments that hold URLs. An egress call whose URLs all name a listed host runs without a hold (sections 2 and 3).
- **Value rules.** Per tool, `deny_values` maps an argument name to regular expressions. A match refuses the call at once, the rule function's deny tier (sections 2 and 4).
- **Grants.** An egress call allowed through its domains is a grant. That is the only grant M2b adds (section 6).
- **Taint.** A call to a tool of class `open-world` taints its session when its answer comes back. From then on every egress call is held, even to a listed host, and the hold's reason names the tool, the time and the ledger seq of the tainting call. Taint lasts for the rest of the session and survives a restart of `serve` within the same Claude Code session (section 7).
- **What a person sees:** the taint in `polarizer holds`, the card and `polarizer stats` (section 9).

**Not in M2b:**
- reading a result's content: taint follows the tool's class only, never what the result says (section 11);
- a command to clear taint: taint is session-long, by the owner's choice (section 14);
- nested argument names (`edits[0].path`), which HOLD-SPEC.md, section 16, decision 3 put here: they stay out, and section 15, deviation 2 says why;
- grants of any other kind, scanning (M1b) and the practice range (M4).

**What it protects.** An agent that has read a web page, an issue or a mail through Polarizer may be carrying instructions written by someone else. Taint makes every outbound call from that point wait for a person, so text the agent read can't send what it knows to a host it names without someone seeing the exact call first. Domain allowlists let routine outbound calls run before that point, and value rules refuse arguments the person never wants sent.

**What it does not protect.** Section 11, stated plainly: taint sees only calls routed through Polarizer, and only the class of the tool.

## 2. The keys in polarizer.toml

Three keys join `class` and `path_args` in each `[upstream.<p>.tools]` entry:

```toml
[upstream.web.tools]
fetch = { class = "open-world" }
post_comment = { class = "egress", url_args = ["url"], domains = ["api.github.com", "*.example.com"], deny_values = { body = ["BEGIN [A-Z ]*PRIVATE KEY", "(?i)password\\s*="] } }
```

- **`url_args`:** a list of top-level argument names that hold URLs, default empty, at most 16, each 1 to 128 characters, exactly as `path_args` is.
- **`domains`:** a list of host names, default empty, at most 64. Each is a host name in ASCII, or `*.` followed by one; section 3 says what each matches. They are compared in lowercase, and stored and hashed lowercased.
- **`domains` and `url_args` go together,** and only with class `egress`. An egress entry with neither is held on every call, as in M2a.
- **`deny_values`:** a table from argument name (1 to 128 characters, at most 16 names) to a list of 1 to 32 patterns, each a Python regular expression of 1 to 1024 characters, compiled once at load. Any class may have it. Section 4 says what a match is.
- Unknown keys stay an error, and a tool named under `tools` that the upstream doesn't list still has no effect until listed.

**The policy hash.** A tool's form in `policy_sha256` (HOLD-SPEC.md, section 2) gains `"domains"`, `"url_args"` and `"deny_values"` only when each is non-empty, so every existing config keeps its `policy_sha256`. `domains` are hashed lowercased, in the order given; `deny_values` as a table of lists, in the order given.

**`policy.loaded`** gains `client_session` on every start (section 8).

### Errors

Each goes to stderr as one line and exits 2, as HOLD-SPEC.md's do, and `serve` starts no upstream. Text from the file goes through `safe()`.

```
polarizer.toml: [upstream.web.tools] "post_comment" url_args must be a list of at most 16 argument names
polarizer.toml: [upstream.web.tools] "post_comment" domains must be a list of at most 64 host names
polarizer.toml: [upstream.web.tools] "post_comment" domains entry "10.0.0.1" is not a host name: an IP address; list host names only
polarizer.toml: [upstream.web.tools] "post_comment" domains and url_args go together
polarizer.toml: [upstream.web.tools] "fetch" domains and url_args go only with class egress
polarizer.toml: [upstream.web.tools] "post_comment" deny_values must be a table of at most 16 argument names, each with a list of patterns
polarizer.toml: [upstream.web.tools] "post_comment" deny_values "body" must be a list of 1 to 32 patterns of 1 to 1024 characters
polarizer.toml: [upstream.web.tools] "post_comment" deny_values "body" pattern "(" is not a valid regular expression: missing ), unterminated subpattern at position 0
```

A domains entry's reason is the first of these that applies, in this order: `empty`, `longer than 253 characters`, `not ASCII; write it in its xn-- form`, `a wildcard only as a leading "*."`, `an IP address; list host names only`, `an empty label`, `a label longer than 63 characters`, `a character other than a letter, a digit or "-"`, `a label that starts or ends with "-"`. An entry is an IP address when Python's `ipaddress` parses it (with brackets removed), or when its last label is all digits or `0x` followed by hexadecimal digits, which URL parsers read as an IPv4 number. A regular expression's message is Python's `re.error` text.

The checks run in this order for each entry: the `url_args` form, the `domains` form, each domains entry, the pairing, the class, then `deny_values`' form, each list, and each pattern.

## 3. Matching hosts

A URL argument is judged by Polarizer's own parser, below, never by `urllib`: parsers disagree on backslashes, on more than one `@`, and on numeric hosts, and each disagreement is a way for a URL to name one host to Polarizer and another to the tool. So anything the rules below don't accept is held, never guessed.

### Which values are URLs

A tool's `url_args` names top-level argument keys. For each name, in the order the config lists them:
- **missing** (the key is absent, or the arguments are not an object): held, `argument "<a>" is missing`;
- **a string:** one URL;
- **a list of strings:** each is a URL, in order; more than 256 is held, `argument "<a>": more than 256 URLs`;
- **anything else:** held, `argument "<a>" is not a string or a list of strings`.

If no URL was found at all (every named argument was an empty list), the call is held: `no URL in url_args to check`. An egress call is never granted for checking nothing.

### Reading the host

For each URL, in order, the first of these that applies holds the call with `argument "<a>": <why>`:

| # | Condition | `<why>` |
|---|---|---|
| 1 | longer than 8192 characters | `longer than 8192 characters` |
| 2 | any character from U+0000 to U+0020, or U+007F | `a space or control character` |
| 3 | a backslash anywhere | `a backslash` |
| 4 | does not start with a scheme (`[A-Za-z][A-Za-z0-9+.-]*`) and `://` | `not an absolute URL with a host` |
| 5 | the authority (after `://`, up to the first `/`, `?` or `#`) holds more than one `@` | `more than one "@" before the host` |
| 6 | after removing userinfo (everything up to and including one `@`), the rest starts with `[` | `an IP address, which never matches a domain` |
| 7 | it holds a `:`, and what follows the first one is not 1 to 5 digits with a value from 0 to 65535 | `a port that is not a number from 0 to 65535` |
| 8 | the host (before the port) is empty | `no host` |
| 9 | the host holds a character outside ASCII | `a host that is not ASCII; send its xn-- form` |
| 10 | after lowercasing and removing one trailing `.`, a label is empty or holds a character other than `a` to `z`, `0` to `9`, `-` and `_` | `a host that is not a host name` |
| 11 | its last label is all digits, or `0x` followed by hexadecimal digits | `an IP address, which never matches a domain` |

So the scheme may be anything (`https`, `wss`, `ftp`), userinfo is dropped and never matched, a port is checked for form and then ignored, the comparison is case-insensitive, and a trailing dot is the same host. A percent sign in a host, a host written with non-ASCII letters, or one in a form some parser may read as a number, is held.

### Comparing with the domains

The host is listed when one entry matches it:
- **`example.com`** matches exactly `example.com`, and nothing else: not `api.example.com`, not `example.com.evil.net`, not `badexample.com`.
- **`*.example.com`** matches a host ending in `.example.com` with at least one label before it: `api.example.com` and `a.b.example.com`, but not `example.com` itself. List both to allow both.
- There is no other wildcard. A `*` anywhere else is a config error.
- **IP literals never match a name.** An entry can't be an IP address (section 2), and a URL whose host is one is held at rows 6 and 11.
- **IDNA.** An entry must be ASCII, so an internationalized name is listed in its `xn--` form (its A-label), and a URL must send that form too. Polarizer converts neither: Python's built-in IDNA codec follows IDNA 2003, URL parsers follow UTS 46, and the two map some letters differently, so a converted name could name a different host to the tool (section 15, deviation 5).

An unlisted host holds the call with `argument "<a>": host <host> is not in this tool's domains`, the host lowercased and through `safe()`.

## 4. Matching values

`deny_values` is checked for every call to a tool with a configured entry, before anything else in the rule function (section 5). For each argument name, in config order:
- **missing:** no match. A value rule refuses what is there; it never requires an argument.
- **the strings searched:** a string value is searched as it is. In a list or an object, at any depth, every string is searched, object keys included, in document order. A number, `true`, `false` or `null`, at any depth, is searched as its JSON text (`12`, `1.5`, `true`, `null`).
- **a match** is `re.search(pattern, string)` finding one, for any pattern of that argument, in order. Patterns are Python regular expressions compiled with no flags, so `(?i)` makes one case-insensitive. Nothing is normalized first: a value with a zero-width character inside a word does not match the word.

The first match refuses the call, rule `deny-value`, reason `argument "<a>" matches the deny_values pattern <pattern>`, the pattern through `safe()`, cut with the whole reason to 1 KiB.

**Refused at once** (the deny tier): serve appends `call.refused` with `rule` `deny-value` and that reason, writes no side file and no `hold.created`, writes `polarizer: refused <tool> (deny-value): <reason>` to stderr, and the client gets `polarizer: <name> was not allowed`, the line every refusing ending gives (HOLD-SPEC.md, section 6). No person is asked: the person who wrote the pattern has already decided.

## 5. The rule function, with M2b

`evaluate(policy, prefix, tool, arguments, definition, taint=None)`. `taint` is the session's taint (section 7), or None. The order of HOLD-SPEC.md, section 4 becomes:

1. **`--no-holds`:** allow, rule `holds-off`. Nothing else is checked, deny_values and taint included.
2. **The class,** as before.
3. **The deny tier:** a configured entry's `deny_values` (section 4). A match refuses.
4. **The hold tier,** first match wins: `unclassified`, `destructive`, then for `egress` the rows below, then `write-unchecked` and the path rules as before.
5. **The allow tier:** `inside-roots`, `local-read`, `open-world` as before, and `domains` (below).

**Egress, row by row.** HOLD-SPEC.md's row 4 is kept for an egress tool with no `domains`, and these rows are added. `<taint>` is `session tainted by <tool> at <ts> (seq <n>)` (section 7), and "a path rule" means the tool's `path_args` judged as `local-read`, as row 4 does.

| # | Condition | Action | Rule | Reason |
|---|---|---|---|---|
| 4 | no `domains` | hold | `egress` | as HOLD-SPEC.md, row 4 |
| 4a | `domains`; a URL check fails (section 3), the first in order | hold | `egress` | that URL reason; with a path rule that would hold, `; ` and its reason |
| 4b | `domains`; every URL listed; a path rule would hold | hold | `egress` | that path rule's reason |
| 4c | `domains`; every URL listed; no path rule; the session is tainted | hold | `tainted` | `<taint>; egress is held for the rest of the session, even to a host in this tool's domains` |
| 4d | `domains`; every URL listed; no path rule; not tainted | allow | `domains` | none recorded |
| 4e | rows 4, 4a and 4b, when the session is tainted | hold | as that row | `<taint>; ` followed by that row's reason |
| 21 | any class, `deny_values` matches | refused at once, no hold | `deny-value` | section 4 |

- **The taint comes first in a reason,** so the 1 KiB cut never removes it, and `stats.pattern_of`, which reads a pattern after the reason's last ` matches `, still finds a path rule's pattern (the taint text holds no ` matches `: a tool name has no spaces).
- **Rule `tainted` means taint is what held the call:** without it, the call would have run. A call held anyway (rows 4, 4a, 4b) keeps its rule and carries the taint in its reason. So `stats` counts exactly the holds taint caused (section 9).
- **A class from annotations** has no entry, so no `domains` and no `deny_values`: its egress calls are held by row 4, with the taint prefix when tainted and row 19's suffix.
- **Taint changes only egress.** Every other class's rows are unchanged, tainted or not.
- `call.sent` of a call allowed by row 4d records `allowed_by` `domains`.

## 6. Grants

**A grant is an egress call allowed through its tool's domains** (row 4d), recorded as `call.sent` with `allowed_by` `domains`. M2b adds no other grant: `inside-roots`, `local-read` and `open-world` allow calls that are not outbound, and a call allowed from a hold is a person's decision on one call, not a grant.

**Revoked on taint.** From the moment the session is tainted (section 7), row 4c replaces row 4d: no call is granted again in that session, by this process or by any `serve` that later starts in the same Claude Code session. A grant is judged when its call arrives. An egress call that arrived before the taint was recorded may still be forwarded; that is safe, because the taint is recorded before the tainting call's answer reaches the client, so a call that arrived earlier was made before the agent could have read that answer.

## 7. Taint

### What taints, and when

**A call taints its session** when its class, configured or from annotations, is `open-world` and it was forwarded: allowed by row 17 or 18, or allowed from a hold (a read-pattern hold that the person allowed). A call refused, denied, expired, cancelled before it was forwarded, or planted, never taints: nothing came back from it.

**The moment** is the first time anything from the upstream could reach the client: before serve relays the call's first progress notification, or before it answers the call, whichever comes first, whatever the answer (`ok`, `tool-error`, `protocol-error`, `transport-error` or `unsupported`; each can carry text the upstream chose). A call cancelled before any of that taints nothing.

**Recorded first.** At that moment, if the session's taint is not already recorded, serve, one call at a time:
1. appends `session.tainted` (section 8), fsynced with `ledger.head` updated (SECURITY_KINDS), and waits for it. The hold fold takes the entry as it is written, so every call evaluated from then on sees the taint;
2. writes `polarizer: session tainted by <tool> (seq <n>); every egress call is held from now on` to stderr;
3. only then relays the progress or answers the call, and writes its `call.returned`.

So taint can never be lost to a crash between the answer and the record: if the process dies before step 1 finishes, the client never got the answer. An egress call evaluated while step 1 runs is not held by the taint, and needn't be: the agent has not seen the answer yet.

**If the append fails** (the writer has stopped, or the write or fsync raised), the process marks the session tainted in its own memory, with the time of the failure, so its egress calls are held from then on; it withholds the answer, and the client gets `polarizer: the answer from <name> was withheld: the ledger could not record that it taints this session`; nothing more from that call reaches the client, and `call.returned` records the upstream's outcome if the ledger still can. The next open-world answer tries to record the taint again.

**Already recorded:** nothing more is written and the answer goes on as usual. One session has one `session.tainted`, the first, which is its source.

### The session, across restarts

Esc in Claude Code ends `serve`, and Claude Code starts a new one (HOLD-SPEC.md, section 7), with a new `session` id and a fresh process. Taint must survive that, or Esc would clear it.

**The rule.** Claude Code 2.1.310 starts each stdio MCP server with `CLAUDE_CODE_SESSION_ID` in its environment, set to the Claude Code session's id, the same value its `--output-format json` reports as `session_id`, and the same in a server started for a resumed session (`claude -p --resume <id>`): observed headless (verified-facts.md, Taint session id). serve reads it once at start, as the **client session**:
- a value of 1 to 128 characters from `A-Z`, `a-z`, `0-9`, `.`, `_`, `:` and `-` is the client session, recorded in `policy.loaded`'s `client_session`;
- anything else, or no variable, is no client session (`client_session` null).

**A serve is tainted** when the ledger holds a `session.tainted` whose `client_session` equals its own client session (any serve process of that Claude Code session wrote it), or, with no client session, one written by its own serve session with `client_session` null. The first such entry in seq order is the source.

**Folded from the ledger at start.** The hold fold (`holds.HoldState`) also folds `policy.loaded`'s `client_session` and every `session.tainted`, during the startup verification pass, with no second read. After serve writes its own `policy.loaded`, its taint is known before any upstream connects, and before any call is served. A restarted serve in a tainted Claude Code session writes `polarizer: this session was tainted by <tool> at <ts> (seq <n>); every egress call is held` to stderr at start, with holds on.

**Two serve processes of one Claude Code session** on one ledger (two server entries naming the same config) share the taint: each adopts the other's `session.tainted` at its next catch-up, which runs at the start of every call.

**Why this and not section 7's lock files.** HOLD-SPEC.md, section 7 proves that a serve process has ended, through its lock file, without process ids. That says nothing about which Claude Code session a new serve belongs to: an ended serve and a new one look the same whether Claude Code restarted it after Esc or a different session started. The session lock stays what it is, and the client session is the one fact that ties a new serve to its predecessors. A process id of the parent was the other candidate, and is rejected: HOLD-SPEC.md, section 7 avoids process ids, because they are reused, and a parent's identity is harder to read on Windows and macOS.

**A new session starts clean.** A new Claude Code session has a new id, so its serve finds no `session.tainted` for it. `claude --resume` continues the same id, and so the same taint: the resumed conversation still holds what the agent read.

**Without a client session** (another client, an older Claude Code, a value Polarizer doesn't accept), taint lasts only as long as the serve process: a restarted serve starts clean. When any tool has `domains`, serve says so once at start, after the unclassified and annotations lines: `polarizer: warning: CLAUDE_CODE_SESSION_ID is not set; taint lasts only while this process runs`, or `polarizer: warning: CLAUDE_CODE_SESSION_ID is not a session id Polarizer reads; taint lasts only while this process runs`. Without `domains` taint changes no verdict, only reasons, so nothing is said.

**No command clears taint.** It lasts for the rest of the Claude Code session (section 14).

### What is not taint

- Under `--no-holds`, no class is used, so nothing taints, and no `session.tainted` is written. A serve started later in the same session without the flag finds no taint from that run (section 11).
- A planted call is never forwarded, so it never taints.
- Reading a file with a `local-read` tool never taints, even a file the agent downloaded: only the class `open-world` does.

## 8. Ledger kinds

The format, the hash, the statuses and the exit codes don't change.

| Kind | `data` | Written by | fsynced |
|---|---|---|---|
| `session.tainted` | `session` (the serve session writing it), `client_session` (or null), `tool` (the tainting call's exposed name), `call_seq` (the seq of its `call.sent`) | `serve`, before the tainting call's answer or first progress reaches the client | yes, with `ledger.head` updated, before serve goes on |

**Changed kinds,** each gaining fields:
- **`policy.loaded`** has `client_session`, the client session or null, on every start from M2b.
- **`call.sent`**'s `allowed_by` gains `domains` (row 4d).
- **`call.refused`** of a call refused by the deny tier has `rule` `deny-value`, and the reason of section 4. No other `call.refused` has `rule`.
- **`hold.created`** gains no field: rule `tainted` and the taint text in the reason say everything.

**SECURITY_KINDS** gains `session.tainted`. Its rule (HOLD-SPEC.md, section 5): entries are fsynced when losing them could make Polarizer do more than the ledger shows. A lost `session.tainted` would let a restarted serve grant egress calls that the taint revoked.

**The time** in the reason and in `holds` is the `session.tainted` entry's `ts`, the moment the taint was recorded; **the seq** is `call_seq`, the tainting call's `call.sent`, so a person can find what was called.

**The conformance fixture** `valid/taint` (from `tools/make_fixtures.py`) holds a session with a grant, an open-world call, its `session.tainted`, a hold by `tainted`, a restarted serve with the same `client_session`, and a deny-tier `call.refused`. Both verifiers find it intact, and `conformance/expected.json` says so.

## 9. What a person sees

**`polarizer holds`,** and the blocks that `allow`, `deny` and the card print through the same renderer: when the hold's session is tainted, as the fold finds it now, the block gains one line after the `session ... started` line:

```
session tainted by web__fetch at 2026-10-02T15:00:00.004Z (seq 3)
```

Every string in it passes through `printable()`. It appears on every hold of a tainted session, whatever its class (section 15, deviation 8).

**The card** shows the block above, and, for a hold of a tainted session, one more row after `Session`: `Session taint`, with the line `tainted by <tool> at <ts> (seq <n>): every egress call of this session is held`. The recommendation (CARD-SPEC.md, section 4) gains a row for rule `tainted`: `Deny, unless you asked for exactly this: the agent read content from outside this machine earlier in this session, and this call would send data out. Text it read can ask it to send what it knows.` A held egress call of a tainted session with another rule keeps that rule's recommendation.

**`polarizer stats`,** when any session is tainted, gains a section after `all sessions` (and after `planted calls`, when present):

```
taint
  tainted: 1 of 2 sessions, every egress call held from then on
  held by taint 2: allowed 1, denied 0, expired 0, abandoned 0, open 1
```

- A session here is a serve session, as everywhere in `stats`; a restarted serve in a tainted Claude Code session counts as tainted.
- "Held by taint" counts holds with rule `tainted`, the holds taint caused (section 5), among the holds every other count uses: planted holds are already out of them (MEASURE-SPEC.md, section 10).
- Rule `tainted` never gets a suggestion: like the built-in patterns, it fails closed by design, and a person who allows every such hold is what drills are for. Its line under `by tool and rule` is as for any rule.
- With no tainted session the section is left out, so every existing golden file stays as it is.

**stderr,** besides section 7's lines: the deny tier's line (section 4).

## 10. Planted calls

MEASURE-SPEC.md, section 11 stays correct, for these reasons:
- **Eligibility.** A trigger's class must be `local-write`, `destructive` or `local-read` (condition 3), never `egress`, and a planted call uses the trigger's own tool. So a planted call is never an egress call, never a grant, and never held by `tainted`: taint changes only egress (section 5). Its verdict is the same with or without taint, which is why the planter evaluates it without one.
- **A trigger refused by the deny tier is not a trigger,** by condition 2 ("forwarded or held, not refused"). serve passes such a call to the planter as refused, so it is counted toward activity, as a call refused for too many holds is, but nothing is planned from it.
- **A planted call that the deny tier would refuse is dropped,** since its verdict must be a hold with one of the four rules. So no planted call is ever refused at once, which would be a tell.
- **No confusion with taint holds.** A planted hold's rule is `write-pattern`, `read-pattern`, `destructive` or `egress`, never `tainted`, and `stats` counts "held by taint" among real holds only. In a tainted session the taint line in `holds` and the card's `Session taint` row appear on every hold of that session, planted ones included, so they tell nothing about which hold is real.
- **A planted call never taints,** since it is never forwarded.

## 11. Limits

Stated plainly, and added to LIMITS.md:
- **Taint sees only calls routed through Polarizer.** A web page fetched by the agent's own shell (`curl`), by Claude Code's own WebFetch or WebSearch tools, or by an MCP server configured directly in Claude Code does not taint the session.
- **A result's content is not inspected,** only the tool's class. A tool classed `local-read` that returns untrusted text (a downloaded file, a log, a mail stored on disk) does not taint, and an `open-world` call whose result is harmless still does.
- **Taint holds egress only.** After taint, local writes, destructive calls and reads keep their M2a rules; injected text can still steer those, and a write that lands in the workspace runs without a hold.
- **Taint across a restart needs `CLAUDE_CODE_SESSION_ID`,** which Claude Code 2.1.310 sets (observed headless only). Without it, or with a value Polarizer doesn't accept, a restarted serve starts clean, and serve says so at start when any tool has `domains`. Whether interactive Claude Code keeps the same value across the restart that follows Esc is the owner's interactive check (docs/dev/MANUAL-CHECK.md, M2b).
- **A session continued under a new id starts clean,** though its context holds what the agent read: for example, a forked session.
- **`--no-holds` records no taint,** so a session restarted without it starts clean.
- **Domain rules check only the arguments named in `url_args`,** and only URLs with a host. A host inside free text (a mail body, a script), an address without `scheme://` (`bob@example.com`), or a redirect the server follows, is not checked. A listed host can still receive anything in the arguments.
- **A listed host is a host, not a destination.** Polarizer does not resolve names, so a listed name whose DNS points elsewhere, or a server behind it that forwards data on, is outside it.
- **Value rules search the strings in an argument as they are.** Encoded, split or normalized forms (base64, a zero-width character inside a word, another letter case without `(?i)`) do not match. A pattern that backtracks badly can stall the call that triggers it, since Python's regular expressions have no time limit; keep patterns simple.
- **A grant judged before the taint stands.** An egress call that arrived before the taint was recorded may be forwarded (section 6).

## 12. Tests

"Default" means the test runs in `scripts/test.sh` and CI on every platform.

### `tests/test_argrules.py`

| Test | Claim | Suite |
|---|---|---|
| `test_argrule_errors` | Each error line of section 2, for each bad key and each domains reason, exactly, exit 2, and serve starts no upstream | Default |
| `test_policy_hash_unchanged_without_the_keys` | A config without the three keys keeps `test_policy_config.py`'s fixed hash; with them, the form gains exactly the three, lowercased domains | Default |
| `test_valid_keys_load` | The three keys load into the tool's rule, domains lowercased and deny_values in order | Default |
| `test_host_reading_holds` | Every row of section 3's table, with the fixed reason | Default |
| `test_host_reading_accepts` | The accepted forms: userinfo, a port, a trailing dot, uppercase, any scheme, a query or fragment after the host, an `xn--` host | Default |
| `test_domain_matching`, `test_domain_entry_problem` | Exact entries, `*.` entries, a subdomain, a lookalike suffix, an `xn--` host; entries that are host names | Default |
| `test_allowlisted_egress_runs` | In a gateway, an egress call to a listed host runs without a hold, `call.sent` has `allowed_by` `domains`, and the upstream sees it | Default |
| `test_tricky_hosts_are_held` | Subdomain, userinfo, port, IP, IDNA, uppercase and the other forms, through a gateway: each held or run as section 3 says, with the reason | Default |
| `test_egress_rows` | Rows 4a to 4d, the path reason appended, no URL to check, a list argument, and an annotations class | Default |
| `test_deny_tier_in_the_rule_function` | Section 4's matching: config order, nested strings and keys, numbers as JSON text, no match on a missing argument, no normalization, any class, and `--no-holds` | Default |
| `test_deny_values_refuse_and_record` | A match is refused at once: `call.refused` with `rule` and the reason, no side file, no hold, the stderr line, and the client's line; nested strings, keys, numbers; no match on a missing argument; config order | Default |
| `test_example_config_loads` | `polarizer.example.toml`'s commented M2b keys, uncommented, load | Default |

### `tests/test_taint.py`

| Test | Claim | Suite |
|---|---|---|
| `test_open_world_taints_and_egress_is_held` | After an open-world call, the same egress call to a listed host is held by `tainted`, its reason naming the tool, the ts and the seq of the tainting call; before it, the call ran | Default |
| `test_taint_recorded_once` | A second open-world call writes no second `session.tainted` | Default |
| `test_taint_written_before_the_answer` | With the taint's fsync held, the client gets no answer; released, it does, and `session.tainted` precedes `call.returned` | Default |
| `test_taint_before_first_progress` | An open-world call that reports progress: `session.tainted` is written before the first progress reaches the client | Default |
| `test_taint_withheld_when_unrecordable` | The taint's append fails: the answer is withheld with section 7's line, and the next egress call is held by the taint kept in memory | Default |
| `test_every_answer_taints` | A tool error, a protocol error and a transport error each taint, before their `call.returned` | Default |
| `test_taint_reason_prefix` | In a tainted session, egress holds by rows 4, 4a and 4b keep their rule and start with the taint text; `stats.pattern_of` still reads a path pattern | Default |
| `test_what_does_not_taint` | A denied, refused, cancelled or `--no-holds` open-world call, and a local-read call, leave the session clean | Default |
| `test_taint_survives_restart_same_session` | A new gateway on the ledger with the same client session is tainted at start, with the stderr line; one with another client session, or none, is clean | Default |
| `test_taint_without_client_session` | With no client session, taint holds for the process; the start-up warning appears only with `domains` | Default |
| `test_client_session_from_environment` | `serve` records `CLAUDE_CODE_SESSION_ID` as `client_session` when it is well formed, null otherwise | Default |
| `test_two_serves_share_taint` | Two gateways with one client session on one ledger: one taints, the other holds its next egress call | Default |
| `test_holds_shows_taint` | `holds_tainted.txt`: the taint line on every hold of a tainted session, none on a clean session's | Default |
| `test_card_shows_taint` | The card's `Session taint` row, the block's line and the recommendation for `tainted` | Default |
| `test_stats_counts_taint` | `stats_tainted.txt`: the taint section, the pair line, no suggestion for `tainted` | Default |
| `test_planted_calls_with_taint` | With planting on in a tainted session: a deny-refused call is no trigger, a planted call the deny tier would refuse is dropped, no planted hold is egress or `tainted`, and real eligible calls still plant | Default |
| `test_taint_fixture_verifies` | `valid/taint` verifies intact in both verifiers | Default |

Also `tests/test_taint_check.py`, for the headless check script's files, its stand-in server and its refusals, as `tests/test_card_check.py` does for the card's.

Changed in the existing suite, and nothing else:
- `tests/test_policy_config.py::test_policy_loaded_fields` gains `client_session: null` in the data it pins, as section 8 says every `policy.loaded` from M2b carries it.
- `tests/test_readme.py`'s pinned limit `No scanning of tool descriptions, no rules on argument value` now pins `No scanning of tool descriptions. A call that no rule holds`: M2b made the rest of that limit false, and LIMITS.md says a limit leaves when the code changes it.

### Golden files

| Situation | Golden file | Exit |
|---|---|---|
| `holds` with a tainted session's two holds (one by `tainted`, one by `egress` with the prefix) and a clean session's hold | `holds_tainted.txt` | 0 |
| `stats` on the same kind of ledger | `stats_tainted.txt` | 0 |

The ledger is built by `tests/helpers/taintledger.py` from named pieces, one function per entry group, each with a docstring, as `holds_mixed.txt`'s is.

## 13. Build order, and the headless check

### Stage 12: argument rules and taint (medium)

1. **The spec** (this file), the milestones status line, and the stage notes started. Small.
2. **The tests** of section 12, written first, the golden files and the fixture. Medium.
3. **Code:** `config.py` (the keys and errors), `src/polarizer/argrules.py` (hosts, domains, values), `policy.py` (the rule function and the hash), `holds.py` (the taint fold and the block line), `proxy.py` (the deny tier, the client session, tainting), `cli.py` (the environment), `writer.py` (SECURITY_KINDS), `card.py`, `stats.py`, `planted.py`'s caller. Medium.
4. **The headless check** (below), and the docs. Small.

### The headless check

`scripts/taint_check.py`, manual only, on Linux or WSL with the repo's `.venv`, modelled on `scripts/card_check.py`: `claude -p` with the pinned model `claude-haiku-4-5-20251001`, `--max-budget-usd 1`, `--strict-mcp-config --mcp-config <file>`, and a ledger in a directory it creates under the repo's gitignored `.repro/`. No reference server has an open-world tool, so it uses `tests/helpers/web_stub.py`, a stdlib MCP server with a `fetch` that returns fixed text and a `post` that records what it was sent, neither touching the network. It shows, on real Claude Code:
1. a `post` to a listed host ran without a hold (`allowed_by` `domains`);
2. after a `fetch`, the same `post` was held by `tainted`, its reason naming `web__fetch`, the time and the seq;
3. that hold was decided through the card;
4. a second `claude -p --resume` of the same session (a new serve) holds the same `post` by `tainted` at once, and a new session runs it.

The interactive check, with Esc, is the owner's (docs/dev/MANUAL-CHECK.md, M2b).

## 14. Decided

The owner's design for M2b, given with the build brief on Nov 7, 2026 (UTC):
1. **Argument rules:** per tool, `domains` lists the hosts an egress tool may reach; an egress call whose URL arguments, named in `url_args` like `path_args`, all resolve to a listed host runs without a hold, and anything else is held as before. `deny_values` maps an argument name to regular expressions, and a match is refused at once (the deny tier), recorded with its reason. The matching is defined precisely (case-insensitive hosts, no wildcard but a leading `*.`, IP literals never matching a name, userinfo and ports handled, IDNA), and the config is checked at start with exact error lines.
2. **Grants:** an egress call allowed through a domain allowlist is a grant, the only one M2b adds.
3. **Taint:** a call to an `open-world` tool taints the session when its result returns. From then on every egress call is held, even to a listed domain, and grants are revoked. The hold's reason names the tool, the time and the ledger seq of the tainting call. Taint lasts for the rest of the session and survives a restart of serve within the same Claude Code session, folded from the ledger at start; a new session starts clean. There is no command to clear it.
4. **Ledger:** a new kind, `session.tainted`, appended and fsynced before the tainting call's result is returned to the agent; LEDGER-SPEC.md, both verifiers and a conformance fixture agree.
5. **What a person sees:** `holds` lists `session tainted by <tool> at <time> (seq N)`, the card shows it on every egress hold of a tainted session, and `stats` counts the holds taint caused. Planted calls are not issued for, or confused with, taint holds.
6. **Limits, stated plainly:** taint sees only calls routed through Polarizer, and only the tool's class, never a result's content.

## 15. Deviations and guesses

1. **A spec file of its own,** not a new section of HOLD-SPEC.md, for the reason at the top of this file. HOLD-SPEC.md, sections 1, 2 and 4 gain one-line pointers here.
2. **Nested argument names stay out of M2b.** HOLD-SPEC.md, section 16, decision 3 placed `edits[].path` in M2b; the owner's M2b design doesn't name it, and a path syntax for nested keys is a design of its own. It stays a stated limit (LIMITS.md, Paths), now for after M2b.
3. **`domains` and `url_args` go together, and only with class `egress`.** The design named them for egress tools; a domain list on another class would allow nothing that class doesn't already allow, and a list without arguments to check would check nothing.
4. **Polarizer parses URLs itself,** with section 3's table, and holds whatever it can't read the way every common parser would: a backslash, more than one `@`, a bracketed or numeric host, a percent sign or non-ASCII letters in the host, an empty port. The design asked for userinfo and ports to be handled; these rows are how.
5. **IDNA is handled by holding non-ASCII hosts,** and entries must be in their `xn--` form. Converting would need either Python's IDNA 2003 codec, which differs from the UTS 46 rules URL parsers use (it maps the German sharp s to "ss", where UTS 46 keeps it), or a new dependency.
6. **A call needs at least one URL** to be granted: an egress call whose `url_args` all hold empty lists is held.
7. **The client session is `CLAUDE_CODE_SESSION_ID`,** read at start. The design asked to work out from HOLD-SPEC.md, section 7 how a restarted serve knows it is the same session; section 7's lock files prove an ended process but not which session a new one belongs to, so this spec adds the client session beside them (section 7, Why this). It was observed headless with Claude Code 2.1.310 only.
8. **The taint line appears on every hold of a tainted session,** in `holds` and on the card, not only on egress holds as the design said for the card. The card's exact-call box is `holds`' block, and the fact is true of the session; showing it on every hold also keeps planted holds, which are never egress, from standing out.
9. **Taint is recorded at the first upstream content the client could see:** the first relayed progress notification, or the answer, whichever comes first, whatever the outcome. The design said "the moment its result returns"; a progress notification can carry the upstream's text before that.
10. **An answer is withheld when its taint can't be recorded,** with its own line, rather than returned untainted, and the process keeps the taint in memory. The session is not marked tainted before the record is written: a call evaluated meanwhile was made before the agent could see the answer.
11. **Only the first taint of a session is recorded.** Later open-world calls of a tainted session write nothing; the first is the source.
12. **Rule `tainted` only when taint is what held the call;** other egress holds of a tainted session keep their rule and gain the taint as a prefix of their reason, so `stats` counts only the holds taint caused.
13. **`deny_values` applies to any class,** searches every string at any depth of an argument (keys and JSON text of numbers included), and a missing argument never matches. The design said "argument name to regex list"; these are the choices that make a match precise.
14. **The deny tier comes after the class and before every hold,** and `--no-holds` skips it, as it skips every rule.
15. **The warning about a missing client session** appears only when some tool has `domains`, since only then does taint change a verdict.
16. **`call.refused` gains `rule`** for the deny tier only, so a reader can tell a value rule's refusal from M1a's and M2a's without parsing the reason.
17. **`stats` counts tainted sessions and holds by `tainted`** in a section of its own, shown only when a session is tainted, and never suggests relaxing `tainted`.
18. **The card's recommendation for `tainted` says deny,** with this spec's text; the design asked the card to show the taint, and the recommendation table is where the card says what a rule means.
19. **The stage is numbered 12,** its notes docs/dev/STAGE12-NOTES.md, and every exact line, rule id, test name, fixture and golden file name is this spec's choice.
20. **The headless check uses a stand-in server,** `tests/helpers/web_stub.py`, since no pinned reference server has an open-world tool, and shows restart survival with `claude -p --resume`, which starts a new serve in the same session.
