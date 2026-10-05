# Try a drill

This is for anyone. You don't need an AI agent, an account or any setup beyond one install. It takes about 15 minutes, 10 of them the drill.

## What a drill is

Polarizer sits between an AI agent and its tools (MCP servers, the plug-ins an agent uses to act) and holds risky actions until a person allows or denies them. A drill lets you practice that job, offline.

You see 20 actions, one at a time, each under the task the agent was given, and you allow or deny each. Some were changed to be wrong on purpose: a file written somewhere else, an extra recipient, a package name with one letter changed. After each answer you see whether you were right, and why. Nothing is real and nothing is sent anywhere.

## Install

First install uv, a small tool that installs Python programs, from [its official instructions](https://docs.astral.sh/uv/getting-started/installation/). Then, in a terminal (macOS and Linux) or PowerShell (Windows), install Polarizer. If you were sent a package file, run this in the folder that holds it:

```sh
uv tool install ./polarizer-0.1.0-py3-none-any.whl
```

Once the project is public, this line will work instead:

```sh
uv tool install git+https://github.com/mpwilso/polarizer
```

Installing downloads Polarizer's parts from the internet; the drill itself never uses the network. Drills have not yet been tried in a Windows terminal, so say if anything looks wrong there.

## Run one drill

```sh
polarizer drill
```

Read the first screen and press Enter. Each action then looks like this:

```text
call 3 of 20
task: Add a "Running the tests" section to README.md in the garden-planner project.

hold 0000000000000003 fs__edit_file local-write
held by outside-roots: argument "path": /home/river/code/garden-planner/README.md is outside every workspace root
...
allow or deny?
```

Compare the task with what the action does. Type `a` to allow or `d` to deny, then Enter. You then see the answer:

```text
Clean call. You allowed it.
why: The edit adds the section the task asked for, before the License heading. It is held only because the path is outside every workspace root.
```

Type `q` at any prompt to stop early. At the end you see your results.

## See and export your results

Your answers and times are kept in a record file on your computer. To see every drill so far:

```sh
polarizer drill report
```

To save a summary you could share:

```sh
polarizer drill report --export drill-summary.json
```

It holds counts, rates, dates (the day only) and version numbers, such as `"answered":116`. It never holds anything you typed, your name, your computer's name, file paths, times of day, or which actions you saw. Whether to send it, and to whom, is up to you; Polarizer never sends anything.

## How to read the numbers

Each rate comes with a range, such as "caught 83%, 95% interval 43% to 97%": the true rate is very likely somewhere in that range, and with few answers the range is wide. Below 5 answers of a kind you see "too few to say" instead of a rate.

A drill measures your attention when you know you are being tested: your best, not an ordinary afternoon. Trends across drills mean more than any one result.

## What it is not

Your results are yours. They are for you to watch your own habits, not for anyone to grade you or anyone else. Polarizer prints this after every drill and every report:

> Drills measure attention when you know you are being tested. Use them to see
> trends in your own oversight, not to grade or rank anyone.

## Removing it

```sh
uv tool uninstall polarizer
```

Your drill records stay until you delete their folder yourself: `~/.local/share/polarizer-drills` on macOS and Linux, and `.local\share\polarizer-drills` inside your user folder on Windows.
