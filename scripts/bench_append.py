"""Print p50 and p95 append times, with and without fsync. Asserts nothing.

    uv run python scripts/bench_append.py [--count N]

Each append goes through LedgerWriter.append(...).result(): the queue, the lock, the size
check, one write and, for "fsync", an fsync of the ledger file (ledger.head is not updated,
so only the entry's own fsync is timed). CI prints this on every runner; the numbers are for
reading, not for gating.
"""

import argparse
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

from polarizer.writer import LedgerWriter

DATA = {"session": "0" * 16, "tool": "probe__wait", "args_commit": "1" * 64, "meta_dropped": []}


def run(count: int, durable: bool) -> list[float]:
    with tempfile.TemporaryDirectory() as tmp:
        writer = LedgerWriter.open(Path(tmp) / "ledger", idle_fsync=False)
        times = []
        for i in range(count):
            start = time.perf_counter()
            writer.append(
                "call.sent", {**DATA, "client_call_id": f"toolu_{i}"}, durable=durable
            ).result()
            times.append(time.perf_counter() - start)
        writer.close()
        return times


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=1000)
    count = parser.parse_args().count
    where = f"{platform.system()} {platform.release()}, Python {platform.python_version()}"
    print(f"append benchmark: {where}, {count} appends")
    for label, durable in [("write only", False), ("write + fsync", True)]:
        times = run(count, durable)
        cuts = statistics.quantiles(times, n=100)
        print(f"{label:14} p50 {cuts[49] * 1e6:9.1f} us   p95 {cuts[94] * 1e6:9.1f} us")
    return 0


if __name__ == "__main__":
    sys.exit(main())
