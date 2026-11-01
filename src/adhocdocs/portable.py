"""What differs between Linux, macOS and Windows on the host, in one place:
file locks, the per-user runtime directory, and background processes.

Text files are a fourth difference, handled where they are opened: always
``encoding="utf-8"``, and ``newline="\\n"`` when writing, so a matter written
on Windows reads the same in the (Linux) session and its hashes match. Tests
turn a missing encoding into an error (``EncodingWarning``, in pyproject.toml).
"""

import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

WINDOWS = os.name == "nt"

# Windows locks are mandatory: a locked byte can't be read by anyone else.
# So lock one byte far past the end of the file, which locks nothing anyone
# reads, and serves as a mutex between writers.
_LOCK_OFFSET = 0x7FFF_FFFF


@contextmanager
def locked(f, wait=True):
    """Hold an exclusive lock on the open file ``f`` (advisory: it excludes
    only others who lock). Raises BlockingIOError if ``wait`` is False and
    someone else holds it."""
    if not WINDOWS:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
        return
    import msvcrt
    f.flush()
    fd = f.fileno()

    def at_lock_byte(op):
        pos = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            op()
        finally:
            os.lseek(fd, pos, os.SEEK_SET)

    while True:
        try:
            at_lock_byte(lambda: msvcrt.locking(fd, msvcrt.LK_NBLCK, 1))
            break
        except OSError:
            if not wait:
                raise BlockingIOError(f"{getattr(f, 'name', 'file')} is locked")
            time.sleep(0.05)
    try:
        yield
    finally:
        f.flush()
        at_lock_byte(lambda: msvcrt.locking(fd, msvcrt.LK_UNLCK, 1))


def runtime_dir() -> Path:
    """A private per-user directory for sockets and other run-time files."""
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "adhocdocs" / "run"
    if os.environ.get("XDG_RUNTIME_DIR"):
        return Path(os.environ["XDG_RUNTIME_DIR"])
    return Path(f"/tmp/adhocdocs-{os.getuid()}")


def spawn(args, log: Path | None = None, **kw) -> subprocess.Popen:
    """Start a background process, in its own process group (so the
    terminal's Ctrl+C doesn't reach it) and, on Windows, without a console
    window. Output goes to ``log``, appended."""
    out = open(log, "ab") if log else subprocess.DEVNULL
    if WINDOWS:
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        return subprocess.Popen(args, stdout=out, stderr=out, stdin=subprocess.DEVNULL, **kw)
    finally:
        if log:
            out.close()        # the child has its own copy


def stop(p: subprocess.Popen):
    """Stop a process started by :func:`spawn`, with its process group where
    there is one."""
    if p.poll() is not None:
        return
    try:
        if WINDOWS:
            p.terminate()
        else:
            import signal
            os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

