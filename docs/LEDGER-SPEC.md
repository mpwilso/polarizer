# Ledger spec, version 1

This is the format of Polarizer's ledger: an append-only, hash-chained log of who decided what and what the agents touched. Part 1 is the chain format, written so another stdlib-only Python tool could adopt it. Part 2 covers how Polarizer writes, verifies and repairs it. Part 3 covers the files that sit beside it. Facts this spec relies on, and how they were checked, are in docs/verified-facts.md.

## Part 1: the chain

### Entries

The ledger is a UTF-8 file with one entry per line. Each line is the entry's canonical bytes followed by `\n`. A v1 entry is a JSON object with exactly these seven keys:

| Key | Value |
|---|---|
| `v` | the integer 1 |
| `seq` | integer; 0 for the genesis entry, then up by one per entry |
| `ts` | UTC time as RFC 3339 with milliseconds and `Z`, e.g. `2026-10-02T14:02:11.123Z` |
| `kind` | dotted lowercase name, e.g. `call.sent` |
| `data` | object; its contents depend on `kind` |
| `prev` | the previous entry's `hash`; 64 zeros for the genesis entry |
| `hash` | 64 lowercase hex characters, defined below |

Any other top-level key makes the entry invalid. The key `sig` is reserved for a later version, which will exclude it from the hash the way `hash` is excluded. Who acted and why go in `data.actor` and `data.reason` when an entry has them.

Verify does not require `ts` to be in order, because clocks move. The chain does not prove when anything happened.

`ts` is the wall clock when the entry is made. `latency_ms` on `call.returned` is measured on a monotonic clock (PROXY-SPEC.md). The two are not comparable: on WSL2 the wall clock was seen stepping back about 1.1 s during a 30 s run, so the gap between a call's two `ts` values can differ from its `latency_ms` by that much (verified-facts.md, Wall clock and monotonic clock on WSL2).

### Canonical bytes and the subset

Canonical bytes are RFC 8785 (JSON Canonicalization Scheme). Every value in an entry must be inside this subset:

- objects whose keys are ASCII;
- integers from -(2^53-1) to 2^53-1, with no floats, NaN or infinities;
- strings that are valid Unicode with no lone surrogates. Any other character is allowed, including control characters, astral characters, U+2028 and U+FEFF;
- `true`, `false`, `null` and arrays.

On this subset, these two produce identical bytes:

    rfc8785.dumps(x)
    json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

That property is the point of the subset. A stdlib-only writer can produce v1 entries without the `rfc8785` package. Polarizer uses the package, and a differential test keeps the property true.

Two rules exist because the encoders would otherwise disagree:

- **ASCII keys.** RFC 8785 sorts keys by UTF-16 code units and Python by code points, which differ for a key pair such as U+E000 and U+1F600.
- **The integer range.** The stdlib serializes 2^53 without complaint, so writers must check the range themselves.

A line is at most 16 KiB, including the newline. Writers refuse larger entries, and cut free text from upstreams (such as error messages) to 1 KiB before it enters `data`.

### Hash

    hash = hex(sha256(b"LEDGER-SPEC/1\n" + canonical(entry without "hash")))

The prefix keeps ledger hashes apart from any other sha256 the same tool computes.

### Genesis and chain id

The first entry has `seq` 0, `kind` `ledger.genesis`, `prev` of 64 zeros, and `data.chain_id`: 32 lowercase hex characters from a cryptographic random source.

The chain id is bound because it sits inside the genesis entry's hashed body, and every later entry reaches genesis through `prev`. A checkpoint can therefore name exactly one chain as (chain id, head seq, head hash), with size = head seq + 1. Later entries don't repeat the chain id. A second `ledger.genesis` anywhere is invalid.

### Kinds used by Polarizer

