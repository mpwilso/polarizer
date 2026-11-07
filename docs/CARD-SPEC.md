# Card spec (M3)

This is the behavior contract for M3: the card, a local approval page for held calls. Holds, their endings and `polarizer allow` and `deny` are in HOLD-SPEC.md, the ledger format in LEDGER-SPEC.md, the command line in PROXY-SPEC.md, and time to decision in MEASURE-SPEC.md, section 10. Everything they say stays as written, except where this file changes it. The owner's answers to M3's open questions are in section 13, and every guess this file had to make is a numbered deviation in section 14.

## 1. What M3 is and is not

**In M3:**
- **`polarizer card`,** a separate process that serves one local web page on 127.0.0.1 and nothing else (section 2).
- **The page,** which lists every open hold as a card: what was called, why it was held, the exact arguments, the paths the call would touch, whether the tool's definition changed since its approval, the hold's history and a recommendation written by code (section 4).
- **Allow and Deny** on the page, recorded exactly as `polarizer allow` and `polarizer deny` record them, with one optional field added: a monotonic `elapsed_ms` (section 6).
- **Live updates:** new holds appear, and holds that end leave the queue with their ending shown (section 7).

**Not in M3:**
- live planted calls (stage 10, MEASURE-SPEC.md, section 11), built after M3; section 8 says what the card does once they exist;
- argument rules and taint (M2b), and scanning (M1b);
- any decision the card makes by itself: the recommendation is a line of text, and nothing is ever allowed or denied without a person's click;
- a notification outside the page (sound, desktop notification). `polarizer holds --wait --bell` stays the way to be told in a terminal.

**Why a separate process.** Esc in Claude Code ends `polarizer serve` (HOLD-SPEC.md, section 7), so a page served from inside it would vanish with the holds it showed (milestones.md, carry-forward notes). The card reads the ledger, side files and session locks, as `polarizer holds` does, and records decisions through the same code as `polarizer allow`. `serve` doesn't know the card exists.

**What the card protects, and what it does not.** It shows the same facts `polarizer holds` shows, plus four that a terminal listing lacks (paths, definition, history, recommendation), in a form a person who is not an engineer can read. It keeps every other local program, and every web page the browser opens, from reading or deciding through it, as section 3 says. It cannot make anyone read, and it cannot stop a program that runs as the same user from reading its memory or the person's screen.

## 2. The process

### Syntax

```
polarizer card (--config <absolute path> | --ledger-dir <absolute path>) [--port <n>] [--allow-no-terminal]
```

- **`--config` or `--ledger-dir`:** exactly one, absolute, with the shared usage lines (HOLD-SPEC.md, section 8). `--config` is read with `require_env=False`, roots are not checked, and no upstream is started; the config's policy is used only to show the paths a call would touch (section 4). With `--ledger-dir` the paths are not shown, and the page says why.
- **`--port <n>`:** a whole number from 1 to 65535. Without it, the operating system chooses a free port. Anything else is `polarizer: --port must be a whole number from 1 to 65535`, exit 2.
- **A terminal.** `card` refuses when stdin is not a terminal, unless `--allow-no-terminal` is given: `polarizer: card needs a terminal: a person decides holds and approvals`, exit 2, as `allow` and `deny` do (HOLD-SPEC.md, section 8), and for the same reason: an agent that starts the card itself would read the token it prints. The line doesn't name the flag. Every decision taken through a card started with the flag has `terminal: false`, as `allow --allow-no-terminal` records.
- **`--no-holds`** is refused with the shared line, `polarizer: --no-holds goes with serve`.

### Start

In this order:
1. the usage checks, then the terminal check;
2. the config, if given (a config error is its line, exit 2);
3. the location, as `allow` checks it: a forbidden `ledger_dir` is refused (exit 2), and a git working tree gets the warning on stderr;
4. **no ledger** (no `ledger.jsonl`, or an empty one): `polarizer: no ledger at <dir>`, exit 2. The card never creates a ledger;
5. it listens on 127.0.0.1 and nowhere else, at `--port` or at a free port the operating system gives. A failure is `polarizer: cannot listen on 127.0.0.1:<port>: <the operating system's message>`, exit 2;
6. it writes **one line** to stdout, flushed: `open http://127.0.0.1:<port>/?t=<token>`, where the token is 32 random bytes from `secrets`, as 64 lowercase hex characters, new for every start;
7. it serves until Ctrl+C or SIGTERM, then exits 0 with nothing more on stdout or stderr.

