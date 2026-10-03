# Pin spec (M1a)

This is the behavior contract for M1a: tool definition pins. It replaces the M1a appendix of m0-plan.md, and where the two differ, this file wins. The ledger format is in LEDGER-SPEC.md, `serve` and the command line are in PROXY-SPEC.md, and the facts this relies on are in verified-facts.md. Everything M0 does stays as PROXY-SPEC.md says, except where this file changes it. Each change is listed under Deviations and guesses at the end.

## 1. What M1a is and is not

**In M1a:**
- **The definition hash.** Every upstream tool definition is hashed in the form Polarizer would serve it (section 2).
- **Pin and hide on drift.** A tool is exposed only while its latest decision is an approval of the hash it has now. Any other state hides it, and a change after approval is recorded.
- **Serving the stored copy.** An exposed tool is served from the approved copy kept on disk, never from the live list.
- **Approve and pin as one recorded act.** `polarizer approve` writes `tool.approved`, fsynced, and that entry is what exposes the tool. There is no other way to expose one, apart from the `--no-pins` flag (section 7).
- **Failing closed on a bad ledger.** Pin state comes only from the ledger, so `serve` exposes nothing unless the ledger verifies as `intact`.

**Not in M1a:**
- scan rules for descriptions, the review UI with diffs, and group approval of unflagged tools (M1b);
- tool `_meta` and upstream `instructions` handling (M1b): `_meta` is never served in M1a, and instructions are never passed on, as in M0;
- policy and holds on calls (M2a), taint (M2b), and the card (M3).

**What pinning protects.** It pins what the model is shown: the name, description, schemas, annotations and the other fields of a tool definition. It does not pin what the upstream does. A server can change what a tool does without changing its definition, and M1a cannot see that. A pin says "the agent is still being shown what a person approved", not "the tool still behaves as it did".

## 2. The definition hash

### The hashed form

For each tool an upstream lists:

1. **Parse.** Take the `mcp_types.Tool` that Polarizer's SDK client already parsed from the upstream's `tools/list`, under the upstream's own name (no prefix).
2. **Dump.** `mono = tool.model_dump(by_alias=True, mode="json", exclude_none=True)`.
3. **Serve it as the SDK would on a 2026-07-28 connection.**

       form = mcp_types.methods.serialize_server_result(
           "tools/list", "2026-07-28",
           {"tools": [mono], "resultType": "complete", "ttlMs": 0, "cacheScope": "private"},
       )["tools"][0]

   The three envelope values are required by the 2026-07-28 result model and don't touch the tool. `mcp_types.methods` is documented in the SDK as supported public API.
4. **Canonical bytes.** `canon = rfc8785.dumps(form)`: full RFC 8785, not the ledger's subset, because schemas have arbitrary keys and floats.
5. **Hash.** `def_hash = hex(sha256(b"POLARIZER-TOOLDEF/1\n" + canon))`, 64 lowercase hex characters.

The stored copy (section 5) holds `canon` exactly, so rehashing a stored copy is step 5 on its bytes.

### Why the 2026-07-28 served form

Stage 3 found that `execution` is missing from tool definitions on 2026-07-28 connections. M1a's spec round (verified-facts.md, M1a spec round) checked where era differences arise in mcp 2.2.0:

- **On Polarizer's upstream side,** the SDK client parses into one version-free `Tool`. An upstream on 2025-11-25 sends `execution` and the `Tool` keeps it. An SDK upstream on 2026-07-28 never sends it. So the plain dump of one tool differs by the upstream's era.
- **On Polarizer's client side,** the SDK serializes through a per-era model. The only `Tool` field that the 2025-11-25 model has and the 2026-07-28 model lacks is `execution`. Every handshake version (2024-11-05 to 2025-11-25) uses the 2025-11-25 model.

Hashing the 2026-07-28 served form removes `execution` whatever the upstream's era, so one upstream tool gives one hash over both eras. It is also exactly what Claude Code is sent: Claude Code negotiates 2026-07-28 with an SDK 2.2 server (verified-facts.md, headless). A field that form lacks never reaches Claude Code, so leaving it out of the hash can't hide a change Claude Code would see.

The same step also drops a null at the top level of `inputSchema` or `outputSchema`, which the SDK drops when serving in both eras. Without it, a change the client can never see would count as drift.

**What this does to what the model sees.** Nothing changes for a 2026-07-28 client, which never got `execution`. An older-era client no longer gets `execution` through Polarizer, because the stored copy has none (section 5). Polarizer doesn't proxy tasks in any era, so `execution` advertised a capability Polarizer can't honor. PROXY-SPEC.md, Results item 4, is updated to say so.

**"Only the names change" no longer holds literally.** M0 promised that a tool reaches the client as the upstream defined it, apart from the prefix on its name and the SDK changes listed in PROXY-SPEC.md, Results. From M1a, no client in any era is served `execution`, and none is served a tool's `_meta` (section 5). Polarizer's own code makes this change, not the SDK. It is a stated limit of M1a, listed with the others in PROXY-SPEC.md, Results. Under `--no-pins`, definitions are served as M0 serves them.

### Edge cases, pinned by tests

- **Unknown fields** at the `Tool` level, and inside `annotations`, `icons` and `execution`, are dropped by the SDK's parse (`extra="ignore"` in the per-era models, the pydantic default in `Tool`), so they are in neither the hash nor the stored copy. Inside `inputSchema` and `outputSchema`, which are free-form JSON Schema, every key is kept, served and hashed. An unknown keyword there changes the hash.
- **Null versus absent:**
  - A null top-level `Tool` field is the same as an absent one (`exclude_none`).
  - So is a null at the top level of `inputSchema` or `outputSchema` (step 3).
  - A null deeper inside a schema, such as `"default": null` on a property, is kept, served in both eras, and hashed. It differs from absent, as it does for the client.
  - `"annotations": {"title": null}` becomes `"annotations": {}`, which is served as `{}` and differs from no `annotations` at all.
- **Key order** never matters: RFC 8785 sorts keys.
- **`_meta`** is part of the hash, as the m0-plan appendix decided, but is never served in M1a. A change to a tool's `_meta` alone therefore hides it until approved. M1b's `_meta` allowlist revisits this.
- **snake_case `input_schema` on the wire.** The SDK client validates results by alias only (`by_name=False`), so a tool sent with `input_schema` instead of `inputSchema` fails the upstream's whole `tools/list`. At connect, that upstream doesn't connect, as in M0. On a later refresh it is a failed refresh (section 6). This corrects the m0-plan appendix, which expected both spellings to give one hash, and verified-facts.md, which drew that from `Tool.model_validate` with its default settings.
- **A definition that can't be hashed:**
  - step 3 fails when the 2026-07-28 model rejects the tool;
  - step 4 fails on an integer outside plus or minus 2^53-1 (`IntegerDomainError`; an int64 bound such as `"maximum": 9223372036854775807` is enough), a non-finite float or a lone surrogate.

  The tool is hidden and recorded as `tool.unservable` with `def_hash` null and the problem `cannot be hashed: <one line>`. It can't be approved. Open question 1 asks what to do about large integers.
- **Names.** The form carries the upstream's own name, and pins are keyed by (prefix, upstream tool name). Two upstreams with identical tools have equal hashes and separate pins. Renaming a prefix in `polarizer.toml` makes every tool behind it pending again.

### When the SDK changes

The form depends on the SDK. `pyproject.toml` pins `mcp==2.2.0` and `mcp-types==2.2.0` exactly, and that pin stays. Two tests catch an upgrade that changes the form: `test_fixed_definition_fixed_hash`, and `test_execution_is_the_only_era_field`, which fails if an SDK release adds another field that only one era serves. A changed form gets a new prefix, `POLARIZER-TOOLDEF/2`, never a silent change under `/1`. Every existing pin then shows as changed and needs approving again, unless a migration is built first (open question 12).

