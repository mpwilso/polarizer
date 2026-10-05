"""Reading the controller side of a pseudo-terminal without losing the end of the output.

Read gives end of file or EIO once everything holding the terminal has closed it: Linux says
EIO, macOS can say either. The test helpers stop reading only at that end, then wait for the
child and read whatever is still there, so nothing printed is dropped on any platform (CI run
37249953282, macOS: hold-check.sh status exited 0 with no output)."""

import errno
import os
import select
import time


def read_some(controller: int, seconds: float) -> bytes | None:
    """What the terminal printed within `seconds`: bytes (empty when nothing came), or None at
    the end (end of file, or EIO). Any other error is raised."""
    if not select.select([controller], [], [], seconds)[0]:
        return b""
    try:
        chunk = os.read(controller, 65536)
    except OSError as e:
        if e.errno == errno.EIO:
            return None
        raise
    return chunk or None


def finish(controller: int, proc, seconds: float = 30) -> tuple[int, bytes]:
    """After the end has been read: waits for the child, then reads until the terminal has
    nothing more, so no output still buffered is dropped. Returns the exit code and the bytes."""
    code = proc.wait(timeout=seconds)
    rest = b""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        chunk = read_some(controller, 0.05)
        if not chunk:
            break
        rest += chunk
    return code, rest