A ledger that is not intact does not stop the card: the page says so, shows no holds and takes no decision (section 7), and recovers when the ledger does (a repair, for a torn tail).

**Nothing else is printed.** The card writes no log of requests: the default HTTP server log would print each request line, the first of which carries the token, to stderr. A decision is printed only on the page and recorded only in the ledger.

## 3. Who can use the page

The card's port is open to every program on the machine, and every web page the browser loads can send requests to it. Each check below closes one of those doors; all of them run on every request, in this order, before anything is read or written.

1. **Bound to loopback.** The socket is bound to 127.0.0.1 only. On Windows it is bound with `SO_EXCLUSIVEADDRUSE`, so no other program can bind the same port, and on every platform without `SO_REUSEADDR`.
2. **The Host header** must be exactly `127.0.0.1:<port>` or `localhost:<port>`. Anything else, or none, is 403. This defeats DNS rebinding, where a page on another site resolves its own name to 127.0.0.1 and then reads the answers as its own.
3. **The token.** `GET /` must carry `?t=<token>` (and no other query field), compared with `hmac.compare_digest`. The page is never served without it, so a reload keeps working only because the address bar keeps the token.
4. **The cookie.** The page's response sets `polarizer_card_<port>=<cookie>; Path=/; HttpOnly; SameSite=Strict`, where `<cookie>` is a second random value of 32 bytes, made at start (deviation 1). Every request other than `GET /` must carry it, compared with `hmac.compare_digest`. `HttpOnly` keeps it from the page's own script; `SameSite=Strict` keeps the browser from sending it with a request that another site started.
5. **The nonce.** Each page load gets a fresh nonce of 32 random bytes, in a `<meta name="polarizer-nonce">` tag of that page. `GET /state` and `POST /decide` must carry it in the `X-Polarizer-Nonce` header, compared with `hmac.compare_digest` against the 16 nonces most recently issued. Another site can't read the page, so it can't learn a nonce; and a custom header makes the browser ask before sending a request from another origin, which the card refuses (OPTIONS is 403).
6. **POST /decide** also needs `Content-Type: application/json` and an `Origin` header of exactly `http://127.0.0.1:<port>` or `http://localhost:<port>`. Browsers send `Origin` with every POST.

Every refusal by these checks is `403` with the body `forbidden`, and the same headers as any answer. Only `GET` and `POST` are served; every other method is 403.

**The requests:**

| Request | Needs | Answer |
|---|---|---|
| `GET /?t=<token>` | Host, token | 200, the page, with the cookie set |
| `GET /card.js`, `GET /card.css` | Host, cookie | 200, the file from package data |
| `GET /state` | Host, cookie, nonce | 200, the state as JSON (section 5) |
| `POST /decide` | Host, cookie, nonce, Origin, JSON | 200 for a decision recorded, 409 for one refused, 400 for a malformed body, 413 for a body over 16 KiB (section 6) |
| anything else | Host, cookie | 404; without the cookie, 403 |

**Headers on every answer,** 403 included:
- `Content-Security-Policy: default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, so the page runs only the script and style served with it, and no other page can frame it;
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` and `X-Frame-Options: DENY`.

**What stays open,** and is stated in LIMITS.md:
- **A program running as the same user** can read the card's memory, the person's screen or the terminal that shows the token, as it could read anything else the person can. That is out of scope, as for every Polarizer process.
- **The token is in the browser's history,** in the address the person opened, and in the terminal's scrollback. It is good for one card process only.
- **Another local web server** on 127.0.0.1 receives the card's cookie when the browser visits it, because browsers send a host's cookies to every port of that host (RFC 6265, section 8.5). That is why the cookie is not the token and grants nothing alone: without a page's nonce it opens nothing but the two static files, and a nonce is only on a page served for the token.

## 4. What a card shows

