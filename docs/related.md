# Related projects

Other MCP proxies and scanners, as their documentation described them in October 2026 (the record: [docs/verified-facts.md](verified-facts.md#related-projects-from-the-owners-research-october-2026-not-re-checked-in-this-repo)):

- [mcpclerk](https://github.com/hishamalward/mcpclerk): a Python gateway on the official MCP SDK with default deny, per-tool allow, deny or approve, held calls where a timeout counts as a refusal, and a hash-chained log with a verify command.
- [mcpproxy-go](https://github.com/smart-mcp-proxy/mcpproxy-go): holds a tool whose definition changed until it is approved again.
- [Trail of Bits' mcp-context-protector](https://github.com/trailofbits/mcp-context-protector): pins tool definitions on first use and scans responses.
- [Invariant's mcp-scan, now Snyk's agent-scan](https://github.com/snyk/agent-scan): pins tools by hash and detects shadowing at scan time.
- [Docker's MCP Gateway](https://docs.docker.com/ai/sandboxes/governance/reference/mcp-policy/): Cedar policies that can read call arguments and annotations, with approval asked through MCP elicitation.
- [vex-mcp](https://pypi.org/project/vex-mcp/): a stdio proxy that inspects tool descriptions, pins tool definitions, enforces a default-deny policy and writes a tamper-evident audit log.
- [warden-mcp](https://pypi.org/project/warden-mcp/): pins tool definitions on first use and quarantines a changed tool until it is approved again.
- [MCPDome](https://docs.rs/mcpdome): schema pinning with canonical SHA-256 hashes and a hash-chained ledger component.
- [hoop's mcpproxy](https://pkg.go.dev/github.com/hoophq/mcpproxy): holds flagged tool calls until a human approves them over a REST API.
- [TrueFoundry's MCP gateway](https://www.truefoundry.com/blog/mcp-tool-approvals-explained): holds a matched tool call and creates an approval request.

Pins, holds and tamper-evident logs exist in several of these, and Polarizer credits three of mcpclerk's ideas ([README.md](../README.md#how-it-was-built)). What Polarizer adds is holds decided by a call's resolved path arguments and its tool class, a ledger checked by a separately written verifier and RFC 8785 test vectors, and drills that measure the person's catch rate and false-flag rate with intervals. In a search in October 2026 I found tools that test AI agents with planted inputs, such as [Agent Canary](https://github.com/Auro-rium/canary) and [AgentCanary](https://github.com/antgroup/Agent3Sigma-Canary), but none that tests the person approving; a search is not proof that none exists.
