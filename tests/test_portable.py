"""Host code that must also run on Windows (see WINDOWS.md)."""

import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

from adhocdocs import portable
from adhocdocs.index import Busy
from adhocdocs.index.base import Index

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tools").rglob("*.py"))


def _text_io_without_encoding(path: Path):
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(n, ast.Call):
            continue
        f, kind = n.func, None
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            kind = f.attr
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
            m = mode.value if isinstance(mode, ast.Constant) else "r" if mode is None else ""
            kind = None if "b" in m else "open"
        if kind and not any(k.arg == "encoding" for k in n.keywords):
            yield f"{path.relative_to(ROOT)}:{n.lineno}: {kind} without encoding"


def test_text_files_name_their_encoding():
    """Windows Python 3.14 reads and writes text in the ANSI code page unless
    told otherwise, so a matter written there wouldn't read back in the session."""
    bad = [b for p in SOURCES for b in _text_io_without_encoding(p)]
    assert not bad, "\n".join(bad)


def test_posix_only_calls_stay_in_portable():
    names = {"fcntl", "getuid", "killpg", "start_new_session"}
    bad = []
    for p in SOURCES:
        if p.name == "portable.py":
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            found = (n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute)
                     else n.arg if isinstance(n, ast.keyword) else
                     n.names[0].name if isinstance(n, ast.Import) else None)
            if found in names:
                bad.append(f"{p.relative_to(ROOT)}:{n.lineno}: {found}")
    assert not bad, "\n".join(bad)


def test_lock_excludes_and_releases(tmp_path):
    path = tmp_path / "lock"
    with open(path, "w", encoding="utf-8") as a, portable.locked(a):
        with open(path, "w", encoding="utf-8") as b:
            with pytest.raises(BlockingIOError):
                with portable.locked(b, wait=False):
                    pass
    with open(path, "w", encoding="utf-8") as b, portable.locked(b, wait=False):
        pass


def test_locked_append_still_appends(tmp_path):
    """On Windows the lock byte is reached by seeking; writes must still land at the end."""
    path = tmp_path / "log.jsonl"
    for i in range(3):
        with open(path, "a", encoding="utf-8", newline="\n") as f, portable.locked(f):
            f.write(f"{i}\n")
    assert path.read_bytes() == b"0\n1\n2\n"


def test_index_lock_reports_busy(tmp_path):
    idx = Index.__new__(Index)
    idx.dir, idx.name = tmp_path / "ix", "ix"
    with idx.lock():
        with pytest.raises(Busy):
            with idx.lock(wait=False):
                pass
    with idx.lock(wait=False):
        pass


def test_spawn_logs_and_stops(tmp_path):
    log = tmp_path / "svc.log"
    p = portable.spawn([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"], log)
    end = time.time() + 10
    while time.time() < end and b"up" not in log.read_bytes():
        time.sleep(0.05)
    assert b"up" in log.read_bytes()
    portable.stop(p)
    assert p.wait(timeout=10) is not None
    portable.stop(p)        # already stopped: no error


def test_runtime_dir_is_absolute():
    assert portable.runtime_dir().is_absolute()


def test_shell_scripts_have_unix_line_endings():
    """The image copies these; a Windows checkout must not turn them into CRLF."""
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "eol=lf" in attrs
    for p in (ROOT / "container").iterdir():
        assert b"\r\n" not in p.read_bytes(), p
    out = subprocess.run(["git", "check-attr", "eol", "container/adhoc-session"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode == 0:
        assert out.stdout.strip().endswith("lf")
