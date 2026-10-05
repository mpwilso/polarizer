# Known limits

Every stated limit of Polarizer 0.1, grouped. They come from the specs and [verified-facts.md](verified-facts.md), and [README.md](../README.md#known-limits) names the most important. A limit leaves this file only when the code or a recorded check changes it.

Polarizer only sees calls routed through it; the agent's own shell, its file tools and MCP servers configured directly in the client are outside it, and an agent whose call is refused can try another route itself. In the M2a check, the model whose move was refused named Bash `mv` as a route it chose not to take; nothing in Polarizer would have stopped it.

## Scope

- Tools only. Resources, prompts and completions from upstream servers are not exposed.
- Requests from an upstream server to the client (elicitation, sampling, roots) are not passed on, and the tool call fails.
- No scanning of tool descriptions, no rules on argument values or domains, and no taint. A call that no rule holds goes straight through, recorded in the ledger.
- Results pass through the MCP Python SDK, which drops fields the protocol doesn't define and turns a result it can't parse into an error. Tools are served in the 2026-07-28 form, without `execution` or `_meta`. [docs/PROXY-SPEC.md](PROXY-SPEC.md#results) lists each change.
- Each upstream starts once per session. One that fails to start stays off until the client restarts Polarizer. An upstream whose tool listing fails has its tools hidden until a listing succeeds again (Polarizer retries after 30 seconds, then less often, up to every 5 minutes); one whose process exits stays hidden until Polarizer restarts.

## Definitions, not behavior

- Pins check a tool's definition (name, description, parameters, annotations), not what the tool does. A server can change its behavior without changing its definition, and Polarizer won't notice.
- If you approve a poisoned definition without reading it, Polarizer keeps serving exactly that definition.
- A class is your statement about a tool. Polarizer can't check it, and a tool can reach files in ways that never appear in its arguments (its own configuration, its working directory, a path it computes, a process it starts).
- A decision is not instant. A call already under way when you reject its tool is not stopped.

## Paths

- Only the top-level arguments you name in `path_args` are checked. A path nested inside an argument (a list of edits, each with its own path) is not checked, and a path inside free text (a shell command, a URL, a script) never is.
- Paths are resolved when the call arrives. A symbolic link created or changed after that is not seen, and an upstream that sees a different file system (a container, another machine) resolves paths its own way.
- A `~` in a hold pattern means the home directory in Polarizer's own `HOME` when it starts, not necessarily your account's home.
- The built-in patterns protect Claude Code's settings in their default place. If you move them with `CLAUDE_CONFIG_DIR`, the patterns don't follow; add the new place to `write_hold_patterns`.
- On Windows, the device names `COM1` to `COM3` and `LPT1` to `LPT3` written with a superscript digit are not recognized as device names.
- `polarizer.example.toml` uses POSIX paths. On native Windows, edit each one to a real absolute path with a drive, such as `C:/Users/<you>/...`; a `workspace_roots` entry without a drive is refused there as not absolute.

## Annotations

- A tool's own annotations (`readOnlyHint`, `destructiveHint`, `openWorldHint`) come from the server. Polarizer shows them as a hint and uses them only for a server you mark with `trust_annotations`, which then chooses its own tools' classes.

## Held calls and Claude Code

- A held call waits inside the Polarizer process that Claude Code started. Pressing Esc in Claude Code stops that process and ends every call it was holding: none of them reaches the server, and the ledger says so. Don't press Esc to go and decide a hold.
- If you allow a call and that process dies before it forwards it, the call does not run.
- In Claude Code 2.1.289, a call still running after about 123 seconds is moved to the background, and the agent keeps working. It can make other calls, or ask for the same call again, which is a new hold. The first hold is unchanged: nothing runs until you allow it, and it is refused at the timeout. This was observed once and may differ in other versions.
- Claude Code's own permission prompt for an MCP tool comes before the call reaches Polarizer, so a held call may be approved twice: once in Claude Code, without the details, and once with `polarizer allow`.
- If `MCP_TOOL_TIMEOUT` is set below the hold timeout, Claude Code cancels the call first.
- There is no page or notification for holds yet. Run `polarizer holds --wait` in another terminal.

## People

- A hold is only as good as the person reading it. If you allow without reading, Polarizer records that you allowed it. Drills measure how often a person catches a planted call, under practice conditions only (Drills, below); nothing measures it during real work yet.
- An agent that can run commands as you can also run `polarizer approve` or `polarizer allow`. They refuse to run without a terminal unless told they are run by a script, which only stops accidents.

## Drills

- A drill measures attention when the person knows it is a test, and that some calls are planted. People read more carefully then, so drill rates are closer to a person's best than to an ordinary afternoon.
- A drill plants 8 calls in 20, far above any real rate of bad calls, so that one drill measures something. Rare targets are missed more often than common ones, so drill catch rates likely run above a person's catch rate at a realistic rate. A condition with fewer planted calls may come later.
- One drill gives a wide interval: with 8 planted calls, a catch rate's 95% interval is 33 to 58 points wide (7 of 8 caught is 52% to 98%). Below 5 calls of a kind, as in a drill stopped early, no rate is printed.
- Every drill of 20 has 8 planted calls, and the intro says so. Each answer is revealed, so a person who counts knows the remaining answers once 8 planted or 12 clean calls have been shown.
- The people who run drills are the people who care about oversight; their numbers say nothing about anyone else's.
- Drill calls are invented, short and self-contained, and come with the task written above them, which real holds don't have.
- The scenario set is finite (150 calls). After many drills a person may recognize one; the report counts repeats.
- The answers are in the installed package. A person who reads the scenario file, or draws the plan from the seed in their own ledger, can score 100%.
- Real holds time out after 300 s and drill calls never do; answers that took longer are marked, and results are shown with and without them.
- No drill entry is fsynced inline, and a drill ledger's `ledger.head` stays at its genesis entry, so a lost tail of a drill ledger is not detected.
- Drills on a native Windows console (PowerShell or the Command Prompt) have not been tried.

## The ledger

- Someone who can write to the ledger directory can rewrite the ledger and its head together. There is no anchoring or signature yet.
- Call arguments are kept in side files in plain text, protected only by file permissions (0600 in a 0700 directory). Deleting one removes it, and the chain still verifies.
- Each entry's time is the wall clock. The chain proves order, not when anything happened.