The page lists every open hold, in seq order, as `polarizer holds` does, one card each. Every text below comes from the ledger, the side file, the session lock, the pins, or the config's policy, and every string from the ledger or a side file passes through `printable()` or the holds renderer, so a hidden or look-alike character shows as an escape. The server builds every line; the page's script places them with `textContent` and never parses them as HTML.

**The heading** is the tool's exposed name and the hold id.

**The rows,** each a plain label and its lines:

| Label | Lines |
|---|---|
| `Tool` | `<tool>, class <class>`, `<tool>, class <class> (from annotations)`, or `<tool>, no class: every call of it is held` |
| `Why it was held` | `<rule>: <reason>` |
| `What it would touch` | section 4, Paths |
| `Its definition` | section 4, Definition |
| `Earlier holds` | section 4, History |
| `Waiting` | `about <age>; times out after <timeout> s`, the age as `holds` prints it |
| `Session` | `<session> started <ts>, <state>`, with the state as `holds` prints it |

**The exact call:** the hold's whole block, line for line as `polarizer holds` prints it (`holds.block`, which calls `holds.render_block`), in a monospace box under the label `The exact call, as polarizer holds prints it`. A side file that fails its check shows the same problem line `holds` shows.

**The recommendation** (section 4, Recommendation), in a box above the rows, labelled as written by Polarizer's code.

**Notes,** each shown only when it applies:
- **Background,** once the hold's age, by the wall clock as `holds` measures it, is 120 s or more: `This call has waited over 2 minutes: probably moved to the background by Claude Code; the agent may have moved on.` Claude Code 2.1.289 did that at about 123 s (HOLD-SPEC.md, section 9); the 120 s is a margin under it, and the 123 s may change between versions.
- **Cannot be allowed,** when the side file fails its check: `This hold cannot be allowed: side file args/<args_commit>.bin <problem>.` The Allow button is disabled; Deny still works, as for `deny`.
- **Session,** when the session lock reads ended: `The session that held this call has ended; no process will act on a decision.` When it reads unknown: `Polarizer cannot tell whether the session that held this call is running.` Both decisions still work, as on the command line.

### Paths

What the call would touch, for a tool whose policy entry names `path_args`, judged as serve judges them (HOLD-SPEC.md, section 3):
- **With `--ledger-dir`:** one line, `not shown: start the card with --config to see the paths`.
- **A tool with no `path_args`** in the config (no entry, a class from annotations, or an entry with an empty list): `this tool has no path arguments in polarizer.toml, so the card cannot say`.
- **A side file that fails its check:** `not shown: the side file <problem>`.
- **Otherwise,** one line per path, in the order of `path_args` and of each list:
  - `"<arg>": <resolved>` when the resolved path is the given text, else `"<arg>": <resolved> (given as <value>)`;
  - `"<arg>": <value> cannot be resolved: <why>`, with the reason `paths.resolve_bounded` gives;
  - `"<arg>": missing`, or `"<arg>": not a string or a list of strings`;
  - after 32 lines, `and <n> more`;
  - and last, `Resolved by the card when it first showed this hold.`

The paths are resolved once per hold, the first time the card shows it, with serve's resolver and its 2 s bound, and kept for the card's life. They are what the paths are now, not when the call arrived: a symbolic link changed in between shows here and not in the hold's reason, and an upstream that sees another file system resolves its own way (HOLD-SPEC.md, section 3).

### Definition

From the pins, for the exposed name split at its first `__` into prefix and tool (a prefix never holds `__`):
- `unchanged since it was approved at seq <q>`: the latest decision approves, and no drift follows it;
- `changed since it was approved at seq <q>: serve hides the tool now, so an allow will be refused`: a `tool.drift` follows the approval;
- `rejected at seq <q>: an allow will be refused`;
- `no approval for this tool in this ledger`.

### History

Every earlier hold, by seq, with the same exposed tool name and rule: `earlier holds of this tool by <rule>: <a> allowed, <d> denied`, counting their endings; or, with no earlier hold of that tool and rule, `none: this is the first hold of this tool by <rule>`. Expired, abandoned and open earlier holds are not counted in either number.

### Recommendation