| Kind | `data` |
|---|---|
| `ledger.genesis` | `chain_id` |
| `session.started` | `session` (16 random hex characters, one per Polarizer process), `polarizer_version`, `config_sha256` |
| `session.client` | `session`, `client_name`, `client_version`, `protocol_version`: written once, on the first request that carries client information. In 2026-07-28 that's every request's `_meta`; in the older era it's `initialize`. |
| `upstream.connected` | `prefix`, then either `protocol_version`, `tools` and `skipped_tools` (a list of names left out), or `error` |
| `call.sent` | `session`, `tool` (the full exposed name, `<prefix>__<tool>`), `args_commit`, `meta_dropped` (a list of strings), `client_call_id` (a string, or `null` when the client sent none). (M2a) A held call that was allowed also has `hold`, the hold id; a call never held has no `hold` key. (M2a) `allowed_by`: the rule that let the call run without a hold (`holds-off`, `inside-roots`, `local-read` or `open-world`), or `hold` for a call allowed from a hold; a call that never passed through the rule function has no `allowed_by` key (HOLD-SPEC.md, section 5). |
| `call.returned` | `call_seq`, `outcome` (`ok`, `tool-error`, `protocol-error`, `transport-error`, `cancelled`, `unsupported`; defined in PROXY-SPEC.md), `latency_ms`, `result_bytes`, plus `error` (one line) for every outcome but `ok`, and `code` for `protocol-error` |
| `call.refused` | `session`, `tool` (the name as called), `reason`: a call Polarizer never forwarded. M0: a name that matches no listed tool. M1a adds hidden tools called by name, with the reasons in PIN-SPEC.md (section 4). M2a adds held calls that end without being forwarded, with `hold` (the hold id), and calls refused because 16 holds already wait, without it (HOLD-SPEC.md, section 5). |
| `tool.approved` | `upstream`, `tool`, `def_hash`, `actor`, `group` (null, or the group id of a group approval) (M1a; PIN-SPEC.md, section 3) |
| `tool.rejected` | `upstream`, `tool`, `def_hash`, `actor`, `reason` (M1a) |
| `tool.drift` | `session`, `upstream`, `tool`, `approved_hash`, `live_hash` (M1a) |
| `tool.seen` | `session`, `upstream`, `tool`, `def_hash`: a live definition no decision covers (M1a) |
| `tool.unservable` | `session`, `upstream`, `tool`, `def_hash` (or null), `problem`: a tool hidden because it can't be hashed, its definition is larger than 262144 bytes, or its stored copy fails its check (M1a) |
| `upstream.refresh_failed` | `session`, `prefix`, `trigger` (`client-list`, `upstream-notice` or `connection-lost`), `error` (M1a) |
| `policy.loaded` | `session`, `holds` (`on` or `off`), `policy_sha256`, `classified`, `workspace_roots`, `hold_timeout_seconds`: the policy a `serve` process runs with, written after `session.started`; fsynced, with `ledger.head` updated (M2a; HOLD-SPEC.md, section 5) |
| `hold.created` | `session`, `hold` (16 random lowercase hex characters), `tool`, `args_commit`, `class` (or null), `class_from` (`config`, `annotations` or null), `rule`, `reason`, `timeout_seconds`: a call held for a person; not fsynced (M2a) |
| `hold.decided` | `hold`, `args_commit`, `decision` (`allow` or `deny`), `actor`, `reason` (or null): a person's decision on a hold; fsynced, with `ledger.head` updated, before the deciding command reports success (M2a) |
| `hold.expired` | `session`, `hold`, `reason`: a hold the holding process ended by timeout, the client's cancel or shutdown; not fsynced (M2a) |
| `hold.abandoned` | `session`, `hold`, `held_by`: an open hold whose process has ended, recorded by a later `serve` at start; not fsynced (M2a) |
| `ledger.repaired` | `bytes`, `sha256`, `file` |
| `ledger.head_rebuilt` | `from_seq`, `from_hash` (the verified chain head it was rebuilt from) |

New kinds can be added without changing the version, as long as their `data` stays inside the subset.

### Statuses

`verify` reads the ledger and its `ledger.head` (Part 2) and reports exactly one status, with a line number and seq where they apply. Every exit code Polarizer's commands use is in this table; none is shared by two meanings except where a row says so.

