"""Session locks, restart and shutdown during holds (docs/HOLD-SPEC.md, sections 6 and 7), and
`holds --wait` (section 8).

A serve holds sessions/<session>.lock for its whole life. A later start records hold.abandoned
only for the open holds of a session proven ended; a running or unknown session's holds are
left alone. In memory, a process "dies" by having its writer closed (so it can record nothing
more) and its session lock released, which is what the operating system does to a killed
process. The stdio tests run `polarizer serve` itself."""

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
from conftest import build_chain
from helpers import holdledger as hl
from helpers import rig
from helpers.raw import RawClient
from test_holds import ARGS, FAST, Calls, setup, text

from polarizer import cli, holds, lock
from polarizer.ledger import verify_bytes
from polarizer.writer import LedgerWriter

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows has no way to send the two signals as Claude Code does: os.kill with "
    "SIGTERM there is TerminateProcess, which can't be caught, and SIGINT needs a shared "
    "console (PIN-SPEC.md, section 10)",
)
SHUTDOWN = "polarizer shut down while the call was held"
HELPERS = Path(__file__).resolve().parent / "helpers"


def die(gw) -> None:
    """What the operating system does to a killed serve, in process: nothing more reaches the
    ledger from it, and its session lock is released."""
    gw.writer.close()
    gw.release_session_lock()


def abandoned(ld: Path) -> list[dict]:
    return [e["data"] for e in rig.kinds(ld, "hold.abandoned")]


def start_and_stop(ld: Path, specs, pol) -> str:
    """Start a gateway on `ld` and stop it again; its session id."""

    async def main():
        async with rig.gateway(ld, specs, policy=pol) as gw:
            return gw.session

    return anyio.run(main)


def hold_then_die(ld: Path, specs, pol, n: int = 2) -> tuple[list[str], str]:
    """A gateway holds `n` calls, then dies without any shutdown work. (hold ids, session)."""

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                for i in range(n):
                    calls.start(i, arguments={"n": i})
                ids = await rig.holds_created(ld, n)
                die(gw)
                tasks.cancel_scope.cancel()
            return ids, gw.session

    return anyio.run(main)


# The session lock ---------------------------------------------------------------------------


def test_serve_holds_its_session_lock(tmp_path):
    """The lock file is created before session.started, exclusively, and held while the
    gateway runs: a probe reads running, then ended once the gateway is gone."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.gateway(ld, specs, policy=pol) as gw:
            path = ld / "sessions" / f"{gw.session}.lock"
            assert path.is_file() and path.stat().st_size == 0
            assert lock.probe(path) == lock.RUNNING
            assert holds.session_state(ld, gw.session) == lock.RUNNING
            return gw.session, path

    session, path = anyio.run(main)
    assert lock.probe(path) == lock.ENDED
    started = rig.kinds(ld, "session.started")
    assert [e["data"]["session"] for e in started] == [session]
    if sys.platform != "win32":
        assert (path.stat().st_mode & 0o777, path.parent.stat().st_mode & 0o777) == (0o600, 0o700)
    with pytest.raises(FileExistsError):  # created exclusively: never reused
        lock.take_session(path)


def test_session_lock_failure_warns(tmp_path, capsys):
    """If serve can't create its lock file, it says so and goes on; its holds read unknown and
    a later start doesn't abandon them."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    ld.mkdir()
    (ld / "sessions").write_bytes(b"")  # a file where the directory should be
    ids, session = hold_then_die(ld, specs, pol, n=1)
    err = capsys.readouterr().err
    path = ld / "sessions" / f"{session}.lock"
    line = [x for x in err.splitlines() if "session lock" in x]
    assert len(line) == 1
    assert line[0].startswith(f"polarizer: warning: cannot create the session lock {path}: ")
    assert line[0].endswith("; this session's holds will show as unknown")
    assert holds.session_state(ld, session) == lock.UNKNOWN
    start_and_stop(ld, specs, pol)
    assert abandoned(ld) == []