Written by `card.recommend(rule, reason)`, a function of the hold's rule and reason only, never of a model or of the arguments' meaning. It says deny in three cases, and otherwise nothing:

| When | Text |
|---|---|
| rule `write-pattern`, or `destructive`, with a built-in pattern named after the reason's last ` matches ` | `Deny, unless you asked for exactly this: the path matches <pattern>, a place Polarizer always holds, because a write there can run code or change who has access.` |
| rule `read-pattern`, or `egress`, with a built-in pattern named the same way | `Deny, unless you asked for exactly this: the path matches <pattern>, a place Polarizer always holds, because it can hold secrets.` |
| rule `polarizer-files` | `Deny: the call would touch Polarizer's own ledger or config, which decide and record what the agent may do.` |
| rule `path-missing`, `path-not-string` or `path-unresolvable` | `Deny, unless you know why: Polarizer could not check the path this call names (<rule>), so it cannot tell where the call would write or read.` |
| anything else | `No recommendation: the rule that held this call does not mark it as dangerous by itself. Read the call and decide.` |

- The built-in patterns are `paths.BUILTIN_WRITE` and `paths.BUILTIN_READ`, read from the reason exactly as `stats` reads them (`stats.pattern_of` and `stats.is_builtin`; MEASURE-SPEC.md, section 10). A pattern the config added gets no recommendation: the person chose to hold it, and can judge it.
- These are the rules `stats` never suggests relaxing, for the same reason: they fail closed by design.
- The recommendation is never acted on. It is conservative: when it is wrong, it says deny to a call the person meant, and the person can still allow.

## 5. The state

`GET /state` answers one JSON object, written with sorted keys and `ensure_ascii`, so every byte is ASCII:

```json
{
  "status": "intact",
  "problem": [],
  "open": [<card>, ...],
  "ended": [<ended>, ...]
}
```

- **`status`** is `intact`, or the ledger's status as `verify` names it (`tampered`, `invalid`, `torn tail`, `truncated`), or `locked`, `unreadable` or `no ledger`.
- **`problem`** is empty when intact; otherwise `verify`'s output lines for that status, `LOCKED_LINE`, `polarizer: cannot read <path>: <message>`, or `no ledger at <dir>`. With a problem, `open` and `ended` are empty.
- **A card** is `{"hold", "tool", "rows": [[label, [lines]], ...], "recommendation": {"decision": "deny" or null, "text"}, "notes": [lines], "block": [lines], "can_allow": true or false}`, from section 4.
- **An ended hold** is `{"hold", "tool", "text"}` (section 7).

**Reading.** The card reads the ledger as `holds` does (`read_files` under the lock, then `verify_bytes`, folding holds and pins), and reads it again only when the ledger file's size or modification time changed since the last read. Session states, ages and the notes are worked out again for every request, so a session that ends or a hold that crosses 120 s shows on the next poll without a ledger change.

## 6. Deciding

`POST /decide` with the body `{"hold": "<16 hex>", "decision": "allow" or "deny", "reason": "<text>" or null}`:
1. **The body** must be JSON, an object with exactly those keys (`reason` may be left out), `hold` 16 lowercase hex characters, `decision` one of the two, and `reason` a string or null, and null for an allow. Otherwise 400, `{"ok": false, "error": "polarizer: the request is not a decision"}`.
2. **The card must have shown the hold** in an answer to `GET /state`; otherwise 409, `polarizer: this card has not shown hold <id>; reload the page`. A decision always has a time measured on this card (below), and a person can only decide a call that was on the page.
3. **The time** is taken now, on the monotonic clock: `elapsed_ms` is the whole milliseconds, rounded down, from the moment the card first put this hold in a `GET /state` answer to this request.
4. **The decision** goes through `decisions.HoldDecider`, exactly as `polarizer allow` and `deny` (HOLD-SPEC.md, section 8, steps 3 to 7): the ledger opened as a writer, with the 2 s lock wait and full verification; the hold and its side file checked; `hold.decided` appended through the conditional append, so a hold decided, expired or abandoned meanwhile is refused with `allow`'s own line; fsynced with `ledger.head` updated before the card answers; the block printed after; the session probed for its warning. A deny's reason is folded as `deny --reason` folds it, and a reason that is empty after folding is null. One decision runs at a time in a card.
5. **The answer:** 200 with `{"ok": true, "lines": [<the block>, "allowed hold <id> at seq <q>"], "warnings": [<section 7's warning, if any>]}`, the lines `allow` or `deny` would print. A refusal is 409 with `{"ok": false, "error": "<the refusal's line>"}`, and nothing is written.

