# Measure spec (M5 and M6, first slice)

This is the behavior contract for the first slice of measurement: drills, stats over real holds, and live planted calls inside `serve`. The ledger format is in LEDGER-SPEC.md, `serve` and the command line are in PROXY-SPEC.md, pins are in PIN-SPEC.md, holds are in HOLD-SPEC.md, and the facts this relies on are in verified-facts.md. Everything M0, M1a and M2a do stays as those files say, except where this file changes it. Each change is listed under Deviations and guesses at the end (section 17), and the owner's answers to this spec's questions are in section 16.

The goal of the whole project is to measure whether a person's approvals still catch anything. This slice is the first part of that measurement. It is built in three stages, each ending in a stop for the owner's review (section 15): drills (stage 8), stats over real holds (stage 9), and live planted calls (stage 10), which is deferred until after M3 (section 16, question 6). Drills are the primary measurement. The owner's decisions for this round are listed in section 17; "decision <n>" refers to them.

## 1. What this slice is and is not

**In this slice:**
- **Drills** (stage 8). `polarizer drill` runs an offline practice session: no agent, no model, no network, no upstream server, and nothing forwarded anywhere. It shows about 20 held calls, one at a time, each under the one-line task the agent was given and rendered exactly as `polarizer holds` renders a real hold. The person answers allow or deny, and each answer is revealed at once as right or wrong, with the reason. `polarizer drill report` summarizes every drill so far, and `--export` writes an anonymized summary a person may choose to share (sections 4 to 9).
- **The prediction gate** (stage 8), a drill condition in which the person writes what they expect the call to do before they see it (section 4).
- **Stats over real holds** (stage 9). `polarizer stats` reads a serve ledger and prints holds and their endings per session, the approval rate with its interval, the time to a decision, how often each tool and rule was allowed, and the warning sign the research names: decisions getting faster while the approval rate does not fall (section 10).
- **Live planted calls** (stage 10, specified here and built after M3). With an opt-in config key, `serve` occasionally creates a hold that the agent never made and that is never forwarded, to see whether the person catches it during real work. Every hold of such a session carries a commitment, and every ending is followed by a reveal that can be checked against it (section 11).

**Not in this slice:**
- the card (M3), argument rules and taint (M2b), scanning (M1b), and the practice range (M4);
- any service that collects results from several people: results stay on the machine, and an export is a file the person may send by hand (section 3);
- scoring of predictions: the prediction is recorded, never judged (section 16);
- live planted calls of any shape but a changed path argument (section 11).

**Terms used with people.** A **drill** is a practice session. A **planted call** is a call that should be denied: in a drill, one written to differ from its task; in `serve`, one Polarizer made up. A **clean call** is one that should be allowed. The industry term for a planted call is "canary"; this spec uses it only in this sentence, and user-facing text never uses it (decision 11). The ledger's kinds and fields say `planted` and `plant`.

### What the numbers can show

- **A drill measures attention under drill conditions.** The person knows a drill is a test, and that some calls are planted. They read more carefully than during real work, so a drill's catch rate is closer to the best this person does than to what they do on an ordinary afternoon.
- **Trends across drills still mean something,** because that bias is roughly the same in every drill one person runs. A catch rate that falls across drills, under conditions where the person is trying, is a stronger signal than one low drill. The two conditions (plain and prediction gate) are both under drill conditions, so comparing them compares like with like.
- **Stats over real holds** show what happened, not why. An approval rate near 100% can mean that the rules hold only calls that should run, or that the person stopped reading. Decisions getting faster can mean less reading, or calls that became easier to judge. TrueFoundry's guide makes the same point about a falling denial rate (section 2). That is why stats never print a verdict on the person, and why drills exist: a drill call has a known answer, so it separates the two explanations.
- **Live planted calls** measure attention during real work, at the cost of a test the person agreed to in advance and can turn off (section 11).

### What the numbers cannot show

- **Small n.** One drill has 6 to 10 planted calls. A catch rate from so few calls has a 95% interval 28 to 64 points wide (52% to 98% for 7 of 8); section 7 prints that interval every time, and prints "too few to say" below 5, which only a drill stopped early reaches.
- **Self-selection.** The people who run drills are the people who care about oversight. Their numbers say nothing about anyone else's.
- **Drill calls are not the person's real work.** They are invented, short and self-contained, and they come with the task written above them, which real holds don't have. A person who catches every planted call in a drill may still miss one in their own project, where they know less about what the agent was asked.
- **Repeats.** The scenario set is finite (section 6). After enough drills, a person may recognize a scenario. Each shown call records how often it was shown before, and the report counts repeats.
- **The answers are in the package.** A person who reads the scenario file, or computes the plan from the seed in their own ledger, can score 100%. A drill measures only a person who doesn't look.
- **The planted share is far above any real rate.** A drill plants 6 to 10 calls in 20, 8 on average, chosen for the measurement (section 4). Rare targets are missed more often than common ones, so a drill's catch rate is likely higher than a person's catch rate at a realistic rate. A low-rate condition may come later (section 16, questions 2 and 15).
- **Counting down.** The number of planted calls in a drill of 20 is drawn from 6 to 10, and the intro gives only that range, so the running count doesn't tell a person when the planted calls are over (section 16, question 15). It can still be partly inferred in extreme cases: once 10 planted calls have been revealed the rest are clean, and once 14 clean calls have been revealed the rest are planted. Over 10,000 seeded drills of 20, that settled at least one answer in 16% of drills, 0.26 calls per drill on average; with a fixed 8 it settled 2.0 per drill, in every drill. Short of certainty, a person who counts can still guess a little better than chance near the end. Calls answered that way measure counting, not reading (section 4, Defaults, and why).
- **Answers past the timeout.** Real holds time out after 300 s and drill calls never do; an answer that took longer is marked, and results are shown with and without such answers (section 4, Time).

## 2. Sources

Cited from the owner's research check of 2026-10-04. None of them was fetched in this round, and nothing below goes beyond what that check recorded.

- **Oversight Has a Capacity: Calibrating Agent Guards to a Subjective, Fatiguing Human,** arXiv 2606.08919, https://arxiv.org/html/2606.08919. Escalating too little misses danger; escalating too much overloads the reviewer, who then rubber-stamps. Used for: why stats look at time per decision and per-tool approval counts, and why the suggestion line exists (section 10).
- **AI Agents Push Humans Out of the Loop,** arXiv 2608.23642, https://arxiv.org/pdf/2608.23642. Oversight quality can be measured empirically, and that has to be balanced against the ethical concern of surveillance. Used for: the consent and framing rules (section 3).
- **TrueFoundry, MCP Tool Approvals, Explained,** https://www.truefoundry.com/blog/mcp-tool-approvals-explained. Recommends measuring approval latency and denial rate, and combining workflow metrics with sampled decision quality, since a falling denial rate may mean better proposals or rubber-stamping. Used for: stats (section 10) and the reason drills and stats go together.
- **Human-in-the-Loop for AI Agents: Most Approval Gates Rubber-Stamp,** https://www.beri.net/article/human-in-the-loop-agent-approval-gates-buyers-guide-reviewer-cost-kill-switch. Recommends seeding a small share of known-wrong actions and tracking how often they are caught. Used for: live planted calls (section 11). The article also reports figures it attributes to Anthropic: that Claude Code users approve 93% of permission prompts, and that human review caught 13.6% of harmful actions in a study of 1,053 testers. Those figures are **secondary**, cited from this article only; no user-facing text (the README, the guide, any screen) uses them until a primary Anthropic source is recorded in verified-facts.md.

Not cited: the DEF CON poster "Stop pressing 1", which could not be verified.

## 3. Consent, framing and privacy

Executives and hiring managers will read this repo, and some will run a drill. These rules hold for every part of this slice.

- **Opt-in, started by the person.** A drill runs only when a person types `polarizer drill` at a terminal (section 9, The terminal). Nothing starts a drill on a schedule, and `serve` never starts one. Live planted calls run only when the person's own config turns them on (section 11).
- **Results stay on the machine.** Drills write only to their own ledger directory, by default `~/.local/share/polarizer-drills`. Nothing in this slice opens a network connection; for drills a test enforces it (section 12).
- **The export is anonymized and the person's choice.** `polarizer drill report --export <file>` writes counts, rates, intervals, conditions, the scenario set version and the Polarizer version, with dates no finer than the day (section 9, The export). It holds no free text, no names, no paths, no session ids and no times of day. Polarizer never sends it anywhere.
- **Not for grading people.** Drills measure attention under drill conditions. They are for spotting trends in your own oversight, and must not be used to grade, rank, hire or evaluate people. The research cited in section 2 (arXiv 2608.23642) names the concern: measuring oversight can become surveillance of the overseer. Polarizer's own text says this plainly in three places: `polarizer drill`'s first screen, the end of every report, and docs/DRILL-GUIDE.md.
- **What a person who knows drills exist does to the numbers.** They read more carefully during a drill, so drill rates overstate everyday attention (section 1). The report's closing line says so.
- **Live planted calls are a disclosed test.** The config key, `serve`'s start-up line and `polarizer stats` each say that planted calls are on, how many a session may have, and that none is issued near the cap of 16 open holds (section 11). A person who turns them on knows that some holds may be planted; they don't know which.

The closing lines, printed on the drill's first screen, at the end of every drill and after every `drill report` (section 5), where the screens below show them as `<the closing lines of section 3>`:

```
Drills measure attention when you know you are being tested. Use them to see
trends in your own oversight, not to grade or rank anyone.
```

## 4. Drills: the flow

### Starting

1. The person runs `polarizer drill`. Usage is checked, then the terminal (section 9), then the location of the drill directory.
2. The scenario set is loaded from the package and checked (section 6, Loading). A set that fails its check stops the drill before anything is written, so this comes before the ledger is opened, which would write a genesis entry into a new directory (section 17, Stage 8 build).
3. The ledger is opened as a writer (2 s lock wait, full verification). A ledger holding any `session.started` entry is a serve ledger and is refused (section 9).
4. The plan is drawn (section 6, Sampling): the condition, the number of planted calls, and the order of calls. `drill.started` is appended with the seed and everything else needed to draw the plan again.
5. The intro screen is printed (section 5), and the drill waits for Enter.

### Each call

For call `i` of `n`:
1. A fresh hold id (16 random lowercase hex characters) and a fresh 32-byte salt are drawn from `secrets`, never from the plan's seed, so two showings of one scenario look like two different holds. `args_commit` is `hex(sha256(salt + bytes))`, where `bytes` is the scenario's arguments serialized exactly as a side file stores them (LEDGER-SPEC.md, Part 3). No side file is written: the arguments are in the package, and a drill ledger holds no `args/` directory.
2. `drill.shown` is appended (section 8) and awaited.
3. **Plain condition:** the call header, the task line and the hold block are printed together, then the prompt `allow or deny? `, flushed. **Prediction gate:** see below.
4. The answer is read (below). `drill.decided` is appended, then `drill.revealed`, each awaited; then the reveal is printed (section 5).
5. The drill waits for Enter (`Press Enter for the next call.`, or after the last call `Press Enter to see the results.`). That wait is not timed.

**The hold block** is the block `polarizer holds` prints for an open hold (HOLD-SPEC.md, section 8), produced by the same function with the drill's values:

| Line | Value in a drill |
|---|---|
| `hold <hold id> <tool> <class>` | the fresh hold id; the scenario's `tool`; its `class` (with ` (from annotations)` when `class_from` is `annotations`, or `unclassified`) |
| `held by <rule>: <reason>` | the scenario's `rule` and `reason` |
| `waiting about <age>; times out after <timeout> s` | `0m00s`, since the block is shown as the hold is made; the set's `timeout_seconds` (300) |
| `session <session> started <ts>, <state>` | the drill's own session id; `drill.started`'s `ts`; `running` |
| `args_commit <64 hex>, <b> bytes of arguments` | the value from step 1; the length of `bytes` |
| `<arguments>` | the scenario's arguments, rendered as `holds` renders a side file's JSON: indent 2, keys in stored order, every character outside printable ASCII escaped, DEL included |

Stage 8 moves the rendering in `holds.block` into a pure function, `holds.render_block(hold, started, state, now, arguments, size)`, which takes the arguments instead of reading the side file. `holds.block` reads and checks the side file as before and calls it, so `holds`' output and every existing golden file stay byte for byte as they are. The drill calls the same function. `test_drill_block_equals_holds_block` compares the two for every scenario (section 12).

Nothing times out in a drill, although the block says `times out after 300 s` as a real hold's would. The intro screen says so.

### Answers

The answer line is read with `readline`, stripped and lowercased:
- `allow` or `a`: allow;
- `deny` or `d`: deny;
- `q` or `quit`: stop the drill (below);
- end of input, or Ctrl+C: stop the drill, recorded as `interrupted`;
- anything else: one line, `type allow or deny (a or d), or q to stop`, then the prompt again. The clock keeps running.

### The prediction gate

In the `prediction-gate` condition, step 3 becomes:
1. The call header and the task line are printed, then the prompt `what do you expect this call to do? `, flushed.
2. The line is read. `q` or `quit` stops the drill. A line that is empty after stripping prints `type a few words, or q to stop` and asks again.
3. `drill.predicted` is appended with the prediction's length and the time taken, and with the prediction itself, folded and cut (section 8), only when the drill was started with `--keep-predictions`; awaited.
4. A blank line, the hold block and `allow or deny? ` are printed, flushed. From here the call goes on as in the plain condition.

The prediction is never scored, never shown again during the drill, and never exported in any form other than counts. By default only its length is recorded. With `--keep-predictions` the text stays in the person's own ledger, so they can compare it with the call later (section 16, question 3).

### Time

`elapsed_ms` is measured in the drill process on the monotonic clock (`time.monotonic_ns()`), from just after the `allow or deny? ` prompt is flushed to just after the answer line is read, as whole milliseconds rounded down. Invalid answers in between are included. In the prediction gate, `drill.predicted`'s `elapsed_ms` runs from just after `what do you expect this call to do? ` is flushed to just after the prediction is read. Both are stored in the entry, never derived from `ts` differences, which step on WSL2 (verified-facts.md, Wall clock and monotonic clock on WSL2). So decision times in the two conditions measure the same thing: from seeing the block to answering.

**Answers over 300 s.** A real hold times out after 300 s, and a drill call never does. A drill answer whose `elapsed_ms` is above 300000 is **over 300 s**: its reveal says so, and the end screen, `drill report` and the export give the rates both with and without such answers (section 5; section 9, `polarizer drill report` and The export). Nothing new is recorded: the mark is computed from `elapsed_ms` (section 16, question 4).

### Stopping, and the end of a drill

