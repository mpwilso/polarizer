# Export spec: spans, OCSF and anchors

This is the contract for three stretch milestones (docs/milestones.md, Stretch): OpenTelemetry spans from `polarizer serve`, `polarizer export --ocsf`, and signed checkpoints anchored in a commit trailer, checked by `polarizer verify --anchor`. They share one spec because each one takes facts out of the ledger to somewhere else (a trace backend, a SIEM, a git history) and the same rule governs all three: what leaves is ledger fields and Polarizer's own decisions, never a call's arguments or results. The chain format itself (docs/LEDGER-SPEC.md, Part 1) does not change: no entry kind, key or hash is added, and the reserved `sig` key stays reserved, because a checkpoint is signed outside the chain. The facts this spec relies on, and how each was checked, are in docs/verified-facts.md, Stretch export.

Built as one stage on Nov 7, 2026 (UTC), on branch `stretch-export` (docs/dev/STAGE14-NOTES.md). The guesses this spec makes are numbered in section 7.

## 1. The one rule

Nothing here exports, prints or sends:
- a call's arguments, or anything read from a side file. Only `args_commit`, the side file's commitment (docs/LEDGER-SPEC.md, Part 3), leaves, and it reveals nothing without the salted file;
- a call's result, or an upstream's error text;
- free text that can quote an argument: a hold's `reason` (it names argument paths, hosts and patterns), `call.refused`'s `reason`, `call.returned`'s `error`, and a person's deny `reason`.

What does leave: entry kinds, seq numbers, hashes, the chain id, session ids, exposed tool names, classes, rule names, decisions, outcomes, durations, counts, and the fixed texts Polarizer writes itself where a field holds only those (the three reasons of `hold.expired`).

## 2. Spans (OpenTelemetry)

### What the SDK gives, and what Polarizer changes

MCP SDK 2.2.0, the pinned version, installs `mcp.server._otel.OpenTelemetryMiddleware` in every low-level `Server`: one SERVER span per inbound message, named `<method>` or `<method> <name>`, with `mcp.method.name`, `mcp.protocol.version`, `jsonrpc.request.id`, and on `tools/call` `gen_ai.operation.name` `execute_tool` and `gen_ai.tool.name`, parented on the request's `_meta` `traceparent`. Its client side opens a CLIENT span named `MCP send <method> <name>` around each request to an upstream and injects `traceparent` into that request's `_meta`. Both use the global tracer provider from `opentelemetry-api`, which does nothing until something installs an SDK provider (docs/verified-facts.md, Stretch export).

The SDK's server middleware sets a failed span's status description to the exception's text, which for a `protocol-error` is the upstream's own message and for any other exception is `str(e)`, and it records the exception as a span event. Either can quote arguments. So `serve` replaces that one middleware with Polarizer's `polarizer.telemetry.SpanMiddleware`, which keeps the SDK's span name, kind, attributes and parenting exactly, and differs only on failure:

| What failed | SDK 2.2.0 | Polarizer |
|---|---|---|
| `MCPError` | `error.type` and `rpc.response.status_code` set to the code; status ERROR with the error's message | the same attributes; status ERROR with the description `error <code>` |
| a pydantic `ValidationError` | status ERROR, `Invalid request parameters` | the same |
| any other exception | `error.type` its class name; an exception event; status ERROR with `str(e)` | `error.type` its class name; no event; status ERROR with no description |
| a result with `isError` true | `error.type` `tool_error`; status ERROR | the same |

The SDK's client spans are kept as they are: they carry only the method, the request id and the upstream's tool name, and the SDK raises an upstream's error after the span has ended (docs/verified-facts.md, Stretch export).

### Polarizer's attributes

A `tools/call` span also gets these, set as the call goes. None holds an argument, a result or a reason.