| Status | Exit code | What triggers it |
|---|---|---|
| `intact` | 0 | Every rule holds. |
| `tampered` | 1 | A line's `seq`, `prev` or `hash` doesn't match, or `ledger.head`'s hash differs from the entry at its seq. |
| (usage error) | 2 | Bad arguments, an unreadable file, a config error, or no ledger. `serve` and `repair` also exit 2 when they refuse a `ledger_dir` inside a forbidden path (Part 2). |
| `invalid` | 3 | An entry breaks a structure rule (an extra or missing key, a float, an integer out of range, a non-ASCII key, a lone surrogate, an oversize line, a missing or second genesis, a mix of v0 and v1), or `ledger.head` isn't a valid head record or names another chain. `serve` and `repair` also exit 3 when `ledger_dir` holds a v0 ledger. |
| `not canonical` | 4 | The entry parses and hashes correctly, but its stored bytes aren't its canonical bytes (v1) or its line form (v0). |
| `torn tail` | 5 | There are bytes after the last newline. |
| `truncated` | 6 | `ledger.head` records a later seq than the ledger's last entry. |
| (locked) | 7 | The ledger lock was still held after 2 seconds; nothing was checked (Part 2). |
| (side file tampered) | 8 | `verify --args` only: a side file's bytes don't match its name (Part 3). |

**Precedence.** The first of these that applies decides the status. Both verifiers implement exactly this order.
1. **Per-line problems**, in file order. Within one v1 line: structure (`invalid`), then `seq`, then `prev`, then `hash` (`tampered`), then canonical bytes (`not canonical`). Structure means the line is valid UTF-8 and JSON, is an object of the same version as the first line, is at most 16 KiB, has exactly the v1 keys with the right types, stays inside the subset, and is the one genesis at line 1. v0 lines have their own order (below).
2. **`ledger.head` problems**, checked only when every complete line passes: an invalid head file (`invalid`), then a head hash that doesn't match the entry at the head's seq (`tampered`), then a head seq past the last entry (`truncated`). A head behind the last entry is normal, because the head moves only on security-state entries. A missing `ledger.head` is not a problem for `verify` (PROXY-SPEC.md shows the extra line it prints).
3. **A torn tail** (`torn tail`).
4. Otherwise `intact`.

A line that isn't valid JSON is `invalid`, not a torn tail, unless it is the bytes after the last newline. Four conformance fixtures pin the order with two faults each: `extra_key_and_bad_hash` (`invalid`), `bad_seq_and_bad_hash` (`tampered`, reason `seq is ...`), `torn_tail_and_truncated` (`truncated` wins) and `tampered_line_and_head_mismatch` (the line wins).

### v0 (Parallax's existing format)

A line with no `v` key is a v0 entry, the format Parallax's ledger writes today.

- **Keys:** exactly `id`, `ts`, `kind`, `actor`, `reason`, `data`, `prev`, `hash`.
- **Hash:** `hash = hex(sha256(json.dumps(body, sort_keys=True).encode()))`, where `body` is the entry without `hash`, with Python's default separators and `ensure_ascii=True`, and no prefix.
- **Genesis:** the first `prev` is 64 zeros.
- **Line form:** each line is `json.dumps(entry, sort_keys=True)`.
- **Check order** for each v0 line: structure (valid UTF-8 and JSON, an object, no `v` key, exactly the v0 keys), then `prev`, then `hash`, then line form. A wrong line form is `not canonical`. v0 has no seq, so v0 messages name only the line, and `conformance/expected.json` records the seq as `null`.
- **No v1 rules:** the subset and the 16 KiB limit don't apply. Floats and non-ASCII keys are legal in v0.
- **No `ledger.head`:** a v0 ledger has no chain id, so any `ledger.head` beside one is an invalid head.
- **Blank lines are `invalid`** (`not valid JSON`). This is a deliberate difference from Parallax's own `verify`, which skips blank lines. The frozen v0 check is byte for byte on line form: every line must be exactly `json.dumps(entry, sort_keys=True)`, and an empty line is the line form of no entry. Parallax's writer never writes one, so a blank line means the file was edited.

