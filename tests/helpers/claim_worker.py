"""Test helper: race other processes to write one "claim" entry with the conditional append.

    python tests/helpers/claim_worker.py <ledger_dir> <go-flag file> <worker id>

It opens the ledger first, so its fold sees no claim, as every worker's does; then waits until
the go-flag file exists and appends "claim" with a check that refuses if a claim is already
there. The check runs under the ledger lock after catching up, so exactly one worker wins.
Prints "won <seq>" or "lost <seq of the winner>".
"""

import sys
import time
from pathlib import Path

from polarizer.writer import LedgerWriter


class Lost(Exception):
    pass


def main(directory: str, go: str, worker: str) -> int:
    claims = []

    def fold(entry):
        if entry["kind"] == "claim":
            claims.append(entry["seq"])

    writer = LedgerWriter.open(Path(directory), on_entry=fold)
    assert claims == [], "a claim was there before the race"
    Path(f"{go}.ready-{worker}").write_text("ready")
    while not Path(go).exists():
        time.sleep(0.001)

    def check():
        if claims:
            raise Lost(claims[0])

    try:
        done = writer.append("claim", {"worker": worker}, check=check).result()
        print(f"won {done.seq}")
    except Lost as e:
        print(f"lost {e.args[0]}")
    writer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