| Attribute | Value |
|---|---|
| `polarizer.session` | serve's session id |
| `polarizer.chain_id` | the ledger's chain id |
| `polarizer.tool` | the exposed name as called, cut to 256 characters |
| `polarizer.decision` | `allow` (the rule function let it run), `hold`, `deny` (the deny tier), `not-routed` (no exposed tool by that name, or a hidden one), or `unrecordable` (the ledger could not record it) |
| `polarizer.class` | the tool's class, when it has one |
| `polarizer.rule` | the verdict's rule: the one that let it run, held it or refused it |
| `polarizer.hold` | the hold id, for a held call |
| `polarizer.hold.ending` | `allow`, `deny`, `expired`, `abandoned`, `cancelled`, `shutdown` or `too-many-holds` |
| `polarizer.outcome` | `call.returned`'s outcome, for a forwarded call |
| `polarizer.latency_ms` | `call.returned`'s `latency_ms` |
| `polarizer.ledger.sent_seq` | `call.sent`'s seq, for a forwarded call |
| `polarizer.ledger.hold_seq` | `hold.created`'s seq, for a held call |
| `polarizer.ledger.seq` and `polarizer.ledger.hash` | the seq and hash of the call's last entry: `call.returned` for a forwarded call, `call.refused` for one never forwarded |

The span's own start and end times are its duration: from the request arriving to the answer, so a held call's span lasts as long as its hold. `polarizer.latency_ms` is the upstream's part, on the monotonic clock.

`polarizer.ledger.hash` is what ties a span to the chain: `polarizer verify` proves the entry with that seq and hash is in an intact chain, and a checkpoint (section 4) proves the chain still extends a point anchored in git.

A planted call (docs/MEASURE-SPEC.md, section 11) has no client request, so it has no span. Every other request (`tools/list`, `subscriptions/listen`, `initialize`) gets the SDK's attributes only.

### Turning export on

Export is off by default: with nothing below set, `serve` installs no tracer provider and its spans go to whatever the global provider is, which is the API's no-op unless something else installed one (for example `opentelemetry-instrument`).

- **Config:** a top-level `[telemetry]` table with one key, `traces`: `"off"` (the default), `"otlp"` or `"console"`. Any other key or value is a config error, exit 2:
  ```
  polarizer.toml: "telemetry" must be a table
  polarizer.toml: [telemetry] unknown key "<key>"
  polarizer.toml: [telemetry] traces must be "off", "otlp" or "console"
  ```
- **The standard environment variables:** `OTEL_SDK_DISABLED` set to `true` (any case) turns export off whatever else says. Otherwise, when the config's `traces` is `"off"`, `OTEL_TRACES_EXPORTER` turns it on: a comma-separated list in which `otlp` and `console` are used, `none` and empty items are ignored, and any other name gets one stderr line, `polarizer: warning: OTEL_TRACES_EXPORTER names <name>, which Polarizer does not export to; ignored`. A config `traces` other than `"off"` wins over `OTEL_TRACES_EXPORTER`.
- **What on means:** `serve` builds an SDK `TracerProvider` with a resource whose `service.name` is `polarizer` (unless `OTEL_SERVICE_NAME` or `OTEL_RESOURCE_ATTRIBUTES` sets one) and `service.version` Polarizer's version, adds one `BatchSpanProcessor` per exporter, and installs it as the global provider, so the SDK's own client spans go with Polarizer's. `otlp` is the SDK's OTLP over HTTP exporter, which reads the standard `OTEL_EXPORTER_OTLP_*` variables (by default `http://localhost:4318/v1/traces`). `console` is the SDK's console exporter writing to stderr, never stdout, which is the protocol channel.
- **The start line:** `polarizer: exporting spans to <names> (from <source>)`, where `<names>` is `otlp`, `console` or `otlp, console` and `<source>` is `polarizer.toml` or `OTEL_TRACES_EXPORTER`.
- **Without the packages:** the exporters are in the optional `otel` extra (`opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`, both pinned). When export is on and they can't be imported, `serve` writes `polarizer: warning: span export is on (from <source>) but the otel extra is not installed; no spans are exported` and carries on. Spans are observability, not a control: the ledger is the record.
- **Shutdown:** after the writer is closed, `serve` flushes the provider for at most 1 s, then exits as before. A collector that isn't running costs at most that second.