v0 checks are frozen: they use Python's `json.dumps(entry, sort_keys=True)` as the line form and `json.dumps(body, sort_keys=True)` for the hash, exactly as Parallax's `ledger.py` does, and they never change. Every verifier keeps them forever, so a Parallax `Ledger-Head:` commit trailer (the full `hash` of the last entry at accept time) can always be checked against its chain. v0 hashes depend on how Python formats floats, which is one reason v1 bans floats.

The v0 conformance fixture comes from running Parallax's own `ledger.py`, extracted at a fixed commit. That commit hash is recorded in `conformance/expected.json`, beside the fixture's expected status, not in the fixture file. Adding it to the chain file would change the very bytes the fixture exists to pin.

A ledger is all v0 or all v1, decided by its first line. A v1 verifier reports a mixed chain as `invalid`. Polarizer only verifies v0: `serve` and `repair` refuse a `ledger_dir` that holds a v0 ledger, with exit 3, because anything they appended would mix versions. Rules for moving a v0 chain to v1 belong to the stretch adoption work, which needs a separate, explicit go-ahead.

## Part 2: writing, verifying and repairing (Polarizer)

### Location and permissions

The ledger directory is `ledger_dir` from polarizer.toml, by default `~/.local/share/polarizer/`, created with mode 0700. Every file Polarizer creates in it gets mode 0600. On Windows the same path under the user's profile is used; POSIX modes don't apply there, and the profile's ACLs are what protect it.

**Forbidden paths.** `serve` (at startup) and `repair` refuse a `ledger_dir` inside any forbidden path, with exit 2.
- **The forbidden paths** are the entries of `ledger_forbidden_paths` in polarizer.toml plus Parallax's runtime and config directories (`~/.local/share/parallax`, `~/.config/parallax`). Those two are always forbidden; the file can add paths but can't remove them.
- **Naming:** the term "protected paths" is reserved for M2a's tool-call policy (writes to protected paths are held). It never means where the ledger may not live.
- **The comparison** uses resolved real paths (symlinks followed) compared component by component, never as string prefixes, so `/a/parallax2` is not inside `/a/parallax`. Where a forbidden path and some ancestor of `ledger_dir` both exist, they also count as the same when the operating system says they are the same directory, which covers case-insensitive file systems.
- **A git working tree** that isn't forbidden only gets a warning on stderr: `serve` and `repair` continue if `git rev-parse --show-toplevel` succeeds in `ledger_dir` (or its nearest existing parent).
- **`verify`** only reads, so it checks no locations.
- The installed tool never reads `.guard-paths`. That file belongs to the development guard, `scripts/guard.sh`.

The exact messages are in PROXY-SPEC.md, Startup.

| File | What it is |
|---|---|
| `ledger.jsonl` | the chain |
| `ledger.jsonl.lock` | the lock file, never deleted |
| `ledger.head` | the last security-relevant head |
| `args/` | argument side files (Part 3) |
| `ledger.jsonl.torn-*` | bytes removed by repair |
| `defs/` | approved tool definitions (M1a) |
| `sessions/` | one empty lock file per `serve` process, held while it runs, so another process can tell whether a session's holds can still be decided (M2a; HOLD-SPEC.md, section 7; read from stage 6, created by `serve` from stage 7) |

### The lock

One exclusive lock, on `ledger.jsonl.lock`, serializes everything that reads the end of the file or changes it: appends, `ledger.head` updates, startup verification, the `verify` command and `repair`. It uses `fcntl.flock` on Unix and `msvcrt.locking` on byte 0 on Windows.

Verify holds the lock only while it reads the file and `ledger.head`. It checks the bytes after releasing it. With the lock held, a line half-written by a live writer in another process can't look like a torn tail.

**`verify` never writes.** It creates, deletes and modifies no file or directory. It opens the ledger, `ledger.head` and side files read-only. It takes the lock only if `ledger.jsonl.lock` already exists, opening it read-only, and never creates it. A missing lock file means no Polarizer writer has used the directory, because writers create it before their first write, so verify then reads without the lock. A test compares a listing of paths, sizes and mtimes before and after `verify`, on a writable directory and on a read-only one.

