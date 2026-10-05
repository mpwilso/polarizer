# Security policy

Polarizer is a v0.1 preview. It has not had an outside security review, and it should not be the only thing between an agent and anything you can't afford to lose. README.md, "What it does not do", lists the limits it has by design; those are not vulnerabilities.

## Reporting a vulnerability

Please report it privately, through GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue, pull request or discussion for it.

Include what you did, what you expected, what happened, the Polarizer commit or version, your platform and Python version, and the client and its version if one was involved. A small `polarizer.toml` and the commands that show the problem help most. Leave out real secrets: a ledger's argument side files hold call arguments in plain text.

This is a project run by one person in spare time. Expect an acknowledgement within a week or two, not a guaranteed timeline for a fix. Fixes go into the next release, and the report is credited in CHANGELOG.md if you want that.

## In scope

- A call to a tool that is not approved, or whose definition changed since it was approved, reaching an upstream server.
- A call that the rules in `polarizer.toml` should hold reaching an upstream server without a person's `polarizer allow`, or after a deny or an expiry.
- A ledger that `polarizer verify` reports as `intact` after an entry was changed, removed, reordered or added, other than by someone who can rewrite both `ledger.jsonl` and `ledger.head` (that needs anchoring, which is not built).
- `polarizer verify` writing anything, or a side file being reported as matching when its bytes changed.
- Text from an upstream server (names, descriptions, errors, arguments shown by `pending` and `holds`) reaching your terminal with control or look-alike characters unescaped.
- Polarizer's own files created with wider permissions than LEDGER-SPEC.md says (0700 directory, 0600 files), or written inside a forbidden path.
- Crashes or hangs that an upstream server or a client can cause and that leave a call forwarded without a ledger entry.

## Out of scope

- Anything the agent does outside Polarizer: its own shell, its file tools, and MCP servers configured directly in the client.
- What an approved tool does. Polarizer pins definitions, not behavior.
- A person who allows or approves without reading, and an agent that can run `polarizer approve` or `polarizer allow` as you (the terminal check only stops accidents).
- Someone who can already write to the ledger directory or to `polarizer.toml`.
- Bugs in the MCP Python SDK, Claude Code or the upstream servers themselves; report those to their projects. If Polarizer relies on the behavior, a report here is welcome too.
