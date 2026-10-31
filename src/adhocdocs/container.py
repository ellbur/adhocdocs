"""The container: one matter, one session, run with rootless Podman.

``adhoc open <matter>`` (on the host) is the connect operation of Principle
2. It starts the matter's allowlist proxy, annotation API and viewer on the
host, each on a Unix socket, and the session container, which has no
network at all (loopback only) and reaches those three through the sockets. The user's terminal is attached
straight to the unmodified ``claude`` inside, as with ``podman run -it``;
nothing here reads or relays what passes through it.

Inside the session:

====================  ==================  ====================================
/matter/store         read-only           originals, layers, provenance log
/matter/annotations   read-only           tags and notes (writes go through the API)
/matter/workspace     read-write          pages, lists, indexes
/home/adhoc/.claude   read-write          the matter's own Claude Code config and
                                          sign-in (``<matter>/claude-home``)
/run/adhoc            sockets             the proxy, viewer and annotation APIs, and
                                          the sockets of services Claude runs
/run/adhoc-audit      read-only           the audit API's socket, alone: connecting
                                          works, replacing it doesn't
====================  ==================  ====================================

``<matter>/audit`` is not mounted: the host writes the session's audit log
there (:mod:`adhocdocs.audit`).

adhocdocs never reads, copies or sends what Claude Code writes in
``claude-home``; it only provides the directory.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import proxy
from .audit import AuditLog, audit_dir
from .matter import Matter, new_event_id

IMAGE = "localhost/adhocdocs:latest"
REPO = Path(__file__).resolve().parents[2]
CONTAINERFILE = REPO / "container" / "Containerfile"
IMAGE_UID = 1000
PROXY_PORT = 3128
SESSION_ENV = {
    "ADHOC_MATTER": "/matter",
    "ADHOC_VIEW_SOCKET": "/run/adhoc/viewer.sock",
    "ADHOC_ANN_SOCKET": "/run/adhoc/annotations.sock",
    "ADHOC_AUDIT_SOCKET": "/run/adhoc-audit/audit.sock",
    "CLAUDE_CONFIG_DIR": "/home/adhoc/.claude",
}
# Passed through by name, never stored: a user's own API key, set the normal way.
PASS_THROUGH = ("ANTHROPIC_API_KEY",)

ACCOUNT_NOTICE = """\
Signing in: Claude Code will show a link. Open it on this computer, sign in,
and paste the code back. This matter keeps its own sign-in.

About your account and confidential documents (our reading, not legal advice):
- Free, Pro and Max accounts fall under Anthropic's Consumer Terms. Chats
  may be used to train models unless you opt out in your privacy settings.
- Team, Enterprise and API accounts fall under the Commercial Terms: no
  training on your content.
