"""Test helper: open a ledger and append entries, run as its own process.

    python tests/helpers/append_worker.py <ledger_dir> <go-flag file> <count> <worker id>

It waits until the go-flag file exists, so several workers start together, then opens the
ledger (creating it on first run) and appends <count> "note" entries.
"""

import sys
import time
from pathlib import Path

from polarizer.writer import LedgerWriter


def main(directory: str, go: str, count: str, worker: str) -> int:
    while not Path(go).exists():
        time.sleep(0.001)
    writer = LedgerWriter.open(Path(directory))
    for i in range(int(count)):
        writer.append("note", {"pid": worker, "i": i}).result()
    writer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
