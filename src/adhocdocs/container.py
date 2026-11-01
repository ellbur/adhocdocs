"""The container: one matter, one session, run with rootless Podman.

``adhoc open <matter>`` (on the host) is the connect operation of Principle
2. It starts the matter's allowlist proxy, annotation API and viewer on the
host, each on a Unix socket, and the session container, which has no
network at all (loopback only) and reaches those three through the sockets. The user's terminal is attached
straight to the unmodified ``claude`` inside, as with ``podman run -it``;
nothing here reads or relays what passes through it.

Inside the session:

====================  ==================  ====================================
/matter/store         read-only           originals, layers, provenance logs
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
there (:mod:`adhocdocs.audit`). Nor is ``<matter>/sync``, this
installation's keys and sync state (:mod:`adhocdocs.origin`, SYNC.md).

adhocdocs never reads, copies or sends what Claude Code writes in
``claude-home``; it only provides the directory.
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import endpoint, proxy
from .audit import AuditLog, audit_dir
from .matter import Matter, new_event_id
from .portable import runtime_dir, spawn, stop

IMAGE = "localhost/adhocdocs:latest"
REPO = Path(__file__).resolve().parents[2]
CONTAINERFILE = REPO / "container" / "Containerfile"
IMAGE_UID = 1000
PROXY_PORT = 3128
BRIDGE_PORT = 7100
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
    r = subprocess.run(["podman", *args], capture_output=capture, text=True, encoding="utf-8")
    if check and r.returncode:
        raise ContainerError(f"podman {' '.join(args[:3])}…: {(r.stderr or '').strip()}")
    return (r.stdout or "").strip()


def slug(m: Matter) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", m.root.name.lower()).strip("-")[:30] or "matter"
    return f"adhoc-{name}-{hashlib.sha256(str(m.root).encode()).hexdigest()[:6]}"


def run_dir(m: Matter) -> Path:
    """Where this matter's sockets live on the host, mounted at /run/adhoc."""
    return runtime_dir() / "adhocdocs" / hashlib.sha256(str(m.root).encode()).hexdigest()[:16]


# -- the image -----------------------------------------------------------------------------

def image_id() -> str | None:
    r = subprocess.run(["podman", "image", "inspect", "--format", "{{.Id}}", IMAGE], capture_output=True, text=True, encoding="utf-8")
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

def _bind(src: Path, dst: str, ro=False, bridged=False) -> list[str]:
    if bridged:
        # --mount, since a Windows path has a colon of its own; podman maps it into its VM
        return ["--mount", f"type=bind,source={src},target={dst}" + (",ro" if ro else "")]
    return ["-v", f"{src}:{dst}" + (":ro" if ro else "")]


def session_args(m: Matter, name: str, sockets: Path, command=(), volumes: tuple[str, str] | None = None) -> list[str]:
    """The `podman run` arguments for a session. Pure, so it can be tested.
    With ``volumes`` (on Windows and macOS: :mod:`adhocdocs.bridge`), the
    socket directories are those two Podman volumes, shared with the bridge,
    instead of host directories."""
    root = m.root
    bridged = volumes is not None
    env = {**SESSION_ENV, "HTTPS_PROXY": f"http://127.0.0.1:{PROXY_PORT}", "https_proxy": f"http://127.0.0.1:{PROXY_PORT}",
           "ADHOC_ALLOWLIST": ",".join(proxy.CLAUDE_CODE_HOSTS), "ADHOC_MATTER_NAME": root.name}
    tty = ["-it"] if sys.stdin.isatty() else ["-i"]
    args = ["run", "--rm", *tty, "--name", name, "--hostname", "adhoc",
            "--network", "none",          # loopback only: no route out, no DNS
            f"--userns=keep-id:uid={IMAGE_UID},gid={IMAGE_UID}",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=4096",
            *_bind(root / "store", "/matter/store", ro=True, bridged=bridged),
            *_bind(root / "annotations", "/matter/annotations", ro=True, bridged=bridged),
            *_bind(root / "workspace", "/matter/workspace", bridged=bridged),
            *_bind(root / "claude-home", "/home/adhoc/.claude", bridged=bridged)]
    if bridged:
        args += ["-v", f"{volumes[0]}:/run/adhoc", "-v", f"{volumes[1]}:/run/adhoc-audit:ro"]
    else:
        args += ["-v", f"{sockets}:/run/adhoc"]
        # Read-only, so nothing in the session can put its own listener in the audit API's place.
        args += ["-v", f"{sockets / 'audit'}:/run/adhoc-audit:ro"]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    for k in PASS_THROUGH:
        if os.environ.get(k):
            args += ["-e", k]          # podman copies the value from our environment
    return args + [IMAGE, *(["--", *command] if command else [])]


# The sockets the session uses, by name: where the bridge listens in the VM,
# and where the host service is, relative to the session's run directory.
BRIDGED = {
    "proxy": ("/run/adhoc/proxy.sock", "proxy.sock"),
    "annotations": ("/run/adhoc/annotations.sock", "annotations.sock"),
    "viewer": ("/run/adhoc/viewer.sock", "viewer.sock"),
    "audit": ("/run/adhoc-audit/audit.sock", "audit/audit.sock"),
}