## 3. Ledger kinds

The format, the hash, the statuses and the exit codes don't change. LEDGER-SPEC.md already allows new kinds whose data stays inside the subset. Every field below is ASCII-keyed and holds strings, integers, null or lists. Tool names are ASCII, because the exposed-name rule admits only `[A-Za-z0-9._-]`. Free text is folded to one line and cut to 1 KiB, as M0's `clip` and `one_line` do.

| Kind | `data` | Written by | fsynced |
|---|---|---|---|
| `tool.approved` | `upstream`, `tool`, `def_hash`, `actor`, `group` (null, or the group id for a group approval) | `polarizer approve` | yes, with `ledger.head` updated, before the command reports success; `serve` acts on it only after its own fsync (section 8) |
| `tool.rejected` | `upstream`, `tool`, `def_hash`, `actor`, `reason` (required, non-empty after folding) | `polarizer reject` | yes, with `ledger.head` updated |
| `tool.drift` | `session`, `upstream`, `tool`, `approved_hash`, `live_hash` | `serve` | yes, with `ledger.head` updated; hiding doesn't wait for it |
| `tool.seen` | `session`, `upstream`, `tool`, `def_hash` | `serve` | no; written, then fsynced when the writer's queue empties |
| `tool.unservable` | `session`, `upstream`, `tool`, `def_hash` (or null), `problem` | `serve` | no |
| `upstream.refresh_failed` | `session`, `prefix`, `trigger` (`client-list`, `upstream-notice` or `connection-lost`), `error` | `serve` | no |

**Existing kinds that gain a field.** `session.started` gains `pins`: `"on"`, or `"off"` under `--no-pins`. It is absent in entries written before M1a. `call.returned` keeps its fields. Its `error` for a cancel caused by shutdown is `polarizer shut down during the call` (section 8).

**The actor.** `tool.approved` and `tool.rejected` have `actor: "person"`. Polarizer can't tell who ran the command, only that a person (or something acting as one) ran the CLI. Entries without `actor` are written by Polarizer itself, and their `session` names the `serve` process that saw what they record.

**What is fsynced, and why.** Only an entry that can make Polarizer expose more needs to be durable before Polarizer acts on it, and in M1a that is `tool.approved` alone. `tool.rejected` and `tool.drift` stay fsynced security-state entries as LEDGER-SPEC.md lists, but they only ever hide, so the in-memory hide happens at once and the record follows. Losing them can only cost a later explanation, never expose anything:
- a lost `tool.rejected` leaves the tool in its earlier state, which the person sees with `pending` and can fix;
- a lost `tool.drift` is detected and recorded again at the next refresh.

The three unfsynced kinds are observations that the next start repeats.

**When each is written.**
- **`tool.seen`:** once for each (upstream, tool, `def_hash`) since that tool's latest decision, when the hash is live and isn't covered by the decision. That means there is no decision, or the latest decision rejected a different hash.
- **`tool.drift`:** once for each (`approved_hash`, `live_hash`) pair since the latest decision, when the latest decision approved a hash and the live hash differs.
- **`tool.unservable`:** once per process for each (upstream, tool, `def_hash`, `problem`). The problems are:
  - `cannot be hashed: <one line>`;
  - `stored copy missing`;
  - `stored copy could not be written: <the operating system's message>`;
  - `stored copy does not match its hash`;
  - `stored copy unreadable: <the operating system's message>`;
  - `stored copy is not a valid tool`;
  - `more than 16 definitions since the last decision`.
- **`upstream.refresh_failed`:** on the first failure after the upstream's last successful listing, once per run of failures. Each failure still gets M0's line on stderr.
- **The cap.** `tool.seen` and `tool.drift` record at most 16 distinct live hashes per tool between decisions. The 17th records `tool.unservable` (`more than 16 definitions since the last decision`), the tool stays hidden, and further new hashes for it are neither stored nor recorded until the next decision. This bounds what a server that changes its definitions on every list can add.

**Decision time.** M1a records none. `approve` and `reject` take the hash as an argument and decide at once, so no interval between showing and deciding exists to measure. If a later milestone records one, it is a monotonic `elapsed_ms` measured in the deciding process and stored in the entry, never a difference of `ts` values, which can step back on WSL2 (verified-facts.md, Wall clock and monotonic clock on WSL2).

## 4. Pin state

### Derived from the ledger, rebuilt at every start

Pin state is a fold over the verified chain, in seq order, keyed by (upstream, tool). For each key it holds:
- **the latest decision:** the last `tool.approved` or `tool.rejected`, with its hash and seq;
- **drifted:** whether a `tool.drift` follows that decision;
- **observed:** the hashes recorded by `tool.seen` and `tool.drift` (`live_hash`) since that decision, with the seq of each record, and the cap state.

`serve` builds it during the startup verification pass, from the same bytes, with no second read. It then updates it from every entry it appends and every entry it adopts from another process (section 8). `polarizer pending`, `approve` and `reject` build it the same way. Nothing that decides exposure lives only in memory. Claude Code can stop the process at any moment (section 8), and a new process must reach the same state from the ledger alone. The process's own live data is only what the upstreams list right now, which every start lists again.

### States

"Live" is the hash from the current successful listing. The stored copy check is section 5.

| State | Condition | Exposed | Refusal reason in `call.refused` |
|---|---|---|---|
| approved | latest decision approves H, no drift since, live hash is H, stored copy passes | yes, as the stored copy | none |
| pending | no decision, or the latest decision rejected a hash other than the live one | no | `upstream <p> tool "<t>" is pending approval` |
| rejected | latest decision rejects the live hash | no | `upstream <p> tool "<t>" was rejected` |
| changed | latest decision approves H, and a drift follows it or the live hash isn't H | no | `upstream <p> tool "<t>" changed after approval` |
| unservable | can't be hashed, over the cap, or the stored copy of an approved H fails its check | no | `upstream <p> tool "<t>" cannot be served: <problem>` |
| list unknown | the upstream's last refresh failed, or its connection was lost | no, for every tool of that upstream | `upstream <p> tool list could not be refreshed` |
| not listed | the upstream's current list doesn't have the tool, whatever its decisions | no | M0's `upstream <p> has no tool "<t>"` |

**Changed is sticky.** Once a drift follows an approval, the tool stays hidden even if the upstream returns to the approved definition. It comes back only after a new decision: approving the new hash, or approving H again. A definition that changes and changes back is itself what a rug pull looks like, and the stickiness also stops a server that flips its definitions from making Polarizer flip its list (section 6). Open question 5.

**What stickiness costs.** A change followed by a revert makes the person approve again a definition they have already approved, byte for byte.
- Until they do, the tool is hidden, and the agent's calls to it fail with `its definition changed after approval`.
- A changed definition can't be group-approved (section 7). After a server upgrade that is rolled back, each affected tool is approved one at a time.
- Approving text already read, again and again, is the habit that wears down attention, which is what Polarizer exists to measure.
- In M1a the only sign of a revert is that `pending` prints the same hash twice: `changed <prefix>__<tool> H, approved H`.

**What M3's card must show** when it asks for a decision on a changed definition:
- whether the live definition is identical to one approved before (the same hash), and if so, the seq and time of that approval;
- every `tool.drift` since that approval, with its hashes and time, so that A, then B, then A again reads as a revert, with the number of changes, not as new text;
- what differs from the approved copy, which is nothing for a revert.

The card never approves a revert by itself. An undone rug pull looks exactly like a revert, so the decision stays the person's.

**The latest decision wins.** A rejection of any hash for a tool replaces an earlier approval. So rejecting a changed definition B also removes the pin on the earlier A, and if the upstream goes back to A, A is pending again. Open question 6.