### A local dashboard

One pinned command runs Jaeger's all-in-one image, which takes OTLP over HTTP on 4318 and shows traces on 16686:

```
docker run --rm -p 16686:16686 -p 4318:4318 jaegertracing/jaeger:2.23.0
```

Then set `[telemetry] traces = "otlp"` (or export `OTEL_TRACES_EXPORTER=otlp` in the environment Claude Code gives serve), install Polarizer with the `otel` extra (for example `uv tool install "polarizer[otel] @ <wheel URL>"`, with the wheel URL from docs/SETUP.md; this line was not run), and open `http://localhost:16686`, service `polarizer`. Jaeger is not a dependency of Polarizer, and no test runs it. Without Docker, `traces = "console"` writes each span to serve's stderr as JSON.

## 3. OCSF export

```
polarizer export --ocsf (--config <absolute path> | --ledger-dir <absolute path>)
```

### The class

OCSF 1.9.0, the version schema.ocsf.io served on Nov 7, 2026 (docs/verified-facts.md, Stretch export). The class is **API Activity** (`class_uid` 6003, category Application Activity, 6): an MCP tool call is a call to an API that a control in the path let through, held or refused, and OCSF has no class for agent tool calls. The **Security Control** profile adds what a policy decision needs: `action_id` (Allowed, Denied, Other), `disposition_id` (Allowed, Approved, Blocked, Delayed, Dropped, Rejected) and `policy`. `activity_id` is 99, Other, because a tool call is not one of the class's create, read, update and delete; `activity_name` is the ledger kind.

### What is exported

One event per ledger entry of these seven kinds, in seq order: `call.sent`, `call.returned`, `call.refused`, `hold.created`, `hold.decided`, `hold.expired` and `hold.abandoned`. Every other kind (sessions, pins, policy, taint, drills, repairs) is not a call event and is left out; the stderr summary counts them.

### Every event

| OCSF attribute | Value | From |
|---|---|---|
| `class_uid`, `class_name` | 6003, `API Activity` | fixed |
| `category_uid`, `category_name` | 6, `Application Activity` | fixed |
| `activity_id`, `activity_name` | 99, the kind | `kind` |
| `type_uid`, `type_name` | 600399, `API Activity: Other` | fixed |
| `severity_id`, `severity` | 1, `Informational` | fixed |
| `time` | milliseconds since the Unix epoch; 0 when `ts` is not RFC 3339 with milliseconds and `Z` | `ts` |
| `metadata.original_time` | the entry's `ts`, as written | `ts` |
| `metadata.version` | `1.9.0` | fixed |
| `metadata.product` | `{"name": "Polarizer", "version": <the exporting Polarizer's version>}` | fixed |
| `metadata.profiles` | `["security_control"]` | fixed |
| `metadata.uid` | the entry's hash | `hash` |
| `metadata.sequence` | the entry's seq | `seq` |
| `metadata.event_code` | the kind | `kind` |
| `metadata.log_version` | `"1"` | `v` |
| `metadata.correlation_uid` | the hold id, for every event of a held call | `data.hold`, or joined |
| `src_endpoint` | `{"svc_name": "mcp"}`: the class requires it, and every call reaches serve over MCP stdio from the local client | fixed |
| `api.operation` | the exposed tool name; `unknown` when a join finds nothing | `data.tool`, or joined |
| `api.service.name` | the upstream prefix, the part of the name before the first `__`, when it has one | `data.tool`, or joined |
| `api.request.uid` | the call's `args_commit`, which ties together every event of one call | `data.args_commit`, or joined |
| `actor.session.uid` | serve's session id (`actor.user` instead for `hold.decided`) | `data.session`, or joined |
| `actor.application` | `{"name": <client_name>, "version": <client_version>}` from that session's `session.client`, when it has one | joined |
| `unmapped.chain_id` | the chain id | the genesis entry |
| `unmapped.prev` | the previous entry's hash | `prev` |

