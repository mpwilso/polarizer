# Known limits

Every stated limit of Polarizer, grouped. They come from the specs and [verified-facts.md](verified-facts.md), and [README.md](../README.md#limits) names the most important. A limit leaves this file only when the code or a recorded check changes it.

Polarizer only sees calls routed through it; the agent's own shell, its file tools and MCP servers configured directly in the client are outside it, and an agent whose call is refused can try another route itself. In the M2a check, the model whose move was refused named Bash `mv` as a route it chose not to take; nothing in Polarizer would have stopped it. Making Polarizer the only path to a tool would mean denying the client's own shell and file tools in its permission settings, which has not been tested.

## Scope

- Tried only with Claude Code, on Linux (WSL2). CI runs the tests on Windows and macOS; no live check has.
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
- The built-in patterns protect git's own files under `.git`, but an agent can still write a directory anywhere in the workspace that looks like a bare git repository, with a config that runs code. It does nothing until a person runs git inside that directory, which git allows by default (`safe.bareRepository` is `all`).
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
- There is no notification for holds outside a terminal or the card's page. Run `polarizer holds --wait` in another terminal, or keep `polarizer card`'s page open; nothing calls you to either.

## The card

- The card shows what the ledger says. A call the rules let through never appears on it.
- The paths it shows are resolved when it first shows a hold, not when the call arrived, so a symbolic link changed in between shows on the card and not in the hold's reason.
- Its recommendation follows the rule that held the call and nothing else. "No recommendation" is not a statement that the call is safe.
- A program running as you can read the card's token from its terminal, the page from the browser, or the card's memory, and decide through it. The token is also in the browser's history for as long as that card runs.
- Another web server on 127.0.0.1 that your browser visits receives the card's cookie, since browsers send cookies to every port of a host. The cookie alone opens nothing but the page's script and style; the token and a page's nonce are needed for anything else.
- The note that a call has probably moved to the background appears at 120 s, from one observed version of Claude Code (2.1.289, about 123 s).
- The card's time to decision (`elapsed_ms`) starts when the card first sends a hold to a page, not when you first look at it, and a page left in a background tab counts as showing it.
- The card was checked headless on real Claude Code, with its endpoints driven by curl; its page has been checked only by a test in a headless Chromium, not yet by a person.

## People

- A hold is only as good as the person reading it. If you allow without reading, Polarizer records that you allowed it. Drills measure how often a person catches a planted call, under practice conditions only (Drills, below). During real work, `polarizer stats` counts holds, the approval rate and the time to each decision, and warns when decisions get faster while the rate stays flat; that can mean less reading or calls that got easier to judge, and nothing in a real ledger tells the two apart. Its times are wall-clock differences between two processes, so each can be off by about a second, and they include the time before a hold was noticed. Its thresholds (20 decided holds for a suggestion; windows of 40 decisions, half the time and a 6 s floor for the trend) are guesses until real ledgers exist.
- An agent that can run commands as you can also run `polarizer approve` or `polarizer allow`. They refuse to run without a terminal unless told they are run by a script, which only stops accidents. A decision made that way is recorded with `terminal: false`, so the ledger shows it, but nothing stops it.

## Drills

- A drill measures attention when the person knows it is a test, and that some calls are planted. People read more carefully then, so drill rates are closer to a person's best than to an ordinary afternoon.
- A drill plants 6 to 10 calls in 20, far above any real rate of bad calls, so that one drill measures something. Rare targets are missed more often than common ones, so drill catch rates likely run above a person's catch rate at a realistic rate. A condition with fewer planted calls may come later.
- One drill gives a wide interval: with 6 to 10 planted calls, a catch rate's 95% interval is 28 to 64 points wide (7 of 8 caught is 52% to 98%). Below 5 calls of a kind, as in a drill stopped early, no rate is printed.
- The number of planted calls in a drill of 20 is drawn from 6 to 10, and the intro gives only that range, so counting the revealed answers doesn't tell when the planted calls are over. It still does in extreme cases: after 10 planted or 14 clean calls the rest are known. That settled at least one answer in 16% of 10,000 seeded drills (0.26 calls per drill on average), and near the end a person who counts can guess a little better than chance.
- Always answering allow would be right about 60% of the time, so a drill's share of right answers says little; its two rates are what it measures.
- Surface features give little away, but not nothing. In a check of the scenario set using only what the screen shows before the answer (tool, class, rule, the number and length of the arguments, the task's length, a few telling strings), no feature value had 90% or more of one answer, and the best one-feature rule, by argument length, was right 64% of the time against 60% for always allowing.
- A guided drill adds a line in plain words under each call. Its results say how well a person does with that help, not how well they read raw holds, which is what real work gives them; they are reported apart from plain and prediction-gate drills, and compared with them only as a difference with its interval. A first drill is guided unless a flag chooses otherwise.
- The plain-words lines were written by hand from each call alone, and checked for forbidden words, length, and two features in the audit (their length, and whether they name the argument that matters). A line can still help with a call in ways those checks don't measure, such as how hard it is to read; the same lines are shown every time a scenario comes back.
- The people who run drills are the people who care about oversight; their numbers say nothing about anyone else's.
- Drill calls are invented, short and self-contained, and come with the task written above them, which real holds don't have.
- Drill reports' counts by kind of mistake are under review. Some planted calls in scenario set 1 are labelled with one kind of mistake (shape) but match the rule for another: checked against a mechanical rule for each shape, 9 of the 60 planted calls carry a label the rules don't give, 8 of them labelled misleading summary (docs/MEASURE-SPEC.md, section 6, Shapes). The report's counts by kind therefore partly count other kinds, and each drill has only one or two calls of each kind. A set 2 with corrected labels is planned, not built.
- The scenario set is finite (150 calls). After many drills a person may recognize one; the report counts repeats.
- The answers are in the installed package. A person who reads the scenario file, or draws the plan from the seed in their own ledger, can score 100%.
- Real holds time out after 300 s and drill calls never do; answers that took longer are marked, and results are shown with and without them.
- No drill entry is fsynced inline, and a drill ledger's `ledger.head` stays at its genesis entry, so a lost tail of a drill ledger is not detected.
- Drills on a native Windows console (PowerShell or the Command Prompt) have not been tried.

## Live planted calls

- They are off unless `planted_calls_per_session` is set in `[policy]`, and off under `--no-holds`. With them on, a held call may be one the agent never made; none is ever forwarded, and none is made while 12 or more holds of the session are open.
- A planted call has no call line in Claude Code, since the agent never made it. A person who matches every hold against Claude Code's screen can tell which one is planted, so a live planted call measures whether you read the held call, not whether you cross-check it with the agent's transcript.
- One shape only: a path argument changed to a place the built-in patterns hold (a git hook, a workflow, `~/.bashrc`, `~/.ssh/authorized_keys`, or for a read, a key or credentials file). The planted call's arguments name your real home directory.
- The rate is low by design: at most 3 per session, about 1 in 20 eligible calls, and only while the agent is active. A catch rate from planted calls takes many sessions to mean anything, and `polarizer stats` prints "too few to say" until it does.
- The card recommends deny for every planted call, as for any real call to the same places, so a catch made on the card partly measures whether you follow its recommendation.
- A planted call comes 1 to 8 seconds after the call it was made from. When the session ends first, as a headless `claude -p` run does right after its last call, the plan is dropped, or the planted hold ends with the session (recorded as expired at shutdown) before anyone could read it. The first headless stage 10 check saw this once.
- A planted hold whose `serve` dies before its reveal is never revealed, and `stats` counts it as not revealed. The salt that proves the label stays in that process's memory, which a debugger running as you could read.

## The ledger

- Someone who can write to the ledger directory can rewrite the ledger and its head together. There is no anchoring or signature yet.
- Call arguments are kept in side files in plain text, protected only by file permissions (0600 in a 0700 directory). Deleting one removes it, and the chain still verifies.
- Each entry's time is the wall clock. The chain proves order, not when anything happened.
