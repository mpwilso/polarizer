"""Live planted calls inside serve (docs/MEASURE-SPEC.md, section 11; stage 10).

Opt-in with [policy] planted_calls_per_session (1 to 3). Right after a real call (the trigger)
is routed and evaluated, and only if it was not refused, the gateway asks `Planter.consider`
whether it may plant: the trigger's class and path, the session's activity, the room under the
cap, then a 1 in 20 chance. If so, `Planter.issue` waits 1 to 8 s, checks the count and the
room again, and makes a planted hold: the trigger's arguments with one path changed to a target
the real rule function holds. The planted hold is recorded exactly as a real one, with the same
stderr line, waits for a person, and is revealed after its ending.

**Never forwarded, by construction.** A Planter is built from the writer, the hold fold, the
policy, the session id, the shared table of open holds and two callbacks (wake the watch, write a
stderr line). It holds no reference to any client session, request or upstream client, and no
code in this module calls the MCP SDK, so no path in it can send a call anywhere.

The commitment: with planting on, every hold of the session, real or planted, carries
plant_commit = sha256(PLANT_PREFIX + salt + label); the salt stays in memory until the hold's
hold.revealed, which serve writes after the ending.
"""

import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import anyio
import anyio.to_thread

from polarizer import paths
from polarizer import policy as policy_mod
from polarizer.canon import EntryRefused
from polarizer.holds import plant_commit
from polarizer.ledgerdir import is_inside
from polarizer.sidefiles import write_args
from polarizer.text import safe
from polarizer.upstream import clip
from polarizer.writer import Stopped

CHANCE = 20  # about 1 in this many eligible calls
DELAY = 8  # 1 to this many seconds between the trigger and the planted hold
MIN_CALLS = 5  # real calls in the session before any plant
ACTIVE_SECONDS = 60.0
ACTIVE_CALLS = 2  # real calls in the last ACTIVE_SECONDS, the trigger included
CAP_MARGIN = 12  # never with this many holds of the session open
CLASSES = ("local-write", "destructive", "local-read")
PLANT_RULES = ("write-pattern", "read-pattern", "destructive", "egress")
WHY_LIMIT = 1024
RECORD_WAIT = 10.0  # seconds a plan waits for its trigger's entry before it is dropped
SHUTDOWN_HOLD = "polarizer shut down while the call was held"

DISCLOSURE = (
    "polarizer: planted calls are on: at most {n} per session, never with 12 or more holds "
    "open; a held call may be one the agent never made, and none is ever forwarded"
)

# What each target is, for the reveal's text (section 11, What it is).
HOOK = "where git runs code"
WORKFLOW = "which can run code when pushed"
SHELL = "which runs at the start of every shell"
KEYS = "which decides who can log in to this account"
SECRETS = "which holds secrets"


def write_targets(root: str, stem: str, home: str) -> list[tuple[str, str]]:
    """(path, what it is) for a local-write or destructive trigger."""
    return [
        (os.path.join(root, ".git", "hooks", "pre-commit"), HOOK),
        (os.path.join(root, ".git", "hooks", "post-checkout"), HOOK),
        (os.path.join(root, ".github", "workflows", f"{stem}.yml"), WORKFLOW),
        (os.path.join(home, ".bashrc"), SHELL),
        (os.path.join(home, ".ssh", "authorized_keys"), KEYS),
    ]


def read_targets(root: str, home: str) -> list[tuple[str, str]]:
    """(path, what it is) for a local-read trigger."""
    return [
        (os.path.join(home, ".ssh", "id_ed25519"), SECRETS),
        (os.path.join(home, ".aws", "credentials"), SECRETS),
        (os.path.join(root, ".env"), SECRETS),
    ]


def why_text(tool: str, original: str, planted: str, what: str) -> str:
    """The reveal's `why`, through safe() with a limit of 1 KiB."""
    text = f"The agent's {tool} call named {original}; this planted call named {planted}, {what}."
    return safe(text, WHY_LIMIT)


@dataclass
class Trigger:
    """A real call a planted call may be made from. `seq` is set once the trigger's call.sent
    or hold.created is written; a trigger never recorded is never planted from."""

    prefix: str
    tool: str  # the upstream's tool name
    name: str  # the exposed name
    arguments: dict
    definition: object
    arg: str  # the path argument to change
    original: str  # its value in the trigger
    planted: str  # the target
    what: str  # what the target is
    seq: int | None = None
    recorded: object = None  # an anyio.Event, set once seq is

    def mark(self, seq: int) -> None:
        """The trigger's call.sent or hold.created was written at `seq`."""
        self.seq = seq
        if self.recorded is not None:
            self.recorded.set()


