"""A headless Chromium driven over the DevTools protocol on a pipe, with the standard library
only, for tests that need to see what a browser draws. Nothing is installed or fetched: it uses
the Chromium that Playwright left in ~/.cache/ms-playwright, or $POLARIZER_CHROMIUM, and
find() returns None when there is neither, or on Windows, where the pipe can't be handed over
this way, so a test can skip.

Pages are timed in real time (time.sleep), never virtual time, so an animation runs as it does
for a person looking at the page.
"""

import base64
import json
import os
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

CANDIDATES = (
    "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    "chromium-*/chrome-linux64/chrome",
)


def find() -> Path | None:
    if os.name != "posix":
        return None
    named = os.environ.get("POLARIZER_CHROMIUM")
    if named:
        return Path(named) if Path(named).is_file() else None
    cache = Path.home() / ".cache" / "ms-playwright"
    for pattern in CANDIDATES:
        found = sorted(cache.glob(pattern))
        if found:
            return found[-1]
    return None


class Browser:
    """One headless Chromium and one page in it. extra is more command-line flags, such as
    --force-prefers-reduced-motion."""

    def __init__(self, binary: Path, extra: tuple[str, ...] = ()):
        self.profile = tempfile.TemporaryDirectory(prefix="polarizer-chromium-")
        to_child_r, self.to_child = os.pipe()
        self.from_child, from_child_w = os.pipe()

        import fcntl  # POSIX only; find() keeps Windows from getting here

        def fds():  # the pipe's ends as fds 3 and 4, where --remote-debugging-pipe looks
            high = [fcntl.fcntl(fd, fcntl.F_DUPFD, 10) for fd in (to_child_r, from_child_w)]
            for want, fd in zip((3, 4), high, strict=True):
                os.dup2(fd, want, inheritable=True)

        argv = [str(binary), "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                "--no-first-run", "--disable-extensions", "--disable-background-networking",
                "--remote-debugging-pipe", f"--user-data-dir={self.profile.name}", *extra,
                "about:blank"]  # fmt: skip
        self.proc = subprocess.Popen(
            argv,
            preexec_fn=fds,
            close_fds=False,  # closing would close fds 3 and 4 after fds() made them
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(to_child_r)
        os.close(from_child_w)
        self.buffer = b""
        self.next_id = 0
        self.events: list[dict] = []
        target = self.call("Target.createTarget", url="about:blank")["targetId"]
        self.session = self.call("Target.attachToTarget", targetId=target, flatten=True)[
            "sessionId"
        ]
        self.call("Page.enable", session=True)

    def close(self) -> None:
        try:
            self.call("Browser.close")
        except (OSError, RuntimeError):
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        os.close(self.to_child)
        os.close(self.from_child)
        self.profile.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self) -> dict:
        while b"\0" not in self.buffer:
            chunk = os.read(self.from_child, 1 << 16)
            if not chunk:
                raise RuntimeError("chromium closed its pipe")
            self.buffer += chunk
        message, self.buffer = self.buffer.split(b"\0", 1)
        return json.loads(message)

    def call(self, method: str, session: bool = False, **params) -> dict:
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params}
        if session:
            message["sessionId"] = self.session
        os.write(self.to_child, json.dumps(message).encode() + b"\0")
        while True:
            reply = self._read()
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})
            self.events.append(reply)

    def wait_for(self, method: str, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for i, event in enumerate(self.events):
                if event.get("method") == method:
                    return self.events.pop(i)
            self.events.append(self._read())
        raise RuntimeError(f"no {method} within {timeout} s")

    def open(self, url: str, width: int, height: int, scale: float = 1.0) -> float:
        """Load url in a width x height viewport and return the monotonic time of its load
        event, the zero for the page's real-time waits."""
        self.call(
            "Emulation.setDeviceMetricsOverride",
            session=True,
            width=width,
            height=height,
            deviceScaleFactor=scale,
            mobile=False,
        )
        self.events.clear()
        self.call("Page.navigate", session=True, url=url)
        self.wait_for("Page.loadEventFired")
        return time.monotonic()

    def screenshot(self) -> bytes:
        data = self.call("Page.captureScreenshot", session=True, format="png")["data"]
        return base64.b64decode(data)

    def at(self, start: float, seconds: float) -> tuple[bytes, float]:
        """A screenshot taken once `seconds` of real time have passed since start, and the
        seconds that had in fact passed when it was taken."""
        time.sleep(max(0.0, start + seconds - time.monotonic()))
        taken = time.monotonic() - start
        return self.screenshot(), taken


def pixels(png: bytes) -> tuple[int, int, list[tuple[int, int, int]]]:
    """(width, height, RGB pixels row by row) of an 8-bit RGB or RGBA, non-interlaced PNG, the
    kind Chromium writes."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, width, height, kind = 8, b"", 0, 0, 0
    while pos < len(png):
        size = int.from_bytes(png[pos : pos + 4], "big")
        name, body = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + size]
        if name == b"IHDR":
            width, height = int.from_bytes(body[0:4], "big"), int.from_bytes(body[4:8], "big")
            depth, kind, interlace = body[8], body[9], body[12]
            assert depth == 8 and kind in (2, 6) and interlace == 0, (depth, kind, interlace)
        elif name == b"IDAT":
            idat += body
        pos += 12 + size
    step = 4 if kind == 6 else 3
    stride = width * step
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(stride)
    for y in range(height):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - step] if i >= step else 0
            b = prev[i]
            c = prev[i - step] if i >= step else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    out = []
    for line in rows:
        out.extend(tuple(line[i : i + 3]) for i in range(0, stride, step))
    return width, height, out


def near(pixel: tuple[int, int, int], color: str, tolerance: int = 24) -> bool:
    want = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    return all(abs(p - w) <= tolerance for p, w in zip(pixel, want, strict=True))


def count(
    png: bytes, box: tuple[int, int, int, int], colors: tuple[str, ...], tolerance: int = 24
) -> int:
    """How many pixels inside box (left, top, right, bottom, in screenshot pixels) are within
    tolerance of any of colors in every channel."""
    width, _, rgb = pixels(png)
    left, top, right, bottom = box
    return sum(
        1
        for y in range(top, bottom)
        for x in range(left, right)
        if any(near(rgb[y * width + x], color, tolerance) for color in colors)
    )


def differing(a: bytes, b: bytes, box: tuple[int, int, int, int], tolerance: int = 24) -> int:
    """How many pixels inside box differ between two same-sized screenshots by more than
    tolerance in any channel."""
    wa, ha, pa = pixels(a)
    wb, hb, pb = pixels(b)
    assert (wa, ha) == (wb, hb)
    left, top, right, bottom = box
    return sum(
        1
        for y in range(top, bottom)
        for x in range(left, right)
        if any(abs(p - q) > tolerance for p, q in zip(pa[y * wa + x], pb[y * wb + x], strict=True))
    )
