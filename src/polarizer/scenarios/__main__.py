"""A development helper: print the drill screen for one scenario of the newest set, for a
reviewer to answer before reading its answer (docs/MEASURE-SPEC.md, section 6, Adding a
scenario).

    python -m polarizer.scenarios show <id>          the screen only
    python -m polarizer.scenarios show <id> --answer the screen, then the answer and its why
"""

import sys

from polarizer import drill, scenarios

TS = "2026-10-06T18:02:11.425Z"


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or argv[0] != "show" or argv[2:] not in ([], ["--answer"]):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    the_set = scenarios.newest()
    scenario = the_set.scenarios.get(argv[1])
    if scenario is None:
        print(f"no scenario {argv[1]} in set {the_set.version}", file=sys.stderr)
        return 2
    block = drill.call_block(
        scenario, "0" * 16, "0" * 64, "1" * 16, TS, TS, the_set.timeout_seconds
    )
    print(f"call 1 of 1\ntask: {scenario['task']}\n")
    print("\n".join(block))
    if argv[2:]:
        shape = scenario.get("shape")
        kind = f"planted ({scenarios.SHAPE_WORDS[shape]})" if shape else "clean"
        print(f"\nanswer: {kind}\nwhy: {scenario['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
