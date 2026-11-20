"""Starting things without a command line: the launcher, and terminals.

``adhoc gui`` opens the launcher, a window of the viewer program
(``adhoc-viewer --launcher``) that does what otherwise takes a command line:
make a matter, add documents, open a session, set up. It does each by
running the same ``adhoc`` commands, so there is one way of doing each thing.

Claude Code needs a terminal. A session started from a terminal stays in it
(``adhoc open``, as always). One started from the launcher, or anywhere else
without a terminal, gets one:

- ``beside``: the viewer runs it, beside the documents in the viewer's window;
- ``window``: the viewer runs it, in a window of its own (and it can move
  between the two while it runs);
- ``app``: in the system's own terminal program (:func:`terminal_app`).

In the viewer it is an ordinary terminal emulator (xterm.js on a
pseudo-terminal, ``viewer/src/term.rs``), not a reformatting of Claude
Code's output: what Claude Code draws is what you see.

The launcher's list of matters is the matters this user made, added to, or
opened here, kept in ``matters.json`` in the user's config directory. It is
only a list of paths: forgetting one leaves the matter where it is.
"""

import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

from .matter import Matter
from .portable import WINDOWS

REPO = Path(__file__).resolve().parents[2]
TERMINAL_MODES = ("here", "beside", "window", "app")


def config_dir() -> Path:
    if WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "adhocdocs"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "adhocdocs"


# -- the list of matters -------------------------------------------------------------------------

def _registry() -> Path:
    return config_dir() / "matters.json"


def _read() -> dict:
    try:
        return json.loads(_registry().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"matters": {}}


def _write(data: dict):
    p = _registry()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def remember(root, opened=False):
    """Put a matter on this user's list (a failure here never stops the command)."""
    try:
        data = _read()
        key = str(Path(root).resolve())
        entry = data.setdefault("matters", {}).setdefault(key, {"added": time.time()})
        if opened:
            entry["opened"] = time.time()
        _write(data)
    except OSError:
        pass


def forget(root):
    data = _read()
    data.get("matters", {}).pop(str(Path(root).resolve()), None)
    _write(data)


def documents(root: Path) -> int | None:
    try:
        return len(Matter(root).state.live_documents())
    except Exception:
        return None


def matters() -> dict:
    """The list, most recently used first, with what the launcher shows."""
    rows = []
    for path, e in _read().get("matters", {}).items():
        p = Path(path)
        ok = (p / "store").is_dir()
        rows.append({"path": path, "name": p.name, "exists": ok, "documents": documents(p) if ok else None,
                     "opened": e.get("opened"), "added": e.get("added")})
    rows.sort(key=lambda r: -(r["opened"] or r["added"] or 0))
    sample = REPO / "corpus" / "manifest.toml"
    app = terminal_app()
    return {"matters": rows, "sample": str(sample) if sample.exists() else None,
            "terminal_app": app[0] if app else None, "home": str(Path.home())}


# -- terminals -----------------------------------------------------------------------------------

# Terminal programs, and how each is told to run a command (the command's words follow).
TERMINALS = [
    ("x-terminal-emulator", ["-e"]),          # Debian's choice of the user's terminal
    ("gnome-terminal", ["--"]), ("kgx", ["--"]), ("ptyxis", ["--"]), ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]), ("mate-terminal", ["-x"]), ("tilix", ["-e"]), ("terminator", ["-x"]),
    ("kitty", []), ("alacritty", ["-e"]), ("foot", []), ("wezterm", ["start", "--"]), ("ghostty", ["-e"]),
    ("lxterminal", ["-e"]), ("qterminal", ["-e"]), ("urxvt", ["-e"]), ("xterm", ["-e"]),
]


def terminal_app() -> tuple[str, list[str]] | None:
    """(name, the command's prefix): ``$ADHOC_TERMINAL`` (a command line to
    which the command is added), else the first terminal program found."""
    if os.environ.get("ADHOC_TERMINAL"):
        words = shlex.split(os.environ["ADHOC_TERMINAL"])
        return (Path(words[0]).name, words)
    if WINDOWS:
        wt = shutil.which("wt")
        return ("Windows Terminal", [wt, "--"]) if wt else ("Command Prompt", ["cmd", "/c", "start", "adhocdocs", "/wait"])
    if sys.platform == "darwin":
        return None          # Terminal.app takes a script, not a command line: not yet
    for name, flags in TERMINALS:
        found = shutil.which(name)
        if found:
            if name == "x-terminal-emulator":
                # which one it is, for saying so (Debian's alternatives: a link, sometimes to a .wrapper)
                name = Path(os.path.realpath(found)).name.removesuffix(".wrapper")
            return (name, [found, *flags])
    return None


def in_terminal_app(argv: list[str]) -> list[str]:
    app = terminal_app()
    if not app:
        raise RuntimeError("no terminal program found: set ADHOC_TERMINAL (e.g. \"gnome-terminal --\"), "
                           "or have the viewer run Claude Code (--terminal beside)")
    return [*app[1], *argv]


def adhoc_command() -> list[str]:
    """How to run this installation's ``adhoc`` from anywhere."""
    return [sys.executable, "-m", "adhocdocs.cli", "adhoc"]


# -- the launcher --------------------------------------------------------------------------------

def launch(wait=True):
    from .view import viewer_binary
    cmd = [viewer_binary(), "--launcher", "--annotate", json.dumps(adhoc_command())]
    if wait:
        import subprocess
        return subprocess.call(cmd)
    from .portable import spawn
    return spawn(cmd).pid


DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=adhocdocs
GenericName=Document review with Claude
Comment=Make matters, add documents, and work on them with Claude Code
Exec={exec}
Icon=folder-documents
Terminal=false
Categories=Office;Viewer;
StartupNotify=true
"""


def desktop_entry_path() -> Path:
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data / "applications" / "adhocdocs.desktop"


def install_desktop_entry() -> Path | None:
    """Put adhocdocs in the desktop's application menu (Linux: a .desktop file)."""
    if WINDOWS or sys.platform == "darwin":
        return None
    p = desktop_entry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    exec_line = " ".join(shlex.quote(w) for w in [*adhoc_command(), "gui"])
    p.write_text(DESKTOP_ENTRY.format(exec=exec_line.replace("%", "%%")), encoding="utf-8", newline="\n")
    return p
