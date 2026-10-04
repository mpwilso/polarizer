"""Decisions on tool definitions: approve one, approve a group, reject (docs/PIN-SPEC.md,
section 7). The command line and the test helpers share this code.

Each decision is a tool.approved or tool.rejected entry with actor "person", fsynced with
ledger.head updated before the call returns. Nothing here creates a ledger.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from polarizer import defhash
from polarizer.ledger import LEDGER
from polarizer.pins import PinState, blocks, group
from polarizer.upstream import one_line
from polarizer.writer import LedgerError, LedgerWriter

ACTOR = "person"


class Refusal(Exception):
    """One stderr line and an exit code. Nothing was written."""

    def __init__(self, line: str, exit_code: int = 2):
        super().__init__(line)
        self.line = line
        self.exit_code = exit_code


@dataclass
class Decider:
    """An open ledger with its pin state, ready to record decisions."""

    ledger_dir: Path
    writer: LedgerWriter
    pins: PinState

    @classmethod
    def open(cls, ledger_dir: Path) -> "Decider":
        """Open the ledger as a writer (2 s lock wait, full verification), folding pin state
        in the same pass. Raises Refusal: no ledger (2), or a ledger that isn't intact (the
        status's code, with serve's line)."""
        ledger_dir = Path(ledger_dir)
        path = ledger_dir / LEDGER
        if not path.is_file() or path.stat().st_size == 0:
            raise Refusal(f"polarizer: no ledger at {ledger_dir}")
        state = PinState()
        try:
            writer = LedgerWriter.open(ledger_dir, on_entry=state.apply)
        except LedgerError as e:
            raise Refusal(e.line, e.exit_code) from None
        return cls(ledger_dir, writer, state)

    def close(self) -> None:
        self.writer.close()

    def _record(self, kind: str, data: dict):
        return self.writer.append(kind, {**data, "actor": ACTOR}).result()

    def approve_one(self, prefix: str, tool: str, def_hash: str, out: Callable[[str], None]):
        """Steps 4 to 7 of `approve` for one definition. Raises Refusal."""
        name = f"{prefix}__{tool}"
        tp = self.pins.get(prefix, tool)
        if def_hash not in tp.seen_ever and def_hash not in tp.approved_ever:
            raise Refusal(f"polarizer: Polarizer has not seen {name} with definition {def_hash}")
        try:
            obj = defhash.read_copy(self.ledger_dir, def_hash)
        except defhash.CopyProblem as e:
            where = defhash.copy_name(def_hash)
            raise Refusal(f"polarizer: stored copy {where} {e}; nothing approved") from None
        if tp.decision == "approved" and tp.decided_hash == def_hash and not tp.drifted:
            out(f"already approved: {name} {def_hash}")
            return
        for line in defhash.render(obj).split("\n"):
            out(line)
        data = {"upstream": prefix, "tool": tool, "def_hash": def_hash, "group": None}
        done = self._record("tool.approved", data)
        out(f"approved {name} {def_hash} at seq {done.seq}")

    def pending_group(self, upstream: str | None = None):
        """(blocks a group approves, group id or None), as `pending` computes them now."""
        return group(blocks(self.pins, self.ledger_dir, upstream))

    def approve_group(
        self,
        group_id: str,
        upstream: str | None,
        out: Callable[[str], None],
        err: Callable[[str], None],
    ) -> int:
        """`approve --group`. Returns the exit code; raises Refusal when the id doesn't match."""
        members, current = self.pending_group(upstream)
        if current is None or current != group_id:
            raise Refusal(
                f"polarizer: group {group_id} does not match what is pending now; "
                "run polarizer pending again"
            )
        for j, block in enumerate(members):
            data = {
                "upstream": block.upstream,
                "tool": block.tool,
                "def_hash": block.def_hash,
                "group": group_id,
            }
            try:
                done = self._record("tool.approved", data)
            except Exception as e:
                why = one_line(getattr(e, "line", None) or str(e) or type(e).__name__)
                err(
                    f"polarizer: could not record an approval: {why}; "
                    f"{j} of {len(members)} were approved"
                )
                return getattr(e, "exit_code", 1)
            out(f"approved {block.upstream}__{block.tool} {block.def_hash} at seq {done.seq}")
        out(f"approved {len(members)} definitions as group {group_id}")
        return 0

    def reject(self, prefix: str, tool: str, def_hash: str, reason: str, out):
        """`reject` after its argument checks. Raises Refusal."""
        name = f"{prefix}__{tool}"
        tp = self.pins.get(prefix, tool)
        current = tp.decided_hash if tp.decision == "approved" else None
        if def_hash not in tp.seen_ever and def_hash != current:
            raise Refusal(f"polarizer: Polarizer has not seen {name} with definition {def_hash}")
        if tp.decision == "rejected" and tp.decided_hash == def_hash:
            out(f"already rejected: {name} {def_hash}")
            return
        data = {"upstream": prefix, "tool": tool, "def_hash": def_hash, "reason": reason}
        done = self._record("tool.rejected", data)
        out(f"rejected {name} {def_hash} at seq {done.seq}")


def fold_reason(text: str | None) -> str:
    """A rejection's reason: whitespace folded to single spaces, cut to 1 KiB. Empty if none."""
    return one_line(text) if text is not None else ""