def test_killed_process_releases_its_session_lock(tmp_path):
    """A process that takes its session lock and is then killed outright (SIGKILL on POSIX,
    TerminateProcess on Windows) leaves the lock free: the probe reads ended. This is what
    abandonment relies on (section 7); on Windows the release may take a moment, so the probe
    is repeated for up to 10 s."""
    path = tmp_path / "s.lock"
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from polarizer import lock\n"
        "held = lock.take_session(Path(sys.argv[1]))\n"
        "print('locked', flush=True)\n"
        "time.sleep(120)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert proc.stdout.readline() == b"locked" + os.linesep.encode()
        assert lock.probe(path) == lock.RUNNING
        proc.kill()
        proc.wait(timeout=60)
        deadline = time.monotonic() + 10
        while lock.probe(path) != lock.ENDED:
            assert time.monotonic() < deadline, "the lock stayed held after the process died"
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=60)
        proc.stdout.close()
        proc.stderr.close()


# Restart ------------------------------------------------------------------------------------


def test_restart_records_abandoned_holds(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    ids, dead = hold_then_die(ld, specs, pol)
    assert [e["kind"] for e in rig.entries(ld)][-2:] == ["hold.created", "hold.created"]
    second = start_and_stop(ld, specs, pol)
    assert abandoned(ld) == [{"session": second, "hold": h, "held_by": dead} for h in ids]
    start_and_stop(ld, specs, pol)
    assert len(abandoned(ld)) == 2  # none on a third start
    folded = holds.HoldState()
    verify_bytes((ld / "ledger.jsonl").read_bytes(), None, folded.apply)
    assert folded.open_holds() == []
    assert fake.calls == []


def test_running_session_is_not_abandoned(tmp_path):
    """A second gateway starts while the first still waits on its hold: it records no
    hold.abandoned, nor does a third; `holds` shows the session running; and the hold can
    still be allowed, which forwards the call through the first gateway."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    seen = {}

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                for _ in range(2):
                    async with rig.gateway(ld, specs, policy=pol, approve=False):
                        pass
                seen["abandoned"] = abandoned(ld)
                seen["state"] = holds.session_state(ld, gw.session)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold)
                result = await calls.result("a")
            return hold, result

    hold, result = anyio.run(main)
    assert seen == {"abandoned": [], "state": lock.RUNNING}
    assert len(rig.kinds(ld, "session.started")) == 3
    assert result.is_error is False and text(result) == json.dumps(ARGS, sort_keys=True)
    assert [args for _, args, _ in fake.calls] == [ARGS]
    sent = rig.kinds(ld, "call.sent")[-1]["data"]
    assert (sent["hold"], sent["allowed_by"]) == (hold, "hold")
    assert abandoned(ld) == []


def test_unknown_session_is_not_abandoned(tmp_path, capsys, monkeypatch):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    [hold], dead = hold_then_die(ld, specs, pol, n=1)
    (ld / "sessions" / f"{dead}.lock").unlink()
    start_and_stop(ld, specs, pol)
    assert abandoned(ld) == []
    capsys.readouterr()
    assert cli.main(["holds", "--ledger-dir", str(ld)]) == 0
    out = capsys.readouterr().out
    assert f"hold {hold} f__echo egress" in out
    assert f"session {dead} started " in out and out.count(", state unknown\n") == 1


def test_decision_on_ended_session_warns(tmp_path, capsys):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    [hold], dead = hold_then_die(ld, specs, pol, n=1)
    capsys.readouterr()
    code = cli.main(["allow", "--ledger-dir", str(ld), hold, "--allow-no-terminal"])
    out, err = capsys.readouterr()
    assert code == 0
    assert out.splitlines()[-1].startswith(f"allowed hold {hold} at seq ")
    assert "ended; no process will act on a decision" in out
    assert err == holds.ENDED_WARNING + "\n"
    decided = rig.kinds(ld, "hold.decided")
    assert [(d["data"]["hold"], d["data"]["decision"]) for d in decided] == [(hold, "allow")]
    start_and_stop(ld, specs, pol)
    assert abandoned(ld) == []  # the allow ended it; nothing to abandon
    assert rig.kinds(ld, "call.sent") == [] and fake.calls == []


def test_abandonment_is_conditional(tmp_path):
    """hold.abandoned goes through the conditional append: a hold that got an ending after the
    start folded the ledger (here, an allow written by another process between the fold and
    the append) gets no hold.abandoned."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    [hold], dead = hold_then_die(ld, specs, pol, n=1)
    state = holds.HoldState()
    writer = LedgerWriter.open(ld, on_entry=state.apply)
    try:
        assert [h.hold for h in state.open_holds()] == [hold]
        rig.decide_hold(ld, hold)  # another process decides after this one folded
        from polarizer.proxy import Gateway

        async def main():
            gw = Gateway(specs, writer, config_sha256=rig.CONFIG_SHA, holds=state, policy=pol)
            await gw._abandon_ended()

        anyio.run(main)
    finally:
        writer.close()
    assert abandoned(ld) == []
    assert state.get(hold).ending.kind == "hold.decided"


# Shutdown during a hold, over stdio ----------------------------------------------------------


def primed_held(tmp_path, cls: str = "egress"):
    """(config, ledger_dir, probe log): the probe as upstream p, every tool approved, and p's
    wait classified `cls`, so every call to it is held."""
    log = tmp_path / "probe.log"
    toml = (
        f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\n"
        f"args = [{rig.toml_str(rig.PROBE)}]\nenv = {{ PROBE_LOG = {rig.toml_str(log)} }}\n"
    )
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
    before = 'wait = { class = "local-read", path_args = [] }'
    text_ = cfg.read_text(encoding="utf-8")
    assert text_.count(before) == 1
    cfg.write_text(text_.replace(before, f'wait = {{ class = "{cls}", path_args = [] }}'), "utf-8")
    return cfg, rig.prime(cfg), log


def complete_entries(ld: Path) -> list[dict]:
    """The ledger's complete lines, read while another process may be writing."""
    data = (ld / "ledger.jsonl").read_bytes()
    return [json.loads(line) for line in data[: data.rfind(b"\n") + 1].splitlines()]


def wait_for(condition, seconds: float = 30):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def hold_one_call(client: RawClient, ld: Path, meta: dict | None = None) -> str:
    """Send tools/call p__wait(30) and wait until serve has recorded its hold; its id."""
    before = sum(e["kind"] == "hold.created" for e in complete_entries(ld))
    params = {"name": "p__wait", "arguments": {"seconds": 30}}
    if meta is not None:
        params["_meta"] = meta
    client.send({"jsonrpc": "2.0", "id": 90 + before, "method": "tools/call", "params": params})
    wait_for(lambda: sum(e["kind"] == "hold.created" for e in complete_entries(ld)) > before)
    return [e for e in complete_entries(ld) if e["kind"] == "hold.created"][-1]["data"]["hold"]


def serve(cfg: Path) -> RawClient:
    return RawClient([sys.executable, "-m", "polarizer", "serve", "--config", str(cfg)])


def finish(client: RawClient, seconds: float = 30) -> int:
    try:
        return client.proc.wait(timeout=seconds)
    finally:
        client.proc.stdin.close()
        client.proc.stdout.close()


def trail(ld: Path, hold: str) -> list[tuple[str, dict]]:
    return [(e["kind"], e["data"]) for e in complete_entries(ld) if e["data"].get("hold") == hold]


def assert_intact(ld: Path) -> None:
    data = (ld / "ledger.jsonl").read_bytes()
    assert verify_bytes(data, (ld / "ledger.head").read_bytes()).status == "intact"


def assert_ended_by_shutdown(ld: Path, hold: str, log: Path) -> None:
    assert_intact(ld)
    assert [k for k, _ in trail(ld, hold)] == ["hold.created", "hold.expired", "call.refused"]
    assert trail(ld, hold)[1][1]["reason"] == SHUTDOWN
    assert trail(ld, hold)[2][1]["reason"] == f"hold {hold} expired: {SHUTDOWN}"
    assert '"tools/call"' not in log.read_text(encoding="utf-8")
    again = rig.run_serve(ld.parent / "polarizer.toml")
    assert again.returncode == 0
    assert rig.kinds(ld, "hold.abandoned") == []


@POSIX_ONLY
def test_esc_during_a_hold(tmp_path):
    """SIGINT, then SIGTERM 100 ms later, as Claude Code sends them on Esc, while a call is
    held: serve exits 0, the hold ends with hold.expired (shutdown) and one call.refused, the
    probe never saw the call, and a new serve abandons nothing."""
    cfg, ld, log = primed_held(tmp_path)
    client = serve(cfg)
    client.initialize()
    hold = hold_one_call(client, ld)
    os.kill(client.proc.pid, signal.SIGINT)
    time.sleep(0.1)
    if client.proc.poll() is None:
        os.kill(client.proc.pid, signal.SIGTERM)
    assert finish(client) == 0
    assert_ended_by_shutdown(ld, hold, log)


def test_stdin_closed_during_a_hold(tmp_path):
    """End of input while a call is held takes the same shutdown path, on every platform."""
    cfg, ld, log = primed_held(tmp_path)
    client = serve(cfg)
    client.initialize()
    hold = hold_one_call(client, ld)
    client.proc.stdin.close()
    assert client.proc.wait(timeout=30) == 0
    client.proc.stdout.close()
    assert_ended_by_shutdown(ld, hold, log)


@POSIX_ONLY
def test_kill_during_a_hold(tmp_path, capsys):
    """SIGKILL while a call is held: the ledger is intact with the hold open and no terminal
    entry; `holds` shows the session ended; a new serve records hold.abandoned, and then the
    call has exactly one terminal entry."""
    cfg, ld, log = primed_held(tmp_path)
    client = serve(cfg)
    client.initialize()
    hold = hold_one_call(client, ld)
    session = trail(ld, hold)[0][1]["session"]
    os.kill(client.proc.pid, signal.SIGKILL)
    assert finish(client) == -signal.SIGKILL
    assert_intact(ld)
    assert [k for k, _ in trail(ld, hold)] == ["hold.created"]
    capsys.readouterr()
    assert cli.main(["holds", "--ledger-dir", str(ld)]) == 0
    out = capsys.readouterr().out
    assert f"hold {hold} p__wait egress" in out
    assert "ended; no process will act on a decision" in out
    assert rig.run_serve(cfg).returncode == 0
    assert_intact(ld)
    terminal = [
        (k, d)
        for k, d in trail(ld, hold)
        if k in ("call.returned", "call.refused", "hold.abandoned")
    ]
    assert len(terminal) == 1 and terminal[0][0] == "hold.abandoned"
    assert terminal[0][1]["held_by"] == session
    assert '"tools/call"' not in log.read_text(encoding="utf-8")


# Progress while held, over stdio --------------------------------------------------------------


class Lines:
    """serve's stdout read on a thread, one JSON message per line, so a test can wait with a
    bound on every platform."""

    def __init__(self, proc):
        self.queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, args=(proc.stdout,), daemon=True).start()

    def _read(self, stream):
        for line in iter(stream.readline, b""):
            self.queue.put(json.loads(line))

    def take(self, seconds: float) -> list[dict]:
        """Every message that arrives within `seconds`."""
        found, deadline = [], time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            try:
                found.append(self.queue.get(timeout=left))
            except queue.Empty:
                break
        return found