"Joined" means read from an earlier entry: `call.returned` takes its tool, session, hold and `args_commit` from the `call.sent` at its `call_seq`; the `hold.*` endings and a `call.refused` with `hold` take theirs from the hold's `hold.created`. An event never gets a field it has no source for, except `api.operation`, which the class requires.

### Each kind's data

Every key of every exported kind's `data` is in this table, mapped or named as not exported. `tests/test_export.py` checks the table against the code and against the fixtures.

| Kind | Key | OCSF attribute |
|---|---|---|
| `call.sent` | `session` | `actor.session.uid` |
| `call.sent` | `tool` | `api.operation`, `api.service.name` |
| `call.sent` | `args_commit` | `api.request.uid` |
| `call.sent` | `meta_dropped` | `unmapped.meta_dropped` |
| `call.sent` | `client_call_id` | `unmapped.client_call_id` |
| `call.sent` | `hold` | `metadata.correlation_uid` |
| `call.sent` | `allowed_by` | `policy.name` |
| `call.returned` | `call_seq` | `unmapped.call_seq` |
| `call.returned` | `outcome` | `status_id`, `status`, `status_code` |
| `call.returned` | `latency_ms` | `duration` |
| `call.returned` | `result_bytes` | `unmapped.result_bytes` |
| `call.returned` | `error` | not exported (upstream text) |
| `call.returned` | `code` | `api.response.code`; `unmapped.code` when it was recorded as a string |
| `call.refused` | `session` | `actor.session.uid` |
| `call.refused` | `tool` | `api.operation`, `api.service.name` |
| `call.refused` | `reason` | not exported (can quote arguments) |
| `call.refused` | `hold` | `metadata.correlation_uid` |
| `call.refused` | `rule` | `policy.name` |
| `hold.created` | `session` | `actor.session.uid` |
| `hold.created` | `hold` | `metadata.correlation_uid` |
| `hold.created` | `tool` | `api.operation`, `api.service.name` |
| `hold.created` | `args_commit` | `api.request.uid` |
| `hold.created` | `class` | `unmapped.class` |
| `hold.created` | `class_from` | `unmapped.class_from` |
| `hold.created` | `rule` | `policy.name` |
| `hold.created` | `reason` | not exported (can quote arguments) |
| `hold.created` | `timeout_seconds` | `unmapped.timeout_seconds` |
| `hold.created` | `plant_commit` | `unmapped.plant_commit` |
| `hold.decided` | `hold` | `metadata.correlation_uid` |
| `hold.decided` | `args_commit` | `api.request.uid` |
| `hold.decided` | `decision` | `action_id`, `action`, `disposition_id`, `disposition` |
| `hold.decided` | `actor` | `actor.user.name` |
| `hold.decided` | `reason` | not exported (a person's free text) |
| `hold.decided` | `terminal` | `unmapped.terminal` |
| `hold.decided` | `elapsed_ms` | `duration` |
| `hold.expired` | `session` | `actor.session.uid` |
| `hold.expired` | `hold` | `metadata.correlation_uid` |
| `hold.expired` | `reason` | `status_detail` (one of three fixed texts) |
| `hold.abandoned` | `session` | `actor.session.uid` |
| `hold.abandoned` | `hold` | `metadata.correlation_uid` |
| `hold.abandoned` | `held_by` | `unmapped.held_by` |

### Decisions and outcomes

| Event | `action_id`, `action` | `disposition_id`, `disposition` |
|---|---|---|
| `call.sent` without `hold` | 1, `Allowed` | 1, `Allowed` |
| `call.sent` with `hold` | 1, `Allowed` | 8, `Approved` |
| `call.returned` | as its `call.sent` | as its `call.sent` |
| `call.refused` | 2, `Denied` | 2, `Blocked` |
| `hold.created` | 99, `Held` | 14, `Delayed` |
| `hold.decided` allow | 1, `Allowed` | 8, `Approved` |
| `hold.decided` deny | 2, `Denied` | 25, `Rejected` |
| `hold.expired` | 2, `Denied` | 6, `Dropped` |
| `hold.abandoned` | 2, `Denied` | 6, `Dropped` |

`call.returned`'s outcome: `ok` is `status_id` 1, `Success`; `cancelled` is 99, `Cancelled`; every other outcome is 2, `Failure`. `status_code` is the outcome itself. `policy` is `{"name": <rule>}` and appears only where the entry has a rule (`allowed_by` or `rule`).

### Output

stdout gets one JSON object per line: compact, keys sorted, UTF-8, each line ending in `\n`. Then stderr gets one line: `polarizer: exported <e> events from <n> entries (OCSF 1.9.0, API Activity); <k> entries of other kinds left out`.

`export` only reads, exactly as `verify` does (docs/LEDGER-SPEC.md, Part 2, The lock), and reads no side file. It exports only an intact ledger. Its stdout is data, so everything else goes to stderr:

| Situation | stderr | Exit |
|---|---|---|
| exported | the summary line | 0 |
| no `--ocsf` | `polarizer: export needs --ocsf (the only format so far)` | 2 |
| no ledger | `polarizer: no ledger at <dir>` | 2 |
| not intact | `polarizer: <verify's first line>; export needs an intact ledger` | that status's code |
| locked | `polarizer: ` and the `locked:` line | 7 |
| a v0 ledger | `polarizer: ledger at <dir> is v0 (Parallax's format); export reads only v1` | 3 |

## 4. Checkpoints and anchors

### The checkpoint

A checkpoint names one position of one chain: (chain id, size, head), where size is the number of entries and head is the hash of the entry at seq size - 1. LEDGER-SPEC.md, Genesis and chain id, says why that names exactly one chain.

It is signed with HMAC-SHA256, from Python's standard library:

    sig = hex(hmac_sha256(key, b"POLARIZER-CHECKPOINT/1\n" + "<chain>:<size>:<head>".encode("ascii")))

and written as one commit trailer line:

    Polarizer-Checkpoint: <chain>:<size>:<head>:<sig>

`<chain>` is 32 lowercase hex characters, `<size>` a decimal integer from 1 with no leading zero, and `<head>` and `<sig>` 64 lowercase hex characters each.

**The key** is `<ledger_dir>/checkpoint.key`: 32 random bytes from `secrets`, stored as 64 lowercase hex characters and one newline, mode 0600 in the 0700 directory. `checkpoint` creates it, exclusively, the first time it runs, and says so on stderr. Any other content is not a key. On POSIX, `checkpoint` refuses a key that its group or others can read or write.

**What it proves.** The signature shows the checkpoint was made by someone holding the key. The anchor's strength is the commit: once a commit with the trailer is pushed, nobody with only the machine can change what it says, so a later ledger that no longer extends it is caught. It does not stop someone who can read the key and write the ledger from anchoring a forged ledger in a new commit; docs/LIMITS.md says so.

### polarizer checkpoint

```
polarizer checkpoint (--config <absolute path> | --ledger-dir <absolute path>)
```

1. Refuse a `ledger_dir` inside a forbidden path (exit 2), and warn inside another git working tree, as `repair` does, because it may create the key.
2. Read the ledger and `ledger.head` as `verify` does, and verify them. Anything but intact is refused.
3. Read the key, or create it.
4. Print the trailer line on stdout, for the last entry of the ledger (not `ledger.head`, which can lag).

It never makes a commit, runs git, or appends to the ledger. The person puts the line in a commit message, for example with `git commit --trailer "$(polarizer checkpoint --config ...)"`.

| Situation | stdout | stderr | Exit |
|---|---|---|---|
| made | `Polarizer-Checkpoint: <chain>:<size>:<head>:<sig>` | nothing, or `polarizer: created the checkpoint key <path>; keep a copy: verify --anchor needs it` the first time | 0 |
| not intact | nothing | `polarizer: <verify's first line>; a checkpoint needs an intact ledger` | that status's code |
| no ledger | nothing | `polarizer: no ledger at <dir>` | 2 |
| a v0 ledger | nothing | `polarizer: ledger at <dir> is v0 (Parallax's format); a checkpoint needs v1` | 3 |
| locked | nothing | `polarizer: ` and the `locked:` line | 7 |
| a bad key | nothing | `polarizer: <path> is not a checkpoint key (64 lowercase hex characters and a newline)` | 2 |
| a key others can use | nothing | `polarizer: <path> must be readable and writable only by its owner (mode 0600); it is <mode>` | 2 |
| inside a forbidden path | nothing | the refusal line from PROXY-SPEC.md, Startup | 2 |

### polarizer verify --anchor

```
polarizer verify (--config <absolute path> | --ledger-dir <absolute path>) [--args] [--anchor <trailer or git ref>]
```

**The argument** is read in this order:
1. a line starting `Polarizer-Checkpoint:`, whose value follows after any spaces;
2. a bare value `<chain>:<size>:<head>:<sig>`;
3. otherwise a git ref, in the current directory's repository. It must not start with `-`, and must hold no control character. Polarizer runs `git -c log.showSignature=false log --max-count=10000 --format=%H%x00%B%x00 <ref> --` (the `-c` keeps a `log.showSignature` setting from printing a signature check before each hash) and reads every line `Polarizer-Checkpoint: <value>` with a well-formed value in those commits. Each commit field must be 40 or 64 lowercase hex characters, or git's output is refused. Of those checkpoints it checks **the furthest anchored position**: once the chain is verified, the one with the largest size among those of the ledger's own chain id, or among all of them when none is of that chain (which then reports another chain); the newest commit's on a tie, and within one message the last line's. So a newer checkpoint at a smaller size does not hide a rewind past an older, larger one.

A value that is not well formed, a ref git can't read, git missing, git output that isn't a commit hash, or no trailer in those commits is a usage error, one line on stderr, exit 2:

```
polarizer: --anchor: not a checkpoint; expected Polarizer-Checkpoint: <chain>:<size>:<head>:<sig>
polarizer: --anchor <ref>: not a git ref Polarizer will pass to git
polarizer: --anchor <ref>: git is not available: <the operating system's message>
polarizer: --anchor <ref>: git log failed: <git's first line, through safe()>
polarizer: --anchor <ref>: git log failed: not a commit hash: <the field, through safe(), cut to 80>
polarizer: --anchor <ref>: no Polarizer-Checkpoint trailer in the <n> commits from <ref>
```

**The check** runs after the chain's lines (and the `--args` lines), only when the chain is `intact`. It reads `checkpoint.key` read-only and without blocking; `verify` still writes nothing. Anything in the key's place that is not a regular file (a directory, a FIFO), or that can't be read, is a bad key, not a wait or a crash. Its first line is always:

    anchor: checkpoint of chain <chain> at seq <size-1>, head <head>

with `, from commit <40 hex>` added when it came from a git ref. Then exactly one of:

| Result | Line | Exit |
|---|---|---|
| no key | `anchor: no key at <path>; cannot check the signature` | 10 |
| a bad key | `anchor: <path> is not a checkpoint key; cannot check the signature` | 10 |
| bad signature | `anchor: signature does not match; this ledger's key did not make the checkpoint` | 10 |
| another chain | `anchor: other chain: the ledger's chain is <chain>` | 9 |
| rewound | `anchor: rewound: the ledger ends at seq <q>, before the anchored seq <s>` | 9 |
| rewritten | `anchor: rewritten: the ledger's entry at seq <s> is <hash>, not the anchored head` | 9 |
| extends | `anchor: intact: the ledger extends the checkpoint by <n> entries` | 0 |

The signature is checked first, with a constant-time compare, and nothing else is reported for a checkpoint whose signature fails. When the chain isn't intact the anchor line is `anchor: not checked; the ledger is not intact`, and the exit code is the chain's. With `--args`, the exit code is the first non-zero of the chain's, the side files' (8) and the anchor's.

A rewound ledger whose `ledger.head` was rewound with it, or deleted, verifies `intact` on its own; the anchor is what reports it. A ledger whose `ledger.head` still records the later seq is `truncated` (exit 6) before the anchor is read.

### Conformance

`conformance/reference_anchor.py` is a second, standard-library-only anchor check (hashlib, hmac, json, re, sys), as `reference_verify.py` is for the chain. `tools/make_fixtures.py` writes the anchor fixtures under `conformance/anchors/`, signing with the reference's `sign`, never Polarizer's, and their expected results in `conformance/expected.json` under `anchors`. Both checks agree on every case: extends, at the head, rewound, rewritten, another chain, a flipped signature and another key; and, for several trailers newest first as a git ref gives them, the furthest position (sizes 5, 13, 5), a rewind behind a newer and smaller checkpoint, another chain's larger checkpoint passed over, and only another chain's checkpoints. `reference_anchor.py` takes the trailers newest first and picks the furthest position itself. The anchor ledgers are intact chains, listed under `fixtures` too, so both verifiers check them as well.

## 5. Exit codes

`verify --anchor` adds two codes to docs/LEDGER-SPEC.md, Statuses: 9, the ledger does not extend the checkpoint, and 10, the checkpoint's signature could not be checked or does not match. `checkpoint` and `export` use only codes already in that table.

## 6. Tests

| Test | Claim | Suite |
|---|---|---|
| `tests/test_telemetry.py` | With an in-memory exporter: a forwarded call's span has every attribute of section 2 with the ledger's own seq and hash; a held then allowed call, a held then denied call, a deny-tier refusal and an unknown name each get theirs; no span holds an argument value, a result, a reason or an upstream error message; the SDK's middleware is replaced and the client's `traceparent` still parents the span and reaches the upstream; the switch's every combination of config and environment; the console exporter writes to stderr only; the OTLP exporter posts to a local stub; a missing extra gives the warning and serve runs. | Default |
| `tests/test_export.py` | The golden files `export_ocsf_<fixture>.jsonl` for four conformance fixtures, byte for byte; the mapping table here equals the code's; the fixtures use every key of every exported kind; each mapped value lands where the table says and no unexported value appears anywhere; every attribute name is one of OCSF 1.9.0's for the class and its objects; each refusal line and exit code; `export` writes nothing. | Default |
| `tests/test_anchor.py` | `checkpoint`'s trailer, its key's creation, mode and refusals; `verify --anchor` with a trailer, a bare value and a git ref (a temporary repository whose commits carry trailers), each result line and exit code, golden files `verify_anchor_<case>.txt`; both anchor checks agree on every fixture; `verify --anchor` writes nothing. (0.6.1) From a git ref, the furthest position is checked (sizes 5, 13, 5), a newer smaller checkpoint doesn't hide a rewind, another chain's checkpoint is passed over; signed commits under `log.showSignature=true` (POSIX, with ssh-keygen) and a commit field that isn't a hash; a directory as the key, and a FIFO (POSIX, in a process with a time limit). | Default |

## 7. Deviations

Each is a guess this spec made where the brief left a choice, recorded for the owner.

1. **One spec for three milestones,** a new file, because each takes ledger facts elsewhere under one rule (section 1), and putting them in LEDGER-SPEC.md would mix Polarizer's own tooling into the portable chain format. LEDGER-SPEC.md and PROXY-SPEC.md point here.
2. **The SDK's server middleware is replaced, not extended** (section 2), because its failure status carries upstream text and exception messages. Polarizer's keeps the SDK's name, kind, attributes and parenting, so "the SDK's own middleware does most of it" holds for everything but the failure text. The client-side spans are the SDK's, unchanged.
3. **The exporters are an optional extra** (`otel`), not a dependency: the OTLP exporter brings protobuf, requests and eight other packages that a proxy which exports nothing by default does not need. Tests install the extra through the dev group.
4. **A missing extra warns and serves** instead of refusing to start, because spans are observability and the ledger is the record.
5. **The config key is `[telemetry] traces`,** one key with three values; the endpoint, headers and protocol come only from the standard `OTEL_EXPORTER_OTLP_*` variables, which the SDK's exporter reads itself. Only OTLP over HTTP is supported.
6. **The config wins over `OTEL_TRACES_EXPORTER`,** and `OTEL_SDK_DISABLED=true` wins over both, as the variable's name says.
7. **The span's seq and hash are the call's last entry's,** with `call.sent`'s and `hold.created`'s seq as separate attributes: the last entry is the one whose hash covers everything the call did.
8. **The dashboard is Jaeger 2.23.0 in Docker,** pinned by tag, documented and never run by a test or CI.
9. **API Activity with the Security Control profile** (section 3) rather than a finding class: a tool call is activity, and the profile's action and disposition carry the decision. `activity_id` is Other.
10. **One event per ledger entry,** not one per call, so each event maps to one hash and seq a person can check with `verify`; events of one call share `api.request.uid` (the `args_commit`) and, when held, `metadata.correlation_uid` (the hold id).
11. **Only the seven call kinds are exported.** Pins, sessions, taint and drills are not tool calls; a later spec can map them to other classes.
12. **Joined fields** (section 3) come from earlier entries, so a `call.returned` or a hold's ending names its tool and session, as a SIEM needs.
13. **Free text is never exported** (section 1), even where it holds no argument in a given entry, because reasons can quote argument paths, hosts and matched patterns. `hold.expired`'s reason is exported because it is one of three fixed texts.
14. **Polarizer-only fields go in `unmapped`,** which OCSF defines for source-specific attributes, rather than stretching an OCSF attribute to fit.
15. **`src_endpoint` is a constant** `{"svc_name": "mcp"}`, since the class requires it and a stdio client has no address.
16. **`time` is 0 when `ts` doesn't parse,** since the class requires it and the ledger does not check `ts`; `metadata.original_time` keeps the text.
17. **HMAC with a key under the ledger directory,** not a public-key signature, per the brief: no new crypto library, and the commit, not the signature, is what an attacker on the machine can't rewrite.
18. **`verify --anchor <ref>` walks back** from the ref over at most 10,000 commits, so `--anchor HEAD` reports an anchored position even when the latest commit has none. (Changed for 0.6.1, below: it no longer stops at the newest commit carrying a trailer.)
19. **Two new exit codes,** 9 and 10, so a script can tell a ledger that moved from a checkpoint it can't trust.
20. **The anchor is not checked on a ledger that isn't intact,** as `--args` isn't, because the chain's own status already says more.
21. **`checkpoint` signs the ledger's last entry,** not `ledger.head`'s, which lags behind entries that aren't security state.
22. **The checkpoint key is created by `checkpoint`,** never by `serve`, so a ledger that is never anchored has no key.

The review fixes of Nov 8, 2026 (UTC), for 0.6.1:

23. **The furthest anchored position, per chain,** not the newest trailer. The review showed that commits anchoring sizes 5, 50 and 5 checked the last 5, so a ledger rewound to 5 and anchored again passed. The largest size wins because a chain only grows: every earlier checkpoint of an intact chain is a prefix of the furthest one. It is the largest of the ledger's own chain, so one repository can anchor several ledgers; only when none is of that chain is the largest of all checked, to report another chain. A forged trailer with a larger size and a bad signature is checked and reported (exit 10) rather than skipped, since skipping it would hide an attempt.
24. **The chosen checkpoint is printed after the chain's lines,** as before, but chosen only then, since it depends on the chain id. A malformed argument is still a usage error before any output.
25. **`log.showSignature` is turned off for the one `git log`,** with `-c` rather than `--no-show-signature`, which older git lacks, and each commit field is checked as a hash, so other settings that add output are refused rather than misread.
26. **The key is opened with `O_NONBLOCK` and must be a regular file,** and any read error is a bad key: a directory gave a traceback on Linux, and a FIFO made `verify` wait forever.
