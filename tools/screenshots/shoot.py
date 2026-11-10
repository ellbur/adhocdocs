"""Take the screenshots in a shot list, on a virtual screen, the same way
every time. See docs/SCREENSHOTS.md.

    uv run python tools/screenshots/shoot.py [--out screenshots] [--only NAME …]

It makes the sample matter from corpus/ (or reuses --matter), then for each
shot starts the viewer afresh on Xvfb, with a fixed screen size, theme,
time zone, locale and an empty home directory, runs the shot's steps, waits
until the screen stops changing, and saves a PNG. It writes index.html (a
gallery) and manifest.json (each shot's hash) beside them.

Nothing here talks to Claude: steps drive the viewer with `view` commands,
shell commands on the matter, and keys and clicks (xdotool).
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from adhocdocs import view as viewmod
from adhocdocs.matter import Matter

REPO = Path(__file__).resolve().parents[2]
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
    log(f"  ingested in {time.time() - t:.0f} s")
    return Matter(path)


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
            raise SystemExit(f"{{doc:{want}}} matches {len(hits)} documents")
        return "doc:" + hits[0]
    return re.sub(r"\{doc:([^}]+)\}", one, s)


# -- the screen ----------------------------------------------------------------------------------

class Screen:
    def __init__(self, display: str, w: int, h: int):
        self.display, self.w, self.h = display, w, h
        self.env = {**os.environ, "DISPLAY": display}
        self.proc = subprocess.Popen(["Xvfb", display, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp",
                                      "-dpi", "96", "+extension", "RANDR"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if subprocess.run(["xdpyinfo"], env=self.env, capture_output=True).returncode == 0:
                return
            time.sleep(0.1)
        raise SystemExit(f"Xvfb didn't start on {display}")

    def xdotool(self, *args, check=True) -> str:
        r = subprocess.run(["xdotool", *map(str, args)], env=self.env, capture_output=True, text=True)
        if check and r.returncode:
            raise SystemExit(f"xdotool {' '.join(map(str, args))}: {r.stderr.strip()}")
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

    def close(self):
        self.proc.terminate()
        self.proc.wait()


# -- the viewer ----------------------------------------------------------------------------------

class Viewer:
    """One viewer, started fresh for a shot, with an empty home of its own
    (so nothing a previous shot left in its local storage carries over)."""

    def __init__(self, m: Matter, screen: Screen, home: Path, theme: str):
        self.m, self.screen = m, screen
        home.mkdir(parents=True)
        (home / ".config" / "gtk-3.0").mkdir(parents=True)
        (home / ".config" / "gtk-3.0" / "settings.ini").write_text(
            GTK_SETTINGS.format(dark="true" if theme == "dark" else "false"))
        self.env = {**screen.env, **base_env(), "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                    "XDG_DATA_HOME": str(home / ".local/share"), "XDG_CACHE_HOME": str(home / ".cache"),
                    "XDG_RUNTIME_DIR": str(home / "run"), "GTK_THEME": "Adwaita:dark" if theme == "dark" else "Adwaita"}
        (home / "run").mkdir(mode=0o700)
        # One socket path for the viewer and for us (and the `view` commands
        # we run): by default it depends on the runtime directory, and ours
        # differs from the viewer's.
        sock = home.parent.parent / "viewer.sock"
        if sock.exists():
            sock.unlink()
        os.environ["ADHOC_VIEW_SOCKET"] = self.env["ADHOC_VIEW_SOCKET"] = str(sock)
        cmd = [viewmod.viewer_binary(), "--matter", str(m.root), "--user", "user:screenshots",
               "--annotate", viewmod.annotate_command()]
        self.log = open(home / "viewer.log", "w")
        self.proc = subprocess.Popen(cmd, env=self.env, stdout=self.log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        self.state = self.wait_ready()
        self.fill_screen()

    def wait_ready(self, limit=30.0):
        t = time.time()
        while time.time() - t < limit:
            if self.proc.poll() is not None:
                raise SystemExit(f"the viewer exited ({self.proc.returncode}); see {self.log.name}")
            try:
                s = viewmod.command(self.m, {"cmd": "state"})
                if s.get("windows"):
                    return s
            except (viewmod.NotRunning, OSError, ValueError):
                pass
            time.sleep(0.1)
        raise SystemExit(f"the viewer didn't answer within {limit} s; see {self.log.name}")

    def windows(self) -> list[str]:
        return self.screen.xdotool("search", "--sync", "--onlyvisible", "--pid", self.proc.pid).split()

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

    def close(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        self.log.close()


def base_env() -> dict:
    return {"TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NO_AT_BRIDGE": "1",
            "FONTCONFIG_FILE": str(Path(__file__).with_name("fonts.conf")),
            "WEBKIT_DISABLE_DMABUF_RENDERER": "1", "LIBGL_ALWAYS_SOFTWARE": "1"}


# -- steps ---------------------------------------------------------------------------------------

def run_step(step: dict, v: Viewer, ids: dict[str, str], workdir: Path):
    """One step of a shot. Each is a table with one of these keys:

    view = ["open", "{doc:…}"]   a `view` command (its arguments)
    sh = "search … | list new …"  a shell command, on the matter, in the shot's work directory
    key = "bracketright"          keys, as xdotool names them (space-separated for several)
    type = "text"                 typed into whatever has focus
    click = [x, y]                a left click at screen coordinates
    move = [x, y]                 the pointer there (for hover cards)
    sleep = 0.5                   seconds
    """
    env = {**v.env, "ADHOC_MATTER": str(v.m.root), "PATH": f"{BIN}{os.pathsep}{os.environ['PATH']}"}
    (kind, arg), = step.items()
    if kind == "view":
        r = subprocess.run([BIN / "view", "--matter", str(v.m.root), *(expand(str(x), ids) for x in arg)],
                           cwd=workdir, env=env, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"view {' '.join(arg)}: {r.stderr.strip() or r.stdout.strip()}")
    elif kind == "sh":
        r = subprocess.run(expand(arg, ids), shell=True, cwd=workdir, env=env, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"sh {arg!r}: {r.stderr.strip() or r.stdout.strip()}")
    elif kind == "key":
        v.screen.xdotool("key", "--clearmodifiers", *arg.split())
    elif kind == "type":
        v.screen.xdotool("type", "--delay", 20, arg)
    elif kind == "click":
        v.screen.xdotool("mousemove", "--sync", *arg)
        v.screen.xdotool("click", 1)
    elif kind == "move":
        v.screen.xdotool("mousemove", "--sync", *arg)
    elif kind == "sleep":
        time.sleep(float(arg))
    else:
        raise SystemExit(f"unknown step {kind!r}")


# -- output --------------------------------------------------------------------------------------

def gallery(out: Path, shots: list[dict]):
    rows = "\n".join(
        f'<figure id="{html.escape(s["name"])}"><a href="{html.escape(s["file"])}">'
        f'<img src="{html.escape(s["file"])}" loading="lazy" alt="{html.escape(s["caption"])}"></a>'
        f'<figcaption><b>{html.escape(s["name"])}</b> {html.escape(s["caption"])}'
        f'{"" if s["settled"] else " <i>(still changing when taken)</i>"}</figcaption></figure>'
        for s in shots)
    out.joinpath("index.html").write_text(f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>adhocdocs screenshots</title>
<style>
body {{ font: 15px/1.4 system-ui, sans-serif; margin: 24px auto; max-width: 1200px; padding: 0 16px; }}
figure {{ margin: 0 0 32px; }} img {{ max-width: 100%; border: 1px solid #ccc; }}
figcaption {{ margin-top: 6px; }}
</style>
<h1>adhocdocs screenshots</h1>
{rows}
""", encoding="utf-8")


