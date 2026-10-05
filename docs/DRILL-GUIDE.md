# Try a drill

This is for anyone. You don't need an AI agent or an account. You do need a terminal, the window where you type commands (Terminal on macOS and Linux, PowerShell on Windows), and two small programs, installed below. It takes about 15 minutes, 10 of them the drill. If you have never used a terminal, a friend who works with computers can do the install with you in five minutes.

## What a drill is

Polarizer sits between an AI agent and its tools (MCP servers, the plug-ins an agent uses to act) and holds risky actions until a person allows or denies them. A drill lets you practice that job, offline.

You see 20 actions, one at a time, each under the task the agent was given, and you allow or deny each. 8 of the 20 were changed to be wrong on purpose: a file written somewhere else, an extra recipient, a package name with one letter changed. Real work has far fewer wrong actions than that; a drill has many so that one drill measures something. After each answer you see whether you were right, and why. Nothing is real and nothing is sent anywhere.

## Words you will see

- **planted call:** an action that was changed to be wrong, and should be denied.
- **clean call:** a normal action, and should be allowed.
- **caught:** you denied a planted call. **false flag:** you denied a clean call.

## Install

1. Install uv, a small tool that installs Python programs. Open [its official instructions](https://docs.astral.sh/uv/getting-started/installation/), copy the one line for your system, paste it into your terminal and press Enter. Then close the terminal and open a new one.
2. Install Polarizer. If you were sent a package file, move it into your home folder (the folder a new terminal starts in) and run:

```sh
uv tool install ./polarizer-0.1.0-py3-none-any.whl
```

Once the project is public, this line will work instead, with no file:

```sh
uv tool install git+https://github.com/mpwilso/polarizer
```

Installing downloads Polarizer's parts from the internet; the drill itself never uses the network. Drills have not yet been tried in a Windows terminal, so say if anything looks wrong there.

Once the repository is public, and with uv installed, you can also run a drill with one command and no step 2. This works only then:

```sh
uvx --from git+https://github.com/mpwilso/polarizer polarizer drill
```

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

**How to read an action.** The line starting `hold` names the tool and its kind (here, a local write). The line starting `held by` says why Polarizer would pause it. The part in braces, `{` to `}`, is exactly what the action would do. Compare that with the task.

Type `a` to allow or `d` to deny, then Enter. You then see the answer:

```text
Clean call. You allowed it.
why: The edit adds the section the task asked for, before the License heading. It is held only because the path is outside every workspace root.
```

Clean calls are held too, like this one. That is how real holds look: Polarizer pauses whatever its rules match, not only what is wrong, so being held doesn't make an action wrong. The answer explains why each one was held.

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

Each rate comes with a range, such as "caught 88%, 95% interval 52% to 98%": the true rate is very likely somewhere in that range. One drill has only 8 planted calls, so its range is wide; it narrows over several drills. Below 5 answers of a kind, as when you stop early, you see "too few to say" instead of a rate.

A drill measures your attention when you know you are being tested: your best, not an ordinary afternoon. It also has far more planted calls than real work, so drill rates likely run higher than real ones. Trends across drills mean more than any one result.

## What it is not

Your results are yours. They are for you to watch your own habits, not for anyone to grade you or anyone else. Polarizer prints this after every drill and every report:

> Drills measure attention when you know you are being tested. Use them to see
> trends in your own oversight, not to grade or rank anyone.

## Removing it

```sh
uv tool uninstall polarizer
```

Your drill records stay until you delete their folder yourself: `~/.local/share/polarizer-drills` on macOS and Linux, and `.local\share\polarizer-drills` inside your user folder on Windows.
