"""Make conformance/valid/v0-parallax.jsonl from Parallax's own ledger.py. Run once, by hand.

    python tools/make_v0_fixture.py <path to a Parallax clone>

It extracts parallax/ledger.py at V0_COMMIT with `git show` into a temporary directory and runs a
small driver there with PYTHONDONTWRITEBYTECODE=1. The driver imports only that one extracted
file, never the Parallax package, and every file it writes is inside the temporary directory.
Nothing is written in the Parallax clone; git is only read. The output keeps Parallax's random
ids and real timestamps, so it is not regenerated; the commit is recorded in expected.json
through tools/make_fixtures.py.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fixtures import ROOT, V0_COMMIT, V0_FILE  # noqa: E402

DRIVER = """
import sys
from pathlib import Path

import ledger  # the extracted file in this directory, nothing else

book = ledger.Ledger(Path(sys.argv[1]))
book.append("task.created", "human", "start", title="Fixture task", n=1)
book.append("tool.called", "agent", "", tool="read", args={"path": "README.md"}, ratio=0.5)
book.append("note", "agent", "caf\\u00e9 \\u2028 \\U0001f600", items=[1, 2.25, None, True])
book.append("task.accepted", "human", "looks right")
"""


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    repo = Path(argv[0])
    git = ["git", "--no-optional-locks", "-C", str(repo)]
    full = subprocess.run(
        [*git, "rev-parse", "--verify", f"{V0_COMMIT}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if full != V0_COMMIT:
        print(f"commit mismatch: {full}", file=sys.stderr)
        return 1
    source = subprocess.run(
        [*git, "show", f"{V0_COMMIT}:parallax/ledger.py"], check=True, capture_output=True
    ).stdout
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "ledger.py").write_bytes(source)
        (Path(tmp) / "driver.py").write_text(DRIVER, encoding="utf-8")
        out = Path(tmp) / "out" / "ledger.jsonl"
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run([sys.executable, "-B", "driver.py", str(out)], cwd=tmp, env=env, check=True)
        target = ROOT / "conformance" / V0_FILE
        target.write_bytes(out.read_bytes())
    print(f"wrote {target} from parallax/ledger.py at {V0_COMMIT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