def progress_of(messages: list[dict], token) -> list[dict]:
    return [
        m["params"]
        for m in messages
        if m.get("method") == "notifications/progress" and m["params"]["progressToken"] == token
    ]


def test_progress_during_a_hold_over_stdio(tmp_path):
    """serve over stdio, with the interval injected at 0.05 s: a held call whose client asked
    for progress gets increasing progress with no total and no message; one that asked for
    none gets none. (The in-memory half is in test_holds.py.)"""
    cfg, ld, _ = primed_held(tmp_path)
    argv = [sys.executable, str(HELPERS / "fast_serve.py"), "0.05", "serve", "--config", str(cfg)]
    client = RawClient(argv)
    try:
        client.initialize()
        lines = Lines(client.proc)
        hold_one_call(client, ld)  # no progress token
        quiet = lines.take(0.5)  # ten intervals
        assert [m for m in quiet if m.get("method") == "notifications/progress"] == []
        hold_one_call(client, ld, meta={"progressToken": "tok-7"})
        got: list[dict] = []
        deadline = time.monotonic() + 10
        while len(progress_of(got, "tok-7")) < 3:
            assert time.monotonic() < deadline, got
            got += lines.take(0.1)
        seen = progress_of(got, "tok-7")
        values = [p["progress"] for p in seen]
        assert values == sorted(set(values)) and all(isinstance(v, int) for v in values)
        assert all("total" not in p and "message" not in p for p in seen)
        assert [m for m in got if m.get("method") == "notifications/progress"] == [
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": p} for p in seen
        ]
    finally:
        assert client.close() == 0


