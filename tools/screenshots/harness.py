"""What shoot.py (still shots) and scenario.py (sessions with Claude Code,
recorded or replayed) share: the sample matter, a virtual screen, the
viewer, a pointer that moves like a person's, and the steps that drive them.
See docs/SCREENSHOTS.md."""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from adhocdocs import view as viewmod
from adhocdocs.matter import Matter
from adhocdocs.portable import spawn, stop

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BIN = Path(sys.executable).parent          # the venv's scripts: adhoc, view, search, list, …

# What makes two runs draw the same pixels, besides the screen size: no
# blinking caret or animations, one theme, one font setting, one clock zone.
GTK_SETTINGS = """[Settings]
gtk-theme-name=Adwaita
gtk-application-prefer-dark-theme={dark}
gtk-cursor-blink=false
gtk-enable-animations=false
gtk-font-name=DejaVu Sans 10
gtk-xft-antialias=1
gtk-xft-hinting=1
gtk-xft-hintstyle=hintslight
gtk-xft-rgba=none
"""


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Failed(Exception):
    """A shot or scenario can't go on."""


def base_env() -> dict:
    return {"TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NO_AT_BRIDGE": "1",
            "FONTCONFIG_FILE": str(HERE / "fonts.conf"),
            "WEBKIT_DISABLE_DMABUF_RENDERER": "1", "LIBGL_ALWAYS_SOFTWARE": "1"}


# -- the matter ----------------------------------------------------------------------------------

def make_matter(path: Path, env) -> Matter:
    """The sample matter, made once: a matter with documents in it is reused."""
    if (path / "matter.toml").exists():
        try:
            m = Matter(path)
            if any(True for _ in m.state.live_documents()):
                log(f"reusing the matter at {path}")
                return m
        except Exception:
            pass
    if path.exists():
        shutil.rmtree(path)
    log(f"making the sample matter at {path}")
    t = time.time()
    subprocess.run([BIN / "adhoc", "init", str(path)], check=True, env=env, stdout=subprocess.DEVNULL)
    subprocess.run([BIN / "adhoc", "--matter", str(path), "ingest", "--manifest", str(REPO / "corpus" / "manifest.toml")],
                   check=True, env=env, stdout=subprocess.DEVNULL)
    subprocess.run([BIN / "idx", "--matter", str(path), "update"], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"  made in {time.time() - t:.0f} s")
    return Matter(path)


def copy_matter(m: Matter, dest_parent: Path) -> Matter:
    """A copy of the matter under the same name (the overview is headed with
    it), for a scenario to change as it likes."""
    dest = dest_parent / m.root.name
    if dest.exists():
        shutil.rmtree(dest)
    dest_parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(m.root, dest, symlinks=True, ignore=shutil.ignore_patterns("run", "claude-home"))
    return Matter(dest)


def doc_ids(m: Matter) -> dict[str, str]:
    """Each document's ID by its path under corpus/ (collection/file; an
    attachment adds /1, /2 …), so shot lists name documents by file rather
    than by hash."""
    from adhocdocs.analysis import _ready
    rows = _ready(m, "fields").rows()
    out = {}
    for d in m.state.live_documents():
        r = rows.get(d) or {}
        if r.get("path"):
            out.setdefault(f"{r.get('collection')}/{r['path']}", d)
    return out


def expand(s: str, ids: dict[str, str]) -> str:
    """``{doc:public/foo.pdf}`` → ``doc:<id>``: a path under corpus/ or the
    end of one, which must name exactly one document."""
    def one(mt):
        want = mt[1]
        hits = sorted({d for p, d in ids.items() if p == want or p.endswith("/" + want)})
        if len(hits) != 1:
            raise Failed(f"{{doc:{want}}} matches {len(hits)} documents")
        return "doc:" + hits[0]
    return re.sub(r"\{doc:([^}]+)\}", one, s)


# -- the screen ----------------------------------------------------------------------------------

