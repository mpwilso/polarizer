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
| `call.sent` | `session`, `tool`, `args_commit`, `meta_dropped`, `client_call_id` (all strings except `meta_dropped`, a list) |
| `call.returned` | `call_seq`, `outcome` (`ok`, `tool-error`, `protocol-error`, `transport-error`, `cancelled`, `unsupported`; defined in PROXY-SPEC.md), `latency_ms`, `result_bytes`, plus `error` (one line) for every outcome but `ok`, and `code` for `protocol-error` |
| `call.refused` | `tool`, `reason` (M1a: hidden or unknown tool called by name) |
| `tool.approved` | `upstream`, `tool`, `def_hash`, `actor` (M1a) |
| `tool.rejected` | `upstream`, `tool`, `def_hash`, `actor`, `reason` (M1a) |
| `tool.drift` | `upstream`, `tool`, `approved_hash`, `live_hash` (M1a) |
| `ledger.repaired` | `bytes`, `sha256`, `file` |
| `ledger.head_rebuilt` | `from_seq`, `from_hash` (the verified chain head it was rebuilt from) |

New kinds can be added without changing the version, as long as their `data` stays inside the subset.

### Statuses

`verify` reads the ledger and reports the first problem, with its line number and seq:

| Status | Exit code | Meaning |
|---|---|---|
| `intact` | 0 | Every rule holds. |
| `tampered` | 1 | A `hash`, `prev` or `seq` doesn't match. |
| (usage error) | 2 | Bad arguments or an unreadable file. |
| `invalid` | 3 | An entry breaks a v1 rule: an extra key, a float, an integer out of range, a non-ASCII key, an oversize line, a second genesis, or a mix of v0 and v1. |
| `not canonical` | 4 | The entry parses and hashes correctly, but its stored bytes aren't its canonical bytes. |
| `torn tail` | 5 | There are bytes after the last newline. |
| `truncated` | 6 | The chain is otherwise intact, but `ledger.head` records a later seq than the ledger holds (Part 2). |
| (locked) | 7 | The ledger lock was still held after 2 seconds; nothing was checked (Part 2). |

**Check order.** Lines are checked in file order, and the first problem found decides the status. Within one line, both verifiers check in this order:
1. structure (`invalid`): the line is valid UTF-8 and JSON, has exactly the v1 keys, stays inside the subset, is at most 16 KiB, and isn't a second genesis or a mixed version;
2. `seq`, then `prev`, then `hash` (`tampered`);
3. canonical bytes (`not canonical`).

`torn tail` and `truncated` are checked only after every complete line passes. A line that isn't valid JSON is `invalid`, not a torn tail, unless it is the bytes after the last newline.

### v0 (Parallax's existing format)

A line with no `v` key is a v0 entry, the format Parallax's ledger writes today.

- **Keys:** exactly `id`, `ts`, `kind`, `actor`, `reason`, `data`, `prev`, `hash`.
- **Hash:** `hash = hex(sha256(json.dumps(body, sort_keys=True).encode()))`, where `body` is the entry without `hash`, with Python's default separators and `ensure_ascii=True`, and no prefix.
- **Genesis:** the first `prev` is 64 zeros.
- **Line form:** each line is `json.dumps(entry, sort_keys=True)`.

v0 rules are frozen. Every verifier keeps them forever, so a Parallax `Ledger-Head:` commit trailer (the full `hash` of the last entry at accept time) can always be checked against its chain. v0 hashes depend on how Python formats floats, which is one reason v1 bans floats.

The v0 conformance fixture comes from running Parallax's own `ledger.py`, extracted at a fixed commit. That commit hash is recorded in `conformance/expected.json`, beside the fixture's expected status, not in the fixture file. Adding it to the chain file would change the very bytes the fixture exists to pin.

A ledger is all v0 or all v1. A v1 verifier reports a mixed chain as `invalid`. Rules for moving a v0 chain to v1 belong to the stretch adoption work, which needs a separate, explicit go-ahead.

## Part 2: writing, verifying and repairing (Polarizer)

### Location and permissions

The ledger directory is `ledger_dir` from polarizer.toml, by default `~/.local/share/polarizer/`, created with mode 0700. Every file Polarizer creates in it gets mode 0600. On Windows the same path under the user's profile is used; POSIX modes don't apply there, and the profile's ACLs are what protect it.