**What `hold.decided` records:** exactly what `allow` and `deny` record, `hold`, `args_commit`, `decision`, `actor` `"person"`, `reason`, and `terminal: false` for a card started without a terminal, plus `elapsed_ms`, an integer. No other field. `serve` reads none of `hold.decided`'s fields besides `hold`, `args_commit` and `decision`, so it acts on a card's decision exactly as on `allow`'s.

**In `polarizer stats`,** a decision with `elapsed_ms` has that time, `(elapsed_ms + 500) // 1000` seconds, in place of the wall-clock difference, and the note under `all sessions` counts it (MEASURE-SPEC.md, section 10, and section 17, Stage 9 build, 5). That is already built; M3 adds the end-to-end test (section 11).

**What `elapsed_ms` measures:** the time from the card showing the hold to the click, in one process on one clock. It leaves out the time before the person opened the page, and it counts the page as showing a hold once the card has sent it, which is within one poll (1.5 s) of its arrival in the ledger; a page that was hidden in a background tab counts as showing.

## 7. Live updates and endings

**Polling.** The page asks `GET /state` every 1.5 s, and never uses a websocket. A new open hold is added at the end of the queue, an updated one changes in place (so a deny reason being typed is kept), and a hold that is no longer open leaves the queue.

**Endings shown.** Below the queue, under `Ended while this card was open`, the card lists the holds that were open at some read or were created after the card's first read and have since ended, newest ending first, at most 20, each as `<tool>, hold <id>: <text>`:

| Ending | Text |
|---|---|
| `hold.decided` allow | `allowed at seq <q>` |
| `hold.decided` deny | `denied at seq <q>`, then `: <reason>` when it has one |
| `hold.expired`, timeout | `expired at seq <q>: timeout after <n> s` |
| `hold.expired`, the client's cancel | `cancelled at seq <q>: the client cancelled the call` |
| `hold.expired`, shutdown | `ended at seq <q>: polarizer shut down while the call was held` |
| `hold.expired`, any other reason | `expired at seq <q>: <reason>` |
| `hold.abandoned` | `abandoned at seq <q>: its session ended before the call was decided` |

**The last decision.** After a click, the page shows the answer's lines and warnings, or the refusal's line, in a box at the top, until the next click.