Which kind to use is your (or your firm's) decision.
"""


class ContainerError(Exception):
    pass


def podman(*args, capture=True, check=True) -> str:
    if not shutil.which("podman"):
        raise ContainerError("podman is not installed")
    r = subprocess.run(["podman", *args], capture_output=capture, text=True)
    if check and r.returncode:
        raise ContainerError(f"podman {' '.join(args[:3])}…: {(r.stderr or '').strip()}")
    return (r.stdout or "").strip()


def slug(m: Matter) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", m.root.name.lower()).strip("-")[:30] or "matter"
    return f"adhoc-{name}-{hashlib.sha256(str(m.root).encode()).hexdigest()[:6]}"


def run_dir(m: Matter) -> Path:
    """Where this matter's sockets live on the host, mounted at /run/adhoc."""
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/adhocdocs-{os.getuid()}")
    return base / "adhocdocs" / hashlib.sha256(str(m.root).encode()).hexdigest()[:16]


# -- the image -----------------------------------------------------------------------------

def image_id() -> str | None:
    r = subprocess.run(["podman", "image", "inspect", "--format", "{{.Id}}", IMAGE], capture_output=True, text=True)
    return r.stdout.strip() or None


def image_exists() -> bool:
    return subprocess.run(["podman", "image", "exists", IMAGE]).returncode == 0


def build_image(no_cache=False):
    """Build the image on this machine. Claude Code comes from Anthropic's
    official installer during the build."""
    args = ["build", "-f", str(CONTAINERFILE), "-t", IMAGE]
    if no_cache:
        args.append("--no-cache")
    podman(*args, str(REPO), capture=False)


# -- the session ----------------------------------------------------------------------------

def session_args(m: Matter, name: str, sockets: Path, command=()) -> list[str]:
    """The `podman run` arguments for a session. Pure, so it can be tested."""
    root = m.root
    env = {**SESSION_ENV, "HTTPS_PROXY": f"http://127.0.0.1:{PROXY_PORT}", "https_proxy": f"http://127.0.0.1:{PROXY_PORT}",
           "ADHOC_ALLOWLIST": ",".join(proxy.CLAUDE_CODE_HOSTS), "ADHOC_MATTER_NAME": root.name}
    tty = ["-it"] if sys.stdin.isatty() else ["-i"]
    args = ["run", "--rm", *tty, "--name", name, "--hostname", "adhoc",
            "--network", "none",          # loopback only: no route out, no DNS
            f"--userns=keep-id:uid={IMAGE_UID},gid={IMAGE_UID}",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=4096",
            "-v", f"{root / 'store'}:/matter/store:ro",
            "-v", f"{root / 'annotations'}:/matter/annotations:ro",
            "-v", f"{root / 'workspace'}:/matter/workspace",
            "-v", f"{root / 'claude-home'}:/home/adhoc/.claude",
            "-v", f"{sockets}:/run/adhoc",
            # Read-only, so nothing in the session can put its own listener in the audit API's place.
            "-v", f"{sockets / 'audit'}:/run/adhoc-audit:ro"]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    for k in PASS_THROUGH:
        if os.environ.get(k):
            args += ["-e", k]          # podman copies the value from our environment
    return args + [IMAGE, *(["--", *command] if command else [])]


def wait_for(path: Path, seconds=10) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def _service(args, log: Path) -> subprocess.Popen:
    out = open(log, "a")
    return subprocess.Popen(args, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True)


def open_session(m: Matter, user: str, viewer=True, command=()) -> int:
    if not image_exists():
        print("Building the session image (first run; this takes a few minutes)…", file=sys.stderr)
        build_image()
    (m.root / "claude-home").mkdir(mode=0o700, exist_ok=True)
    (m.root / "run").mkdir(exist_ok=True)
    first = not any((m.root / "claude-home").iterdir())
    base = slug(m)
    n = 1
    while subprocess.run(["podman", "container", "exists", f"{base}-{n}"]).returncode == 0:
        n += 1
    name = f"{base}-{n}"
    sockets = run_dir(m) / str(n)
    sockets.mkdir(parents=True, exist_ok=True, mode=0o700)
    (sockets / "audit").mkdir(exist_ok=True, mode=0o700)
    py = [sys.executable, "-m"]
    session = "ses_" + new_event_id().removeprefix("ev_")
    audit = AuditLog(audit_dir(m))

    started = []
    code = None
    try:
        # Everything the session can reach, on sockets in one directory.
        started.append(_service(py + ["adhocdocs.proxy", "--unix", str(sockets / "proxy.sock"),
                                      "--audit", str(audit.root), "--session", session], m.root / "run" / "proxy.log"))
        started.append(_service(py + ["adhocdocs.cli", "adhoc", "--matter", str(m.root), "ann-api",
                                      "--socket", str(sockets / "annotations.sock")], m.root / "run" / "ann-api.log"))
        started.append(_service(py + ["adhocdocs.cli", "adhoc", "--matter", str(m.root), "audit-api",
                                      "--socket", str(sockets / "audit" / "audit.sock"), "--session", session],
                                m.root / "run" / "audit-api.log"))
        if viewer:
            from .view import viewer_binary
            started.append(_service([viewer_binary(), "--matter", str(m.root), "--user", user,
                                     "--socket", str(sockets / "viewer.sock"),
                                     "--annotate", f"{sys.executable} -m adhocdocs.cli adhoc"], m.root / "run" / "viewer.log"))
        for sock in ("proxy.sock", "annotations.sock", "audit/audit.sock"):
            if not wait_for(sockets / sock):
                raise ContainerError(f"{sock} did not start; see {m.root / 'run'}")
        if first:
            print(ACCOUNT_NOTICE, file=sys.stderr)
        args = session_args(m, name, sockets, command)
        audit.append(session=session, source="launcher", kind="session-start", user=user, container=name,
                     image=IMAGE, image_id=image_id(), command=list(command) or None, viewer=viewer,
                     allowlist=list(proxy.CLAUDE_CODE_HOSTS), podman_args=args)
        # The terminal goes straight to the container: no relay in between.
        code = subprocess.call(["podman", *args])
        return code
    finally:
        audit.append(session=session, source="launcher", kind="session-end", exit=code)
        for p in started:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        shutil.rmtree(sockets, ignore_errors=True)


# -- inside the session: what Claude can observe -----------------------------------------------

def _mounts() -> dict:
    out = {}
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            f = line.split()
            point, opts = f[4], f[5]
            if point.startswith("/matter") or point in ("/home/adhoc/.claude", "/run/adhoc", "/run/adhoc-audit"):
                out[point] = "read-only" if "ro" in opts.split(",") else "read-write"
    except OSError:
        pass
    return out


def _socket_ok(path: str) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def _direct_internet() -> str:
    """Try to reach the outside without the proxy; it should fail."""
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=2).close()
        return "reachable"
    except OSError as e:
        return f"blocked ({e.strerror or e})"


def status() -> dict:
    """Matter, mounts, network and services as seen from inside a session.
    Nothing about credentials."""
    inside = os.environ.get("ADHOC_MATTER") == "/matter" and Path("/matter/store").is_dir()
    if not inside:
        return {"in_session": False, "note": "not inside an adhocdocs session container"}
    return {
        "in_session": True,
        "matter": os.environ.get("ADHOC_MATTER_NAME"),
        "mounts": _mounts(),
        "network": {"proxy": os.environ.get("HTTPS_PROXY"),
                    "allowlist": [h for h in os.environ.get("ADHOC_ALLOWLIST", "").split(",") if h],
                    "direct_internet": _direct_internet()},
        "viewer": "running" if _socket_ok(os.environ.get("ADHOC_VIEW_SOCKET", "")) else "not running",
        "annotation_api": "running" if _socket_ok(os.environ.get("ADHOC_ANN_SOCKET", "")) else "not running",
        "audit_api": "running" if _socket_ok(os.environ.get("ADHOC_AUDIT_SOCKET", "")) else "not running",
    }