class Screen:
    def __init__(self, display: str, w: int, h: int):
        self.display, self.w, self.h = display, w, h
        self.env = {**os.environ, "DISPLAY": display}
        self.video = None
        self.proc = subprocess.Popen(["Xvfb", display, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp",
                                      "-dpi", "96", "+extension", "RANDR"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if subprocess.run(["xdpyinfo"], env=self.env, capture_output=True).returncode == 0:
                return
            time.sleep(0.1)
        raise Failed(f"Xvfb didn't start on {display}")

    def xdotool(self, *args, check=True) -> str:
        r = subprocess.run(["xdotool", *map(str, args)], env=self.env, capture_output=True, text=True)
        if check and r.returncode:
            raise Failed(f"xdotool {' '.join(map(str, args))}: {r.stderr.strip()}")
        return r.stdout

    def grab(self) -> bytes:
        """The screen's pixels, raw RGB (no file format, so nothing like a
        timestamp can make two equal screens differ)."""
        return subprocess.run(["import", "-display", self.display, "-window", "root", "-depth", "8", "rgb:-"],
                              check=True, capture_output=True).stdout

    def save(self, rgb: bytes, path: Path):
        subprocess.run(["convert", "-size", f"{self.w}x{self.h}", "-depth", "8", "rgb:-",
                        "-define", "png:exclude-chunks=date,time", "-strip", str(path)],
                       input=rgb, check=True)

    def settle(self, least=0.6, quiet=1.0, limit=20.0) -> tuple[bytes, bool]:
        """Wait at least ``least`` seconds, then until the screen hasn't
        changed for ``quiet`` seconds (or ``limit`` passes). Returns the last
        picture and whether it settled."""
        time.sleep(least)
        last, since, t0 = self.grab(), time.time(), time.time()
        while time.time() - t0 < limit:
            time.sleep(0.25)
            now = self.grab()
            if now != last:
                last, since = now, time.time()
            elif time.time() - since >= quiet:
                return last, True
        return last, False

    def record(self, path: Path, fps=30):
        """Start recording the screen, pointer included, to an MP4."""
        self.video = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-draw_mouse", "1", "-framerate", str(fps),
             "-video_size", f"{self.w}x{self.h}", "-i", self.display,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(path)],
            stdin=subprocess.PIPE, env=self.env)

    def stop_recording(self):
        if self.video:
            try:
                self.video.communicate(b"q", timeout=30)
            except subprocess.TimeoutExpired:
                self.video.kill()
            self.video = None

    def close(self):
        self.stop_recording()
        self.proc.terminate()
        self.proc.wait()


# -- the pointer ---------------------------------------------------------------------------------

def ease(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


class Pointer:
    """The mouse. For video it glides along a gentle curve, quick in the
    middle and slow at either end, and pauses a moment before a click, as a
    hand does; for stills it jumps, so nothing it passes over can leave a
    trace (a hover card, a highlight) that differs from run to run."""

    def __init__(self, screen: Screen, human: bool):
        self.screen, self.human = screen, human
        self.x, self.y = screen.w - 1, screen.h - 1
        self.moves = 0
        screen.xdotool("mousemove", "--sync", self.x, self.y)

    def move(self, x: float, y: float):
        x, y = round(x), round(y)
        if not self.human:
            self.screen.xdotool("mousemove", "--sync", x, y)
        else:
            dist = math.hypot(x - self.x, y - self.y)
            if dist >= 2:
                seconds = min(1.1, max(0.3, 0.25 + dist / 1500))
                n = max(2, int(seconds * 60))
                # bow to one side or the other, alternately, by a tenth of the way
                bow = (1 if self.moves % 2 else -1) * min(80, dist / 10)
                nx, ny = -(y - self.y) / dist, (x - self.x) / dist
                cx, cy = (self.x + x) / 2 + nx * bow, (self.y + y) / 2 + ny * bow
                chain = []
                for i in range(1, n + 1):
                    t = ease(i / n)
                    px = (1 - t) ** 2 * self.x + 2 * (1 - t) * t * cx + t ** 2 * x
                    py = (1 - t) ** 2 * self.y + 2 * (1 - t) * t * cy + t ** 2 * y
                    chain += ["mousemove", str(round(px)), str(round(py)), "sleep", f"{seconds / n:.4f}"]
                self.screen.xdotool(*chain)
                self.moves += 1
        self.x, self.y = x, y

    def click(self, button=1, double=False):
        if self.human:
            time.sleep(0.15)
        self.screen.xdotool("click", *(["--repeat", "2", "--delay", "90"] if double else []), button)
        if self.human:
            time.sleep(0.1)

    def drag(self, x0: float, y0: float, x1: float, y1: float):
        """Press at one point, move to the other, release: selecting text."""
        self.move(x0, y0)
        if self.human:
            time.sleep(0.15)
        self.screen.xdotool("mousedown", 1)
        self.move(x1, y1)
        self.screen.xdotool("mouseup", 1)
        if self.human:
            time.sleep(0.1)

    def wheel(self, notches: int):
        """Scroll at the pointer: positive is down."""
        button = 5 if notches > 0 else 4
        for _ in range(abs(notches)):
            self.screen.xdotool("click", button)
            if self.human:
                time.sleep(0.06)


# -- the viewer ----------------------------------------------------------------------------------

def viewer_home(home: Path, theme: str, screen: Screen) -> dict:
    """An empty home for a viewer (so nothing a previous shot left in its
    local storage carries over), and the environment that uses it."""
    home.mkdir(parents=True)
    (home / ".config" / "gtk-3.0").mkdir(parents=True)
    (home / ".config" / "gtk-3.0" / "settings.ini").write_text(
        GTK_SETTINGS.format(dark="true" if theme == "dark" else "false"), encoding="utf-8")
    (home / "run").mkdir(mode=0o700)
    return {**screen.env, **base_env(), "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"), "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_RUNTIME_DIR": str(home / "run"), "GTK_THEME": "Adwaita:dark" if theme == "dark" else "Adwaita"}