**A stale page.** A 403 from `GET /state` (the card restarted, or the page's nonce is no longer one of the 16 kept) stops the polling and says: `This page is out of date or the card has stopped. Open the link polarizer card printed again.`

**A ledger that is not intact** replaces the queue with the problem lines and `Run polarizer verify.`; the polling goes on, so the queue returns once the ledger is intact again.

## 8. Planted calls (stage 10)

Live planted calls were built after M3, as stage 10 (MEASURE-SPEC.md, section 11, and section 17, Stage 10 build). The card is held to the same rule as `holds`: a planted hold is shown exactly as a real one until it is decided. Everything the card shows of an open hold comes from fields a planted hold has too (HOLD-SPEC.md, section 12), and the recommendation is a function of rule and reason, which serve computes the same way for both. Stage 10 adds:
- after a decision on a hold with `plant_commit`, the card waits up to 2 s for its `hold.revealed`, as `allow` and `deny` do, for real and planted holds alike, and adds the reveal's lines to the answer (`reveal`);
- an ended hold revealed as planted gets its reveal lines in the ended list;
- holds revealed as planted are left out of every card's history.

## 9. The page

- **Files:** `index.html`, `card.js` and `card.css`, in `src/polarizer/card_page/`, shipped as package data and read with `importlib.resources`. No external asset, no font, no build step. `index.html` has two placeholders the card fills: the nonce, and the ledger directory through `printable()` and HTML escaping.
- **Script:** one file of plain JavaScript, loaded with `<script src="/card.js" defer>`. No inline script, no inline style, no `on...=` attribute, no `eval`, no `innerHTML` with server text; events are bound with `addEventListener`. All of it works under section 3's Content-Security-Policy.
- **Words:** plain labels (section 4), the buttons `Allow` and `Deny`, a box `Reason for a deny (optional)`, the heading `Held calls`, and `Nothing is held right now.` for an empty queue. The page names the ledger directory it watches.
- **Layout:** one column, readable at 80 characters of text, the exact call in a monospace box that wraps no line and scrolls sideways, the recommendation in a box above the rows, and the buttons apart from each other.

## 10. Limits

Added to LIMITS.md when M3 ships:
- The card shows what the ledger says. A call the rules let through never appears on it.
- Its paths are resolved when it first shows a hold, not when the call arrived (section 4).
- Its recommendation follows the rule alone; "No recommendation" is not a statement that the call is safe.
- A program running as the same user can read the token, the cookie and the page (section 3).
- The background note is a guess from one observed version of Claude Code (section 4).
- `elapsed_ms` starts when the card first sends a hold to a page, not when a person looks at it (section 6).
- The card is a page, so a person must keep it open; nothing calls them to it.

## 11. Tests

"Default" means the test runs in `scripts/test.sh` and CI on every platform. "Browser" means it needs a headless Chromium (`tests/helpers/chromium.py`) and is skipped without one, and on Windows.

### `tests/test_card.py`

| Test | Claim | Suite |
|---|---|---|
| `test_open_line_and_loopback_only` | `polarizer card` as a subprocess prints exactly one line, `open http://127.0.0.1:<port>/?t=<64 hex>`; the socket's address is 127.0.0.1; `--port` is honored; Ctrl+C or SIGTERM ends it with exit 0 and nothing on stderr | Default |
| `test_token_required` | `GET /` without `t`, with a wrong one, with an extra query field, or with the right one in another case: 403 `forbidden`; with the right one: 200, the cookie with `HttpOnly`, `SameSite=Strict` and `Path=/`, and the nonce in the page | Default |
| `test_cookie_is_not_the_token` | The cookie's value differs from the token, and the token alone (as a cookie) opens nothing but `GET /` | Default |
| `test_secrets_compared_in_constant_time` | The token, cookie and nonce checks go through `hmac.compare_digest` (counted with a wrapper) | Default |
| `test_host_header_checked` | Host `evil.example:<port>`, `127.0.0.1:<other port>`, `127.0.0.1` alone, or none: 403; `localhost:<port>`: served | Default |
| `test_state_needs_cookie_and_nonce` | `GET /state` without the cookie, without the nonce, or with a nonce not issued: 403; with both: 200 JSON; the 17th-oldest nonce is no longer accepted | Default |
| `test_decide_needs_post_origin_and_json` | A decision by GET, without `Origin`, with another `Origin`, with a form content type, without the nonce: 403, nothing written; OPTIONS and PUT: 403 | Default |
| `test_headers_on_every_answer` | 200, 403, 404 and 409 answers all carry the Content-Security-Policy and the four other headers of section 3 | Default |
| `test_no_request_log` | After requests with the token, stderr is empty | Default |
| `test_card_state_golden` | The state for `holds_mixed`'s ledger plus a decided and an expired hold, with the clock injected, equals `card_state_mixed.json` byte for byte | Default |
| `test_card_rows` | Each row's text for each case of section 4: classes, definition states, history counts, every recommendation row, session notes | Default |
| `test_paths_with_config` | With `--config`, a write under `.git/hooks` through a symbolic link shows the resolved path and `(given as ...)`; a missing argument, a non-string, an unresolvable path; a tool with no `path_args`; with `--ledger-dir`, the `not shown` line | Default, Symlinks for the link case |
| `test_recommendation_rule` | Every row of section 4's recommendation table, a configured pattern giving none, and a reason with ` matches ` inside its path reading the last one | Default |
| `test_background_note_at_120_s` | At 119 s no note; at 120 s the note, exactly | Default |
| `test_allow_through_card_matches_the_cli` | On two copies of one ledger, `polarizer allow` and the card's allow write `hold.decided` entries whose `data` differs only by `elapsed_ms`; the same for deny with a reason, and for a card started with `--allow-no-terminal` against `allow --allow-no-terminal` | Default |
| `test_card_refusals_are_the_cli_lines` | Already decided, expired, abandoned, a side file altered, and a ledger not intact: 409 with exactly `allow`'s stderr line, nothing written | Default |
| `test_not_shown_is_refused` | A decision for a hold the card never put in a state answer: 409, nothing written | Default |
| `test_elapsed_ms_is_monotonic` | With an injected monotonic clock, shown at 10.0 s and decided at 14.2379 s: `elapsed_ms` 4237 | Default |
| `test_decision_is_fsynced_before_the_answer` | The answer comes after the ledger's fsync and `ledger.head`'s update (counted `FileOps`) | Default |
| `test_card_and_cli_race` | The card and `polarizer allow` deciding one hold at once: exactly one `hold.decided`, the other refused | Default |
| `test_endings_leave_the_queue` | A hold that expires, is cancelled, is abandoned or is decided elsewhere leaves `open` and appears in `ended` with section 7's text, newest first, at most 20 | Default |
| `test_state_when_not_intact` | Tampered, torn tail and locked ledgers give their status and problem lines and no holds; after a repair, the holds return | Default |
| `test_stats_uses_the_card_time` | After a decision through the card, `polarizer stats` prints the time from `elapsed_ms` and `1 of 1 decision were timed on one clock, from the card that showed the hold.` | Default |
| `test_page_obeys_the_policy` | `index.html` has no inline script or style, no `on...=` attribute and no external URL; `card.js` has no `innerHTML`, `eval` or `Function(`; all three files are ASCII | Default |
| `test_card_usage` | Usage errors and refusals: `card_usage.txt` (stderr, one line per case, exit 2) | Default |
| `test_card_refuses_forbidden_and_missing_ledgers` | A forbidden `ledger_dir`: exit 2 and the line; no ledger: `polarizer: no ledger at <dir>`; nothing created | Default |
| `test_card_page_in_a_browser` | A headless Chromium loads the page from the open line, shows the hold's block and recommendation, and a click on Deny records the deny with `elapsed_ms` | Browser |

Added while building (section 14, deviation 15): `test_card_with_config` (the card from `--config` as a subprocess), `test_paths_through_a_link` (the symbolic-link case of `test_paths_with_config`, skipped where `os.symlink` raises `OSError`), `test_rejected_definition_and_history_counts`, `test_malformed_decisions`, `test_page_names_the_ledger_and_escapes_it`, and `tests/test_card_check.py` for the headless check script's files and refusals.

### Golden files

| Situation | Golden file | Exit |
|---|---|---|
| The state for the mixed fixture (section 11, `test_card_state_golden`) | `card_state_mixed.json` | none |
| Usage errors and the terminal refusal | `card_usage.txt` | 2 |

### Fixtures

The state fixture is `tests/helpers/holdledger.py`'s `mixed` ledger, with two pieces added in `tests/helpers/cardledger.py`, each a function with a docstring saying what it shows, as `holds_mixed.txt`'s are: an earlier allowed hold of `fs__write_file` by `write-pattern` (history), and an approval of the `fs` tools so the definition row reads approved.

## 12. Build order and size

### Stage 11: the card (large)

1. **The spec** (this file), the milestones status line, and the stage notes started. Small.
2. **The tests** of section 11, written first, and the golden files. Medium.
3. **`src/polarizer/card.py`** (the server, the checks, the state, the rows, the recommendation, deciding), the page in `src/polarizer/card_page/`, `card` in `cli.py`, and `elapsed_ms` in `decisions.HoldDecider.decide`. Medium.
4. **Docs:** LIMITS.md, EVIDENCE.md, README.md, CHANGELOG.md, LEDGER-SPEC.md's `hold.decided` row, HOLD-SPEC.md's pointers, docs/dev/README.md, docs/dev/STAGE11-NOTES.md, and a headless check on real Claude Code recorded in verified-facts.md. Small.

**Stop for review,** then the owner's interactive check of the card.

## 13. Decided

The owner's answers to M3's open questions, given with the build brief on Nov 6, 2026 (UTC):
1. **Form:** a local web page from a separate process, `polarizer card`, on 127.0.0.1 only, a random port unless given, printing one `open` line with a 32-byte hex token; the token on every request (the query first, then an HttpOnly, SameSite=Strict cookie), compared in constant time, 403 without it; the Host header checked against DNS rebinding; POST with the token and a per-page nonce for allow and deny; the standard library only.
2. **What a card shows:** the hold id, tool and class, rule and reason, the exact arguments through `printable()` and the holds renderer, the resolved paths, whether the definition changed since approval, the history of this tool and rule, and a recommendation written by code, never a model, conservative, and never acted on.
3. **Deciding:** exactly the path of `allow` and `deny`, so `hold.decided` is identical except for an optional `elapsed_ms`, measured on the monotonic clock from the card first showing the hold to the click; `stats` prefers it (MEASURE-SPEC.md, section 16, question 5).
4. **The background note** past about 120 s (HOLD-SPEC.md, section 9's open question): yes.
5. **Live updates:** a JSON endpoint polled every 1 to 2 s, no websockets; expired, cancelled and abandoned holds leave the queue with their ending shown.
6. **The page:** plain HTML and a little plain JavaScript from package data, no external assets, nothing a strict Content-Security-Policy (`default-src 'self'`) forbids, no build step, readable by a person who is not an engineer.
7. **Checks:** headless checks on real Claude Code, scripted; the interactive checks stay the owner's.

## 14. Deviations and guesses

1. **The cookie is a second random value, not the token.** The brief said the token is sent in the query and then in a cookie. Browsers send a host's cookies to every port of it (RFC 6265, section 8.5), so a cookie holding the token would hand the token to any other server on 127.0.0.1 the browser visits, an agent's development server among them. The cookie holds a separate value, and grants nothing alone; the token is still needed to load a page, and each page's nonce to read or decide.
2. **The page always needs the token in its query,** a reload included, rather than the cookie for later loads, for the same reason: a leaked cookie must not open a page and its nonce.
3. **The cookie's name carries the port** (`polarizer_card_<port>`), since two cards on two ports would otherwise overwrite each other's cookie.
4. **`GET /state` needs the nonce too,** not only the decision. The state holds the arguments of every open hold, which another local server holding the cookie must not read.
5. **`POST /decide` needs an `Origin` of the card's own,** besides the nonce and the JSON content type; browsers always send it with a POST.
6. **`card` needs a terminal,** with `--allow-no-terminal` as for `allow` and `deny`, and a card started with that flag records `terminal: false` on each decision. The brief didn't say; an agent that could start the card without one would read the token it prints.
7. **`elapsed_ms` starts when the card first sends a hold to a page,** in a `GET /state` answer, not when a browser draws it: the server can't see the drawing, and a time the page reported would be the page's word. The difference is one request's travel on loopback.
8. **A decision for a hold the card never sent is refused,** so every card decision has its `elapsed_ms`.
9. **The recommendation also says deny for a destructive or egress call whose reason names a built-in pattern,** read from the reason as `stats` reads it; the brief listed built-in pattern rules, fail-closed path rules and `polarizer-files`. A destructive move into `.git/hooks` is held by rule `destructive`, and the same caution applies.
10. **The paths are resolved by the card, once, when it first shows the hold,** with serve's resolver and bound, because no entry records the resolved paths; the line says so.
11. **The ended list** shows holds open at some read or created after the card's first read, at most 20; holds that ended before the card started are not listed.
12. **One decision at a time** per card, and the HTTP answer codes (200, 400, 403, 404, 409, 413) and the JSON shapes are this spec's choice, as are every exact line, file name and test name.
13. **The card prints nothing after its open line,** not even decisions: the brief asked for one line, and a request log would print the token.
14. **The stage is numbered 11,** and its notes are docs/dev/STAGE11-NOTES.md: stage 10, live planted calls, is named in MEASURE-SPEC.md and comes after M3, so its notes keep the name STAGE10-NOTES.md.
15. **Extra tests** beyond section 11's table, listed under it, and a committed headless check script, `scripts/card_check.py`, which section 12 asked for as "a headless check" without naming one. It drives the card with curl only, as the brief asked.