**Waiting for the lock.**
- **Writers** wait for it as long as it takes. Other holders keep it only for an append or a short read.
- **Startup verification, the `verify` command and `repair`** wait up to 2 seconds, then refuse with one line: `locked: ledger is locked by another process (waited 2 s); try again or close other Polarizer sessions`.
- **Where that line goes, and the exit code:** `verify` and `repair` print it on stdout. `serve` prints it on stderr, prefixed `polarizer: `, and exits without serving. All three exit 7.
- **The wait** is a loop of non-blocking attempts every 50 ms, because `msvcrt.locking` has no timed wait.

### Appending

Polarizer verifies the whole chain once when it opens the ledger. It then keeps the head in memory: seq, hash, and the byte offset of the end of the file.

One writer thread owns the file and takes work from a queue. Async code awaits a future that resolves when the line is written, and fsynced if the kind requires it, so the event loop never waits on a lock or an fsync.

For each append, the writer takes the lock and compares the file size with its offset:

- **Equal:** append the line with one `write`, then release the lock.
- **Larger:** another process appended. Read only the new bytes, check that each complete line chains from the in-memory head, adopt the new head, then append.
- **Smaller, new bytes that don't chain, or a file that doesn't end in a newline:** stop writing and refuse every later call. Tell the person, on stderr, to run `polarizer verify`. Nothing is rewritten.

### First run, and two processes starting together

Every process opens the ledger the same way, under the lock, waiting up to 2 seconds:

1. Create `ledger_dir` (0700) if it's missing.
2. Take the lock.
3. **Empty or missing `ledger.jsonl`:** write the `ledger.genesis` entry, fsync it, and write `ledger.head` pointing at it. Whichever process takes the lock first creates the genesis entry.
4. **Otherwise:** verify the chain. If `ledger.head` is missing, rebuild it (below).
5. Release the lock.

A second process starting at the same moment waits for the lock, then finds a non-empty ledger and verifies it, so there is never a second genesis. A ledger file that exists but is empty counts as missing. One that holds only a torn genesis line is a torn tail, and `repair` handles it like any other.

**Rebuilding a missing `ledger.head`.** When the ledger is non-empty and `ledger.head` is missing, Polarizer does this under the same lock:
1. verify the chain;
2. append `ledger.head_rebuilt` with the verified head's seq and hash, and fsync it;
3. write `ledger.head` pointing at that new entry.

This weakens the lost-tail check. Anyone who can delete `ledger.head` can also remove lines from the end, and the rebuilt head will match. As before, an attacker with write access to `ledger_dir` is out of scope until anchoring. The `ledger.head_rebuilt` entry at least makes each rebuild visible.

### fsync policy

Measured on WSL2 ext4: a plain write took 0.3 µs at p50, and write plus fsync took 0.9 ms at p50 and 1.3 ms at p95. Windows and macOS are measured by the CI benchmark job.

- **Security-state entries are fsynced before Polarizer acts on them:** `ledger.genesis`, `tool.approved`, `tool.rejected`, `tool.drift`, `ledger.repaired`, `ledger.head_rebuilt`, and from M2a `hold.decided` and `policy.loaded`. `hold.created`, `hold.expired` and `hold.abandoned` only ever make Polarizer do less, so they are written like `call.sent`, fsynced when the writer's queue empties (HOLD-SPEC.md, section 5).
- **`call.sent` is written before the call is forwarded, but not fsynced inline.** The write alone survives a Polarizer crash. An inline fsync would add about 1 ms to every call only to cover an operating-system crash. The writer fsyncs whenever its queue empties.
- **A `call.sent` with no `call.returned`** means "outcome unknown", not a missing call.
- **M1a (PIN-SPEC.md, section 3):** `tool.seen`, `tool.unservable` and `upstream.refresh_failed` are written like `call.sent`, without an inline fsync. `tool.approved` must be durable before anything acts on it: `approve` fsyncs it before reporting success, and `serve` fsyncs the ledger itself before acting on one another process wrote. `tool.rejected` is fsynced, and `ledger.head` updated, before `reject` reports success, because a rejection can revoke an approval and a lost one would bring the tool back. The startup head check catches such a lost tail only while `ledger.head` survives. `tool.drift` is fsynced, but it only ever hides a tool, so `serve` hides at once without waiting for the fsync, and it hides at once on a rejection it adopts too.