# holds --wait (section 8) -------------------------------------------------------------------


class Reads:
    """Counts holds --wait's reads of the ledger and its polls between them."""

    def __init__(self, monkeypatch):
        self.reads = self.polls = 0
        real_read = cli.read_files

        def read(ledger_dir):
            self.reads += 1
            return real_read(ledger_dir)

        def sleep(seconds):
            self.polls += 1
            time.sleep(seconds)

        monkeypatch.setattr(cli, "read_files", read)
        monkeypatch.setattr(cli, "_wait_sleep", sleep)

    def until(self, condition, seconds: float = 30):
        wait_for(condition, seconds)


def append_hold(ld: Path, session: str, hold: str, salt: int, started: bool = False) -> None:
    """What a serve writes when it holds a call: (session.started,) hold.created, appended by
    a writer of this process as another process's would be."""
    writer = LedgerWriter.open(ld)
    try:
        if started:
            writer.append(*hl.started(session)).result()
        commit = hl.side_file(ld, {"n": salt}, salt)
        reason = "class egress is held on every call"
        entry = hl.created(session, hold, "web__post", commit, "egress", "config", "egress", reason)
        writer.append(*entry).result()
    finally:
        writer.close()


def test_holds_wait(tmp_path, capsys, monkeypatch):
    """holds --wait started before any hold keeps waiting while the only open hold is a dead
    session's, and prints the listing and exits 0 within 2 s of a running session's hold
    appearing (it polls every 0.25 s)."""
    ld = tmp_path / "ledger"
    ld.mkdir()
    build_chain(ld, [hl.started(hl.ENDED)], head_at=0)
    hl.lock_files(ld, hl.ENDED, hl.RUNNING)
    reads = Reads(monkeypatch)
    seen = {}

    def other_processes():
        reads.until(lambda: reads.reads >= 1 and reads.polls >= 1)
        append_hold(ld, hl.ENDED, hl.DENIED, 1)  # an open hold, but its session has ended
        reads.until(lambda: reads.reads >= 2)
        polls = reads.polls
        reads.until(lambda: reads.polls >= polls + 4)  # a second of polling after the read
        seen["still waiting"] = "returned" not in seen
        running = hl.hold_running(ld)
        seen["lock"] = running
        append_hold(ld, hl.RUNNING, hl.WRITE_PATTERN, 2, started=True)
        seen["appeared"] = time.monotonic()

    thread = threading.Thread(target=other_processes, daemon=True)
    thread.start()
    try:
        code = cli.main(["holds", "--ledger-dir", str(ld), "--wait"])
        seen["returned"] = time.monotonic()
        thread.join(30)
    finally:
        if "lock" in seen:
            seen["lock"].close()
    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert seen["still waiting"] is True
    assert seen["returned"] - seen["appeared"] < 2
    assert out.startswith("holds: 2 open\n") and chr(7) not in out
    assert f"hold {hl.DENIED} web__post egress" in out and f"hold {hl.WRITE_PATTERN} " in out
    assert "ended; no process will act on a decision" in out and ", running\n" in out