### Startup fails closed

Startup is M0's, step for step. Opening the ledger verifies the chain and `ledger.head` before anything else, and builds pin state in the same pass. Any status other than `intact` exits with that status's code and one line on stderr, and no upstream is started or tool exposed. That includes a `ledger.head` ahead of the file (`truncated`), a lock still held after 2 s (7) and a v0 ledger (3).

Pin state is the latest decision per tool, so losing the end of the ledger could silently undo a decision. If a `tool.rejected` at the tail were lost, the earlier `tool.approved` would again be the latest decision and the rejected tool would be exposed: that is the case `ledger.head` and the startup check exist to catch.

**While running.** If the writer stops (another process wrote something that doesn't chain, or the file shrank; LEDGER-SPEC.md, Appending), pin state can no longer be trusted. `serve` then exposes nothing: `tools/list` returns no tools, a change notice is sent, and calls get M0's line `polarizer: <name> was not called: the ledger could not record it`.

## 5. The stored copy

- **Where:** `<ledger_dir>/defs/<def_hash>.json`. `defs/` is created with mode 0700 and each file with 0600, as for every file in `ledger_dir`. LEDGER-SPEC.md already lists `defs/`.
- **The bytes:** exactly `canon` from section 2, with no newline. `sha256(b"POLARIZER-TOOLDEF/1\n" + file bytes)` must equal the name.
- **Written by `serve`, when it first sees a hash.** The file is written before the `tool.seen` or `tool.drift` entry that names the hash, so every recorded hash has a copy unless writing failed. The steps:
  1. write `defs/<def_hash>.json.tmp-<pid>-<8 random hex>` and fsync it;
  2. give it its final name without ever replacing an existing file: `os.link` then remove the temp file on POSIX, `os.rename` on Windows, which fails if the target exists;
  3. fsync `defs/` on POSIX.

  An existing file is never overwritten. If writing fails, the tool gets `tool.unservable` with `stored copy could not be written: <message>`.
- **Read with a rehash every time.** Every time `serve` builds the exposed list, it reads each approved tool's copy and rehashes it. `pending` and `approve` read and rehash it too. There is no in-memory cache of a copy's contents. A copy that is missing, unreadable, fails the rehash or won't parse as a tool makes the tool unservable (hidden, and `tool.unservable` recorded once per process). A missing copy is written again the next time `serve` sees the same live definition, since its bytes are determined by the hash. An altered copy stays until a person deletes it, and `serve` then writes it again at its next refresh.
- **What is served:** the stored JSON object without `_meta`, with `name` set to `<prefix>__<tool>`, parsed into `mcp_types.Tool` by alias, and served by the SDK in the client's era.
  - For a 2026-07-28 client, the wire definition equals the stored object minus `_meta`, renamed. A test pins this.
  - For an older-era client the same holds, because the stored copy has no `execution` for the 2025-11-25 model to add.
  - Tools appear in config order of upstreams, each upstream's tools in the order its current list gives them, as in M0.

**What changes from M0.** M0 served each upstream's live list, with `_meta`, and with `execution` for older-era clients. M1a serves only approved tools, each from its stored copy, never with `_meta` or `execution`. The live list is used only to compute hashes and to know which tools the upstream still has.

## 6. Drift detection

### Triggers

- **Startup:** the listing that connects each upstream (PROXY-SPEC.md, Startup step 5).
- **An upstream's change notice:** a listen event from a 2026-07-28 upstream, or `notifications/tools/list_changed` from an older-era one. That upstream is listed again. At most one refresh per upstream runs at a time, and notices that arrive during one cause exactly one more afterwards.
- **Every client `tools/list`:** every connected upstream is listed again, each bounded by its `connect_timeout_seconds`, as in M0.
- **A new entry from another process** (section 8): pin state is updated and exposure recomputed, with no upstream listing.
- **A lost connection:** the upstream's tools are hidden at once.

Every listing uses `cache_mode="refresh"` and follows `next_cursor` within M0's page and tool limits.

### Each successful refresh

The upstream's tools are processed in list order, under one lock in the event loop, so two refreshes never interleave their records:
1. Apply M0's name rule; skipped names are never hashed.
2. Compute the hash (section 2).
3. Write the stored copy if it's missing (section 5).
4. Record `tool.seen`, `tool.drift` or `tool.unservable` if section 3 says so.
5. Recompute that tool's state.

A tool the upstream no longer lists becomes "not listed". Then the exposed list is rebuilt.

### A failed refresh

A failure is a timeout, an error, a listing limit, a list the SDK can't parse (including snake_case `input_schema`), or a lost connection. It is never read as "unchanged". Every tool of that upstream is hidden (state "list unknown"), `upstream.refresh_failed` is recorded on the first failure of a run, and the M0 stderr line is written. The next successful refresh restores each tool to whatever its state then is, which may be changed. M0 kept the last good list (STAGE2-NOTES.md, guess 2); M1a doesn't, for pins.

**Coming back needs no new approval.** A failed refresh is not a decision and records no `tool.drift`, so it never makes a tool changed. When a later refresh succeeds, a tool whose live hash is still its approved hash is exposed again at once, with no new approval. A tool whose hash now differs is changed, with `tool.drift` recorded, as after any refresh.

**If no refresh succeeds:**
- The upstream's tools stay hidden for the rest of the process, and every call to one is refused with `its server's tool list could not be checked`.
- Only the first failure of the run is recorded. Each later failure writes only its stderr line.
- M1a has no timer that retries. Another refresh happens only at the next client `tools/list` or upstream change notice. In the headless runs, Claude Code listed only at connect and after a change notice (verified-facts.md), so a recovered upstream can stay hidden until restart. Open question 14.
- A lost connection can't succeed again within the process, because M0 never reconnects an upstream.

In every case, the tools come back when a new Polarizer process starts and lists the upstream successfully. Reconnecting the server in `/mcp` does that (section 8).

### When a tool hides and comes back

A tool hides as soon as a trigger puts it in any state but approved. It comes back:
- **pending or rejected:** when a person approves the live hash;
- **changed:** when a person approves the live hash, or approves the old hash again while it is live;
- **unservable:** when its stored copy passes again;
- **list unknown:** after the next successful refresh, with no new decision, if its live hash is then still the approved one.

### Telling the client

After each trigger, `serve` compares the exposed list (the exact served definitions, in order) with the list it last announced or returned. If it differs, it publishes `ToolsListChanged()` on its listen bus for 2026-07-28 clients and calls `send_tool_list_changed()` on each older-era session, as M0 does for upstream notices. Notices are sent at most once per second; a change inside that second is folded into one notice at its end. An upstream notice that changes nothing in the exposed list is no longer passed on. A client's own `tools/list` gets the new list in its response, which counts as announced.

### Calls

- **No re-list per call.** A call doesn't list its upstream again. It uses the state from the last refresh, after a ledger check (section 8) so a decision made seconds ago applies. A server that changes behavior can do so without changing its definition, so a per-call listing would add latency for little.
- **Routing order:** M0's checks first (`not a prefixed name`, unknown prefix, `did not connect`), then list unknown, then not listed, then the pin states in the table.
- **A hidden tool called by name** is refused. The ledger gets one `call.refused` with `session`, `tool` (the name as called) and the reason from the table. There is no side file, `call.sent` or `call.returned`, and the upstream is not called.
- **The client** gets a result with `isError` and one line, `polarizer: <name> is not available: <why>`. `<why>` is one of:
  - `waiting for approval`;
  - `rejected`;
  - `its definition changed after approval`;
  - `it cannot be served`;
  - `its server's tool list could not be checked`.

  The line names no command and never includes a rejection's reason, because it reaches the model.

## 7. The command line

### Syntax

```
polarizer pending (--config <absolute path> | --ledger-dir <absolute path>) [--upstream <prefix>]
polarizer approve (--config <absolute path> | --ledger-dir <absolute path>) <prefix> <tool> <def_hash>
polarizer approve (--config <absolute path> | --ledger-dir <absolute path>) --group <group id> [--upstream <prefix>]
polarizer reject (--config <absolute path> | --ledger-dir <absolute path>) <prefix> <tool> <def_hash> --reason <text>
polarizer serve --config <absolute path> [--no-pins]
```

**Shared rules:**
- `--config` and `--ledger-dir` work as for `verify` and `repair`: exactly one, absolute, with the same usage lines naming the command. `--config` is read with `require_env=False`, so no upstream secrets are needed, and no command here starts an upstream.
- `pending` only reads, like `verify`: it creates, deletes and modifies nothing, and checks no locations.
- `approve` and `reject` write, so they check the location as `repair` does: they refuse a forbidden `ledger_dir` (exit 2) and warn inside another git working tree.
- None of them creates a ledger. A missing or empty `ledger.jsonl` gives `no ledger at <dir>`, exit 2.
- Results go to stdout, and every refusal and usage error goes to stderr as one line starting `polarizer: `, as PROXY-SPEC.md's output-streams rule says. No new exit codes are added: every refusal of an argument or decision exits 2, and ledger statuses keep their codes.

### `polarizer pending`

It reads the ledger and `ledger.head` as `verify` does: the lock only if the lock file exists, up to 2 s. If the status isn't `intact`, it prints exactly `verify`'s output for that status, lists nothing, and exits with the status's code. Otherwise it lists every definition waiting for a decision, sorted by prefix, then tool name, then the seq of its record. `--upstream` limits the listing to one prefix.

```
pending: <n> new, <c> changed, <u> unservable

new <prefix>__<tool> <def_hash>
<definition>

new <prefix>__<tool> <def_hash>, after rejecting <rejected_hash>
<definition>

changed <prefix>__<tool> <live_hash>, approved <approved_hash>
<definition>

unservable <prefix>__<tool> <def_hash or ->: <problem>

group <group id> covers the <k> new definitions above
```

- **Each block** is separated by one blank line. With nothing waiting, the whole output is `pending: nothing waits for a decision`.
- **`<definition>`** is the stored copy rendered with `json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True)`. Every character outside ASCII therefore appears as an escape, so hidden or look-alike characters are visible. `_meta` is shown too, since it is part of what is approved.
- **A copy that fails its check** is listed in place of the definition as one line, `stored copy <problem>; it cannot be approved`, and its block doesn't count toward the group.
- **The group line** appears when `<k>` is at least 1. `<k>` counts the `new` blocks whose definitions were printed.
- **The group id** is `hex(sha256(b"POLARIZER-GROUP/1\n" + rfc8785.dumps(sorted list of [prefix, tool, def_hash])))` over those blocks.
- **`changed` blocks are never part of a group** (open question 2).
- **Exit 0** whenever the ledger is intact, whatever is waiting.

### `polarizer approve`

**One definition.** The command:
1. checks the location;
2. refuses a malformed hash with `polarizer: <arg> is not a definition hash (64 lowercase hex characters)`;
3. opens the ledger as a writer (2 s lock wait, full verification). A status other than `intact` gives serve's line, `polarizer: <verify's first line>; run polarizer verify`, with that status's code;
4. requires that (prefix, tool, hash) was seen by `serve`: named by a `tool.seen` or as a `tool.drift` `live_hash`, or approved before for that tool. Otherwise it refuses with `polarizer: Polarizer has not seen <prefix>__<tool> with definition <def_hash>`;
5. reads and rehashes the stored copy. A failure refuses with `polarizer: stored copy defs/<def_hash>.json <problem>; nothing approved`;
6. if the latest decision already approves this hash with no drift since, prints `already approved: <prefix>__<tool> <def_hash>`, writes nothing, and exits 0;
7. otherwise prints the definition, appends `tool.approved` (`group` null, fsynced, `ledger.head` updated), prints `approved <prefix>__<tool> <def_hash> at seq <q>`, and exits 0.

**A group.** The command checks the location and opens the ledger as above. It then recomputes the new definitions `pending` would group, with the same `--upstream` filter, and their group id.
- **A different id**, or nothing pending, refuses with `polarizer: group <id> does not match what is pending now; run polarizer pending again`. Nothing is written.
- **A match** appends one `tool.approved` per definition, in the listing's order, each fsynced, each with `group` set to the id, and prints `approved <prefix>__<tool> <def_hash> at seq <q>` for each. A final line follows: `approved <k> definitions as group <id>`. Exit 0.
- **If the writer fails partway,** the lines already printed stand, and stderr gets `polarizer: could not record an approval: <why>; <j> of <k> were approved`. The exit code is the writer error's own (1 for a stopped writer). Each approval is complete on its own, and the rest stay pending.

A group is bound to exactly the definitions `pending` printed. Any change between the two commands (a new tool, a changed definition, another person's decision) changes the id, and nothing is approved.

### `polarizer reject`

`--reason` is checked first. A missing reason, or one that is empty after folding whitespace, gives `polarizer: reject needs --reason <text>`, exit 2, before the ledger is opened. Then the command runs steps 1 to 3 as for `approve`. The hash must have been seen for that tool, or be its currently approved hash (to revoke it). If the latest decision already rejects this hash, it prints `already rejected: <prefix>__<tool> <def_hash>` and exits 0. Otherwise it appends `tool.rejected` (fsynced) with the folded reason cut to 1 KiB, prints `rejected <prefix>__<tool> <def_hash> at seq <q>`, and exits 0. No stored copy check is needed to reject.

### `serve --no-pins`

The documented switch for running without pins, off by default. With it, `serve` exposes every listed tool with its live definition and refuses only what M0 refuses, exactly as M0 does. It still hashes, stores copies, and records `tool.seen`, `tool.drift`, `tool.unservable` and `upstream.refresh_failed`, so `pending` works afterwards.

Every start under the flag is visible in two places:
- **stderr:** each time `serve` starts with `--no-pins`, it writes `polarizer: warning: --no-pins: every listed tool is exposed without approval` before connecting any upstream. Every start, not only the first.
- **the ledger:** every `serve` process writes its own `session.started`, and under the flag its `pins` is `"off"` (`"on"` otherwise). That is the field section 3 adds; no other field or kind is needed.

It exists only as a command-line flag, never as a `polarizer.toml` key, so a config file can't turn pins off quietly. That follows the idea credited to mcpclerk for M3's approve-everything switch. Open question 4 asks whether to keep it at all.

### First run

Nothing is exposed until approved, so a new user does this once:
1. Start Claude Code, or record the upstreams' definitions without it: `polarizer serve --config <path> < /dev/null`. `serve` connects every upstream, records what it sees, reads end of input, and exits 0. `/mcp` shows `polarizer` connected with no tools, and serve's stderr says `polarizer: <n> tools wait for approval; run polarizer pending`.
2. `polarizer pending --config <path>`: read every definition.
3. `polarizer approve --config <path> --group <id>`.
4. A running `serve` notices within about a second and tells Claude Code (section 8). If `/mcp` still shows no tools, reconnect the server from `/mcp`.

### Golden-file tests

Every row gets a file under `tests/golden/`, run on a ledger built by a deterministic helper, comparing stdout byte for byte with the exit code, as PROXY-SPEC.md does for `verify`.

| Command and situation | Golden file | Exit |
|---|---|---|
| `pending`, nothing waiting | `pending_nothing.txt` | 0 |
| `pending`, two new, one after a rejection, one changed, one unservable, one bad stored copy | `pending_mixed.txt` | 0 |
| `pending --upstream probe` | `pending_one_upstream.txt` | 0 |
| `pending` on each non-intact status (tampered, invalid, torn tail, truncated) | `pending_<status>.txt` | that status's code |
| `pending`, locked; no ledger | `pending_locked.txt`, `pending_no_ledger.txt` | 7, 2 |
| `approve` one definition | `approve_one.txt` | 0 |
| `approve` already approved | `approve_already.txt` | 0 |
| `approve --group`, matching | `approve_group.txt` | 0 |
| `approve` refusals: bad hash, not seen, stored copy altered, group mismatch, broken ledger | `approve_refused_<case>.txt` (stdout empty; the stderr line is compared too) | 2, or the status's code |
| `reject` with a reason; already rejected | `reject_one.txt`, `reject_already.txt` | 0 |
| `reject` without `--reason`, or with a blank one | `reject_no_reason.txt` (stderr) | 2 |
| usage errors for each command | `usage_pins.txt` (stderr, one line per case) | 2 |

### Existing M0 tests and the manual check

- **In-memory tests.** `tests/helpers/rig.py` gains `approve_all(ledger_dir)`. It runs the same library code as `approve --group` on whatever is pending, with `actor: "person"`. `rig.gateway()` and `rig.proxied()` gain `approve=True` by default: once the gateway has started and recorded its `tool.seen` entries, they approve all and wait until the exposed list is complete. M0 tests then see M0's tools. Their entry counts grow by the `tool.seen` and `tool.approved` entries, and tests that count all entries are updated to count by kind. Pin tests pass `approve=False`.
- **Stdio tests** (`test_stdout`, `test_fidelity`'s raw tests, `test_reference`) use `rig.prime(config)`, which runs `polarizer serve --config <config>` with stdin closed and then `approve_all`. Their `serve` then starts with every tool approved. The expected bytes change only where `_meta` or `execution` was served to an older-era client.
- **`scripts/live-check.sh`** primes its generated temp ledger the same way before it runs `claude`. That is a `serve` run and an `approve`, with no model call. Its pass criteria don't change.
- **docs/MANUAL-CHECK.md** gains the first-run steps above after "Start Claude Code", and an M1a section for the rug-pull check (section 9). `serve --no-pins` is documented there as the fallback, not used by default.

## 8. Approving while serve runs, and process lifetime

### How serve notices a decision

The CLI and `serve` are separate processes sharing one ledger. `serve` doesn't hear about a decision directly; it reads it from the file.

- **The watch.** Once a second, and at the start of every client `tools/list` and every `tools/call`, `serve` asks its writer thread to catch up. The writer compares the file's size with its offset using `fstat` without the lock. If they are equal, nothing else happens. If the file grew, it takes the lock and runs the existing catch-up (LEDGER-SPEC.md, Appending): it reads only the new bytes, checks each complete line chains from its head, and adopts them. The catch-up now also hands each adopted entry to pin state.
- **Durable before acting.** The CLI fsyncs `tool.approved` and updates `ledger.head` before it releases the lock. So `serve`, which reads new bytes only under the lock, can't see an approval whose writer hasn't finished its fsync. A writer whose fsync failed still leaves its line in the file, so `serve` also calls `fsync` on the ledger itself before acting on any adopted `tool.approved`. fsync is per file, so this flushes another process's write too. If that fsync fails, the writer stops and nothing more is exposed.
- **A catch-up that fails** (the file shrank, a partial line, a line that doesn't chain) stops the writer, as in M0, and section 4's "While running" applies.
- **How soon:** within about one second of the CLI's command returning, or at the next `tools/list` or `tools/call`, whichever comes first. A call to a just-approved tool works without waiting for the timer, and a call to a just-rejected one is refused.

### Telling Claude Code

When a decision changes the exposed list, the notice in section 6 goes out:

| Path | Status |
|---|---|
| `ToolsListChanged()` on the listen bus, for a 2026-07-28 client | Verified headless with Claude Code 2.1.287: it re-listed within 1 s each time (verified-facts.md, Spike). Not verified in an interactive session. |
| `send_tool_list_changed()` to an older-era session | Verified SDK to SDK only (`tests/test_eras.py::test_eras`). Never seen with Claude Code, which negotiates 2026-07-28 with Polarizer. |
| Claude Code's `subscriptions/listen` stream after Polarizer restarts | After Esc, Claude Code logged `subscriptions/listen stream dropped (remote); attempting to re-listen` (verified-facts.md, Interactive). Whether a new process's notices reach it is not verified. |

**Re-listing is unverified where approvals happen.** Claude Code re-listed after a change notice in headless runs. Whether it does in an interactive session, where a person approves, is not verified (open question 9). So the documented step after approving, if `/mcp` doesn't show the tool, is to restart the server by reconnecting it in `/mcp`. The new process rebuilds pin state from the ledger and exposes the approved tool from its first listing. Esc during a Polarizer call also ends the process (Shutdown and restart, below). In the one interactive check, Claude Code started a new process about 12 s later, but what started it is not known, so the documented fallback is the `/mcp` reconnect.

If Claude Code doesn't re-list, the decision still holds, and a hidden tool is refused whatever Claude Code thinks it has.

### Shutdown and restart

What is known, from an interactive session with Claude Code 2.1.288 on protocol 2026-07-28 (verified-facts.md, Interactive):
- Pressing Esc during a 30 s call made Claude Code send SIGINT to the Polarizer process, then SIGTERM 100 ms later, and the call failed with `Connection closed`.
- Polarizer wrote `call.returned` with outcome `cancelled` before it exited, and exited about 50 ms after the SIGTERM. A new process started about 12 s later.
- Whether Claude Code also sends `notifications/cancelled` on Esc is unverified. A headless run saw it on Claude Code's own timeout.
- Closing a session without pressing Esc during a call is unverified.

So `serve` can receive SIGINT, then SIGTERM about 100 ms later, or just lose its stdin, at any moment: in the middle of a call, a refresh, a stored-copy write, or while another process approves. It may also be killed outright, so it must not rely on any of this.

**What serve does.** SIGINT, SIGTERM (POSIX) and end of input all start one shutdown path. The handlers use `anyio.open_signal_receiver`. On Windows only end of input and Ctrl+C apply.
1. **Stop serving and cancel every in-flight call.** Each call's handler records `call.returned` with outcome `cancelled` in a shielded scope, as M0 does. The error is `polarizer shut down during the call` when shutdown caused the cancel, and M0's `the client cancelled the call` when a `notifications/cancelled` arrived first. The SDK's upstream clients send their own `notifications/cancelled` upstream as part of the cancel.
2. **Close the upstream clients in parallel, bounded at 1 s in total.** An upstream still running after that is left to see end of input when Polarizer's pipes close.
3. **Close the writer.** It writes what is queued, fsyncs, and releases its files. Then the process exits 0.

A second signal during shutdown skips the rest of step 2 and goes straight to step 3.

**Nothing important depends on shutdown work.** Claude Code allows about 100 ms, and the process may be killed outright.
- Decisions are made by the CLI and are durable before they matter.
- Observations (`tool.seen`, `tool.drift`, `tool.unservable`, `upstream.refresh_failed`) are repeated by the next start, which lists every upstream and dedupes against the ledger.
- A stored-copy write killed halfway leaves only a temp file, never a partial copy under the final name.
- A `call.sent` with no `call.returned` already means "outcome unknown" (LEDGER-SPEC.md, fsync policy).
- A line cut off mid-write is a torn tail, as in M0: the next start refuses until `polarizer repair`.
- Pin state is rebuilt from the ledger at every start (section 4), so a process killed at any point and started again exposes the same tools, given the same upstream lists.

**Not dependent on `notifications/cancelled`.** If Claude Code sends it, the call ends with `cancelled` as in M0. If Claude Code only signals the process or closes stdin, the call ends with `cancelled` through step 1. If the process is killed before either, the call reads as outcome unknown. Exactly one `call.returned` is written per call in every case, because the handler ends only once.

**A session-end entry is not worth adding in M1a.** Every Esc ends the process by signal within about 150 ms, so a missing session end would be routine and prove nothing. Writing one would be shutdown work, which this section says nothing may depend on. Nothing in M1a would read it. Open question 7 proposes one for later.

## 9. The probe's rug-pull mode

**`tests/helpers/probe_server.py`** gains two things.
- **`PROBE_RUGPULL=<path>`.** At start, if the file at `<path>` doesn't exist, the probe creates it (empty) and serves its usual definitions. If it exists, the probe changes `wait`'s description to `Wait for a number of seconds. Changed after approval.` So the first start shows the original, and every later start shows the change: a server that changes itself after being approved. The manual check uses this.
- **A `change` tool.** It switches `wait`'s description the same way within the running process, answers `changed`, and then sends `notifications/tools/list_changed`. This drives mid-session drift from an older-era upstream over stdio. `test_stdout`'s expected tool list gains `change`.

**In memory,** `tests/helpers/fakes.py`'s `FakeUpstream` gains `async rug_pull()`. It bumps the descriptions to `Definition v<n+1>` and announces the change through the right channel for the connection's era: a `ToolsListChanged()` on its listen bus for 2026-07-28, or `send_tool_list_changed()` on the last session that listed it for 2025-11-25. Tests call it directly, so no tool call through the proxy is needed. The existing `change` tool stays. `FakeUpstream` also gains `definitions=` to serve fixed `Tool` objects, for the hash tests, and `fakes.from_env` reads them from a JSON file named by `FAKE_DEFINITIONS`, so `modern_server.py` can serve them over stdio.

**The manual check** (MANUAL-CHECK.md, an M1a section written in stage 5):
1. Add `PROBE_RUGPULL = "/tmp/polarizer-rugpull"` to the probe's `env` and remove that file. Start Claude Code, approve the group, and see `probe__wait` in `/mcp`.
2. Restart the probe by reconnecting the server in `/mcp`.
3. Expect `polarizer pending` to show `changed probe__wait <new hash>, approved <old hash>`, and Claude Code to no longer list `probe__wait`. Asking Claude to call it should give `polarizer: probe__wait is not available: its definition changed after approval`.

## 10. Tests

"Default" means the test runs in `scripts/test.sh` and CI on every platform. "POSIX" means it is skipped on Windows, with the reason in the test. "Reference" means it runs only with `POLARIZER_REFERENCE=1`, locally, never in CI.

### `tests/test_defhash.py`

| Test | Claim | Suite |
|---|---|---|
| `test_fixed_definition_fixed_hash` | The probe's `wait` definition hashes to `ac0778cf2bd27c6f802ca57ffc736ffddcd97d9773898dcc1c9c68ea9bc54e8b`, and its stored bytes are exactly the canonical JSON written in the test. | Default |
| `test_same_hash_in_both_eras` | One SDK tool with `execution` set, served by a `FakeUpstream` that Polarizer's client reaches in `legacy` mode (2025-11-25) and `auto` mode (2026-07-28), in memory and over stdio through `modern_server.py` with `FAKE_DEFINITIONS`, gives one hash. `execution` reaches the parsed `Tool` only in the first. | Default |
| `test_execution_is_the_only_era_field` | A tool using every `Tool` field, serialized by the SDK for 2025-11-25 and for 2026-07-28, differs only in `execution`. It fails if an SDK upgrade adds another era-only field. | Default |
| `test_null_and_absent` | Null top-level fields and null top-level schema keys hash as absent. A null nested in a schema property doesn't, and `"annotations": {"title": null}` hashes as `{}`. | Default |
| `test_key_order` | The same definition with keys reordered at every level gives the same hash. | Default |
| `test_nested_description_changes_hash` | Adding `"description": "How long."` to `wait`'s `seconds` property changes the hash to `3462be54bba570aacd2a87ea2bb68c6806a68abac4761e0d848d1b6deb8a11b5`. | Default |
| `test_unknown_field_dropped` | Unknown fields at the `Tool`, `annotations` and `icons` level change neither the hash nor the stored copy. An unknown keyword inside `inputSchema` changes the hash and is served. | Default |
| `test_meta_hashed_not_served` | A change to `_meta` alone changes the hash; the served copy has no `_meta`. | Default |
| `test_snake_case_input_schema_fails_listing` | An upstream that sends `input_schema` fails to connect, and on a later refresh records `upstream.refresh_failed`. | Default |
| `test_unhashable_definition_hidden` | A schema with `"maximum": 9223372036854775807` makes the tool hidden, records `tool.unservable` with `def_hash` null, and `approve` can't approve it. | Default |

### `tests/test_pins.py` (in-memory gateway unless noted)

| Test | Claim | Suite |
|---|---|---|
| `test_pending_tools_hidden` | On a fresh ledger nothing is exposed, each tool gets one `tool.seen` and one stored copy, and serve's stderr names how many wait. | Default |
| `test_approve_exposes` | After `approve`, the tool is listed from its stored copy and the client gets a change notice. | Default |
| `test_reject_hides_and_needs_reason` | `reject` without a reason writes nothing. With one, it hides an approved tool, records the reason, and the client gets a change notice. | Default |
| `test_state_from_ledger_matches_live` | After a scripted run of seen, approve, drift, reject and approve again, pin state folded from the ledger file alone equals the gateway's, and gives the same exposed list for the same upstream lists. | Default |
| `test_restart_rebuilds_exposed_set` | A new gateway on the same ledger and upstreams exposes the same list and writes no new `tool.seen` or `tool.drift`. | Default |
| `test_drift_mid_session_modern` | A 2026-07-28 upstream's `rug_pull()` hides the tool through a listen event, records one `tool.drift`, and publishes a change to the client. | Default |
| `test_drift_mid_session_handshake` | The same through a 2025-11-25 upstream's `tools/list_changed`, in memory and with the probe's `change` tool over stdio. | Default |
| `test_drift_once_per_pair_and_sticky` | Repeated lists of a changed definition record one drift; returning to the approved definition keeps the tool hidden and records nothing. | Default |
| `test_served_copy_is_stored_copy` | The definition a raw client receives, in both client eras, equals the stored copy minus `_meta`, renamed. | Default |
| `test_tampered_stored_copy_hides` | One changed byte in an approved copy hides the tool, records `stored copy does not match its hash` once, and refuses calls. Deleting the file lets the next refresh store it again and expose the tool. | Default |
| `test_unwritable_stored_copy_hides` | With `defs/` read-only, a new definition records `stored copy could not be written` and stays hidden. | POSIX: Windows has no read-only directories in this sense |
| `test_hidden_tool_called_by_name` | For each hidden state, a call records `call.refused` with the exact reason, the client gets the exact line, and the upstream sees no call and no side file is written. | Default |
| `test_failed_refresh_recorded_not_unchanged` | A refresh that times out hides the upstream's tools and records one `upstream.refresh_failed`. A definition changed during the failure is detected as drift afterwards, not taken as unchanged. | Default |
| `test_lost_upstream_hides_tools` | An upstream that exits after connecting hides its tools and records `connection-lost`. | Default |
| `test_flip_flop_is_bounded` | An upstream with a new definition on every list records at most 16 observations, then one cap entry, and the client gets at most one notice per second. | Default |
| `test_notice_only_when_exposed_list_changes` | An upstream notice that changes nothing exposed sends the client nothing. | Default |
| `test_no_pins_flag` | `serve --no-pins` exposes the live list as M0 does, records `pins: "off"` and the stderr warning, and still records `tool.seen`. | Default |

### `tests/test_pin_cli.py`, `tests/test_golden.py` and `tests/test_cli_subprocess.py`

| Test | Claim | Suite |
|---|---|---|
| `test_golden.py` (new rows) | Every row of the golden table in section 7. | Default |
| `test_group_is_bound_to_what_was_printed` | A new or changed definition between `pending` and `approve --group` refuses the group and writes nothing. | Default |
| `test_approve_only_what_serve_saw` | A hash never seen for that tool is refused, even if its stored copy exists. | Default |
| `test_approve_updates_head` | After `approve`, `ledger.head` names the approval's seq. | Default |
| `test_pending_writes_nothing` | `pending` leaves paths, sizes and mtimes unchanged, as `verify` does. | Default |
| `test_pin_commands_refuse_forbidden_ledger_dir` | `approve` and `reject` refuse a forbidden `ledger_dir` with exit 2; `pending` doesn't check. | Default |
| `test_pin_commands_subprocess` | `python -m polarizer pending` prints the golden bytes, including a non-ASCII description shown as ASCII escapes. | Default |

### `tests/test_pin_startup.py`

| Test | Claim | Suite |
|---|---|---|
| `test_startup_fails_closed_on_each_status` | On each broken fixture (tampered line, tampered head, invalid line, invalid head, not canonical, torn tail, v0), serve exits with that status's code and the probe log shows no start. | Default |
| `test_head_ahead_of_file` | A `ledger.head` past the last entry exits 6 and exposes nothing, even when the lost tail held a `tool.rejected`. | Default |
| `test_writer_stopped_exposes_nothing` | A non-chaining line appended by another process empties the exposed list, sends a notice, and refuses calls. | Default |

### `tests/test_pin_durability.py`

| Test | Claim | Suite |
|---|---|---|
| `test_approval_fsynced_before_exposed` | With the approving writer's fsync held on an event, the watch runs repeatedly and the tool stays hidden. Once the fsync is released, it is exposed. | Default |
| `test_serve_fsyncs_adopted_approval` | When the approving writer's fsync raises after its write, serve calls its own fsync (counted through `FileOps`) before exposing. If that fsync raises too, nothing is exposed and the writer stops. | Default |
| `test_approve_from_second_process` | `python -m polarizer approve` run as a subprocess while a gateway serves exposes the tool within 2 s, and the client receives a change notice. | Default |
| `test_watch_reads_only_new_bytes` | The once-a-second watch reads nothing when the file hasn't grown, and only the new bytes when it has, at 100 and 10,000 entries. | Default |

### `tests/test_shutdown.py`

| Test | Claim | Suite |
|---|---|---|
| `test_sigint_then_sigterm_during_call` | `polarizer serve` over stdio, mid `wait(30)`, gets SIGINT and SIGTERM 100 ms later. It exits, the ledger verifies as intact, and the call has one `call.returned` with outcome `cancelled` and error `polarizer shut down during the call`. | POSIX: Windows has no way to send SIGINT and SIGTERM to a child the way Claude Code does. `os.kill` with SIGTERM there is `TerminateProcess`, which can't be caught, and SIGINT needs a shared console |
| `test_sigterm_alone_during_call` | The same with SIGTERM only. | POSIX, for the same reason |
| `test_stdin_closed_during_call` | Closing serve's stdin mid-call gives the same ledger result. | Default |
| `test_kill_then_restart` | SIGKILL mid-call leaves an intact ledger with a `call.sent` and no `call.returned`, and a new serve exposes the same tools. | POSIX, for the same reason |
| `test_prime_with_closed_stdin` | `serve` with stdin closed records `tool.seen` for every tool and exits 0. | Default |

### `tests/test_reference.py` (additions)

| Test | Claim | Suite |
|---|---|---|
| `test_reference_tools_hash_and_serve` | Every tool of the pinned Everything and Filesystem servers hashes without error. Approved, each is served equal to its stored copy minus `_meta`, and two runs give the same hashes. | Reference |

## 11. Threat model, for the README

Draft text, describing only M1a:

> **What pinning does.** When you approve a tool, Polarizer records a hash of its definition (its name, description, parameters and annotations) and keeps a copy. The agent is shown only that approved copy. If a server later changes a tool's definition, Polarizer hides the tool, records the change in its ledger, and tells Claude Code its tool list changed; the tool stays hidden until you approve the new definition. A hidden tool called by name is refused, and the refusal is recorded.
>
> **What it does not do.** It checks definitions, not behavior: a server can change what a tool does without changing its description, and Polarizer won't notice. It doesn't judge definitions yet. If the first definition you approve is poisoned and you approve it without reading it, Polarizer will keep serving exactly that poisoned definition. It does nothing outside the proxy. That includes MCP servers configured directly in Claude Code, the agent's own shell, and any program that can write to the ledger directory. An agent that can run commands as you can also run `polarizer approve`.
>
> **What it relies on.** The ledger staying intact: Polarizer refuses to start on a damaged ledger, but someone who can write to its directory can rewrite it. And you reading what you approve. Group approval exists for the first run; it approves exactly the definitions `polarizer pending` printed, and nothing that changed since.

## 12. Build order and size

M1a stays **medium**, as milestones.md says: about one to two weeks, split into two stages that each end in a stop for review.

### Stage 4: pins at rest

1. **`defhash.py`**: the form, the hash, the canonical bytes, and the stored copy (write without replacing, read with rehash). Done when `test_defhash.py` passes, with the fixed hashes. Small.
2. **Pin state**: the fold, the states table, and the hook in the startup verification pass. Done when a fold over a generated ledger gives the expected state for every row of the table. Small.
3. **New kinds**: `tool.seen`, `tool.unservable` and `upstream.refresh_failed`, the added fields, and `SECURITY_KINDS` unchanged. Done when generated fixtures holding every kind verify in both verifiers. Small.
4. **Gateway enforcement at startup and on client `tools/list`**: observe, hide, serve stored copies, refuse hidden calls with the reasons, hide on a failed refresh, and `--no-pins`. Done when `test_pins.py` passes except its mid-session, notice and second-process tests. Medium.
5. **CLI**: `pending`, `approve` (one and group) and `reject`, with golden files. Done when `test_pin_cli.py` and the new golden rows pass. Small to medium.
6. **Helpers and M0 tests**: `approve_all`, `approve=True`, `prime`, and the count updates. Done when the whole M0 suite passes unchanged in what it asserts, apart from entry counts and the `_meta`/`execution` change for older-era clients. Small.
7. **Startup tests**: `test_pin_startup.py`. Small.

**Stop for review.** Pins work for a session that starts after its decisions. Mid-session changes still need a restart.

### Stage 5: pins in motion and process lifetime

1. **The watch**: catch-up on a timer and before each list and call, handing entries to pin state, and serve's own fsync before acting on an adopted approval. Done when `test_pin_durability.py` passes. Small to medium.
2. **Mid-session drift**: coalesced upstream refreshes, change notices only when the exposed list changes, at most one per second, sticky drift and the cap. Done when the remaining `test_pins.py` tests pass. Small.
3. **Shutdown**: signal handlers, the shutdown path with its bound, and the shutdown error text. Done when `test_shutdown.py` passes on Linux and its POSIX skips are in place. Small.
4. **Rug-pull test servers**: `PROBE_RUGPULL`, the probe's `change` tool, `FakeUpstream.rug_pull()`. Done with the tests that use them. Small.
5. **Reference test and live check**: `test_reference_tools_hash_and_serve` run locally, and `live-check.sh` primed. Done when both pass locally (the live check only if the owner runs it). Small.
6. **Docs**: MANUAL-CHECK.md's first-run and rug-pull steps, QUICKSTART-DRAFT.md's first run and the threat model paragraph, STAGE4/STAGE5 notes and claims tables. Small.

**Stop for review,** then the owner's manual check.

## 13. Open questions, deviations and guesses

### Open questions for Matt

1. **Large integers in schemas.** Full RFC 8785 refuses integers outside plus or minus 2^53-1, so a tool whose schema says `"maximum": 9223372036854775807` can never be approved. Options: (a) keep it hidden and report it in `pending` (this spec); (b) canonicalize such integers by their exact digits, which is no longer pure RFC 8785; (c) convert them to floats, which lets two different bounds hash alike. Recommendation: (a) for M1a, and count how often it happens on the reference servers.
2. **Group approval covers only new tools.** A changed definition is exactly what a rug pull looks like, so it must be approved alone. After a server upgrade that changes many tools, that is tedious. Is that acceptable until M1b's reviewed group approval?
3. **A failed refresh hides the upstream's tools.** One slow `tools/list` (past `connect_timeout_seconds`) makes all of that upstream's tools vanish from that response, and they come back at the next good refresh. A grace period would keep them, at the cost of trusting an unknown state. Recommendation: keep it strict and see how often it happens in real sessions.
4. **Keep `serve --no-pins`?** Tests and the live check use the real approve path, so the flag exists only as a documented fallback. Anyone who can edit `.mcp.json` can add it, though `session.started` records it. Keep it, or drop it?
5. **Sticky drift.** A tool whose definition changes and changes back stays hidden until approved again. Agreed?
6. **The latest decision wins.** Rejecting a changed definition also unpins the earlier approved one. Agreed, or should a rejection of B leave an approval of A standing?
7. **A session-end entry.** Not in M1a (section 8). If M5's stats want session lengths, a later milestone could add `session.ended` with `session`, `reason` (`end of input`, `SIGINT` or `SIGTERM`) and `calls`, written best effort and never relied on. Wanted later, or never?
8. **The cap of 16 definitions per tool between decisions.** Right size?
9. **Interactive re-listing.** Whether interactive Claude Code re-lists after a listen notice is unverified. Stage 5's manual check would answer it with one step: approve while a session is open, and see whether `/mcp` shows the tool without reconnecting. Add that step?
10. **The refusal line the model sees.** `polarizer: <name> is not available: waiting for approval` tells the agent a person must act. Should it instead be M0's neutral `polarizer: no tool named <name>`, which reveals less?
11. **`actor: "person"`.** Is a fixed value right, or should it record the operating-system user name?
12. **SDK upgrades.** A form change means a new prefix and approving every tool again. Is a migration command (re-approve when the old and new forms of a stored copy agree on every served field) wanted before the first SDK upgrade, or is re-approval fine?
13. **An agent approving itself.** An agent with a shell can run `polarizer approve`. A cheap speed bump: refuse `approve` when stdin isn't a terminal, with a flag for scripts. An agent can pass the flag too, so it only stops accidents. Worth adding in M1a, or leave it to M3's approval page?
14. **Retrying a failed upstream.** After a failed refresh, M1a waits for the next client `tools/list` or upstream notice, which may never come, so a briefly slow upstream can stay hidden until the server restarts (section 6). A retry on a timer while the list is unknown, each bounded by `connect_timeout_seconds`, would bring it back without a restart. It can't help a lost connection, which M0 never reconnects. Add the timer in stage 5, or keep restart-only?

### Deviations from the existing documents, and guesses

1. **The hashed form** is the SDK's 2026-07-28 served form, not the plain `model_dump` of the m0-plan appendix. That removes `execution` and top-level schema nulls, which differ by era (section 2).
2. **The appendix's snake_case test is replaced.** `input_schema` on the wire fails the listing through the SDK client. verified-facts.md is corrected in the same round.
3. **`execution` is never served** in M1a, to any client. PROXY-SPEC.md, Results item 4, said only that 2026-07-28 clients lack it.
4. **"The served copy is a subset of the approved copy"** (appendix) is made exact: the stored copy minus `_meta`, renamed.
5. **Three new kinds:** `tool.seen`, `tool.unservable` and `upstream.refresh_failed`. **New fields:** `session` on `tool.drift`, `group` on `tool.approved`, and `pins` on `session.started`. The prompt named approval, rejection, drift and failed refresh. `tool.seen` exists so `pending` can work from the ledger alone, and `tool.unservable` so a hidden tool's reason is recorded, as section 5 requires.
6. **`pending` reads only the ledger and `defs/`,** and never starts upstreams. To see definitions before the first Claude Code session, run `serve` with stdin closed. The m0-plan appendix didn't say where `pending`'s definitions came from.
7. **`approve` takes the full definition hash** (`approve <prefix> <tool> <def_hash>`), where the appendix had `approve <prefix> <tool>`. That binds the approval to what the person read.
8. **Group approval** by a group id that binds the exact printed set. The appendix had none.
9. **No new exit codes.** Approve and reject refusals exit 2, and ledger statuses keep their codes.
10. **`approve` and `reject` check the ledger location** as `repair` does; `pending` doesn't, like `verify`.
11. **No re-list on each call.** The appendix left this to M1a planning. Calls use the last refresh, plus a ledger check.
12. **Upstream change notices are no longer republished as they arrive.** They trigger a refresh, and the client is told only when the exposed list changes, at most once per second. The appendix said "only if the set of tools it exposes changed".
13. **Changed is sticky** until a new decision. The appendix said a tool is exposed when its latest decision matches the live hash, with no stickiness.
14. **"Recorded once per pair"** is scoped to the time since the tool's latest decision, and capped at 16 distinct hashes.
15. **A failed refresh hides** the upstream's tools. STAGE2-NOTES.md guess 2 (keep the last list) stays true only under `--no-pins`.
16. **Hiding never waits for an fsync.** LEDGER-SPEC.md says security-state entries are fsynced before Polarizer acts on them. This spec reads that as applying to acts that expose. Hiding is done at once, and `tool.drift` and `tool.rejected` are still fsynced.
17. **`serve` fsyncs the ledger itself** before acting on an approval another process wrote.
18. **Signal handling is new.** M0 had no handlers. SIGINT already cancelled the main task through anyio's asyncio runner (`asyncio.Runner`'s SIGINT handler), which is why the Esc run recorded `cancelled`. SIGTERM is now handled the same way on POSIX.
19. **The shutdown error text** `polarizer shut down during the call` replaces `the client cancelled the call` (STAGE2-NOTES.md, guess 1) when shutdown caused the cancel. The outcome stays `cancelled`.
20. **The Esc facts** are treated as verified, as the owner confirmed. verified-facts.md said whether Esc itself caused the signals was unresolved, and it is updated in the same round.
21. **The stored copy** is written by `serve` when it first sees a hash, not by `approve`. That way `pending` can show a definition and `approve` can check it.
22. **m0-plan.md still says `mcp>=2.2,<3`** (build step 1 and corrections item 3), while `pyproject.toml` pins `mcp==2.2.0` and `mcp-types==2.2.0`. This spec relies on the exact pin. m0-plan.md is left as it is.
23. **The probe's mid-session change** is a `change` tool, matching `FakeUpstream`'s, rather than a list-count trigger.
24. **Test file names** (`test_defhash.py`, `test_pins.py`, `test_pin_cli.py`, `test_pin_startup.py`, `test_pin_durability.py`, `test_shutdown.py`) are this spec's choice.
25. **The two fixed hashes in section 10** were computed in this round with a throwaway script, outside the repo, on mcp 2.2.0, mcp-types 2.2.0 and rfc8785 0.1.4. Stage 4's test is the first committed check of them.