def bridge_args(name: str, volumes: tuple[str, str]) -> list[str]:
    """The `podman run` arguments for a session's bridge. Pure, so it can be
    tested. Its token comes from the launcher's environment, by name."""
    args = ["run", "-d", "--rm", "--name", f"{name}-bridge", "--hostname", "adhoc-bridge",
            f"--userns=keep-id:uid={IMAGE_UID},gid={IMAGE_UID}",     # the session's user, so each can use the other's sockets
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "-p", f"127.0.0.1::{BRIDGE_PORT}",                       # on the host's loopback only
            "-v", f"{volumes[0]}:/run/adhoc", "-v", f"{volumes[1]}:/run/adhoc-audit",
            "-e", "ADHOC_BRIDGE_TOKEN", "--entrypoint", "python", IMAGE,
            "-m", "adhocdocs.bridge", "serve", "--port", str(BRIDGE_PORT)]
    for svc, (inside, _) in BRIDGED.items():
        args += ["--socket", f"{svc}={inside}"]
    return args


def _start_bridge(name: str, sockets: Path, volumes: tuple[str, str], started: list, log: Path) -> list[str]:
    """Start the bridge and the link (Windows and macOS). Returns the bridge's arguments."""
    token = endpoint.new_token()
    env = {**os.environ, "ADHOC_BRIDGE_TOKEN": token}
    # A crashed launcher's bridge would hold the name; its session is gone (we checked).
    podman("rm", "-f", "-t", "0", f"{name}-bridge", check=False)
    for v in volumes:
        podman("volume", "create", "--ignore", v)
    args = bridge_args(name, volumes)
    r = subprocess.run(["podman", *args], capture_output=True, text=True, encoding="utf-8", env=env)
    if r.returncode:
        raise ContainerError(f"the bridge did not start: {r.stderr.strip()}")
    published = podman("port", f"{name}-bridge", str(BRIDGE_PORT)).splitlines()[0].strip()
    host, _, port = published.rpartition(":")
    address = f"{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    # The viewer reaches Claude's services (view serve) through the bridge too.
    endpoint.write_endpoint(sockets / "svc.endpoint", address, token)
    link = [sys.executable, "-m", "adhocdocs.bridge", "link", address, "--ready", str(sockets / "link.ready")]
    for svc, (_, outside) in BRIDGED.items():
        link += ["--service", f"{svc}={sockets / outside}"]
    started.append(spawn(link, log, env=env))
    if not wait_for(sockets / "link.ready", 30):
        raise ContainerError(f"the bridge did not answer; see `podman logs {name}-bridge`")
    return args


def wait_for(path: Path, seconds=10) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


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
    shutil.rmtree(sockets, ignore_errors=True)       # a crashed launcher's leftovers would look like live services
    sockets.mkdir(parents=True, exist_ok=True, mode=0o700)
    (sockets / "audit").mkdir(exist_ok=True, mode=0o700)
    py = [sys.executable, "-m"]
    # Windows and macOS: the session is in Podman's VM, reached through a bridge (adhocdocs.bridge).
    bridged = endpoint.tcp_mode()
    volumes = (f"{name}-run", f"{name}-audit") if bridged else None
    bridge = None
    session = "ses_" + new_event_id().removeprefix("ev_")
    audit = AuditLog(audit_dir(m))

    started = []
    code = None
    try:
        # Everything the session can reach, on sockets in one directory.
        started.append(spawn(py + ["adhocdocs.proxy", "--unix", str(sockets / "proxy.sock"),
                                      "--audit", str(audit.root), "--session", session], m.root / "run" / "proxy.log"))
        started.append(spawn(py + ["adhocdocs.cli", "adhoc", "--matter", str(m.root), "ann-api",
                                      "--socket", str(sockets / "annotations.sock")], m.root / "run" / "ann-api.log"))
        started.append(spawn(py + ["adhocdocs.cli", "adhoc", "--matter", str(m.root), "audit-api",
                                      "--socket", str(sockets / "audit" / "audit.sock"), "--session", session],
                                m.root / "run" / "audit-api.log"))
        if viewer:
            from .view import annotate_command, viewer_binary
            started.append(spawn([viewer_binary(), "--matter", str(m.root), "--user", user,
                                  "--socket", str(sockets / "viewer.sock"),
                                  "--annotate", annotate_command()], m.root / "run" / "viewer.log"))
        for sock in ("proxy.sock", "annotations.sock", "audit/audit.sock"):
            if not wait_for(sockets / sock):
                raise ContainerError(f"{sock} did not start; see {m.root / 'run'}")
        if bridged:
            bridge = _start_bridge(name, sockets, volumes, started, m.root / "run" / "link.log")
        if first:
            print(ACCOUNT_NOTICE, file=sys.stderr)
        args = session_args(m, name, sockets, command, volumes)
        audit.append(session=session, source="launcher", kind="session-start", user=user, container=name,
                     image=IMAGE, image_id=image_id(), command=list(command) or None, viewer=viewer,
                     allowlist=list(proxy.CLAUDE_CODE_HOSTS), podman_args=args,
                     **({"bridge_args": bridge} if bridged else {}))
        # The terminal goes straight to the container: no relay in between.
        code = subprocess.call(["podman", *args])
        return code
    finally:
        audit.append(session=session, source="launcher", kind="session-end", exit=code)
        for p in started:
            stop(p)
        if bridged:
            podman("rm", "-f", "-t", "1", f"{name}-bridge", check=False)
            podman("volume", "rm", "-f", *volumes, check=False)
        shutil.rmtree(sockets, ignore_errors=True)


# -- inside the session: what Claude can observe -----------------------------------------------

def _mounts() -> dict:
    out = {}
    try:
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            f = line.split()
            point, opts = f[4], f[5]
            if point.startswith("/matter") or point in ("/home/adhoc/.claude", "/run/adhoc", "/run/adhoc-audit"):
                out[point] = "read-only" if "ro" in opts.split(",") else "read-write"
    except OSError:
        pass
    return out


def _socket_ok(path: str) -> bool:
    try:
        endpoint.connect(path, 1).close()
        return True
    except (OSError, ValueError):
        return False


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