def test_holds_wait_bell(tmp_path, capsys, monkeypatch):
    """holds --wait --bell on a ledger with two open holds of a running session (and one of an
    ended session) writes two BELs, then the listing, byte for byte as holds_wait_bell.txt,
    which is holds_mixed.txt after two BELs. Without --bell, no BEL. --bell without --wait is
    a usage error. A hold seen while its session's state was unknown gets its BEL once the
    session reads as running."""
    golden = Path(__file__).resolve().parent / "golden"
    monkeypatch.setattr(cli, "_now", lambda: hl.NOW)
    ld = hl.mixed(tmp_path / "ledger")
    running = hl.hold_running(ld)
    try:
        assert cli.main(["holds", "--ledger-dir", str(ld), "--wait", "--bell"]) == 0
        bell = capsys.readouterr()
        assert cli.main(["holds", "--ledger-dir", str(ld), "--wait"]) == 0
        plain = capsys.readouterr()
    finally:
        running.close()
    want = (golden / "holds_wait_bell.txt").read_bytes()
    assert want == b"\x07\x07" + (golden / "holds_mixed.txt").read_bytes()
    assert (bell.out.encode("utf-8"), bell.err) == (want, "")
    assert (plain.out.encode("utf-8"), plain.err) == (want[2:], "")
    assert cli.main(["holds", "--ledger-dir", str(ld), "--bell"]) == 2
    assert capsys.readouterr() == ("", "polarizer: --bell goes with --wait\n")

    # A session whose state can't be told at first: its hold rings once it reads as running.
    ld2 = tmp_path / "later"
    ld2.mkdir()
    build_chain(ld2, [hl.started(hl.NO_LOCK)], head_at=0)
    append_hold(ld2, hl.NO_LOCK, hl.UNKNOWN_SESSION, 3)
    reads = Reads(monkeypatch)
    held = []

    def session_comes_up():
        reads.until(lambda: reads.reads >= 1 and reads.polls >= 2)
        hl.lock_files(ld2, hl.NO_LOCK)
        held.append(hl.hold_running(ld2, hl.NO_LOCK))
        append_hold(ld2, hl.NO_LOCK, hl.MISSING, 4)  # the ledger grows: read again

    thread = threading.Thread(target=session_comes_up, daemon=True)
    thread.start()
    try:
        assert cli.main(["holds", "--ledger-dir", str(ld2), "--wait", "--bell"]) == 0
        thread.join(30)
    finally:
        for one in held:
            one.close()
    out = capsys.readouterr().out
    assert out.startswith("\x07\x07holds: 2 open\n") and out.count("\x07") == 2