### ledger.head

`ledger.head` holds the canonical JSON (RFC 8785) of an object with exactly the keys `chain_id`, `hash` and `seq`, followed by one newline, for example `{"chain_id":"5f0c9e2a7b14d3e8a1c6f9b2d4e7a0c3","hash":"<64 hex>","seq":12}`. `chain_id` is 32 lowercase hex characters, `hash` is 64, and `seq` is an integer from 0 to 2^53-1. Any other content, or a `chain_id` that differs from the genesis entry's, makes it an invalid head (`invalid`, exit 3).

After each fsynced security-state entry, the writer updates it while still holding the ledger lock:

1. Write a temp file `ledger.head.tmp-<pid>` and fsync it.
2. Replace `ledger.head` with it using `os.replace`.
3. On Unix, fsync the directory.

It only moves forward: the writer skips the update if the recorded head is valid, names the same chain, and its seq is already at or past the new one.

**Windows.** `os.replace` fails with `PermissionError` while another process has `ledger.head` open without delete sharing, for example an antivirus scanner or a backup tool. Polarizer's own readers open it only under the lock, so they never cause this. The writer retries five times over about 250 ms. If every try fails, it leaves the temp file, writes one line to stderr, and carries on; the next security event tries again.

That fails safe: a lagging `ledger.head` can only miss a truncation, never report a false one. The security entry itself is already durable in the ledger. A test on the Windows CI runner holds `ledger.head` open from a second handle, triggers an update, and checks that the writer doesn't crash, the ledger entry is intact, and `ledger.head` catches up once the handle closes.

**Startup and verify.** If `ledger.head` records a seq greater than the ledger's last seq, the status is `truncated`. If its seq is at or before the last entry but its hash differs from that entry's hash, the status is `tampered`. The order of these checks is under Statuses. At startup a missing `ledger.head` is rebuilt (see First run); `verify` only reports it. This catches whole lines lost from the end, for example a restored backup, which the chain alone can't see. It doesn't stop someone who can write both files; that needs anchoring.

### Torn tail and repair

A line is committed when its newline is written. Any bytes after the last newline are a torn tail, even if they would parse.

- **The writer never appends after a torn tail.**
- **Startup** with any status other than `intact` exits non-zero, with one line on stderr naming the status and the command to run. Claude Code then shows the server as failed in `/mcp`. A server listing zero tools would look healthy, so exiting is better.

`polarizer repair` handles a torn tail and nothing else:

0. Refuse a `ledger_dir` inside a forbidden path (exit 2), and warn inside another git working tree (see Location and permissions).
1. Take the lock, waiting up to 2 seconds (see The lock). If it's still held, refuse with the one-line message.
2. Under the lock, verify again; a torn tail seen before taking the lock doesn't count. A v0 ledger is refused (exit 3). If the status isn't `torn tail`, refuse and show the status, with that status's exit code. Tampered, invalid, not-canonical and truncated ledgers need a person, not a tool. Because head problems take precedence over a torn tail, repair never runs on a ledger whose `ledger.head` shows lost lines.
3. Copy the torn bytes to `ledger.jsonl.torn-<seq>-<first 12 hex of their sha256>`, created exclusively, and fsync it. `<seq>` is the seq the torn line would have had: one past the last complete entry, or 0 for a torn genesis.
   - **If a file by that name already exists** and its bytes equal the torn bytes exactly, repair reuses it: it is fsynced, never created again or written, and the success line says `moved <n> bytes to <name> (an earlier repair had saved them)`. If its bytes differ, repair refuses with `polarizer: cannot repair: <path of the side file> already exists and its contents differ from the ledger's torn tail; nothing was changed` on stderr, exit 2, and changes nothing. A symbolic link by that name is not followed: repair refuses with `polarizer: cannot repair <path of the side file>: <the system's error>`, exit 2, changing nothing.