def git_rev() -> str:
    r = subprocess.run(["git", "-C", str(REPO), "describe", "--always", "--dirty"], capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


# -- main ----------------------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--shots", default=str(Path(__file__).with_name("shots.toml")))
    p.add_argument("--out", default="screenshots")
    p.add_argument("--matter", help="the sample matter's directory, kept to reuse (default: a temporary one); "
                                    "its name heads the overview")
    p.add_argument("--only", nargs="*", help="only these shots")
    p.add_argument("--display", default=":97")
    a = p.parse_args()

    spec = tomllib.loads(Path(a.shots).read_text(encoding="utf-8"))
    w, h = spec.get("screen", {}).get("size", [1600, 1000])
    shots = [s for s in spec["shot"] if not a.only or s["name"] in a.only]
    if a.only and len(shots) != len(a.only):
        raise SystemExit(f"no such shots: {sorted(set(a.only) - {s['name'] for s in shots})}")

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="adhoc-shots-"))
    env = {**os.environ, **base_env()}
    # The overview is headed with the matter's directory name.
    name = spec.get("matter", {}).get("name", "sample-matter")
    m = make_matter(Path(a.matter).resolve() if a.matter else tmp / name, env)
    ids = doc_ids(m)

    screen = Screen(a.display, w, h)
    done, failed = [], []
    try:
        for i, shot in enumerate(shots):
            name = shot["name"]
            log(f"[{i + 1}/{len(shots)}] {name}")
            work = tmp / "work" / name
            work.mkdir(parents=True)
            v = None
            try:
                v = Viewer(m, screen, tmp / "home" / name, shot.get("theme", "light"))
                screen.xdotool("mousemove", "--sync", w - 1, h - 1)     # out of the way: hovering changes things
                for step in shot.get("steps", []):
                    run_step(step, v, ids, work)
                    if "sleep" not in step:
                        screen.settle(least=0.2, quiet=0.3, limit=5)
                screen.settle(least=shot.get("settle", 0.6))
                v.repaint()
                rgb, settled = screen.settle()
                if not settled:
                    log("  the screen was still changing; taken anyway")
                f = out / f"{name}.png"
                screen.save(rgb, f)
                done.append({"name": name, "caption": shot.get("caption", ""), "file": f.name, "settled": settled,
                             "sha256": hashlib.sha256(rgb).hexdigest()})
            except SystemExit as e:
                log(f"  failed: {e}")
                failed.append(name)
                viewer_log = tmp / "home" / name / "viewer.log"
                if viewer_log.exists():
                    shutil.copyfile(viewer_log, out / f"{name}.viewer.log")
            finally:
                if v:
                    v.close()
    finally:
        screen.close()

    gallery(out, done)
    out.joinpath("manifest.json").write_text(json.dumps(
        {"commit": git_rev(), "screen": [w, h], "shots": done, "failed": failed}, indent=1) + "\n")
    log(f"{len(done)} shots in {out}" + (f"; failed: {', '.join(failed)}" if failed else ""))
    if not a.matter:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