class Viewer:
    """The viewer, started fresh: on its own (a still shot), or with Claude
    Code in its terminal (``run``: the command, run beside the documents)."""

    def __init__(self, m: Matter, screen: Screen, home: Path, theme="light", run: list[str] | None = None,
                 cwd: Path | None = None, sock: Path | None = None, launcher: list[str] | None = None,
                 extra_env: dict | None = None):
        self.m, self.screen = m, screen
        self.env = {**viewer_home(home, theme, screen), **(extra_env or {})}
        # One socket path for the viewer and for us (and the `view` commands
        # we run): by default it depends on the runtime directory, and ours
        # differs from the viewer's.
        sock = sock or home.parent / f"{home.name}.sock"
        if sock.exists() and not launcher:
            sock.unlink()
        self.sock = sock
        os.environ["ADHOC_VIEW_SOCKET"] = self.env["ADHOC_VIEW_SOCKET"] = str(sock)
        # ``launcher``: a command that starts the viewer itself (`adhoc open`), on ``sock``
        cmd = launcher or [viewmod.viewer_binary(), "--matter", str(m.root), "--user", "user:screenshots",
                           "--annotate", viewmod.annotate_command(), "--test-hooks",
                           *(["--dock", "beside", "--run", *run] if run else [])]
        self.log = home / "viewer.log"
        self.proc = spawn(cmd, self.log, env=self.env, cwd=cwd)
        self.wait_ready()
        self.fill_screen()

    def command(self, cmd: dict) -> dict:
        try:
            return viewmod.command(self.m, cmd)
        except viewmod.ViewerError as e:
            raise Failed(str(e))

    def alive(self) -> bool:
        return self.proc.poll() is None

    def wait_ready(self, limit=60.0):
        t = time.time()
        while time.time() - t < limit:
            if not self.alive():
                raise Failed(f"the viewer exited ({self.proc.returncode}); see {self.log}")
            try:
                if viewmod.command(self.m, {"cmd": "state"}).get("windows"):
                    return
            except (viewmod.NotRunning, viewmod.ViewerError, OSError, ValueError):
                pass
            time.sleep(0.1)
        raise Failed(f"the viewer didn't answer within {limit:.0f} s; see {self.log}")

    def windows(self) -> list[str]:
        return self.screen.xdotool("search", "--sync", "--onlyvisible", "--name", "^adhocdocs").split()

    def fill_screen(self):
        """Every window the viewer has, at the screen's top left and its full size."""
        ids = self.windows()
        for w in ids:
            self.screen.xdotool("windowmove", w, 0, 0)
            self.screen.xdotool("windowsize", w, self.screen.w, self.screen.h)
        self.screen.xdotool("windowactivate", "--sync", ids[0], check=False)
        self.screen.xdotool("windowfocus", "--sync", ids[0], check=False)

    def repaint(self):
        """Make the web view paint everything afresh, by resizing its windows
        a pixel and back. What a screen shows after many partial repaints can
        differ by a few antialiased pixels (rounded corners) from run to
        run; a whole repaint is the same every time."""
        for w in self.windows():
            self.screen.xdotool("windowsize", w, self.screen.w, self.screen.h - 1)
            self.screen.settle(least=0.2, quiet=0.3, limit=5)
            self.screen.xdotool("windowsize", w, self.screen.w, self.screen.h)

    def box(self, target: dict) -> dict:
        """A target's box on the screen ({x, y, width, height}): ``{text,
        selector, pane, nth, scroll}``. The windows fill the screen from its
        top left, so the window's CSS pixels are the screen's."""
        q = {k: v for k, v in target.items() if k in ("text", "selector", "pane", "nth", "scroll")}
        r = self.command({"cmd": "locate", **q})
        if target.get("scroll"):
            self.screen.settle(least=0.3, quiet=0.3, limit=5)
            r = self.command({"cmd": "locate", **{**q, "scroll": False}})
        return r

    def locate(self, target: dict) -> tuple[float, float]:
        """Where on the screen a target is: a point inside its box (``at``:
        [fx, fy] as fractions of it; the middle by default)."""
        r = self.box(target)
        fx, fy = target.get("at", [0.5, 0.5])
        return r["x"] + r["width"] * fx, r["y"] + r["height"] * fy

    def close(self):
        stop(self.proc)        # its process group: the viewer, or `adhoc open` and what it started
        try:
            self.proc.wait(20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


# -- steps ---------------------------------------------------------------------------------------

STEPS_DOC = """Steps: each a table with one of these keys.

    view = ["open", "{doc:…}"]   a `view` command (its arguments)
    sh = "search … | list new …"  a shell command, on the matter, in a work directory of its own
    key = "bracketright"          keys, as xdotool names them (space-separated for several)
    type = "text"                 typed into whatever has focus
    click = TARGET                a left click (double = true: a double click)
    select = TARGET               the target's text selected, by dragging across it
    hover = TARGET                the pointer there, then a pause while a hover card comes up (wait = seconds)
    wheel = 3                     scroll at the pointer, notches (negative: up)
    pause = 0.5                   seconds (sleep = … is the same)
    prompt = "text"               (scenarios) typed at Claude's prompt, then Enter; waits for Claude's answer
    shot = "name"                 (scenarios) a still picture now

A TARGET is [x, y] on the screen, or a table that finds it:
{text = "…", selector = "css", pane = {kind = "list", title = "…"}, nth = 0, scroll = false,
at = [0.5, 0.5]} (viewer `locate`: the text, an element, or the text within elements; a pane by
its kind and part of its title, the newest that matches, or by its ID, "p2").
Other keys in a step's table go with it: { click = {...}, double = true }.
"""


class Steps:
    def __init__(self, v: Viewer, ids: dict[str, str], workdir: Path, pointer: Pointer, session=None,
                 typing_delay_ms=45, on_shot=None):
        self.v, self.ids, self.work, self.pointer = v, ids, workdir, pointer
        self.session, self.typing_delay_ms, self.on_shot = session, typing_delay_ms, on_shot
        workdir.mkdir(parents=True, exist_ok=True)

    def env(self) -> dict:
        return {**self.v.env, "ADHOC_MATTER": str(self.v.m.root), "PATH": f"{BIN}{os.pathsep}{os.environ['PATH']}"}

    def where(self, target) -> tuple[float, float]:
        if isinstance(target, list):
            return target[0], target[1]
        if isinstance(target, dict):
            return self.v.locate({k: expand(v, self.ids) if isinstance(v, str) else v for k, v in target.items()})
        raise Failed(f"not a target: {target!r}")

    def run(self, step: dict):
        kinds = [k for k in step if k in ("view", "sh", "key", "type", "click", "hover", "move", "wheel",
                                          "select", "pause", "sleep", "prompt", "shot")]
        if len(kinds) != 1:
            raise Failed(f"a step has one of the step keys: {step}")
        kind = kinds[0]
        arg = step[kind]
        if kind == "view":
            r = subprocess.run([BIN / "view", "--matter", str(self.v.m.root), *(expand(str(x), self.ids) for x in arg)],
                               cwd=self.work, env=self.env(), capture_output=True, text=True)
            if r.returncode:
                raise Failed(f"view {' '.join(arg)}: {r.stderr.strip() or r.stdout.strip()}")
        elif kind == "sh":
            r = subprocess.run(expand(arg, self.ids), shell=True, cwd=self.work, env=self.env(),
                               capture_output=True, text=True)
            if r.returncode:
                raise Failed(f"sh {arg!r}: {r.stderr.strip() or r.stdout.strip()}")
        elif kind == "key":
            self.v.screen.xdotool("key", "--clearmodifiers", *arg.split())
        elif kind == "type":
            self.v.screen.xdotool("type", "--delay", self.typing_delay_ms if self.pointer.human else 5, arg)
        elif kind == "click":
            self.pointer.move(*self.where(arg))
            self.pointer.click(double=bool(step.get("double")))
        elif kind in ("hover", "move"):
            self.pointer.move(*self.where(arg))
            if kind == "hover":
                time.sleep(float(step.get("wait", 1.0)))
        elif kind == "select":
            # Drag across the text, from just inside its left edge to just inside its right one.
            if not isinstance(arg, dict):
                raise Failed("select takes a target found by text or selector, not a point")
            b = self.v.box({k: expand(v, self.ids) if isinstance(v, str) else v for k, v in arg.items()})
            y = b["y"] + b["height"] / 2
            self.pointer.drag(b["x"] + 1, y, b["x"] + b["width"] - 1, y)
        elif kind == "wheel":
            self.pointer.wheel(int(arg))
        elif kind in ("pause", "sleep"):
            time.sleep(float(arg))
        elif kind == "prompt":
            if not self.session:
                raise Failed("prompt steps are for scenarios (scenario.py)")
            self.session.prompt(arg, self)
        elif kind == "shot":
            if not self.on_shot:
                raise Failed("shot steps are for scenarios (scenario.py)")
            self.on_shot(arg, step)
        if kind not in ("pause", "sleep", "prompt", "shot"):
            self.v.screen.settle(least=0.2, quiet=0.3, limit=5)