class _PlantedWaiter:
    """A planted hold in the gateway's table of open holds: the gateway's watch wakes it like any
    other. It has no request and no arguments to forward."""

    def __init__(self, hold: str):
        self.hold = hold
        self.event = anyio.Event()


class Planter:
    """The planted calls of one serve session. See the module docstring for what it may hold.

    `chance`, `delay`, `choose`, `clock` and `sleep` are the draws and the clocks; tests replace
    them. `home` is the home directory the targets use (None: serve's expanded "~")."""

    def __init__(self, writer, holds, policy, session: str, open_holds: dict, wake, log,
                 hold_timeout: float | None = None, home: str | None = None):  # fmt: skip
        self.writer = writer
        self.holds = holds
        self.policy = policy
        self.session = session
        self.open_holds = open_holds  # shared with the gateway: the cap counts planted holds
        self.wake = wake
        self.log = log
        self.hold_timeout = hold_timeout
        self.home = home
        self.limit = policy.planted_calls_per_session if policy.holds else 0
        self.issued = 0
        self.pending = 0
        self.calls = 0
        self.recent: deque[float] = deque()
        self.chance = lambda: secrets.randbelow(CHANCE) == 0
        self.delay = lambda: secrets.randbelow(DELAY) + 1
        self.choose = secrets.choice
        self.clock = time.monotonic
        self.sleep = anyio.sleep

    @property
    def on(self) -> bool:
        return self.limit > 0

    # Commitments (every hold of a planting session)

    @staticmethod
    def commitment(label: str) -> tuple[bytes, str]:
        """A fresh salt and the plant_commit for `label`."""
        salt = secrets.token_bytes(32)
        return salt, plant_commit(salt, label)

    async def reveal(self, hold: str, label: str, salt: bytes, why=None, derived_from=None):
        """Append hold.revealed. Not fsynced inline (section 8); a failure is one stderr line."""
        data = {
            "session": self.session,
            "hold": hold,
            "label": label,
            "salt": salt.hex(),
            "why": why,
            "derived_from": derived_from,
        }
        try:
            await self.writer.append_async("hold.revealed", data)
        except (Stopped, EntryRefused) as e:
            self.log(f"polarizer: could not record hold.revealed: {e}")

    # Eligibility (section 11, When one is issued)

    def _room(self) -> bool:
        return self.issued + self.pending < self.limit and len(self.open_holds) < CAP_MARGIN

    def _active(self) -> bool:
        now = self.clock()
        while self.recent and now - self.recent[0] > ACTIVE_SECONDS:
            self.recent.popleft()
        return self.calls >= MIN_CALLS and len(self.recent) >= ACTIVE_CALLS

    def _home(self) -> str:
        return self.home if self.home is not None else os.path.expanduser("~")

    def _target(self, prefix, tool, name, arguments, verdict) -> Trigger | None:
        """The trigger, with its target, or None: conditions 3 (class and path) of the rule."""
        if verdict.cls not in CLASSES:
            return None
        rule = self.policy.rule_for(prefix, tool)
        if rule is None or not rule.path_args or not isinstance(arguments, dict):
            return None
        strings = [a for a in rule.path_args if isinstance(arguments.get(a), str)]
        if not strings:
            return None
        arg = strings[-1] if verdict.cls == "destructive" else strings[0]
        original = arguments[arg]
        try:
            resolved = paths.resolve_bounded(
                original, self.policy.resolve_bound, self.policy.resolver, self.policy.stuck
            )
        except paths.Unresolvable:
            return None
        roots = [r for r in self.policy.workspace_roots if is_inside(Path(resolved), Path(r))]
        root = roots[0] if roots else os.path.dirname(resolved)
        stem = os.path.splitext(os.path.basename(resolved))[0] or "ci"
        if verdict.cls == "local-read":
            choices = read_targets(root, self._home())
        else:
            choices = write_targets(root, stem, self._home())
        planted, what = self.choose(choices)
        return Trigger(prefix, tool, name, arguments, None, arg, original, planted, what)

    async def consider(
        self, prefix, tool, name, arguments, definition, verdict, refused=False
    ) -> Trigger | None:
        """Count this real call, then the rule's six conditions for it as a trigger. A Trigger
        means a planted call is planned; the caller schedules issue() and sets its seq."""
        self.calls += 1
        self.recent.append(self.clock())
        if refused or not self.on or not self._room() or not self._active():
            return None
        found = await anyio.to_thread.run_sync(self._target, prefix, tool, name, arguments, verdict)
        if found is None or not self.chance():
            return None
        found.definition = definition
        found.recorded = anyio.Event()
        self.pending += 1
        return found

    # The planted hold (section 11, What it is; Endings of a planted hold)

    async def issue(self, trigger: Trigger) -> None:
        """After the delay: check the count and the room again, run the real rule function on
        the planted arguments, and hold them, or drop the plan."""
        try:
            await self.sleep(self.delay())
        finally:
            self.pending -= 1
        if not (self.issued < self.limit and len(self.open_holds) < CAP_MARGIN):
            return
        if trigger.seq is None and trigger.recorded is not None:
            with anyio.move_on_after(RECORD_WAIT):  # a trigger still being recorded
                await trigger.recorded.wait()
        if trigger.seq is None or self.writer.stopped:
            return
        arguments = {**trigger.arguments, trigger.arg: trigger.planted}
        verdict = await anyio.to_thread.run_sync(
            policy_mod.evaluate,
            self.policy,
            trigger.prefix,
            trigger.tool,
            arguments,
            trigger.definition,
        )
        if verdict.action != "hold" or verdict.rule not in PLANT_RULES:
            return
        self.issued += 1
        await self._hold(trigger, arguments, verdict)

    async def _hold(self, trigger: Trigger, arguments: dict, verdict) -> None:
        waiter = _PlantedWaiter(secrets.token_hex(8))
        salt, commit = self.commitment("planted")
        why = why_text(trigger.name, trigger.original, trigger.planted, trigger.what)
        self.open_holds[waiter.hold] = waiter
        try:
            with anyio.CancelScope(shield=True):
                try:
                    data = {
                        "session": self.session,
                        "hold": waiter.hold,
                        "tool": clip(trigger.name, 256),
                        "args_commit": write_args(self.writer.ledger_dir, arguments),
                        "class": verdict.cls,
                        "class_from": verdict.class_from,
                        "rule": verdict.rule,
                        "reason": verdict.reason,
                        "timeout_seconds": self.policy.hold_timeout_seconds,
                        "plant_commit": commit,
                    }
                    await self.writer.append_async("hold.created", data)
                except (Stopped, EntryRefused, OSError) as e:
                    self.log(f"polarizer: could not record a planted hold: {e}")
                    return
            self.log(
                f"polarizer: held {safe(trigger.name)} as hold {waiter.hold} ({verdict.rule}); "
                "run polarizer holds"
            )
            self.wake()
            started = anyio.current_time()
            try:
                ended = await self._wait(waiter, started)
            except anyio.get_cancelled_exc_class():
                with anyio.CancelScope(shield=True):  # shutdown, best effort
                    if await self._expire(waiter.hold, SHUTDOWN_HOLD):
                        await self.reveal(waiter.hold, "planted", salt, why, trigger.seq)
                raise
            if ended:
                await self.reveal(waiter.hold, "planted", salt, why, trigger.seq)
        finally:
            self.open_holds.pop(waiter.hold, None)

    async def _wait(self, waiter: _PlantedWaiter, started: float) -> bool:
        """Until the hold has an ending (True), or the writer stops (False). On timeout,
        hold.expired through the conditional append."""
        timeout = self.hold_timeout
        if timeout is None:
            timeout = self.policy.hold_timeout_seconds
        deadline = started + timeout
        while True:
            if self.writer.stopped:
                return False
            held = self.holds.get(waiter.hold)
            if held is not None and held.ending is not None:
                return True
            if anyio.current_time() >= deadline:
                reason = f"timeout after {self.policy.hold_timeout_seconds} s"
                with anyio.CancelScope(shield=True):
                    return await self._expire(waiter.hold, reason)
            with anyio.move_on_at(deadline):
                await waiter.event.wait()
            waiter.event = anyio.Event()

    async def _expire(self, hold_id: str, reason: str) -> bool:
        """hold.expired unless the hold already has an ending. False if the writer stopped."""

        class HasEnding(Exception):
            pass

        def check():
            held = self.holds.get(hold_id)
            if held is None or held.ending is not None:
                raise HasEnding()

        data = {"session": self.session, "hold": hold_id, "reason": reason}
        try:
            await self.writer.append_async("hold.expired", data, check=check)
        except HasEnding:
            pass
        except (Stopped, EntryRefused) as e:
            self.log(f"polarizer: could not record hold.expired: {e}")
            return False
        return True