4. Truncate the ledger to its last newline. This is the only time Polarizer shortens the file, and it removes only bytes that were never committed.
5. If the file is now empty (the torn line was the genesis), write a fresh `ledger.genesis` with a new chain id first.
6. Append `ledger.repaired` with `data.bytes`, `data.sha256` and `data.file`, fsync it, update `ledger.head`, then release the lock.

**A crash during repair.** Each step is fsynced before the next begins, and the lock is released when the process ends. A repair that crashes leaves one of these states:
- **After the torn bytes are saved, before the truncate:** the ledger and `ledger.head` are unchanged and still show the torn tail. The side file holds the torn bytes, or part of them if the crash came while writing it. Its name depends only on the torn bytes and their seq, and nothing appends after a torn tail, so a second repair picks the same name; it would differ only if someone changed the torn bytes. If the side file holds all the torn bytes, the second repair reuses it (step 3) and finishes. If the crash came while writing it, it holds only part of them, so the second repair refuses with the contents-differ line (step 3), exit 2, changing nothing; a person compares the side file with the ledger's tail, removes it, and runs repair again.
- **After the truncate, before `ledger.repaired` is appended:** the ledger ends at its last complete line and is `intact`, with no record of the repair. `ledger.head` is unchanged, at or before that line. The side file holds the removed bytes, and its name gives the seq they would have had. A second repair prints `nothing to repair: ledger is intact`, and the next `serve` starts normally.
- **A torn genesis, after the truncate:** the ledger is empty. Repair then prints `no ledger at <dir>` (exit 2), and the next process to open the ledger writes a fresh `ledger.genesis` with a new chain id, as on a first run, with no `ledger.repaired`. If the crash comes after repair's own new genesis is fsynced and before `ledger.head` is written, the ledger holds that genesis alone, under a new chain id, with no `ledger.head`: repair finds it intact, and the next open rebuilds `ledger.head` and appends `ledger.head_rebuilt`. Either way the old chain id is left only in the side file.
- **After `ledger.repaired` is fsynced, before `ledger.head` moves:** the ledger is intact, with the record, and `ledger.head` lags behind it, which verify accepts (see ledger.head).

Writers in other processes that saw the torn tail have already stopped (see Appending) and must be restarted.

## Part 3: argument side files

Arguments can hold secrets, floats and arbitrary text, so they never enter the chain. Each call's arguments go in a side file, and `call.sent` holds a commitment to it.

**Side files hold the arguments in plaintext.** Only the file mode (0600, in a 0700 directory) protects them. Deleting a side file is how a secret is removed, and the chain still verifies, because verify never reads side files.

**Format.** 32 random bytes of salt, then the arguments as compact UTF-8 JSON, exactly as Polarizer serialized them (`json.dumps(args, separators=(",", ":"), ensure_ascii=False)`). No canonical form is needed, because the commitment covers the stored bytes.

**Commitment and name.** `args_commit = hex(sha256(file bytes))`, which is sha256(salt || stored bytes). Without the salt, short or guessable arguments could be confirmed by hashing guesses.

The file is named by its commitment, `args/<args_commit>.bin`, and created exclusively (`O_CREAT | O_EXCL`). It is written before `call.sent`.

The name settles a collision a seq-based name would have. A crash after the side file is written but before `call.sent` reaches the file leaves an orphan, and the next process may reuse that seq. With the commitment as the name, the new call's file can't collide: its random salt gives it a different name. The orphan stays harmless.

**Checking.** `polarizer verify --args` reports each side file as:
- **matching**;
- **missing**, the normal state after a deletion;
- **tampered**, when its bytes don't match the name;
- **orphaned**, when no `call.sent` refers to it.

It never deletes anything. The exact output and exit codes are in PROXY-SPEC.md. A tampered side file exits 8. Missing and orphaned files don't fail the check.