Startup refuses a `ledger_dir` inside any path listed in `.guard-paths`, or inside Parallax's runtime or config directories (`~/.local/share/parallax`, `~/.config/parallax`). It warns on stderr, and continues, if `ledger_dir` is inside any other git working tree (`git rev-parse --show-toplevel` succeeds there). The exact messages are in PROXY-SPEC.md, Startup.

| File | What it is |
|---|---|
| `ledger.jsonl` | the chain |
| `ledger.jsonl.lock` | the lock file, never deleted |
| `ledger.head` | the last security-relevant head |
| `args/` | argument side files (Part 3) |
| `ledger.jsonl.torn-*` | bytes removed by repair |
| `defs/` | approved tool definitions (M1a) |

### The lock

One exclusive lock, on `ledger.jsonl.lock`, serializes everything that reads the end of the file or changes it: appends, `ledger.head` updates, startup verification, the `verify` command and `repair`. It uses `fcntl.flock` on Unix and `msvcrt.locking` on byte 0 on Windows.

Verify holds the lock only while it reads the file and `ledger.head`. It checks the bytes after releasing it. With the lock held, a line half-written by a live writer in another process can't look like a torn tail.

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

- **Security-state entries are fsynced before Polarizer acts on them:** `ledger.genesis`, `tool.approved`, `tool.rejected`, `tool.drift`, `ledger.repaired`, `ledger.head_rebuilt`, and later milestones' holds and decisions.
- **`call.sent` is written before the call is forwarded, but not fsynced inline.** The write alone survives a Polarizer crash. An inline fsync would add about 1 ms to every call only to cover an operating-system crash. The writer fsyncs whenever its queue empties.
- **A `call.sent` with no `call.returned`** means "outcome unknown", not a missing call.

### ledger.head

`ledger.head` holds one line: `{"seq": N, "hash": "..."}`. After each fsynced security-state entry, the writer updates it while still holding the ledger lock:

1. Write a temp file `ledger.head.tmp-<pid>` and fsync it.
2. Replace `ledger.head` with it using `os.replace`.
3. On Unix, fsync the directory.

It only moves forward: the writer skips the update if the recorded seq is already at or past the new one.

**Windows.** `os.replace` fails with `PermissionError` while another process has `ledger.head` open without delete sharing, for example an antivirus scanner or a backup tool. Polarizer's own readers open it only under the lock, so they never cause this. The writer retries five times over about 250 ms. If every try fails, it leaves the temp file, writes one line to stderr, and carries on; the next security event tries again.

That fails safe: a lagging `ledger.head` can only miss a truncation, never report a false one. The security entry itself is already durable in the ledger. A test on the Windows CI runner holds `ledger.head` open from a second handle, triggers an update, and checks that the writer doesn't crash, the ledger entry is intact, and `ledger.head` catches up once the handle closes.

**Startup.** If `ledger.head` records a seq greater than the ledger's last seq, the status is `truncated`. A missing `ledger.head` is rebuilt (see First run). This catches whole lines lost from the end, for example a restored backup, which the chain alone can't see. It doesn't stop someone who can write both files; that needs anchoring.

### Torn tail and repair

A line is committed when its newline is written. Any bytes after the last newline are a torn tail, even if they would parse.

- **The writer never appends after a torn tail.**
- **Startup** with any status other than `intact` exits non-zero, with one line on stderr naming the status and the command to run. Claude Code then shows the server as failed in `/mcp`. A server listing zero tools would look healthy, so exiting is better.

`polarizer repair` handles a torn tail and nothing else:

1. Take the lock, waiting up to 2 seconds (see The lock). If it's still held, refuse with the one-line message.
2. Under the lock, verify again; a torn tail seen before taking the lock doesn't count. If the status isn't `torn tail`, refuse and show the status. Tampered, invalid, not-canonical and truncated ledgers need a person, not a tool.
3. Copy the torn bytes to `ledger.jsonl.torn-<seq>-<first 12 hex of their sha256>`, created exclusively, and fsync it.
4. Truncate the ledger to its last newline. This is the only time Polarizer shortens the file, and it removes only bytes that were never committed.
5. If the file is now empty (the torn line was the genesis), write a fresh `ledger.genesis` with a new chain id first.
6. Append `ledger.repaired` with `data.bytes`, `data.sha256` and `data.file`, fsync it, update `ledger.head`, then release the lock.

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