@POSIX_ONLY
def test_holds_wait_ends_on_ctrl_c(tmp_path):
    """Ctrl+C (SIGINT) ends `polarizer holds --wait` by the default action: no traceback and
    nothing on stdout. POSIX, as the signal tests are."""
    ld = tmp_path / "ledger"
    ld.mkdir()
    build_chain(ld, [hl.started(hl.ENDED)], head_at=0)
    argv = [sys.executable, "-m", "polarizer", "holds", "--ledger-dir", str(ld), "--wait"]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # A SIGINT before `holds` sets the default action would meet Python's own handler, and a
    # traceback. On Linux, /proc shows whether SIGINT is caught: wait until Python's handler is
    # in place, then until `holds` has replaced it. Elsewhere, wait 5 s.
    status = Path(f"/proc/{proc.pid}/status")
    if status.exists():

        def caught() -> bool:
            for line in status.read_text(encoding="ascii").splitlines():
                if line.startswith("SigCgt:"):
                    return bool(int(line.split()[1], 16) & (1 << (signal.SIGINT - 1)))
            return False

        wait_for(caught, 30)
        wait_for(lambda: not caught(), 30)
    else:
        time.sleep(5)
    assert proc.poll() is None
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=30)
    assert (proc.returncode, out, err) == (-signal.SIGINT, b"", b"")