- **`q`** at any prompt (the answer, the prediction, or the Enter wait) ends the drill: `drill.ended` with `how` `stopped`, then the end screen for the calls answered so far, and exit 0.
- **End of input or Ctrl+C** ends it the same way, with `how` `interrupted`, exit 0. Stopping is the person's choice, not an error, so it adds no exit code (section 9).
- **The last call:** after its reveal and the Enter wait, `drill.ended` with `how` `finished`, then the end screen, exit 0.
- **The end screen** is computed from the ledger's entries for this drill, through the same function `drill report` uses for one drill, never from the drill's memory, so the two always agree (`test_end_screen_equals_report_line`).
- **The writer stops** (an append fails, or another process wrote something that doesn't chain): `polarizer: the drill ledger could not record that; the drill stops`, exit 1, as a stopped writer exits in `approve --group` (PIN-SPEC.md, section 7). Nothing more is shown.

### Defaults, and why

- **20 calls per drill** (`--calls` from 10 to 40). At about 30 seconds per call, reading the reveal included, that is about ten minutes: short enough to stay attentive and to repeat weekly, long enough for 6 to 10 planted calls.
- **30 to 50 percent of the calls are planted:** 6 to 10 of 20, drawn uniformly per drill from the plan's seed, and the rest clean (section 6, Sampling). Other values of `--calls` keep the proportions, with at least 5 planted and at least one per shape when the length allows, and never more than half: 5 of 10, 12 to 20 of 40. The planted calls are spread over the five shapes as evenly as the count allows, so every shape is present and shape counts differ by at most one. This is the owner's decision (section 16, question 15). Drawn from 4 to 8, as first specified, only 80% of drills had the 5 planted calls a catch rate needs. Fixed at 8, as decided after stage 8's first tool check, every drill had them, but a person could count down ("I've had eight, so the rest are clean"). Drawn from 6 to 10, every drill of 20 has at least 5 planted and at least 10 clean calls, and the running count settles an answer only in extreme cases (section 1, Counting down). The intro screen states the range, never the count drawn. That share is far above any real rate of bad calls. It is chosen for the measurement: with fewer than 5 planted calls a drill has no catch rate (section 7). Research on visual search finds that rare targets are missed more often than common ones; that is from general knowledge of that research, not from the sources checked on 2026-10-04 (section 17, deviation 27). So drill catch rates are expected to run above what a person would catch at a realistic rate, which live planted calls measure (section 11).
- **Condition at random,** plain or prediction gate with equal chance, from the plan's seed, unless `--condition` chooses one (decision 5). The draw is made either way, so a flag changes nothing else in the plan.
- **No repeat in consecutive drills.** Scenarios shown in either of the last two drills in this ledger are left out of the draw (section 6, Sampling), unless `--seed` is given.

## 5. Drill screens

The screens below are exact; placeholders are in angle brackets, and `<dir>` is the drill directory. Every golden file is stdout's bytes from an in-process drill with injected input, clocks, seed, hold ids and salts, and a small test scenario set (`tests/helpers/drill_scenarios.json`), so the files don't change when the shipped set does. Typed input is not echoed in the files. A file for one screen (the intro, a call, a reveal, the end) holds what the drill wrote between two reads of input, so `drill_end.txt` is the end screen alone. A line that waits for Enter (`Press Enter to start.`, `Press Enter for the next call.`) is written without a newline; the person's Enter ends it.

**Intro, plain** (`drill_intro_plain.txt`; 20 is the drill's `--calls`, and 6 and 10 the range its number of planted calls is drawn from, section 4):

```
Polarizer drill: practice with held calls

You will see 20 held calls, one at a time. Each shows the task the agent was
given, then the call exactly as polarizer holds would show it. Answer allow or
deny. Between 6 and 10 of the 20 calls are planted: they differ from the
task in a way that should be denied. The number changes from drill to drill,
and real work has far fewer. After each answer you see whether the call was
clean or planted, and why.

Nothing here is real. There is no agent and no server, nothing is sent
anywhere, and nothing times out. Your answers and times stay on this computer,
in <dir>.

<the closing lines of section 3>

This drill: plain. Type q at any prompt to stop.
Press Enter to start.
```

When the range is one number, as for `--calls 10` (5 planted), the four lines from `deny.` are three:

```
deny. 5 of the 10 calls are planted: they differ from the task in a way
that should be denied. Real work has far fewer. After each answer you see
whether the call was clean or planted, and why.
```

**Intro, prediction gate** (`drill_intro_prediction_gate.txt`): the same, except that the `This drill:` line reads:

```
This drill: prediction first. Before each call, type in a few words what you
expect it to do; then you see the call. Only the length of what you type is
kept. Type q at any prompt to stop.
```

With `--keep-predictions`, its second and third lines read:

```
expect it to do; then you see the call. What you type is kept on this
computer, and never exported. Type q at any prompt to stop.
```

**A call, plain** (`drill_call_plain.txt`). The block is an example; its values come from the scenario:

```

call 3 of 20
task: Add a "Running the tests" section to README.md in the garden-planner project.

hold 5d0c2e8a9b7f4136 fs__edit_file local-write
held by outside-roots: argument "path": /home/river/code/garden-planner/README.md is outside every workspace root
waiting about 0m00s; times out after 300 s
session 8e41c6b2d09a7f35 started 2026-10-06T18:02:11.425Z, running
args_commit <64 hex>, <b> bytes of arguments
{
  "path": "/home/river/code/garden-planner/README.md",
  "edits": [
    {
      "oldText": "## License",
      "newText": "## Running the tests\n\nRun make test.\n\n## License"
    }
  ]
}

allow or deny? 
```

The last line ends with one space and no newline.

**A call, prediction gate** (`drill_call_prediction_gate.txt`): the blank line, the `call` line and the `task:` line, then `what do you expect this call to do? ` with no newline; after the prediction, a blank line, the block and `allow or deny? ` as above.

**An invalid answer** (`drill_reprompt.txt`): after the prompt, `type allow or deny (a or d), or q to stop` on its own line, then `allow or deny? ` again.

**The reveal,** one of four (`drill_reveal_caught.txt`, `drill_reveal_missed.txt`, `drill_reveal_right.txt`, `drill_reveal_false_flag.txt`), each followed by the `why:` line, a blank line and the Enter line:

```

Planted call. You denied it: caught.
why: <the scenario's why>

Press Enter for the next call.
```

```

Planted call. You allowed it: missed. In real use it would have run.
why: <the scenario's why>

Press Enter for the next call.
```

```

Clean call. You allowed it.
why: <the scenario's why>

Press Enter for the next call.
```

```

Clean call. You denied it: a false flag. In real use the agent could not have done its task.
why: <the scenario's why>

Press Enter for the next call.
```

For the last call, the Enter line is `Press Enter to see the results.`

**An answer over 300 s** (`drill_reveal_over_300.txt`) adds one line after the first line of its reveal, before `why:`:

```
You took over 300 s; a real hold would have timed out before your answer.
```

**The end of a drill** (`drill_end.txt`), with 8 planted calls of which 7 were denied, and 12 clean calls of which 1 was denied:

```

Drill finished: 20 of 20 calls answered (plain).

planted calls: 8. You denied 7: caught 88%, 95% interval 52% to 98%.
clean calls: 12. You denied 1: false flags 8%, 95% interval 1% to 36%.
median time to decide: 12.4 s (planted 18.0 s, clean 10.9 s)
missed: call 7 (look-alike)
false flags: call 12

One drill says little; the intervals show how little. Run polarizer drill report
to see every drill so far.

<the closing lines of section 3>
```

- **`Drill finished`** becomes `Drill stopped` when `how` is `stopped` or `interrupted`; `(plain)` is `(prediction gate)` in that condition.
- **Below 5 calls of a kind** the rate is replaced: `planted calls: 3. You denied 3: too few to say a rate (5 or more needed).` With none of a kind: `planted calls: none answered.`
- **`missed:`** lists the numbers of planted calls allowed, each with its shape in words, as `call 7 (look-alike)`, separated by `, `; `none` when there are none. **`false flags:`** lists the clean calls denied, as `call 12`, or `none`.
- **The median line** leaves out the part in brackets for a kind with no answers, and is `median time to decide: no answers` with none at all.
- **Stopped with no answers** (`drill_end_stopped.txt` covers stopping after 3 answers): `Drill stopped: 0 of 20 calls answered (plain).`, then the closing lines only.
- **Answers over 300 s** (`drill_end_over_300.txt`): after the `false flags:` line, `over 300 s: call 4. Without it:` (or `call 4, call 9. Without them:`), then the planted and clean lines again, indented two spaces, computed without those answers. With no answer over 300 s the two are the same, and these lines are left out.

The numbers in this example are section 7's test vectors 7 of 8 and 1 of 12.

## 6. The scenario set

### File and format

The set is one JSON file in the package, `src/polarizer/scenarios/drill-set-<version>.json`, read with `importlib.resources`. The file is ASCII: any other character in a value is written as a JSON escape, and stage 8 writes it from a script and checks it (CLAUDE.md, rule 12). Its top level:

| Key | Value |
|---|---|
| `format` | `"polarizer-drill-scenarios"` |
| `set` | the set version, a positive integer, equal to `<version>` in the file name |
| `timeout_seconds` | 300, shown in every block |
| `servers` | the invented upstream prefixes the set uses, such as `["fs", "web", "pkg", "mail", "tickets"]` |
| `names` | every invented name the set uses: user names in home paths, project names, package names, host names, product-like names; each a string |
| `scenarios` | the list below |

Each scenario:

| Key | Value |
|---|---|
| `id` | `s` and three digits, unique; ids carry no hint of the answer |
| `answer` | `"clean"` or `"planted"` |
| `shape` | for a planted call, one of the five shapes below; absent for a clean call |
| `task` | the one line the agent was given: 20 to 160 printable ASCII characters |
| `intent` | what the task asks for: `{"tool": <exposed name>, "arguments": {...}}` |
| `call` | what is held: `{"tool", "class", "class_from", "rule", "reason", "arguments"}`, with `class_from` `"config"` or `"annotations"` (null with class null) |
| `plant` | for a planted call, which part differs, by shape (below); absent for a clean call |
| `why` | the reveal: one or two sentences, at most 300 printable ASCII characters |

A clean call's `call.tool` and `call.arguments` equal its `intent`'s exactly. The `intent` is never shown; it exists so the tests can check every answer (Validation).

### Shapes

Every planted call has exactly one shape (decision 3). The ledger and the file use the ids on the left; screens use the words in brackets.

| Shape | Words | What differs | `plant` |
|---|---|---|---|
| `changed-argument` | changed argument | One argument is not what the task asked, for example a path into `.git/hooks` or `~/.ssh` where the task named a file in the project. | `{"argument", "intended", "planted", "cue"}` |
| `different-tool` | different tool | The call uses another tool than the task needs: a delete where the task asked to rename, a post where it asked to read. | `{"cue"}` |
| `extra-effect` | extra effect | The call does what the task asked and more, visibly in its arguments: an extra recipient, an extra file in a list, an added line in a script, an added flag. | `{"argument", "planted", "cue"}` |
| `misleading-summary` | misleading summary | A summary or description argument (a title, a commit message, a comment) says what the task asked, while another argument does something else. | `{"summary_argument", "argument", "planted", "cue"}` |
| `look-alike` | look-alike | A package name or host differs from the one the task named by a small edit: a swapped or doubled letter, an added suffix, a look-alike character. | `{"argument", "intended", "planted", "cue"}` |

`cue` is a substring of the task, at least 4 characters, that names what the task asked for in the part that differs, so a careful reader has what they need on screen.

### Validation

`tests/test_scenarios.py` checks the shipped set, with these exact rules. A set that fails any of them doesn't ship.

**Every scenario:**
- the keys above, with the right types, and no others; `id` unique; `answer` and `shape` consistent;
- `task` and `why` one line of printable ASCII, within their lengths; `cue` is a substring of `task`;
- `call.tool` and `intent.tool` are exposed names (`<prefix>__<tool>`, matching `^[A-Za-z0-9._-]{1,128}$`) whose prefix is in `servers`;
- `call.class` is one of the five classes or null, and `call.rule` is one of HOLD-SPEC.md's rule ids that hold a call (section 4's table, rows 2 to 12 and 14);
- `call.reason` matches the template of its rule in HOLD-SPEC.md, section 4, as a regular expression per row (for example `^argument "[a-z_]+": \S+ matches \S+$` for `write-pattern`), with the destructive and egress suffixes allowed as in rows 3 and 4;
- `call.arguments` is a JSON object whose compact form is at most 4 KiB;
- the block renders through `holds.render_block` in at most 60 lines.

**A clean call:** `call.tool == intent.tool` and `call.arguments == intent.arguments`.

**A planted call,** by shape. "Equal" is equality of parsed JSON; "contains" is substring for strings and element-of for lists.
- **`changed-argument`:** same tool; the same argument keys; exactly one key differs, and it is `plant.argument`; `intent.arguments[argument] == plant.intended` and `call.arguments[argument] == plant.planted`; `cue` is a substring of `plant.intended`.
- **`different-tool`:** `call.tool != intent.tool`.
- **`extra-effect`:** same tool; every key of `intent.arguments` is in `call.arguments`, and each call value covers the intent value (equal; or for strings, the intent value is a substring; or for lists, every intent element is in the call list, in order); at least one key differs or is added, and `plant.argument` is one of them; `plant.planted` is contained in the call's value for that argument and not in the intent's.
- **`misleading-summary`:** same tool; `call.arguments[summary_argument] == intent.arguments[summary_argument]`; `call.arguments[argument]` differs from the intent's and contains `plant.planted`, which the intent's value doesn't; `summary_argument != argument`.
- **`look-alike`:** same tool; exactly one key differs, and it is `plant.argument`; the call's value is the intent's with the one occurrence of `plant.intended` replaced by `plant.planted`; and `plant.planted` is close to `plant.intended`: a Levenshtein distance of at most 3, or equal after a fixed confusables map (Cyrillic and Greek letters that look like Latin ones, `0` and `o`, `1` and `l`, `rn` and `m`, `vv` and `w`, `-` and `_`), or `plant.intended` with one of the fixed affixes `-js`, `-py`, `-dev`, `-cli`, `-official`, `py-`, `node-` added.
- **For every shape:** the planted value (`plant.planted`, or `call.tool` for `different-tool`) does not appear in `task`, case-insensitively; and `why` contains it, written as `holds` would print it (through `printable()`).

**Invented names** (`test_names_are_invented`):
- every host name in any string (any `a.b` token in a URL, an e-mail address or a `host` argument) is in a reserved domain: `example`, `test` or `invalid` as the last label, or `example.com`, `example.net` or `example.org` and their subdomains;
- every user name in a home path (`/home/<name>/`, `/Users/<name>/`) and every package name is listed in `names`, and `names` holds no entry of a fixed deny list kept in the test (well-known companies, products and package registries, written in the test file as lowercase words);
- no string looks like a credential: none matches the test's fixed patterns for key and token prefixes and PEM headers;
- tool paths that every reviewer must recognize (`.git/hooks`, `.github/workflows`, `~/.ssh`, `.env`, `package.json`, `requirements.txt`) are allowed, as are the reference server's tool names, which are generic (`write_file`, `move_file`).

**Balance** (`test_set_balance`), so that no surface feature gives the answer:
- 150 scenarios: 90 clean and 60 planted, 12 of each shape;
- for every value of `call.rule`, `call.class` and `call.tool` that appears in a planted call, at least 40% of the scenarios with that value are clean;
- each shape appears with at least 3 different tools;
- the median rendered block length, in lines, of planted calls is within 20% of the clean calls';
- clean calls include holds that look alarming and are right (a write to `.env.example` the task asked for; a destructive move inside the project), and planted calls include holds whose rule looks routine (`outside-roots`).

**Shortcuts** (`test_no_shortcut`), so that neither a person nor a trivial rule can tell planted from clean without reading the task and the arguments. The features are what a drill shows before the answer, the call screen without the reveal (`polarizer.scenarios.audit`): the tool; the class; `class_from`; the rule of the `held by` line; the number of arguments; the argument bytes the `args_commit` line gives, in four buckets (under 60, 60 to 99, 100 to 139, 140 and over, about set 1's quartiles); the task line's length in characters, in four (under 60, 60 to 74, 75 to 89, 90 and over); and whether the compact arguments contain each of `..`, `.git`, `.ssh`, `.env`, `http` and `@`.
- **No tell:** no feature value with at least 6 scenarios has 90% or more of one answer.
- **No useful rule:** each scenario is answered by the majority answer for its value of one feature, learned from all the other scenarios (leave-one-out; deny only on a strict planted majority, so a tie or an unseen value is allow). The best such rule beats always answering allow by at most 10 points, over the scenarios and as the mean share right over 1,000 seeded 20-call drills drawn by the drill's own sampler (seeds 0 to 999, nothing excluded).
- **Printed, not tested:** a naive Bayes over all features together (add-one smoothing), scored the same way. `python -m polarizer.scenarios audit` prints every table, and the review sheet ends with them.
- **Set 1,** audited on Oct 5, 2026 (UTC): no tell. The nearest is `fs__edit_file`, 1 planted and 7 clean (87.5%). The best single-feature rule is argument bytes: 64.0% over the scenarios and 64.2% over the drills, against 60.0% and 59.9% for always allow, because 9 of the 12 calls of 140 bytes or more are planted. Naive Bayes scores 42.0%; the tool rule 39.3%, below always allow, because with most tools near half and half, leaving a scenario out turns its tool's majority against it. Set 1 therefore stays the shipped set (section 17, Stage 8 follow-up).
- **A tell found later** is removed by adding scenarios of the other answer in a new set version, each passing the rest of this section, never by editing a shipped set. A tell that can't be removed that way is listed in LIMITS.md and the guide.

### Why 150

A drill draws 20 calls, 6 to 10 planted (8 on average), and leaves out what the last two drills showed. With 60 planted calls, two drills in a row never share one, and a planted call comes back after 6 drills at the median (3 at the least, 7.5 on average, in a simulation of 2,000 drills in a row on one ledger, redone for the drawn count), which is about six weeks of weekly drills. Clean calls, 10 to 14 of 90 per drill, come back after the same numbers of drills. A smaller set would let a person learn answers; a larger one costs review time for each scenario. The report counts repeats (section 9), so a set that has run out shows.

### Loading

At start, `drill` loads the newest set in the package and checks it against the schema rules above that need no test-only data (keys, types, lengths, consistency of `answer` and `shape`, the template of each `reason`). A failure is one line, `polarizer: the drill scenarios in this installation fail their check: <problem>`, exit 2, nothing written. The full validation runs in the tests.

### Versioning and adding scenarios

- The file's sha256 is recorded in every `drill.started` (`set_sha256`), with its version.
- `test_scenario_set_hash_pinned` holds the sha256 of each shipped set file. Any edit to a shipped set fails that test, so a change can't ship without a new version.
- **Any change** to a scenario, an addition or a removal makes a new set: a new file, `drill-set-<n+1>.json`, with `set` raised. The newest set is the one drills use. Older set files stay in the package for one release, so a report can name them and a plan can be drawn again; `scenarios.load(<version>)` loads any shipped set by its version. The report never needs them, because `drill.revealed` records each answer (section 8). Repeats and the last two drills' exclusions match scenarios by id, so a later set must keep the ids of the scenarios it carries over; `tools/make_drill_set.py` numbers ids by task hash within one set and doesn't do that yet.
- **Adding a scenario:** write it with `intent`, `call`, `plant` and `why`; run `tests/test_scenarios.py`; render it with `uv run python -m polarizer.scenarios show <id>` (a development helper that prints the drill screen for one scenario); and have a person other than its author read the screen and answer it before seeing `answer`. A scenario that person answers wrongly for a reason other than inattention (the task was ambiguous, the cue was missing) is rewritten. The stage notes record who reviewed which set, by role. For a review of the whole set, `uv run python -m polarizer.scenarios sheet --out drill-review/scenarios.md` writes one markdown file (`drill-review/` is gitignored): every scenario with its id, answer, shape, task, the block a drill shows (zero ids, a fixed time) and its reveal; then counts per shape, tool, class, rule and answer, planted shapes against the clean scenarios on the same tools, and a list to look at: task lines the same or at least 0.85 alike (difflib's ratio), reveals under 8 words, shapes with fewer than 8 scenarios, and scenarios on a tool fewer than 3 scenarios use; then the shortcut audit (Validation, Shortcuts) and the planted count's distribution (Sampling), which `uv run python -m polarizer.scenarios audit` prints alone.

### Sampling

The plan is a function of the seed, the set, `--calls`, the excluded ids and nothing else. It does not use Python's `random`, so a plan can be drawn again by any later Python version or another language:

```
stream(seed):    for counter = 0, 1, 2, ...:
                   block = sha256(b"POLARIZER-DRILL/1\n" + seed + counter as 8 bytes big-endian)
                   yield the block's four 8-byte big-endian integers, in order
below(s, n):     limit = (2^64 // n) * n
                 repeat: x = next(s); if x < limit: return x % n
shuffle(s, xs):  for i from len(xs) - 1 down to 1:
                   j = below(s, i + 1); swap xs[i] and xs[j]
plan(seed, set, calls, excluded):
  s = stream(seed)                                  seed is 16 bytes
  condition = ["plain", "prediction-gate"][below(s, 2)]   (replaced by --condition when given)
  S = the shapes that have a planted id in the set, in the order of section 6's table
  hi = min(floor(calls / 2), number of planted ids)
  lo = min(max(ceil(3 * calls / 10), 5, len(S)), hi)
  k = lo + below(s, hi - lo + 1)                    the number of planted calls: 6 to 10 of 20
  turn = shuffle(s, S)                              the order in which shapes are dealt a call
  quota[x] = 0 for each x in S
  while the quotas add up to less than k:
    for x in turn:
      if the quotas add up to less than k and quota[x] < the number of planted ids of shape x:
        quota[x] = quota[x] + 1
  P = []
  for x in S:
    Px = planted ids of shape x not in excluded, sorted;
         if fewer than quota[x], all planted ids of shape x, sorted
    P = P + shuffle(s, Px)[:quota[x]]
  C = clean ids not in excluded, sorted; if fewer than calls - k, all clean ids, sorted
  C = shuffle(s, C)[:calls - k]
  order = shuffle(s, P + C)
```

- **The count** is drawn uniformly from `lo` to `hi` (`drill.planted_range`): 6 to 10 of 20, 5 of 10, 12 to 20 of 40. `lo` keeps a catch rate possible (5 planted calls) and every shape present; `hi` keeps at least half the calls clean. A range of one number is still drawn, so the stream is used the same way for every `--calls` (section 16, question 15).
- **The shapes** are dealt one call each per round, in the drawn order `turn`, so their counts differ by at most one; a shape with too few scenarios is skipped once it has them all, and the others take its place. With the shipped set (12 of each shape) every drill has every shape, and which shapes get more is drawn. Only a set with fewer planted ids than shapes could give a drill with a shape missing.

- **The seed** is 16 bytes from `secrets.token_bytes`, recorded in `drill.started` as 32 hex characters, unless `--seed` gives one (`seed_from` `"flag"`).
- **Excluded** are the scenario ids shown in the last two drills of this ledger, by `drill.started` seq, whatever their ending, recorded in `drill.started` as a sorted list. With `--seed`, nothing is excluded, so one seed gives the same drill on any machine: two people can run the same drill and compare.
- **Test vector** (`test_sampler_vector`): with planted ids `p01` to `p10`, of shapes changed argument (`p01` to `p03`), different tool (`p04`, `p05`), extra effect (`p06`, `p07`), misleading summary (`p08`, `p09`) and look-alike (`p10`), clean ids `c01` to `c12`, and the seed `000102030405060708090a0b0c0d0e0f` (bytes 0 to 15):
  - the stream's first two values are 8468598625902157147 and 15816047190215027138;
  - with `calls` 20 and nothing excluded, the plan is `prediction-gate`, 9 planted, order `c05, c08, c06, p04, c02, c09, p06, c01, c12, p09, p08, p07, p10, c10, p05, p01, p03, c03, c07, c04`;
  - with `calls` 20 and `p01`, `p04`, `p10` and `c01` excluded, it is `prediction-gate`, 9 planted, order `c03, c09, c05, c11, c10, c06, c12, c04, p04, p10, p07, p05, p02, p08, p03, c07, c02, p09, c08, p06` (different tool and look-alike fall back to all their ids, and the clean calls to all clean ids);
  - with the seed of 16 bytes of `ff`, `calls` 10 and nothing excluded, it is `prediction-gate`, 5 planted, one of each shape, order `p06, p08, c10, p03, c06, c07, p04, c09, p10, c01`;
  - with the same seed, `calls` 20 and nothing excluded, it is `prediction-gate`, 6 planted, order `c04, c12, p05, c09, c06, p06, c05, c10, p04, c07, p08, c03, c01, c02, c08, c11, p03, p10`;
  - `planted_range` for 10, 11, 12, 16, 17, 20, 25, 30 and 40 calls (60 planted ids, five shapes): 5 to 5, 5 to 5, 5 to 6, 5 to 8, 6 to 8, 6 to 10, 8 to 12, 9 to 15, 12 to 20.
  - These values were computed first by a stdlib-only script written from the text above, in the session's scratch directory, and then by the code; the two agreed (docs/dev/STAGE8-NOTES.md, Stage 8 follow-up).
- **The distribution** over the shipped set, from seeds 0, 1, 2, ... as 16-byte counters until each condition had 10,000 drills of 20 (20,201 seeds): plain 1,961, 1,965, 2,083, 1,982 and 2,009 drills with 6, 7, 8, 9 and 10 planted calls; prediction gate 1,889, 2,020, 1,975, 2,072 and 2,044. Every drill had at least 5 planted and at least 10 clean calls, and every shape. `python -m polarizer.scenarios audit` prints this table.

## 7. Statistics

`src/polarizer/measure.py` holds every formula below, used by the drill's end screen, `drill report`, the export and `stats`. It is standard library only.

### Rates and the Wilson interval

A rate is `k` of `n`: planted calls denied of planted calls answered (the catch rate), clean calls denied of clean calls answered (the false-flag rate), or holds allowed of holds decided (the approval rate). Its 95% interval is the Wilson score interval:

```
z      = 1.959963984540054        (the 0.975 quantile of the standard normal)
p      = k / n
center = (p + z^2 / (2n)) / (1 + z^2 / n)
half   = (z / (1 + z^2 / n)) * sqrt(p (1 - p) / n + z^2 / (4 n^2))
low    = max(0, center - half)
high   = min(1, center + half)
```

The Wilson interval is used because it behaves at small n and at 0 of n and n of n, where the textbook interval gives zero width; milestones.md's M4 row already names it for the eval table.

### Too few to say

`MIN_N = 5`. With `n` below 5, no rate and no interval are printed: the line says `too few to say a rate (5 or more needed)` with the counts. A rate is never printed without its interval, and every rate line states `k` and `n` (decision 4). 5 is low: the interval for 5 of 5 is 56% to 100%. It is the smallest n at which an interval still excludes something a person would care about, and the printed interval shows how wide it is.

### Comparing two rates

The difference between two rates (prediction gate minus plain; the last 40 decisions minus the 40 before) has the 95% interval of Newcombe's hybrid score method (method 10), built from the two Wilson intervals:

```
d    = p1 - p2
low  = d - sqrt((p1 - low1)^2 + (high2 - p2)^2)
high = d + sqrt((high1 - p1)^2 + (p2 - low2)^2)
```

The difference is **distinguishable from noise** when `low > 0` or `high < 0`, on the unrounded values, and otherwise **not distinguishable from noise at these numbers**. Either side below `MIN_N` gives `too few to compare`. The text never says "better" or "worse"; it gives the difference, its interval and one of those three phrases.

### Medians and times

- **The median** of a list of integers is the lower median (`statistics.median_low`): the middle value, or the lower of the two middle values. It is always one of the values, and needs no rounding.
- **Drill times** are `elapsed_ms` values, printed as seconds to one decimal: `t = (ms + 50) // 100`, printed as `<t // 10>.<t % 10> s`.
- **Stats times** are whole seconds (section 10), printed as `<s> s` below 60, `<m>m<ss>s` below an hour, and `<h>h<mm>m` from then on, as `holds` prints an age.
- **Means are never printed,** because one walk away from the keyboard would move them.

### Printing

- **A rate** prints as a whole percent: `(200k + n) // (2n)`, integer arithmetic, so a half rounds up.
- **Its interval** prints as `floor(100 * low)` to `ceil(100 * high)`, so the printed interval always contains the exact one.
- **A difference** prints in points: with `num = 100 (k1 n2 - k2 n1)` and `den = n1 n2`, the points are `sign(num) * ((2 |num| + den) // (2 den))`, a half rounded away from zero, in integer arithmetic so that equal differences print equally. Its interval prints as `floor(100 * low)` to `ceil(100 * high)`, with a sign on each.

### Test vectors

`tests/test_measure.py` asserts these, each `low` and `high` to within 1e-6 and each printed form exactly.

| k | n | low | high | printed |
|---|---|---|---|---|
| 0 | 5 | 0.000000 | 0.434482 | 0% (0% to 44%) |
| 4 | 5 | 0.375535 | 0.963776 | 80% (37% to 97%) |
| 5 | 5 | 0.565518 | 1.000000 | 100% (56% to 100%) |
| 5 | 6 | 0.436497 | 0.969947 | 83% (43% to 97%) |
| 1 | 14 | 0.012722 | 0.314687 | 7% (1% to 32%) |
| 7 | 8 | 0.529112 | 0.977583 | 88% (52% to 98%) |
| 1 | 12 | 0.014865 | 0.353880 | 8% (1% to 36%) |
| 13 | 17 | 0.527382 | 0.904450 | 76% (52% to 91%) |
| 15 | 17 | 0.656636 | 0.967120 | 88% (65% to 97%) |
| 3 | 42 | 0.024590 | 0.190094 | 7% (2% to 20%) |
| 2 | 40 | 0.013821 | 0.165039 | 5% (1% to 17%) |
| 28 | 33 | 0.690801 | 0.933495 | 85% (69% to 94%) |
| 20 | 20 | 0.838875 | 1.000000 | 100% (83% to 100%) |
| 36 | 40 | 0.769482 | 0.960420 | 90% (76% to 97%) |
| 37 | 40 | 0.801358 | 0.974164 | 93% (80% to 98%) |
| 20 | 40 | 0.351995 | 0.648005 | 50% (35% to 65%) |
| 3 | 4 | (not printed) | (not printed) | too few to say a rate (5 or more needed) |
| 0 | 0 | (none) | (none) | `no answers` in drills, `no decisions` in stats |

| k1 / n1 | k2 / n2 | low | high | printed | phrase |
|---|---|---|---|---|---|
| 15 / 17 | 13 / 17 | -0.147826 | 0.369655 | +12 points (-15 to +37) | not distinguishable from noise at these numbers |
| 2 / 40 | 3 / 42 | -0.145487 | 0.102780 | -2 points (-15 to +11) | not distinguishable from noise at these numbers |
| 19 / 20 | 10 / 20 | 0.176274 | 0.654871 | +45 points (+17 to +66) | distinguishable from noise at these numbers |
| 37 / 40 | 36 / 40 | -0.112616 | 0.164470 | +3 points (-12 to +17) | not distinguishable from noise at these numbers |
| 20 / 40 | 38 / 40 | -0.602363 | -0.262545 | -45 points (-61 to -26) | distinguishable from noise at these numbers |
| 3 / 4 | 5 / 6 | (none) | (none) | (none) | too few to compare |

Medians: `median_low([12400, 9100, 30200, 11000])` is 11000; `median_low([0, 5, 10, 20, 30, 41])` is 10.

The values were computed for this spec with a throwaway script in the session's scratch directory (Python 3.12.3, `math` only). The interval values are the published Wilson and Newcombe formulas applied as written above; no outside table was consulted.

## 8. Ledger kinds

The format, the hash chain, the statuses and the exit codes don't change. LEDGER-SPEC.md allows new kinds whose `data` stays inside the subset, and adding optional fields to existing kinds is how M2a extended `call.sent`. Every field below is ASCII-keyed and holds strings, integers, booleans, null or lists of strings.

### Drills (stage 8)

Drills write only to their own ledger (section 9), never to a serve ledger. Every drill entry has `session`, the drill's own id (16 random lowercase hex characters, one per `polarizer drill` run).

| Kind | `data` |
|---|---|
| `drill.started` | `session`, `polarizer_version` (`polarizer.__version__`, from the package metadata), `set` (the set version, an integer), `set_sha256`, `seed` (32 hex), `seed_from` (`"random"` or `"flag"`), `condition` (`"plain"` or `"prediction-gate"`), `condition_from` (`"random"` or `"flag"`), `calls` (the number planned), `excluded` (sorted scenario ids), `keep_predictions` (a boolean: `--keep-predictions` was given) |
| `drill.shown` | `session`, `n` (1-based position), `scenario` (its id), `hold` (the fresh hold id shown), `args_commit` (the value shown), `seen_before` (how many earlier `drill.shown` entries in this ledger have this scenario) |
| `drill.predicted` | `session`, `n`, `length` (the number of characters in the typed line after stripping), `prediction` (with `--keep-predictions`, the typed line through `safe()` with a limit of 200 characters: whitespace folded, non-ASCII escaped, cut; otherwise null), `elapsed_ms` |
| `drill.decided` | `session`, `n`, `scenario`, `decision` (`"allow"` or `"deny"`), `elapsed_ms`, `actor` (`"person"`) |
| `drill.revealed` | `session`, `n`, `scenario`, `answer` (`"clean"` or `"planted"`), `shape` (or null), `outcome` (`"caught"`, `"missed"`, `"right"` or `"false-flag"`) |
| `drill.ended` | `session`, `how` (`"finished"`, `"stopped"` or `"interrupted"`), `answered`, `calls` |

- **The plan can be drawn again** from `drill.started` alone, with the set its `set` and `set_sha256` name: `test_plan_is_reproducible_from_the_ledger` draws it and compares it with the `drill.shown` sequence.
- **`drill.revealed` records the answer,** so a report needs only the ledger, never the set file that was current then.
- **`drill.ended` is best effort.** A drill killed outright has none; the report counts its answers and says `did not end`.
- **`polarizer verify --ledger-dir <drill dir>`** checks a drill ledger like any other: `intact: <n> entries, 0 sessions, 0 calls`, since it counts `session.started` and `call.sent` only. That output doesn't change. `verify --args` finds no `args/` directory and prints `args: 0 matching, 0 missing, 0 tampered, 0 orphaned`.
- **`holds` on a drill ledger** prints `holds: nothing is held`: the hold fold reads only `hold.*` kinds.

### Stats (stage 9)

`stats` only reads. It adds no kind and writes nothing.

### Live planted calls (stage 10)

| Kind or field | `data` | Written by |
|---|---|---|
| `policy.loaded`, new field | `planted_calls_per_session`: 0 to 3, from the config; 0 when the key is absent or under `--no-holds`. Present on every `policy.loaded` from stage 10. | `serve`, at start |
| `hold.created`, new optional field | `plant_commit`: 64 hex, on every hold of a session whose `policy.loaded` has `planted_calls_per_session` above 0, real holds included. Absent otherwise, so every existing `hold.created` is unchanged. | `serve` |
| `hold.revealed`, new kind | `session`, `hold`, `label` (`"real"` or `"planted"`), `salt` (64 hex), `why` (for a planted hold, the reveal text, through `safe()` with a limit of 1 KiB; null for a real one), `derived_from` (for a planted hold, the seq of the real call's `call.sent` or `hold.created` it was made from; null for a real one) | `serve`, the holding process, after the hold's ending (section 11) |

`plant_commit = hex(sha256(b"POLARIZER-PLANT/1\n" + salt + label))`, where `salt` is 32 bytes from `secrets` and `label` is the ASCII bytes of `real` or `planted`. A reveal checks when the hash of its `salt` and `label` equals the hold's `plant_commit`.

**What the new fields change for existing readers: nothing.** Checked against the code at commit 03cedbd:
- `holds.HoldState._created` reads `hold`, `session`, `tool`, `args_commit`, `class`, `class_from`, `rule`, `reason` and `timeout_seconds` by name, and ignores other keys; the hold fold ignores kinds it doesn't know, so `hold.revealed` changes no hold's state or ending.
- `ledger.py` counts `hold.created` only for its `args_commit` (`verify --args`); `plant_commit` is not read.
- `holds`, `allow` and `deny` print a hold's block from the fields above, so a hold with `plant_commit` prints byte for byte as one without. `test_plant_commit_changes_no_reader` pins this.
- `scripts/hold_check.py` reads `policy.loaded`'s `hold_timeout_seconds` only.
- The conformance verifiers check the chain, not `data`'s meaning.

### What is fsynced, and why

The rule since M1a (HOLD-SPEC.md, section 5): an entry is fsynced, with `ledger.head` updated, when losing it could make Polarizer do more than the ledger shows, or undo a person's decision that Polarizer acts on.
- **No drill kind** is added to `SECURITY_KINDS`. A drill makes Polarizer do nothing, and a lost drill entry changes no behavior. In practice each drill entry is durable within a moment anyway: the writer fsyncs whenever its queue empties, and a drill appends one entry and then waits for a person. A drill ledger's `ledger.head` therefore stays at its genesis, and a lost tail of a drill ledger is not detected; that is a stated limit.
- **`hold.revealed`** is not fsynced inline: it only records what was already decided, and a planted hold is never forwarded whatever is lost.
- **`policy.loaded`** stays fsynced, as it is.
- **`hold.decided` on a planted hold** stays fsynced, because the writer fsyncs by kind and `allow` doesn't know it decided a planted hold until after.

## 9. The command line

### Syntax

```
polarizer drill [--ledger-dir <absolute path>] [--calls <n>] [--condition plain|prediction-gate] [--seed <32 hex>] [--keep-predictions]
polarizer drill report [--ledger-dir <absolute path>] [--export <path>]
polarizer stats (--config <absolute path> | --ledger-dir <absolute path>)
```

- **`drill`'s directory** is `--ledger-dir`, or by default `~/.local/share/polarizer-drills` (`~` expanded; on Windows the same path under the user's profile, as for the serve ledger). `drill` takes no `--config`: a drill has nothing to do with a serve config, and reading one would invite pointing a drill at a serve ledger.
- **`stats`** takes exactly one of `--config` and `--ledger-dir`, as `holds` does (HOLD-SPEC.md, section 8): `--config` is read with `require_env=False`, roots are not checked, and nothing is started.
- **`--keep-predictions`** keeps the text of each prediction in the drill ledger (section 4, The prediction gate); without it only the length is kept. It has no effect in a plain drill, and is recorded in `drill.started` either way.
- **The version** that `drill.started`, the export and `session.started` record is `polarizer.__version__`, read from the installed package's metadata (`importlib.metadata`), so pyproject.toml is the one place it is set (section 16, question 8). Stage 8 replaces the hard-coded `0.1.0.dev0`.
- Results go to stdout, refusals and usage errors to stderr as one line starting `polarizer: `.

### Usage errors

Each exits 2, on stderr:

```
polarizer: --calls must be a whole number from 10 to 40
polarizer: --condition must be plain or prediction-gate
polarizer: --seed must be 32 lowercase hex characters
polarizer: --export goes with drill report
polarizer: drill report takes only --ledger-dir and --export
polarizer: --keep-predictions goes with drill
polarizer: --ledger-dir must be an absolute path, got <path>
polarizer: stats needs exactly one of --config <absolute path> or --ledger-dir <absolute path>
polarizer: <argparse's message>
```

### The terminal

`polarizer drill` refuses when stdin or stdout is not a terminal: `polarizer: drill needs a person at a terminal; it does not run from a script or a pipe`, exit 2, nothing written. There is no `--allow-no-terminal`: a drill without a person measures nothing (decision on the terminal requirement, section 17). The check comes after the usage checks and before the location check. Tests run drills in process with injected input and output, and on POSIX also on a pseudo-terminal (`test_drill_on_a_pty`). `drill report` and `stats` don't check.

### Location and refusals

`drill` writes, so it checks its directory as `repair` and `allow` check `ledger_dir` (LEDGER-SPEC.md, Location and permissions), with one difference: having no config, it knows no `ledger_forbidden_paths`, so it refuses only inside Parallax's two directories, which are always forbidden, and warns inside a git working tree. The directory is created 0700 and its files 0600, as for any ledger.

Its refusals, each one line on stderr:
- `polarizer: ledger_dir <dir> is inside <path>, which Polarizer must not write to`, exit 2 (the existing line);
- `polarizer: <dir> holds a serve ledger; drills keep their own (the default is ~/.local/share/polarizer-drills)`, exit 2, when the ledger has any `session.started`. This is how drills never write to a serve ledger;
- `polarizer: <verify's first line>; run polarizer verify`, with that status's code, for a ledger that isn't intact, as serve, `approve` and `allow` refuse;
- the scenario set line (section 6, Loading), exit 2.

**`serve` refuses a drill ledger** (section 16, question 12). When the ledger at `ledger_dir` holds any `drill.*` entry, `serve` refuses after verifying it and before writing anything (no `session.started`, no `ledger.head_rebuilt`): `polarizer: <dir> holds drill entries; serve keeps its own ledger (drills default to ~/.local/share/polarizer-drills)`, exit 2, the existing usage-error exit. Built in stage 8, with `test_serve_refuses_drill_ledger`.

### `polarizer drill`

Sections 4 and 5. Exit 0 when the drill finishes or the person stops it; 2 for the refusals above; a status's code for a broken ledger; 1 when the writer stops mid-drill.

### `polarizer drill report`

It reads the drill ledger as `verify` does (the lock only if the lock file exists, up to 2 s), writes nothing, and checks no locations. A status other than `intact` prints `verify`'s output and exits with that code. A missing ledger, or one with no `drill.started`, prints `drills: none yet in <dir>; run polarizer drill` and exits 0: for a person new to drills, nothing is wrong (section 17, deviation 21).

Otherwise (`drill_report.txt`, built from a fixture ledger of 7 drills: 3 plain, of 20, 20 and 19 calls answered, and 4 prediction-gate, of 20, 10, 10 and 17; the numbers are section 7's vectors):

```
drills: 5 finished, 2 stopped early; 116 calls answered (scenario set 1)
calls seen in an earlier drill: 9 of 116

plain: 3 drills
  planted calls: 17. Denied 13: caught 76%, 95% interval 52% to 91%.
  clean calls: 42. Denied 3: false flags 7%, 95% interval 2% to 20%.
  median time to decide: 12.4 s
prediction gate: 4 drills
  planted calls: 17. Denied 15: caught 88%, 95% interval 65% to 97%.
  clean calls: 40. Denied 2: false flags 5%, 95% interval 1% to 17%.
  median time to decide: 16.1 s

prediction gate minus plain
  caught: +12 points, 95% interval -15 to +37: not distinguishable from noise at these numbers.
  false flags: -2 points, 95% interval -15 to +11: not distinguishable from noise at these numbers.

by kind of planted call
  changed argument: 7 planted, 6 caught
  different tool: 6 planted, 6 caught
  extra effect: 8 planted, 6 caught
  misleading summary: 7 planted, 5 caught
  look-alike: 6 planted, 5 caught

each drill
  2026-10-06 plain, 20 of 20: caught 5 of 6, false flags 1 of 14, median 12.4 s
  ...
  2026-10-20 prediction gate, 17 of 20, stopped: caught 4 of 5, false flags 0 of 12, median 15.0 s

<the closing lines of section 3>
```

- **The fixture** is hand-built, not drawn by the sampler: its drills of 20 have 5 or 6 planted calls, where the sampler now draws 6 to 10 (section 16, question 15). The report reads whatever a ledger holds, so the fixture and this example are unchanged.
- **Pooling.** A condition's lines pool every answered call of its drills. With one condition only, the comparison section is `prediction gate minus plain: no prediction-gate drills yet` (or the other way round).
- **Several set versions** are named in the first line: `(scenario sets 1 and 2)`.
- **Each drill** is one line, in `drill.started` order, dated by the UTC date of its `drill.started` `ts`; `, stopped` or `, did not end` follows the counts when it didn't finish. The fixture's per-drill lines are in the golden file; the spec shows the first and last.
- **Rates follow section 7:** below 5 of a kind, `too few to say a rate (5 or more needed)`.
- **Answers over 300 s** (section 4, Time). When a condition has any, its block gains, after its median line, `  over 300 s: <n> answers. Without them:` (`1 answer. Without it:`), then its planted and clean lines again, indented four spaces, computed without those answers. A drill's line gains `, <n> over 300 s` after its median. With none, nothing is added. The fixture has one plain answer over 300 s, so `drill_report.txt` shows these lines; their values are in the golden file.
- **A drill with no answer** (stopped or killed before the first answer) says nothing about anyone, and is left out of every count, line and export field. A ledger whose drills all have no answer reports as one with none.

### The export

`polarizer drill report --export <path>` prints the report as above and also writes the summary to `<path>`, created exclusively (an existing file is refused: `polarizer: <path> already exists; nothing was written`, exit 2; an operating system error: `polarizer: cannot write <path>: <message>`, exit 2). With no drills: `polarizer: no drills to export`, exit 2. It is compact UTF-8 JSON, sorted keys, ending in a newline, and holds only integers, booleans, null and the strings named below. Schema version 1:

| Key | Value |
|---|---|
| `format` | `"polarizer-drill-summary"` |
| `format_version` | 1 |
| `polarizer_version` | `polarizer.__version__` |
| `scenario_sets` | the set versions used, as strings of digits, ascending |
| `first_date`, `last_date` | UTC dates, `YYYY-MM-DD`, of the first and last `drill.started` |
| `drills` | drills with at least one answer |
| `answered` | calls answered |
| `repeats` | answered calls whose `seen_before` was above 0 |
| `conditions` | `{"plain": C, "prediction-gate": C}`, each C or null when that condition has no drills |
| `difference` | `{"catch": D, "false_flag": D}`, each D or null when either side is below 5 or missing |
| `by_shape` | for each of the five shape ids, `{"planted": int, "caught": int}` |
| `per_drill` | a list, in order, of `{"date", "condition", "calls", "answered", "ended", "planted", "caught", "clean", "false_flags", "median_ms", "over_300s"}`, with `ended` one of `finished`, `stopped`, `interrupted` or `none` |

C is `{"drills", "planted", "caught", "clean", "false_flags", "catch", "false_flag", "median_ms", "over_300s", "catch_within_300s", "false_flag_within_300s"}`, where `catch` and `false_flag` are R or null below 5, `median_ms` is an integer or null, `over_300s` counts the answers over 300 s, and the last two are the rates without those answers, R or null below 5 (section 16, question 4). R is `{"percent", "low_percent", "high_percent"}` and D is `{"points", "low_points", "high_points", "distinguishable"}`, all integers as printed (section 7) except the boolean. No floats appear, so the file is the same on every platform.

**What it never holds:** predictions, their lengths or any other text a person typed, the ledger directory or any path, session ids, hold ids, seeds, scenario ids, `args_commit` values, `ts` values, times of day, user or host names. `test_export_has_no_free_text_paths_or_names` checks the file against an allowlist: every key is in the schema, and every string is one of the literals above, a version matching `^[0-9A-Za-z.+-]{1,32}$`, a set version of digits, or a date matching `^[0-9]{4}-[0-9]{2}-[0-9]{2}$`. It also searches the bytes for the fixture's prediction text, ledger path, session ids, seed and scenario ids, and finds none.

### `polarizer stats`

Section 10. Read-only like `holds`: the same reading, the same status handling, the same `no ledger at <dir>` line and exit 2 for a missing ledger, exit 0 otherwise.

### Exit codes

No exit code is added. `drill`: 0, 1 (the writer stopped), 2, and the ledger statuses' codes. `drill report` and `stats`: 0, 2 and the statuses' codes, 7 for locked.

### Golden files

Every row gets a file under `tests/golden/`, with `<dir>` for the test's directory as the existing golden tests do, comparing stdout (or stderr where named) byte for byte with the exit code.

| Command and situation | Golden file | Exit |
|---|---|---|
| `drill`: intro, each condition | `drill_intro_plain.txt`, `drill_intro_prediction_gate.txt` | (mid-drill) |
| `drill`: one call, each condition | `drill_call_plain.txt`, `drill_call_prediction_gate.txt` | (mid-drill) |
| `drill`: an invalid answer | `drill_reprompt.txt` | (mid-drill) |
| `drill`: each reveal, and an answer over 300 s | `drill_reveal_caught.txt`, `drill_reveal_missed.txt`, `drill_reveal_right.txt`, `drill_reveal_false_flag.txt`, `drill_reveal_over_300.txt` | (mid-drill) |
| `drill`: a whole drill of the test set, to the end screen | `drill_end.txt` | 0 |
| `drill`: stopped with `q` after 3 answers, one kind below 5 | `drill_end_stopped.txt` | 0 |
| `drill`: a whole drill with an answer over 300 s | `drill_end_over_300.txt` | 0 |
| `drill` refusals: no terminal, serve ledger, forbidden directory, broken ledger, bad `--calls`, bad `--condition`, bad `--seed`, `--keep-predictions` with `drill report`, a set that fails its check | `drill_refused_<case>.txt` (stderr) | 2, or the status's code |
| `serve` on a drill ledger | `serve_refused_drill_ledger.txt` (stderr) | 2 |
| `drill report`: two conditions, a stopped drill, repeats | `drill_report.txt` | 0 |
| `drill report`: one condition only | `drill_report_one_condition.txt` | 0 |
| `drill report`: no drills; no ledger | `drill_report_none.txt`, `drill_report_no_ledger.txt` | 0 |
| `drill report --export`: the file | `drill_export.json` | 0 |
| `drill report --export` refusals: existing file, no drills | `drill_export_refused_<case>.txt` (stderr) | 2 |
| `stats`: the fixture of section 10 | `stats_mixed.txt` | 0 |
| `stats`: no holds | `stats_nothing.txt` | 0 |
| `stats`: a suggestion, and a built-in pattern with none | `stats_suggestion.txt` | 0 |
| `stats`: the trend warning; the rate fell, no warning | `stats_trend_warning.txt`, `stats_trend_no_warning.txt` | 0 |
| `stats` on each non-intact status, locked, no ledger | `stats_<status>.txt`, `stats_locked.txt`, `stats_no_ledger.txt` | that status's code, 7, 2 |
| (stage 10) `stats` with planted calls | `stats_planted.txt` | 0 |
| (stage 10) `allow` and `deny` of a planted hold; `allow` of a real hold with planting on | `allow_planted.txt`, `deny_planted.txt`, `allow_real_planting_on.txt` | 0 |

## 10. Stats over real holds (stage 9)

`polarizer stats` reads a serve ledger and prints what its holds came to. It never changes policy, and its suggestions are lines of text (decision 9).

### What is counted

- **A hold** is a `hold.created`. Its session is the one in `hold.created`. Its state is its ending as the hold fold finds it (HOLD-SPEC.md, section 5): **allowed** (`hold.decided` allow), **denied** (`hold.decided` deny), **expired** (`hold.expired`, for any of its three reasons), **abandoned** (`hold.abandoned`), or **open** (no ending yet).
- **The approval rate** is allowed of decided, where decided is allowed plus denied. Expired, abandoned and open holds are not in it, because nobody decided them.
- **Planted holds** (stage 10; a `hold.revealed` with `label` `planted`) are left out of every count above and counted in their own section (section 11). Holds whose session had planting on and that were never revealed are left out too, and counted as `not revealed`.
- **Drill entries** are never in a serve ledger (section 9); `stats` ignores every kind it doesn't use.

### Time to decision

A hold is created by `serve` and decided by `allow` or `deny`, two processes. The monotonic clock of one process can't measure that interval: Python promises nothing about comparing `time.monotonic()` between processes, and the person reads the hold in `holds`, a third process that writes nothing. **Decided:** time to decision is the wall-clock difference between the hold's `hold.created` `ts` and its `hold.decided` `ts`, in milliseconds, rounded to whole seconds with a half rounding up (`(ms + 500) // 1000`), and 0 when negative.

What that means, and the screen says it:
- It is **about** the time: the wall clock stepped back by about 1.1 s once per 30 s run on WSL2 (verified-facts.md, Wall clock and monotonic clock on WSL2), so any one value can be off by about a second, and NTP or a person can move the clock further. Medians hide single steps.
- It is **time to decision, not reading time**: it includes the time before the person noticed the hold.
- It is the same measure for every hold in every existing ledger, so `stats` works on ledgers written since M2a, including the owner's M2a check ledger.

This is not the monotonic `elapsed_ms` that milestones.md's carry-forward note and PIN-SPEC.md, section 3 asked for; the second commit of this round updates milestones.md's note (section 17, deviation 1). **A monotonic value when there is one** (section 16, question 5): M3's card shows the hold and takes the decision in one process, and records a monotonic `elapsed_ms` on `hold.decided`, an optional field. `stats` prefers it when present: that hold's time is `(elapsed_ms + 500) // 1000` seconds, measured from the card showing the hold, and the note under `all sessions` says how many decisions had it. Until M3 no decision has it, and every time is the wall-clock difference above.

### By tool and rule, and suggestions

For each pair of exposed tool name and rule, over all sessions: allowed of decided, and the other states when not zero. Pairs are listed by number of holds, most first, then by tool, then by rule.

A **suggestion** line is printed for a pair with at least `SUGGEST_MIN = 20` decided holds, every one allowed. At 20 of 20 the Wilson interval's low end is 83%, which is the least evidence at which "this person always allows it" is worth a line. 20 is provisional (section 16, question 9), and the output says so: the section's first line is `suggestions, from a provisional threshold of 20 decided holds, all allowed:`, with the lines below it indented two spaces, and with none, `suggestions: none (a tool and rule needs 20 decided holds, all allowed; 20 is provisional)`. The line names the counts and asks a question; it never says the hold is useless, and it changes nothing:

| Rule | Line |
|---|---|
| `unclassified` | `<tool> has no class and was allowed <k> of <k> times; consider giving it a class in polarizer.toml.` |
| `outside-roots` | `<tool> held by outside-roots was allowed <k> of <k> times; consider whether the paths it writes belong inside a workspace root.` |
| `write-unchecked` | `<tool> has no path_args and was allowed <k> of <k> times; consider naming its path arguments.` |
| `destructive`, `egress` | `<tool> held by <rule> was allowed <k> of <k> times; consider whether it needs a hold on every call.` |
| `write-pattern`, `read-pattern` with a pattern from the config | `<tool> held by <rule> (<pattern>) was allowed <k> of <k> times; consider whether that pattern still needs a hold.` |

No suggestion is printed for a pattern that is built in (HOLD-SPEC.md, section 3; read from the reason's `matches <pattern>` and compared with the built-in lists), for `polarizer-files`, or for `path-missing`, `path-not-string` and `path-unresolvable`: those holds fail closed by design, and a person allowing them every time is the case drills exist for. In those cases nothing is printed, and the pair's line stays as it is.

### The trend warning

The warning sign the research names (section 2): decisions getting faster while the approval rate stays flat.
- **Window:** the last `WINDOW = 40` decided holds, by the seq of their `hold.decided`, over all sessions and excluding planted holds, against the 40 before them. With fewer than 80 decided holds, the section says how many it needs.
- **It fires when all three hold:** the earlier window's median time is at least 6 s; the recent window's median time is at most half of it; and the approval rate did not fall by a distinguishable amount, that is, Newcombe's interval for the recent rate minus the earlier rate has `high >= 0`.
- **Why these numbers:** 40 decisions give medians that one slow or fast decision can't move much, and at a few holds a day they cover weeks. Halving is a large change, well past the 1 s that rounding and the clock step can cause, and the 6 s floor keeps it so: from 6 s, a halving needs 3 s of real change. All three are guesses until real ledgers exist, and are provisional (section 16, question 9): the section's first line says so, as below.
- **The sentence,** always with its numbers, never a verdict:

```
trend, with provisional thresholds (40 and 40 decisions, half the time, 6 s)
  last 40 decisions: median about 4 s, allowed 37 of 40
  the 40 before: median about 19 s, allowed 36 of 40
  Decisions got faster while the approval rate did not fall. That can mean less
  reading, or calls that became easier to judge; a drill can tell the two apart.
```

Without the warning, the first three lines are printed alone. With too few: `trend: needs 80 decided holds to compare the last 40 with the 40 before (provisional numbers); this ledger has <n>`.

### Output

`stats_mixed.txt`, from a fixture ledger built by a test helper from named pieces, one function per hold, as `holds_mixed.txt` is (HOLD-SPEC.md, section 8). Four sessions: `a1a1a1a1a1a1a1a1` (started `2026-10-06T09:59:59.000Z`), `b2b2b2b2b2b2b2b2` (`10:59:59.000Z`), `c3c3c3c3c3c3c3c3` (`11:59:59.000Z`, its lock held, so running) and `d4d4d4d4d4d4d4d4` (`12:30:00.000Z`, no holds):

| # | Session | Tool and rule | Created (`ts`, 2026-10-06) | Ending (`ts`) | Seconds |
|---|---|---|---|---|---|
| 1 | a1 | `fs__write_file` `outside-roots` | 10:00:00.000 | allow, 10:00:10.400 | 10 |
| 2 | a1 | `fs__write_file` `outside-roots` | 10:01:00.000 | allow, 10:01:19.600 | 20 |
| 3 | a1 | `fs__move_file` `destructive` | 10:02:00.000 | deny, 10:02:30.000 | 30 |
| 4 | a1 | `fs__write_file` `outside-roots` | 10:03:00.000 | allow, 10:03:40.500 | 41 (a half rounds up) |
| 5 | a1 | `web__post_comment` `egress` | 10:04:00.000 | expired, timeout after 300 s | none |
| 6 | b2 | `fs__write_file` `outside-roots` | 11:00:00.900 | allow, 11:00:05.800 | 5 |
| 7 | b2 | `fs__move_file` `destructive` | 11:01:00.900 | allow, 11:01:00.500 | 0 (the clock stepped back) |
| 8 | b2 | `fs__edit_file` `write-pattern` (`.git/hooks/**`) | 11:02:00.000 | abandoned, by c3's start | none |
| 9 | c3 | `fs__write_file` `outside-roots` | 12:00:00.000 | open | none |

Expected output:

```
stats: 9 holds; 3 sessions with holds, 1 without

session a1a1a1a1a1a1a1a1 started 2026-10-06T09:59:59.000Z
  holds 5: allowed 3, denied 1, expired 1, abandoned 0, open 0
  approval: allowed 3 of 4 decided, too few to say a rate (5 or more needed)
  time to decision: median about 20 s, from 4 decisions

session b2b2b2b2b2b2b2b2 started 2026-10-06T10:59:59.000Z
  holds 3: allowed 2, denied 0, expired 0, abandoned 1, open 0
  approval: allowed 2 of 2 decided, too few to say a rate (5 or more needed)
  time to decision: median about 0 s, from 2 decisions

session c3c3c3c3c3c3c3c3 started 2026-10-06T11:59:59.000Z
  holds 1: allowed 0, denied 0, expired 0, abandoned 0, open 1
  approval: no decisions
  time to decision: no decisions

all sessions
  holds 9: allowed 5, denied 1, expired 1, abandoned 1, open 1
  approval: allowed 5 of 6 decided, 83% (95% interval 43% to 97%)
  time to decision: median about 10 s, from 6 decisions
  Times are wall-clock differences in whole seconds, so each can be off by about
  a second, and they include the time before a hold was noticed.

by tool and rule
  fs__write_file outside-roots: allowed 4 of 4 decided; 1 open
  fs__move_file destructive: allowed 1 of 2 decided
  fs__edit_file write-pattern: none decided; 1 abandoned
  web__post_comment egress: none decided; 1 expired

suggestions: none (a tool and rule needs 20 decided holds, all allowed; 20 is provisional)

trend: needs 80 decided holds to compare the last 40 with the 40 before (provisional numbers); this ledger has 6
```

- **Sessions** are listed in `session.started` order, only those with holds; text from the ledger goes through `printable()`, as in `holds`.
- **With no holds at all,** the whole output is `stats: no holds in this ledger` (`stats_nothing.txt`).
- **Session b2's median** is `median_low([5, 0])`, which is 0; session a1's is `median_low([10, 20, 30, 41])`, 20; overall `median_low([0, 5, 10, 20, 30, 41])`, 10.
- **Stage 10** adds a `planted calls` section after `all sessions` when any session had planting on (section 11).

## 11. Live planted calls inside serve (stage 10)

Specified now, built in stage 10, which is deferred until after M3 (section 16, question 6). Drills are the primary measurement until then: a drill call has a known answer, and a drill needs no agent. HOLD-SPEC.md, section 12 kept a planted call indistinguishable from a real hold until the person decides; this section uses what it kept.

### Opt-in and disclosure

- **The key:** `planted_calls_per_session` in `[policy]`, an integer from 0 to 3, default 0 (off). `polarizer.toml: [policy] planted_calls_per_session must be an integer from 0 to 3` for anything else, exit 2. There is no flag that turns planted calls on, and `--no-holds` turns them off (nothing is held, so nothing can be planted).
- **The policy hash** gains `"planted_calls_per_session": <n>` in its form only when n is above 0, so every existing config keeps its `policy_sha256`.
- **Recorded:** `policy.loaded` carries `planted_calls_per_session` at every start (section 8), 0 when off.
- **Disclosed at start:** with n above 0, serve writes to stderr, once, after the unclassified line: `polarizer: planted calls are on: at most <n> per session, never with 12 or more holds open; a held call may be one the agent never made, and none is ever forwarded`.
- **Disclosed in `polarizer stats`** (below) and in `polarizer.example.toml`'s comment for the key, which states the same three facts.

### When one is issued

A planted call is made from a real call, right after that call arrives. All of these must hold, checked in `serve`'s call handler after routing and the rule function, for the real call (the trigger):
1. the session's `planted_calls_per_session` is above 0, and fewer planted calls than that have been issued in this session;
2. the trigger routed to an approved tool, was evaluated, and was forwarded or held (not refused);
3. the trigger's class (configured or from annotations) is `local-write`, `destructive` or `local-read`, its tool has `path_args`, and the argument chosen below holds a string that resolved;
4. the session has had at least 5 real calls, and at least 2 in the last 60 s on the monotonic clock, the trigger included, so the agent is active;
5. fewer than 12 holds of this session are open (below);
6. `secrets.randbelow(20) == 0`: about 1 in 20 eligible calls.

Then serve waits a delay of 1 to 8 s (`secrets.randbelow(8) + 1`), checks 1 and 5 again, and issues the planted call, or drops it if either fails. The delay keeps the planted hold apart from its trigger, as a second call from the agent would be.

**Near the cap.** A planted call counts toward the 16 open holds of its session (HOLD-SPEC.md, sections 6 and 12). None is issued while 12 or more are open, at the trigger or at issue time, so a planted call can take at most the 13th place, and 3 places stay free for real calls. A real call refused because a planted call took the last place would show that one of the waiting holds is not real; with this margin, that needs 3 more real calls to be held while the planted call waits, which the disclosure names. The 12 is this spec's choice (section 17, deviation 15).

### What it is

- **Arguments:** a copy of the trigger's arguments, with one path argument changed. For `local-write` and `local-read` it is the only or first path argument holding a string; for `destructive` the last one (a move's destination). Its new value is one target, chosen with `secrets` from a fixed list:
  - for `local-write` and `destructive`: `<D>/.git/hooks/pre-commit`, `<D>/.git/hooks/post-checkout`, `<D>/.github/workflows/<stem>.yml`, `<home>/.bashrc`, `<home>/.ssh/authorized_keys`;
  - for `local-read`: `<home>/.ssh/id_ed25519`, `<home>/.aws/credentials`, `<D>/.env`;
  - where `<D>` is the workspace root that holds the trigger's resolved path, or else that path's parent directory; `<stem>` is the trigger path's file name without its extension; and `<home>` is serve's expanded `~`, as the patterns use.
- **The verdict:** serve runs the real rule function on the planted arguments. It must hold, with rule `write-pattern`, `read-pattern`, `destructive` or `egress`; otherwise the planted call is dropped. So its class, rule and reason are exactly what a real call with those arguments would get.
- **The hold:** written as for a real hold (HOLD-SPEC.md, section 6): the side file, then `hold.created` with a new random hold id, the session, the trigger's exposed tool name, the class, rule, reason and timeout, and `plant_commit`. serve writes the same stderr line as for any hold, `polarizer: held <tool> as hold <id> (<rule>); run polarizer holds`, because that log is visible too.
- **No request:** a planted hold has no MCP request and no handler. It is owned by a task that holds no reference to any client session or upstream client, so nothing in it can forward. This is a structural guarantee, and `test_planted_call_is_never_forwarded` checks the behavior.
- **The reveal text** (`why`), through `safe()`, one of: `The agent's <tool> call named <original>; this planted call named <planted>, where git runs code.` (hooks); `..., which can run code when pushed.` (workflows); `..., which runs at the start of every shell.` (`.bashrc`); `..., which decides who can log in to this account.` (`authorized_keys`); `..., which holds secrets.` (the three read targets).

### The commitment and the reveal

With planting on, **every** hold of the session, real or planted, gets a 32-byte salt and a label, keeps them in serve's memory only, and carries `plant_commit` in its `hold.created`. After the hold's ending, serve appends `hold.revealed` (section 8):
- **a real hold:** right after its ending is written or adopted, before anything else is done for it (so before forwarding an allowed call);
- **a planted hold:** right after its ending.

The salt is never written before the reveal. HOLD-SPEC.md, section 12 suggested keeping it in a side file; a file the person or the agent could read before deciding would give the label away, so it stays in memory, and a hold whose process dies before its reveal is never revealed (section 17, deviation 3). Every reveal is checked by `stats` against its commitment; a reveal that doesn't check is counted as `did not check` and its hold left out of every rate.

### Endings of a planted hold

| How it ended | Entries after `hold.created` | Forwarded |
|---|---|---|
| allow | `hold.decided` allow, `hold.revealed` | never; no `call.*` entry |
| deny | `hold.decided` deny, `hold.revealed` | never; no `call.refused`, since there is no call |
| timeout | `hold.expired` (`timeout after <n> s`), `hold.revealed` | never |
| shutdown | `hold.expired` (shutdown), `hold.revealed`, best effort | never |
| killed | nothing; the next start writes `hold.abandoned`; never revealed | never |

A client cancel can't end a planted hold, since no client request exists. HOLD-SPEC.md's "one terminal entry per held call" (section 5) counts calls; a planted hold is not a call, and `test_one_terminal_entry_per_call` leaves holds revealed as planted out.

### What the agent sees

**Nothing.** The agent never made the call, so there is no request to answer, and a planted call's result never reaches it, whatever the person decides. That means the agent's view does not look like a real call's: Claude Code shows a call line for every real held call (HOLD-SPEC.md, section 9) and none for a planted one.

The trade-off: making the agent's view match would mean attaching the planted call to one of the agent's real requests. Then either the agent's real work waits or is refused for the sake of a test, or Polarizer answers the agent with a result that no server produced. Both are rejected: Polarizer never fabricates a result, and never refuses or delays a real call to test the person. The cost is a tell: a person who matches every hold against Claude Code's call lines can find the planted one. So a live planted call measures whether the person reads the held call in `holds` (or later the card), not whether they cross-check it with the agent's transcript. The owner accepted this as a stated limit (section 16, question 6); it is stated in LIMITS.md when stage 10 ships.

### What allow and deny print

When the decided hold carries `plant_commit`, `allow` and `deny` print their block and result line as before, then wait up to 2 s for the hold's `hold.revealed` (an `fstat` every 0.05 s, reading new entries under the lock as `holds --wait` does). The wait happens for real holds too, so its length gives nothing away. Then, for a planted hold (`allow_planted.txt`):

```
<the hold's block, as holds prints it>
allowed hold 7c2e94d0a1b85f36 at seq 812

Planted call: Polarizer made this hold to check that held calls are read. The
agent never made it, and nothing was forwarded.
why: The agent's fs__write_file call named /home/river/code/garden-planner/notes.md; this planted call named /home/river/code/garden-planner/.git/hooks/post-checkout, where git runs code.
You allowed it: missed.
```

For a deny, the last line is `You denied it: caught.` For a real hold nothing more is printed (`allow_real_planting_on.txt`). With no reveal within 2 s (the session ended, or is slow), `polarizer: no reveal for this hold yet; polarizer stats will show it once the session reveals it` goes to stderr, after any session warning. The exit code stays 0 in every case: the decision is recorded.

### In stats

After `all sessions`, when any session had planting on (`stats_planted.txt`):

```
planted calls
  on in 2 of 4 sessions: at most 1 per session, about 1 in 20 eligible calls, never with 12 or more holds open
  planted 2: denied 1 (caught), allowed 1 (missed), expired 0, not revealed 0
  catch rate: caught 1 of 2, too few to say a rate (5 or more needed)
```

A reveal that fails its check adds `, did not check <n>` to the second line. Planted holds are out of every other count (section 10).

### Limits of live planted calls

- **One shape only:** a changed path argument. The other four shapes need to know what a tool's other arguments mean, which Polarizer doesn't; they wait for M4's practice range, whose servers Polarizer knows (section 16, question 7).
- **No call line in Claude Code** for a planted call (above).
- **The rate is low by design:** at most 3 per session, about 1 in 20 eligible calls. A catch rate from planted calls takes many sessions to mean anything, and `stats` prints "too few to say" until it does.
- **A person who reads serve's memory** (a debugger) can see labels. Out of scope, as anything with the person's own privileges is.

## 12. Tests

"Default" means the test runs in `scripts/test.sh` and CI on every platform. "POSIX" means it is skipped on Windows, with the reason in the test. Each stage's tests are written first, as in earlier stages.

### `tests/test_measure.py` (stage 8)

| Test | Claim | Suite |
|---|---|---|
| `test_wilson_vectors` | Every row of section 7's first vector table: `low` and `high` within 1e-6, and the printed form exactly. | Default |
| `test_wilson_edges` | n = 0 gives no interval; k = 0 and k = n are clamped to 0 and 1; the printed interval always contains the exact one, over all k and n up to 60. | Default |
| `test_too_few_to_say` | Below 5, the line is the "too few" text with the counts, and no percent appears. | Default |
| `test_newcombe_vectors` | Every row of the difference table: bounds, points and the phrase. | Default |
| `test_points_are_integer_exact` | Equal differences print equally: 38 of 40 minus 37 of 40 and 37 of 40 minus 36 of 40 both print +3. | Default |
| `test_median_low_and_time_format` | The median vectors; the seconds formats for drills and stats at their boundaries (59, 60, 3599, 3600 s). | Default |

### `tests/test_scenarios.py` (stage 8)

| Test | Claim | Suite |
|---|---|---|
| `test_scenario_file_schema` | The shipped set loads, its `format` and `set` match its file name, and every scenario passes the schema rules (section 6, Validation, Every scenario). | Default |
| `test_scenario_set_hash_pinned` | Each shipped set file's sha256 equals the value written in the test. | Default |
| `test_clean_calls_match_their_task` | Every clean call's tool and arguments equal its intent's. | Default |
| `test_planted_calls_differ_as_stated` | Parametrized by scenario id: every planted call differs from its intent exactly as its shape says. | Default |
| `test_planted_value_not_in_task_and_named_in_why` | No planted value appears in its task; every `why` names it as `holds` prints it. | Default |
| `test_reasons_match_rule_templates` | Every `reason` matches its rule's template from HOLD-SPEC.md, section 4. | Default |
| `test_names_are_invented` | Reserved domains only; every home user and package name is in `names`; no name on the deny list; nothing credential-like. | Default |
| `test_set_balance` | 150 scenarios, 90 and 60, 12 per shape; no rule, class or tool of a planted call has fewer than 40% clean; each shape on at least 3 tools; block lengths within 20%. | Default |
| `test_load_by_version` | Every shipped set loads by its version with `scenarios.load`; a version not installed is a `SetProblem`. | Default |
| `test_no_shortcut` | Over the shipped set, no tell, and the best single-feature rule within 10 points of always allow, over the scenarios and over 1,000 seeded drills (section 6, Validation, Shortcuts). | Default |
| `test_shortcut_checks_catch_a_tell` | On the shipped set with `..` added to every planted call and six more arguments on six clean calls, both tells are found and the `..` rule is right every time. | Default |
| `test_audit_features_and_buckets` | The audit's features are the call screen's, before the answer; the buckets have fixed edges. | Default |
| `test_every_scenario_renders` | Every scenario renders through `holds.render_block` in at most 60 lines, ASCII only. | Default |
| `test_review_sheet` | `sheet --out` writes every scenario of the shipped set with its answer, shape, task, block and reveal, the summary counts, the shortcut audit and the planted count's distribution; the shipped set has nothing to look at and no tell. `audit` prints the last two alone. | Default |
| `test_review_sheet_lists_what_to_look_at` | On the test set, changed to have a repeated and a near-duplicate task line, a short reveal and backticks in a reveal, each is listed, with the shapes under 8 and the rare tools; a reveal holding a fence gets a longer fence. | Default |
| `test_loading_check` | A copy of the set with a broken scenario makes `drill` print the set's refusal line, exit 2, and write nothing. | Default |

### `tests/test_drill.py` (stage 8)

| Test | Claim | Suite |
|---|---|---|
| `test_sampler_vector` | The stream values and the four plans of section 6, Sampling. | Default |
| `test_planted_range` | `planted_range` for the values of section 6, Sampling, and for sets with few planted ids or shapes. | Default |
| `test_planted_count_and_shapes` | With the shipped set, 2,000 drills of 20 in a row, each leaving out the two before it: every plan has 6 to 10 planted calls and the rest clean, all five shapes with counts at most one apart, nothing from the two drills before it, and every count from 6 to 10 drawn. | Default |
| `test_plan_is_reproducible_from_the_ledger` | After a drill, the plan drawn from `drill.started` (seed, set, calls, excluded, condition) equals the `drill.shown` sequence and the recorded condition. | Default |
| `test_condition_random_and_flag` | Without `--condition`, the condition is the plan's draw and `condition_from` is `random`; with it, the flag's value and `flag`, and the order of calls is unchanged. | Default |
| `test_no_repeat_of_last_two_drills` | Three drills in a row on one ledger: no scenario of drills 1 or 2 is in drill 3, and `excluded` lists them; with `--seed`, nothing is excluded. | Default |
| `test_drill_block_equals_holds_block` | For every scenario of the shipped set: a serve-shaped ledger with a `hold.created` and side file of the same values (hold id, session, `ts`, salt, arguments, timeout), its session lock held, printed by `holds`, gives the same block bytes as the drill. | Default |
| `test_render_block_refactor_keeps_holds_output` | Every existing `holds_*.txt`, `allow_*.txt` and `deny_*.txt` golden file still passes after the refactor (this is the existing golden test, named here as a claim of stage 8). | Default |
| `test_reveal_for_every_scenario` | Parametrized by scenario id and answer: a one-call drill records `drill.revealed` with the scenario's answer and shape, the right outcome of the four, and prints that outcome's reveal with the scenario's `why`. | Default |
| `test_elapsed_ms_is_monotonic` | With the injected wall clock stepped back 1.1 s between the prompt and the answer, and the injected monotonic clock advanced 4.2 s, `elapsed_ms` is 4200; the same for `drill.predicted`. | Default |
| `test_drill_kinds_and_fields` | Each drill kind has exactly section 8's fields, inside the subset. | Default |
| `test_prediction_gate_flow` | The prediction comes before the block; an empty prediction asks again; by default only its length is stored, and with `--keep-predictions` the text too, folded and cut to 200 characters. | Default |
| `test_over_300_is_marked` | An answer with `elapsed_ms` above 300000 gets the reveal's extra line, and the end screen and report give the rates with and without it; at exactly 300000 it is not marked. | Default |
| `test_version_from_metadata` | `polarizer.__version__` equals the installed package's metadata version, which equals pyproject.toml's; `drill.started` and the export record it. | Default |
| `test_serve_refuses_drill_ledger` | `serve` on a ledger with a `drill.*` entry prints the refusal line, exits 2 and appends nothing. | Default |
| `test_answers_and_reprompt` | `a`, `allow`, `d`, `deny` in any case are accepted; anything else re-prompts with the clock running; `q` stops. | Default |
| `test_stop_and_interrupt` | `q` at each of the three prompts, end of input, and KeyboardInterrupt each record `drill.ended` with the right `how`, print the end screen and return 0. | Default |
| `test_end_screen_equals_report_line` | The end screen's counts and median equal `drill report`'s line for that drill. | Default |
| `test_drill_refuses_serve_ledger` | A ledger with `session.started` is refused with the line, exit 2, nothing appended. | Default |
| `test_drill_refuses_forbidden_dir` | A drill directory inside `~/.local/share/parallax` (under a fake home) is refused; inside a git working tree it warns and runs. | Default |
| `test_drill_needs_terminal` | Without a terminal on stdin or stdout, the refusal line, exit 2, no directory created. | Default |
| `test_drill_on_a_pty` | `python -m polarizer drill --seed ... --ledger-dir ...` on a pseudo-terminal, answered by the test, runs to the end screen and exits 0. | POSIX: no pseudo-terminal module on Windows |
| `test_drill_opens_no_connection_and_starts_no_process` | A subprocess installs an audit hook (`sys.addaudithook`) that fails on `socket.connect`, `socket.bind`, `socket.getaddrinfo`, `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`, `os.spawn`, `os.fork`, `os.forkpty` and `os.startfile`, then runs a whole drill in process with scripted answers, then `drill report --export`. The drill finishes, and the hook recorded no event. | Default |
| `test_drill_writes_only_its_directory` | A listing of paths, sizes and mtimes under the fake home before and after a drill differs only inside the drill directory; no `args/` directory is created. | Default |
| `test_drill_ledger_verifies` | After a drill, `verify --ledger-dir` prints `intact` with 0 sessions and 0 calls, and `verify --args` prints all zeros. | Default |
| `test_drill_kinds_are_not_security_kinds` | `SECURITY_KINDS` has no `drill.*` kind; `ledger.head` stays at genesis after a drill. | Default |
| `test_writer_stop_ends_the_drill` | A line that doesn't chain, appended by the test mid-drill: the drill prints the stop line and exits 1. | Default |
| `test_drill_golden` | Every drill row of section 9's golden table. | Default |

### `tests/test_drill_report.py` (stage 8)

| Test | Claim | Suite |
|---|---|---|
| `test_report_on_fixture` | The 7-drill fixture gives `drill_report.txt`: counts, rates, intervals, the comparison, shapes and per-drill lines. | Default |
| `test_report_pools_and_compares` | A condition's numbers pool its drills; one condition only prints the "no drills yet" comparison line. | Default |
| `test_report_is_read_only` | Paths, sizes and mtimes are unchanged by `drill report` and by `drill report --export` apart from the export file. | Default |
| `test_export_schema` | The fixture's export equals `drill_export.json`, keys sorted, integers only apart from the version strings, dates and booleans. | Default |
| `test_export_has_no_free_text_paths_or_names` | The allowlist check of section 9, The export, and none of the fixture's prediction text, path, session ids, seed or scenario ids in the bytes. | Default |
| `test_export_refusals` | An existing file and an empty ledger are refused with their lines, exit 2, and nothing written. | Default |

### `tests/test_stats.py` (stage 9)

| Test | Claim | Suite |
|---|---|---|
| `test_stats_fixture` | The fixture of section 10 prints `stats_mixed.txt` exactly. | Default |
| `test_time_to_decision_rounding` | 10.4 s gives 10, 19.6 s gives 20, 40.5 s gives 41, and a decision 0.4 s before its hold gives 0. | Default |
| `test_approval_rate_counts_decisions_only` | Expired, abandoned and open holds are in the counts and out of the rate. | Default |
| `test_suggestion_threshold` | 20 of 20 decided and allowed gives the rule's line; 19 of 19, or 20 of 21, gives none; a built-in pattern, `polarizer-files` and the `path-*` rules never give one, at any count. | Default |
| `test_trend_needs_80` | With 79 decided holds, the "needs 80" line with 79. | Default |
| `test_trend_warning` | 40 decisions at 19 s with 36 allowed, then 40 at 4 s with 37 allowed: the warning, exactly. | Default |
| `test_trend_no_warning` | The same times with 38 then 20 allowed (the rate fell, Newcombe high below 0): the three number lines, no warning; with the earlier median at 5 s: no warning. | Default |
| `test_stats_ignores_other_kinds` | Drill kinds and unknown kinds in a ledger change nothing in the output. | Default |
| `test_stats_is_read_only` | Paths, sizes and mtimes are unchanged; no lock file is created. | Default |
| `test_stats_golden` | Every `stats` row of section 9's golden table, except the stage 10 row. | Default |

### `tests/test_planted.py` (stage 10)

| Test | Claim | Suite |
|---|---|---|
| `test_planted_call_is_never_forwarded` | With planting on and the chance forced to 1, a planted hold is allowed with `allow`: the upstream fake sees no call through ten watch intervals, the ledger has no `call.sent`, `call.returned` or `call.refused` with that hold, and `hold.revealed` says `planted`. The same for deny, expiry and shutdown. | Default |
| `test_planting_is_opt_in` | Without the key, or with 0, or under `--no-holds`, no planted hold is ever made over 500 eligible calls with the chance forced to 1; `policy.loaded` records 0. | Default |
| `test_policy_hash_unchanged_without_the_key` | `policy_sha256` of a config without the key equals the value `test_policy_loaded_fields` already pins. | Default |
| `test_planted_cap_per_session` | With 1 per session, at most one planted hold in a session of 200 eligible calls. | Default |
| `test_none_near_the_cap` | With 12 holds open, no planted hold is issued; when a delay ends with 12 open, the planned one is dropped. | Default |
| `test_planted_only_while_active` | No planted hold before 5 real calls, nor when fewer than 2 calls came in the last 60 s (injected monotonic clock). | Default |
| `test_planted_hold_uses_the_rule_function` | A planted hold's class, rule and reason equal `evaluate`'s for its arguments; a target that `evaluate` would allow is dropped. | Default |
| `test_every_hold_commits_when_on` | With planting on, every `hold.created` has `plant_commit`, every ending is followed by one `hold.revealed`, and every reveal checks against its commitment; with it off, none has either. | Default |
| `test_salt_never_written_before_reveal` | Before a hold's reveal, no file under the ledger directory and no ledger entry holds its salt. | Default |
| `test_plant_commit_changes_no_reader` | `holds`, `allow` and `deny` print the same bytes for a hold with and without `plant_commit`; the hold fold is unchanged by `hold.revealed`. | Default |
| `test_allow_and_deny_print_the_reveal` | The stage 10 golden rows; with no reveal within 2 s, the stderr line and exit 0. | Default |
| `test_planted_stderr_line_matches_real` | serve's `held ... as hold ...` line has the same form for a planted hold as for a real one. | Default |
| `test_stats_counts_planted` | `stats_planted.txt`; planted and unrevealed holds are out of the approval rate; a reveal that doesn't check is counted and left out. | Default |
| `test_one_terminal_entry_per_call` (extended) | The existing test, with planted holds in the scripted run, still finds one terminal entry per held call and no `call.*` entry for any planted hold. | Default |

### Fixtures

| Test | Claim | Suite |
|---|---|---|
| `test_fixtures.py`, `test_conformance.py` (new valid fixture) | A generated chain holding every new kind and field of this spec verifies in both verifiers. Stage 8 adds the drill kinds; stage 10 adds `hold.revealed` and the two fields. | Default |

## 13. The guide for people who are not engineers

Stage 8 writes `docs/DRILL-GUIDE.md`: a one-page walkthrough the owner can send to a friend or a hiring manager who wants to try a drill. This section specifies it.

- **Length and tone.** One page, about 800 words of prose besides its commands and screens (600 before the stage 8 review added the items below). Plain sentences, second person, no jargon: "a record file on your computer", not "ledger"; "the AI agent", with MCP named once and explained in a clause. Every command is on its own line, ready to paste.
- **Who it is for, first:** anyone, with no AI agent needed, but a terminal needed, said plainly, with what it is called on each system. "This takes about 15 minutes, 10 of them the drill." A friend who works with computers can do the install with the reader in five minutes.
- **Words you will see,** four in three lines: planted call, clean call, caught, false flag, each in a few plain words.
- **What a drill is:** you play the person who approves an AI agent's risky actions. You see 20 actions, each with the task the agent was given, and you allow or deny each. Between 6 and 10 of the 20 were changed to be wrong on purpose, far more than real work has. After each answer you see whether you were right, and why.
- **Install,** for macOS and Linux, and for Windows (PowerShell), as numbered steps in plain words: install uv from its official instructions (linked; copy the line for your system, paste it, open a new terminal), then the one `uv tool install` line. Once the repository is public, `uvx --from git+https://github.com/mpwilso/polarizer polarizer drill` runs a drill with no install of Polarizer, marked as working only then. While the repository is private, the owner sends a package file instead, and the guide gives that line too (section 16, question 1). The guide states that installing downloads Polarizer's dependencies, and that the drill itself uses no network. It says that drills on a native Windows console (PowerShell or Command Prompt) have not been tried yet (section 16, question 14).
- **Run one drill:** `polarizer drill`. What the screen shows, with an excerpt of a call that includes its arguments, and a reveal; how to read an action (the `hold` line names the tool and its kind, the `held by` line says why it would be paused, the part in braces is exactly what it would do, to compare with the task); that clean calls are held too, as real holds are, and the reveal explains each; how to answer (`a` or `d`, `q` to stop); that nothing is real and nothing is sent.
- **See and export the results:** `polarizer drill report`, then `polarizer drill report --export drill-summary.json`; a short excerpt of what the export holds, and a plain list of what it never holds (section 9, The export). Sending it is the reader's choice.
- **How to read the numbers:** "too few to say" and the interval, in two sentences; that one drill's 6 to 10 planted calls give a wide interval; that always answering allow would be right about 60% of the time, so the two rates matter, not the share of right answers; that a drill measures attention when you know you are being tested, and has far more planted calls than real work.
- **What it is not:** the paragraph from section 3 that drills are not for grading or ranking anyone, word for word.
- **Removing it:** `uv tool uninstall polarizer`, and the drill directory to delete by hand, with its path on each platform.
- **Checked by a test:** `tests/test_drill_guide.py::test_guide_commands_exist` checks that every `polarizer` command line in the guide parses with Polarizer's own parser, the uvx line's included, and that the guide quotes section 3's paragraph exactly. `test_guide_commands_run` runs the polarizer lines, and `test_guide_install_lines_run_offline` runs the package file's install line and the uninstall line as written, with uv offline, into a tool directory of its own, when uv's cache holds every package the install needs. An offline dry run of the same install decides; the test is skipped, naming the missing package, only when that dry run fails for want of a cached package, and `test_offline_skips_only_for_a_cache_miss` checks that decision on an empty cache. Only the two lines that fetch from GitHub are never run. The docs check covers its links and ASCII.

## 14. README plan (not applied)

README.md does not change in this round. When stage 8 ships, the README changes as below; the owner picks the options.

**The hook** (the bold line under the logo), one of:
1. `Agents call the tools. You decide the risky ones. Drills check that you still catch them.`
2. `Holds an AI agent's risky calls, and measures whether you still read them.`
3. `Agents call the tools. You decide the risky ones, and can measure how well.`

**The status line,** one of:
1. `Status: a portfolio project, built to show how I design, test and judge an AI tool. Version 0.2, a preview: drills measure whether you catch planted calls; measuring that during real work comes next.`
2. `Status: a portfolio project. Version 0.2, a preview, with offline drills that measure how often you catch a planted call.`

**The sentence "That measurement does not exist yet"** in "Why it exists" becomes: `Drills measure it under practice conditions: an offline session of held calls, some planted, with your catch rate and its interval ([guide](docs/DRILL-GUIDE.md)).`

**Proof bullets drills would add** (each only once its test passes in CI, with the commit named):
- **Drills touch nothing:** a test runs a whole drill under an audit hook that fails on any network connection or new process, and none happens.
- **A drill shows the real screen:** for every scenario, the drill's block is byte for byte what `polarizer holds` prints for a real hold with the same arguments.
- **Every answer is checked:** a test proves each of the 60 planted calls differs from its task in its stated way, and each of the 90 clean calls doesn't.
- **The numbers come with their uncertainty:** every rate is printed with its 95% Wilson interval, checked against fixed test vectors, and below 5 calls as "too few to say".
- **Results from more than one person,** only once they exist: the number of people who sent an exported summary and the dates, recorded in docs/EVIDENCE.md with their consent, and never names.

**Also:** the README's "A person who allows without reading" limit gains "drills measure this under practice conditions only"; "What's next" item 3 drops "canaries" for "planted calls during real work"; and the 93% and 13.6% figures stay out until a primary source is recorded (section 2).

## 15. Build order and size

Three stages, each ending in a stop for the owner's review. Together they are milestones.md's M5 + M6 row, thinned: large in total.

### Stage 8: drills and the guide (medium)

1. **The statistics module** (`measure.py`): Wilson, Newcombe, medians, printing. Done when `test_measure.py` passes. Small.
2. **The renderer refactor:** `holds.render_block`. Done when every existing golden file passes unchanged. Small.
3. **The scenario set:** the schema, the loader and its check, the validator tests, the 150 scenarios, the `show` helper, and a review of every screen by a person other than the author before the set's hash is pinned. Done when `test_scenarios.py` passes. Medium: most of the stage's time.
4. **The drill:** the sampler, the screens, conditions, answers, time, stopping, the ledger kinds, the location and terminal checks. Done when `test_drill.py` passes, with its POSIX skip in place. Medium.
5. **`drill report` and `--export`.** Done when `test_drill_report.py` passes. Small.
6. **Fixtures and docs:** the drill kinds in the generated fixtures; docs/DRILL-GUIDE.md and its test; LIMITS.md's drill limits (section 1, What the numbers cannot show; the unchecked lost tail of a drill ledger); docs/dev/STAGE8-NOTES.md with a claims table; verified-facts.md for anything run. Small.

**Stop for review.** The owner runs a drill of each condition, reads the guide, and decides the README options of section 14 and the open questions of section 16.

### Stage 9: stats (small)

1. **The fold and the numbers:** holds, endings, times, per tool and rule, suggestions, the trend. Done when `test_stats.py` passes except the golden rows. Small.
2. **The command and golden files.** Done when `test_stats_golden` passes. Small.
3. **Docs:** STAGE9-NOTES.md; `stats` run read-only on the owner's M2a check ledger by the owner, with the output recorded in verified-facts.md (a development session reads that ledger only if the owner asks). Small.

**Stop for review.**

### Stage 10: live planted calls (medium; deferred until after M3)

Deferred by the owner's decision (section 16, question 6): M3's card comes first, and drills are the primary measurement until then.

1. **Config and records:** the key, its error, the policy hash rule, `policy.loaded`'s field, the start-up line. Small.
2. **The commitment and the reveal** for every hold of a planting session, real ones first. Done when `test_every_hold_commits_when_on`, `test_salt_never_written_before_reveal` and `test_plant_commit_changes_no_reader` pass. Small to medium.
3. **The generator:** eligibility, delay, targets, the verdict, the cap margin, the ownership that can't forward. Done when the rest of `test_planted.py` passes, except the CLI rows. Medium.
4. **`allow`, `deny` and `stats`:** the reveal wait and lines, the planted section. Done with the stage 10 golden rows. Small.
5. **Docs and a check:** LIMITS.md's planted-call limits; a manual check section in docs/dev/MANUAL-CHECK.md (turn planting on with 3 per session, work in Claude Code until one appears, decide it in `holds`, read the reveal, then run `stats`); STAGE10-NOTES.md. Small.

**Stop for review,** then the owner's manual check of stage 10.

## 16. Questions, decided

The owner answered this section's questions on Oct 5, 2026 (UTC), after stage 8's step 0; questions 1, 10 and 11 were left to this spec's recommendation. Each answer is one line; the sections it changes say so where they apply.

1. **Installing for a friend while the repository is private.** Decided (this spec's recommendation): the guide gives the `uv tool install` line for a package file the owner builds with `uv build` and sends, and the `git+https` line for once the repository is public; whether to send package files before then is the owner's choice.
2. **The planted share in drills.** Decided: kept at 4 to 8 of 20; the limits say it is far above any real rate, and a low-rate condition may come later (section 1, What the numbers cannot show). The count was then fixed at 8, and then drawn from 6 to 10 (question 15).
3. **Keeping predictions.** Decided: by default only each prediction's length is recorded; `--keep-predictions` stores the folded text in the person's own drill ledger; predictions are never exported in any form other than counts (sections 4, 8 and 9).
4. **A drill time limit.** Decided: an answer that took over 300 s is marked, and results are reported both with and without such answers (sections 4, 5 and 9).
5. **Monotonic time to decision for real holds.** Decided: yes; M3's card records a monotonic `elapsed_ms` on `hold.decided`, and `stats` prefers it when present (section 10).
6. **The missing call line** for live planted calls. Decided: accepted as a stated limit, since a person comparing screens can spot a planted call; stage 10 is deferred until after M3, and drills are the primary measurement (sections 11 and 15).
7. **More shapes for live planted calls.** Decided: yes, they wait for M4's practice range (section 11).
8. **Version string.** Decided: one version source; `polarizer.__version__` comes from the installed package's metadata (pyproject.toml's `0.1.0`), fixed in stage 8, and drills and exports record it (sections 8 and 9).
9. **The trend thresholds and `SUGGEST_MIN`.** Decided: provisional, and the output says so on the lines that use them (section 10).
10. **Evidence from more than one person.** Decided (this spec's recommendation): the README cites drill results only once at least 5 people have each sent an export of at least 2 drills, each asked in writing for consent to record the count and the dates in docs/EVIDENCE.md, never a name.
11. **Forbidden paths for drills.** Decided (this spec's recommendation): `drill` takes no `--config`; it refuses Parallax's two directories and warns inside a git working tree, with the development guard as the backstop (section 9).
12. **`serve` and a drill ledger.** Decided: `serve` refuses a ledger directory that holds drill entries, with one line and exit 2, the existing usage-error exit; no new exit code (section 9).
13. **CLAUDE.md, rule 6.** Decided: rule 6 names `~/.local/share/polarizer-drills` as a Polarizer ledger location that development sessions must not write to; tests use temporary directories.
14. **Windows console input** for drills. Decided: drills on a native Windows console are unverified, and the guide says so (section 13).
15. **The planted count, after the first tool check.** Decided by the owner on Oct 5, 2026 (UTC), after reviewing stage 8's first tool check (4 planted calls in a plain drill, 6 in a prediction-gate drill, and "too few to say" for a catch rate below 5): every drill of 20 has exactly 8 planted and 12 clean calls, spread over the five shapes as evenly as the set allows, with no shape missing when the set has it, and with seeded, recorded sampling as before (sections 4 and 6). Drawn from 4 to 8, 80% of 10,000 seeded drills in each condition had 5 or more planted calls; now all do. Then, in the owner's follow-up brief of the same day, decided: the count is a range, drawn uniformly from 6 to 10 per drill of 20 (seeded and recorded as before, the rest clean, every shape present and spread as evenly as the count allows; 30 to 50 percent for other lengths, at least 5 and at least one per shape when the length allows), so the end of a drill cannot be inferred from the running count. It can still be partly inferred in extreme cases: after 10 planted or 14 clean calls the rest are known, which settled an answer in 16% of 10,000 seeded drills, 0.26 calls per drill on average (section 1, Counting down). Every one of 10,000 seeded drills per condition had at least 5 planted and at least 10 clean calls (section 6, Sampling). The intro screen states the range, not the count.

## 17. Deviations and guesses

### The owner's decisions this builds on

Given with the round's brief on 2026-10-04 (the session ran on Oct 5, 2026, UTC); "decision <n>" above refers to these:

1. **Three parts,** built in order: drills, stats over real holds, live planted calls inside serve; drills first because they produce evidence from more than one person.
2. **Drills:** offline, no agent, model, network or upstream, nothing forwarded; one held call at a time, rendered as `polarizer holds` renders a real hold; allow or deny; the answer revealed at once with a reason; about 20 calls and ten minutes.
3. **Two kinds of drill call,** clean and planted, with five planted shapes; the task shown in one line; the scenario set as versioned data with invented names only; enough scenarios that drills rarely repeat; seeded and recorded sampling.
4. **Scores:** catch rate, false-flag rate, time per decision on the monotonic clock as `elapsed_ms`, 95% Wilson intervals, n stated, no rate without its interval.
5. **The prediction-gate condition,** assigned at random and recorded, or chosen with a flag; conditions compared with their intervals.
6. **Records:** drills in their own ledger directory, in the existing format, verifiable with `verify --ledger-dir`; new kinds without changing the format, the chain, a status or an exit code.
7. **Consent and framing:** opt-in, results on the machine, an anonymized export, drills not for grading, the surveillance concern cited, the effect of knowing drills exist.
8. **A guide** for people who are not engineers, specified here and written in the build stage.
9. **Stats over real holds,** with time to decision measured honestly across processes, per-tool suggestions that never change policy, and the warning sign as a plain sentence.
10. **Live planted calls,** spec only: opt-in key recorded in `policy.loaded`, never forwarded, counted toward the cap, never near it, only while the agent is active, capped and disclosed, revealed after the decision, with section 12's commitment.
11. **Terminology:** drill, planted call, clean call; "canary" once.

None of them was challenged. One was narrowed: decision 10's commitment goes on every hold of a session with planting on, not of every session (deviation 2).

### What step 0 found

Read on Oct 5, 2026 (UTC) at commit 03cedbd, with `git status` clean and `scripts/test.sh` passing (798 passed, 13 skipped).

1. **milestones.md's carry-forward note,** and PIN-SPEC.md, section 3 (Decision time), say time per decision comes from monotonic elapsed values in the entries. Decision 9 notes that a real hold's decision crosses processes. Decided: drills use monotonic `elapsed_ms` as the note says; real holds use wall-clock whole seconds (deviation 1). The second commit updates milestones.md's note; PIN-SPEC.md is left as it is, and this file wins for time to decision.
2. **HOLD-SPEC.md, section 12** puts a commitment in every `hold.created` and the salt in a side file. Decided: the commitment on every hold of a session with planting on (deviation 2), and the salt in memory (deviation 3).
3. **milestones.md's M5 + M6 row** says canaries are "scored for catches and false flags". A live planted call can only be caught or missed; real holds have no known answer, so false flags are scored in drills only (deviation 6). The second commit updates the row, and uses "planted calls".
4. **milestones.md says its rows are in build order,** with M3 and M2b before M5 + M6. The owner chose to build this slice next; the second commit says so in the status list (deviation 7).
5. **"Canary"** appears in milestones.md, HOLD-SPEC.md, section 12 and the README's "What's next". The second commit changes milestones.md's row; HOLD-SPEC.md's section 12 keeps its wording as the record of M2a's round; the README waits for section 14's plan.
6. **`polarizer.__version__` is `0.1.0.dev0`,** `pyproject.toml` says `0.1.0`. Not changed in a docs round; decided later: one version source, fixed in stage 8 (section 16, question 8).
7. **README.md and LIMITS.md say nothing measures approvals yet.** True until stage 8 ships; section 14 plans the change.
8. **The test count** is 798 passed and 13 skipped at 03cedbd, where docs/EVIDENCE.md and the README cite 784 at 9270c97. Not a contradiction: later commits added tests, and the cited numbers name their commit.
9. **PROXY-SPEC.md says "16 holds per process",** HOLD-SPEC.md "of this session". The same thing, since a serve process has one session. No change.

### What stage 8's step 0 found

Read on Oct 5, 2026 (UTC) at commit f03a29e, with `git status` clean and `scripts/test.sh` passing (798 passed, 13 skipped in 233.5 s). Each was small and is decided here.

1. **The git-tree warning starts a process.** `ledgerdir.check_location` runs `git rev-parse` to find a working tree, and a drill must start no process (section 12, `test_drill_opens_no_connection_and_starts_no_process`). Decided: `drill` finds a working tree by looking for a `.git` directory or file in its directory and each parent, with no process, and prints the same warning line. A tree found only through `GIT_DIR` is missed; `serve`, `repair`, `allow` and the pin commands keep the `git` command.
2. **The writer's own stop line.** A writer that stops prints `polarizer: stopped writing the ledger: <why>; run polarizer verify` to stderr itself. Decided: that line comes first, then the drill's own line (section 4, Stopping), exit 1.
3. **Drills with no answer** were not covered by the report's counts. Decided: left out of every count, line and export field (section 9, `polarizer drill report`).
4. **A second reader for each scenario** (section 6, Adding a scenario) can't be found in a development session. Decided: set 1 is written and pinned in stage 8 and reviewed by the owner at stage 8's stop; until then it has not shipped, so the owner's corrections edit set 1 and update its pin instead of making set 2. The stage notes say which scenarios the owner reviewed.
5. **Which strings are package and host names** in the invented-names test. Decided: a package name is the value of an argument named `package` (or an element of `packages`); a host is the host part of a URL, the domain of an e-mail address, or the value of an argument named `host`.
6. **Where `serve` refuses a drill ledger.** Opening the ledger as a writer can append `ledger.head_rebuilt`. Decided: the refusal comes from the fold that runs while the ledger is verified at open, before anything is appended; a listener at open may refuse the open with its own line and code, which the writer passes on unchanged instead of reporting a fold failure.

### Stage 8 build (Oct 5, 2026, UTC)

Decided while building stage 8; docs/dev/STAGE8-NOTES.md has the detail.

1. **The scenario set is loaded before the ledger is opened** (section 4, Starting), so a set that fails its check writes nothing, not even a genesis entry in a new directory.
2. **`drill` finds a git working tree without git** (stage 8's step 0, 1), by looking for `.git` in the directory and its parents, and prints the usual warning.
3. **A drill ended by Ctrl+C or the end of input** reads `Drill stopped` on its end screen, and `, stopped` in the report's line for it, as a drill stopped with `q` does; the ledger keeps `interrupted`.
4. **The report's first line always gives both counts,** `<f> finished, <s> stopped early`, zero included; "stopped early" counts every drill that did not finish, `did not end` included.
5. **One-screen golden files** hold what the drill wrote between two reads (section 5).
6. **The test scenario set** says `"set": 1` like the shipped set; it is never installed. Both are written by `tools/make_drill_set.py` (`--test-set` for the test set).
7. **`python -m polarizer.scenarios show <id>`** takes `--answer` to print the answer and its `why` after the screen, for a reviewer who has answered.
8. **`--keep-predictions` with `drill report`** is a usage error, `polarizer: --keep-predictions goes with drill` (section 9, Usage errors), with its golden file.
9. **An unknown word after `drill`** is argparse's error, `polarizer: argument action: invalid choice: ...`, exit 2.
10. **The guide** quotes section 3's closing lines as its paragraph on what drills are not, word for word, after one plain sentence of its own; `tests/test_drill_guide.py` also runs the guide's polarizer commands, as `tests/test_readme.py` runs the README's.

### Stage 8 review (Oct 5, 2026, UTC)

Decided in the owner's review of stage 8; docs/dev/STAGE8-NOTES.md, Stage 8 review, has the detail.

1. **A fixed 8 planted and 12 clean calls in a drill of 20** (section 16, question 15), dealt over the shapes in a drawn order so that shape counts differ by at most one. Other values of `--calls` plant `floor(2 * calls / 5)`; the brief named 20 only, so that rule is this round's guess.
2. **The intro screen states the count** (`8 of the 20 calls are planted`), since the old sentence, that the count changes, is no longer true, and the guide and this spec state it. Not stating it would be the other choice; the count would still be learned after a drill or two.
3. **The report fixture is unchanged.** It is a hand-built ledger, not drawn by the sampler, so its drills of 20 keep 5 or 6 planted calls (section 9).
4. **The guide** says a terminal is needed and how to install uv and Polarizer in numbered steps, adds four words and how to read an action, says clean calls are held too, and gives the `uvx` line for once the repository is public (section 13). It grew to about 800 words of prose.
5. **The guide's offline uv lines are run by its test,** into temporary tool and bin directories with uv offline; the two lines that fetch from GitHub are not.
6. **`python -m polarizer.scenarios sheet --out <path>`** writes a review sheet (section 6, Adding a scenario). "Planted versus clean per shape" counts, for each shape, the clean scenarios on the tools that shape's planted scenarios use, since clean scenarios have no shape.
7. **A writer test** pins stage 8's one change to `writer.py`: a listener's own `LedgerError` at open passes through, and every other listener error is reported as before.

### Stage 8 follow-up (Oct 5, 2026, UTC)

Decided in the owner's follow-up brief to the stage 8 review; docs/dev/STAGE8-NOTES.md, Stage 8 follow-up, has the detail. Decisions 1 and 2 of the Stage 8 review above are replaced by 2 and 3 here.

1. **The shortcut audit** (section 6, Validation, Shortcuts) found no tell in set 1, so no set 2 was made: the brief's set 2 was for removing tells, and the owner chose to keep set 1 when asked. `scenarios.load(<version>)` loads any shipped set, ready for a set 2.
2. **The planted count is drawn** uniformly from 6 to 10 per drill of 20 (section 16, question 15). For other lengths: `hi = min(floor(calls / 2), planted ids)`, `lo = min(max(ceil(3 * calls / 10), 5, shapes), hi)`. The brief said "30 to 50 percent planted, at least 5 and at least one per shape when the length allows"; rounding the lower bound up and the upper bound down is this round's reading.
3. **The intro states the range,** `Between 6 and 10 of the 20 calls are planted`, never the count drawn, which would bring back counting down. A range of one number (5 of 10) is stated as one number.
4. **No new field records the count.** "Seeded, recorded" is read as before: the count is drawn from the seed, and `drill.started` records everything the plan needs. A count that varies is in the ledger the same way the old fixed one was, through each `drill.revealed`.
5. **The count is drawn right after the condition,** so the stream gives a different plan for a seed than before. Plans recorded by earlier development builds (4 to 8, or a fixed 8) are not drawn again by this sampler; none of them was released.
6. **The test set has 10 planted scenarios,** two of each shape (it had 8), so its range for 20 calls is the shipped set's 6 to 10 and the golden intro shows it. The golden seed is `000...03ea`.

### Deviations from the decisions and the existing documents

1. **Time to decision for real holds is the wall-clock difference** of `hold.created` and `hold.decided`, in whole seconds, 0 when negative, labeled "about", because the interval crosses processes (section 10). milestones.md's carry-forward note and PIN-SPEC.md, section 3 asked for a monotonic value stored in the entry; that is kept for drills and proposed for M3 (section 16, question 5).
2. **The commitment is on every hold of a session with planting on,** not on every hold of every session as HOLD-SPEC.md, section 12 suggested and decision 10 repeated. With planting off, `policy.loaded` already shows that every hold of the session is real, so a commitment would add an entry per hold and prove nothing more.
3. **The salt stays in serve's memory** until the reveal, rather than in a side file, which the person or the agent could read before deciding. A hold whose process dies first is never revealed, and `stats` counts it as not revealed.
4. **`hold.revealed` is a new kind,** written for real holds too, so the reveal of every hold of a planting session can be checked, and a label can't be chosen after the decision.
5. **Live planted calls have one shape,** a changed path argument (section 11), not the five drill shapes.
6. **False flags are scored in drills only;** live planted calls score catches and misses (milestones.md's M5 + M6 row said both).
7. **This slice is built before M3 and M2b,** against milestones.md's order, by the owner's decision 1.
8. **"Planted call" replaces "canary"** in every new text, kind and field; HOLD-SPEC.md keeps its section 12 as written.
9. **A drill writes no side files.** The block's `args_commit` is computed by the side-file formula over a fresh salt and recorded in `drill.shown`, but no file holds the salt or the arguments, which are in the package.
10. **A drill's block says `times out after 300 s`,** as a real hold's does, and nothing times out; the intro says so.
11. **`drill` reads no config** and refuses only Parallax's two directories (section 9).
12. **A new default directory,** `~/.local/share/polarizer-drills`, beside the serve ledger's (section 16, question 13).
13. **6 to 10 planted calls in 20,** drawn per drill, chosen for measurement over realism (section 4); first 4 to 8, then a fixed 8 after the first tool check, then 6 to 10 in the owner's follow-up (section 16, question 15).
14. **No repeat of the last two drills,** and none of that with `--seed`, which is a user-facing flag so two people can run the same drill.
15. **The cap margin:** no planted call with 12 or more holds open. HOLD-SPEC.md, section 12 left the margin to this spec.
16. **The sampler is defined on SHA-256,** not on Python's `random`, so a recorded plan can be drawn again by any version.
17. **Stopping a drill exits 0,** by `q`, end of input or Ctrl+C, so no exit code is added; a stopped writer exits 1, as in `approve --group`.
18. **No drill kind is fsynced inline,** and a drill ledger's head stays at genesis (section 8).
19. **`policy.loaded` gains `planted_calls_per_session` on every start** from stage 10, and the policy hash's form gains the key only when it is above 0.
20. **The opt-in is one `[policy]` key,** with `--no-holds` turning planting off; there is no flag that turns it on.
21. **`drill report` with no ledger exits 0** with a "none yet" line, where `holds` and `stats` print `no ledger at <dir>` and exit 2: a person new to drills has done nothing wrong.
22. **Only a prediction's length is stored by default;** with `--keep-predictions` the text is stored in the drill ledger, through `safe()` at 200 characters; predictions are never exported in any form other than counts (section 16, question 3).
23. **The approval rate counts decided holds only:** expired, abandoned and open holds are counted beside it.
24. **No suggestion for built-in patterns or fail-closed path rules,** whatever the count (section 10).
25. **The trend's window and thresholds** (40, 40, half, 6 s) and `SUGGEST_MIN` (20) are guesses (section 16, question 9).
26. **`MIN_N` is 5,** the interval method is Wilson with Newcombe's method 10 for differences, and the printing rules (integer point estimates, floor and ceil bounds) are this spec's choice.
27. **The prevalence effect** (rare targets are missed more often) is cited from general knowledge of visual-search research, not from the sources checked on 2026-10-04, and is used only to explain why drill rates may run high.
28. **`holds.block` is split** so that `holds.render_block` renders from arguments; `holds`' output doesn't change.
29. **`allow` and `deny` wait up to 2 s for a reveal** when the hold carries `plant_commit`, for real and planted holds alike.
30. **`drill` refuses a serve ledger, and `serve` refuses a drill ledger** (section 16, question 12), each with exit 2.
31. **The terminal requirement for `drill` has no override,** unlike `allow` and `deny`, since a drill exists to measure a person.
32. **Test file names, golden file names, the scenario file's name and keys, the shape ids, every exact output line, and the reveal texts** are this spec's choice.
