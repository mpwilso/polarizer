# Try a drill

This is for people who approve what AI assistants do, such as developers who let an AI agent change their code, and for anyone curious about that job: your first drill is guided, with every action also described in plain words. You need no AI agent and no account, only a terminal, the window where you type commands (Terminal on macOS and Linux, PowerShell on Windows), and two small programs, installed below. A first drill of 10 actions takes about five minutes. If you have never used a terminal, a friend who works with computers can do the install with you in five more.

## What a drill is

Polarizer sits between an AI agent and its tools (MCP servers, the plug-ins an agent uses to act) and holds risky actions until a person allows or denies them. People who do that job tend to approve more as time goes on; a drill, offline, shows how well you are catching mistakes.

You see actions one at a time, each under the task the agent was given, and you allow or deny each. Half of a first drill's 10 actions, and between 6 and 10 of a full drill's 20, were changed to be wrong on purpose: a file written somewhere else, an extra recipient, a package name with one letter changed. Real work has far fewer; a drill has many so that one drill measures something. After each answer you see whether you were right, and why. Nothing is real and nothing is sent anywhere.

There are three kinds of drill: guided adds a line in plain words under each action, plain shows the action as it is, and prediction first asks you to write what you expect before you see it. Your first drill is guided and later ones are plain or prediction first at random, and each kind's results are kept apart, because help changes how well anyone does.

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

Once the repository is public, and with uv installed, this runs a full drill with no step 2:

```sh
uvx --from git+https://github.com/mpwilso/polarizer polarizer drill
```

## Run your first drill

```sh
polarizer drill --calls 10
```

Later, `polarizer drill` runs a full drill of 20. Read the first screen and press Enter. Each action then looks like this:

```text
call 3 of 10
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
In plain words: Edit README.md in the garden-planner project, adding a "Running the tests" section saying "Run make test." above the License heading.

allow or deny?
```

**How to read an action.** The line starting `hold` names the tool and its kind (here, a local write). The line starting `held by` says why Polarizer would pause it. The part in braces, `{` to `}`, is exactly what the action would do, and in a guided drill the line starting `In plain words` says the same in everyday words. Compare that with the task.

Type `a` to allow or `d` to deny, then Enter. You then see the answer:

```text
Clean call. You allowed it.
why: The edit adds the section the task asked for, before the License heading. It is held only because the path is outside every workspace root.
```

Clean calls are held too, like this one, as real holds are: Polarizer pauses whatever its rules match, so being held doesn't make an action wrong. The answer explains why each one was held.

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

Each rate comes with a range, such as "caught 88%, 95% interval 52% to 98%": the true rate is very likely somewhere in that range. A drill of 10 has 5 planted and 5 clean calls, just enough for a rate, so its range is wide: 4 of 5 caught reads "caught 80%, 95% interval 37% to 97%". Below 5 answers of a kind, as when you stop early, you see "too few to say" instead of a rate. Always answering "allow" would be right about half the time or more, so look at the two rates, not at the share of right answers.

A drill measures your attention when you know you are being tested: your best, not an ordinary afternoon. It also has far more planted calls than real work, so drill rates likely run higher than real ones. A guided drill shows how well you do with help, so the report shows it on its own lines. Trends across drills mean more than any one result.

## What it is not

Your results are yours. They are for you to watch your own habits, not for anyone to grade you or anyone else. Polarizer prints this after every drill and every report:

> Drills measure attention when you know you are being tested. Use them to see
> trends in your own oversight, not to grade or rank anyone.

## Removing it

```sh
uv tool uninstall polarizer
```

Your drill records stay until you delete their folder yourself: `~/.local/share/polarizer-drills` on macOS and Linux, and `.local\share\polarizer-drills` inside your user folder on Windows.
