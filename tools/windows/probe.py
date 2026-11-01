"""Check, on a Windows (or macOS) machine, what adhocdocs assumes about
Podman there. Run it from the repository, with Podman's machine started:

    uv run python tools/windows/probe.py            # or: ... --image localhost/adhocdocs:latest

Each check prints ok / FAIL / note and the facts it found; a JSON report
goes to probe-report.json. Nothing here touches a matter. See WINDOWS.md,
"What the probe answers".
"""

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from adhocdocs import portable  # noqa: E402

RESULTS = []


def podman(*args, check=False, timeout=300, **kw):
    r = subprocess.run(["podman", *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, **kw)
    if check and r.returncode:
        raise RuntimeError(f"podman {' '.join(args[:4])}…: {r.stderr.strip()}")
    return r


def record(name, ok, **facts):
    mark = {True: "ok   ", False: "FAIL ", None: "note "}[ok]
    print(f"{mark} {name}" + "".join(f"\n        {k}: {v}" for k, v in facts.items()), flush=True)
    RESULTS.append({"check": name, "ok": ok, **facts})


def check(fn):
    def run(*a):
        try:
            fn(*a)
        except Exception as e:  # report, keep going
            record(fn.__name__.removeprefix("probe_").replace("_", " "), False, error=f"{type(e).__name__}: {e}")
    return run


@check
def probe_machine(image):
    r = podman("machine", "inspect")
    info = json.loads(r.stdout or "[]")
    m = info[0] if info else {}
    v = podman("version", "--format", "json")
    record("machine", bool(m), state=m.get("State"), vm_type=m.get("VMType"),
           rootful=m.get("Rootful"), user_mode_networking=m.get("UserModeNetworking"),
           podman=(json.loads(v.stdout or "{}").get("Client") or {}).get("Version"),
           host=f"{platform.system()} {platform.release()} {platform.machine()}")


@check
def probe_bind_mounts(image, tmp):
    src = tmp / "bind"
    (src / "ro").mkdir(parents=True)
    (src / "rw").mkdir()
    (src / "ro" / "hello.txt").write_text("from the host\n", encoding="utf-8", newline="\n")
    script = ("cat /x/ro/hello.txt && (touch /x/ro/nope 2>/dev/null && echo RO-WRITABLE || echo ro-refused) "
              "&& echo from-container > /x/rw/out.txt && id -u && stat -c %u /x/ro/hello.txt")
    r = podman("run", "--rm", "--userns=keep-id:uid=1000,gid=1000",
               "--mount", f"type=bind,source={src / 'ro'},target=/x/ro,ro",
               "--mount", f"type=bind,source={src / 'rw'},target=/x/rw",
               "--entrypoint", "sh", image, "-c", script)
    out = r.stdout.split()
    back = (src / "rw" / "out.txt").read_text(encoding="utf-8").strip() if (src / "rw" / "out.txt").exists() else None
    ok = r.returncode == 0 and "ro-refused" in out and back == "from-container"
    record("bind mounts (--mount, Windows paths, ro, keep-id)", ok, stdout=r.stdout.strip(),
           stderr=r.stderr.strip()[-300:], written_back=back)


@check
def probe_v_with_drive_letter(image, tmp):
    src = tmp / "v"
    src.mkdir()
    (src / "a").write_text("x", encoding="utf-8")
    r = podman("run", "--rm", "-v", f"{src}:/x:ro", "--entrypoint", "ls", image, "/x")
    record("-v with a drive letter (C:\\…:/x:ro)", None if r.returncode else True, works=r.returncode == 0,
           stderr=r.stderr.strip()[-200:])


@check
def probe_volume_sockets(image):
    """Two containers share a named volume; one listens on a Unix socket, the
    other (no network) connects: the session ↔ bridge path."""
    vol = f"adhoc-probe-{uuid.uuid4().hex[:8]}"
    name = vol + "-srv"
    try:
        podman("volume", "create", vol, check=True)
        srv = ("import socket,os\n"
               "p='/run/adhoc/t.sock'\n"
               "s=socket.socket(socket.AF_UNIX); s.bind(p); os.chmod(p,0o660); s.listen()\n"
               "c,_=s.accept(); c.sendall(b'hello-through-volume'); c.close()\n")
        podman("run", "-d", "--rm", "--name", name, "--userns=keep-id:uid=1000,gid=1000", "-v", f"{vol}:/run/adhoc",
               "--entrypoint", "python", image, "-c", srv, check=True)
        time.sleep(2)
        cli = ("import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/adhoc/t.sock'); print(s.recv(100).decode())")
        r = podman("run", "--rm", "--network", "none", "--userns=keep-id:uid=1000,gid=1000",
                   "-v", f"{vol}:/run/adhoc:ro", "--entrypoint", "python", image, "-c", cli)
        record("Unix socket in a shared volume, client read-only and without network",
               "hello-through-volume" in r.stdout, stdout=r.stdout.strip(), stderr=r.stderr.strip()[-300:])
    finally:
        podman("rm", "-f", name)
        podman("volume", "rm", "-f", vol)


@check
def probe_published_port(image):
    """The bridge's port, published on the host's loopback: the link dials it."""
    name = f"adhoc-probe-{uuid.uuid4().hex[:8]}"
    srv = ("import socket\n"
           "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('0.0.0.0',7100)); s.listen()\n"
           "while True:\n c,_=s.accept(); d=c.recv(100); c.sendall(b'echo:'+d); c.close()\n")
    try:
        podman("run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::7100", "--entrypoint", "python", image, "-c", srv,
               check=True)
        port_line = podman("port", name, "7100", check=True).stdout.strip().splitlines()[0]
        host, _, port = port_line.rpartition(":")
        got, start = None, time.time()
        while time.time() - start < 20 and not got:
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=3) as s:
                    s.sendall(b"ping")
                    got = s.recv(100)
            except OSError:
                time.sleep(0.5)
        # Is it reachable from other interfaces? It shouldn't be.
        lan = None
        try:
            ip = socket.gethostbyname(socket.gethostname())
            if not ip.startswith("127."):
                with socket.create_connection((ip, int(port)), timeout=2):
                    lan = f"REACHABLE on {ip}"
        except OSError:
            lan = "not reachable from the LAN address"
        record("published port on 127.0.0.1 (host dials in)", got == b"echo:ping", podman_port=port_line,
               reply=got, lan=lan, seconds_until_answer=round(time.time() - start, 1))
    finally:
        podman("rm", "-f", name)


@check
def probe_host_from_container(image):
    """Not needed by the design (the host dials in), but worth knowing."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    srv.settimeout(15)
    port = srv.getsockname()[1]
    cli = f"import socket; s=socket.create_connection(('host.containers.internal',{port}),timeout=5); print('connected')"
    proc = subprocess.Popen(["podman", "run", "--rm", "--entrypoint", "python", image, "-c", cli],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        srv.accept()[0].close()
        reached = True
    except OSError:
        reached = False
    out, err = proc.communicate(timeout=60)
    record("container → host 127.0.0.1 via host.containers.internal", None, reached=reached,
           stdout=out.strip(), stderr=err.strip()[-200:])


@check
def probe_locks_across_vm(image, tmp):
    """Does a lock the host holds stop the container's flock, and back? If
    not, host and session must not both update the same index."""
    d = tmp / "locks"
    d.mkdir()
    lock = d / "lock"
    lock.write_text("", encoding="utf-8")
    script = ("import fcntl,sys\n"
              "f=open('/x/lock','w')\n"
              "try:\n fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB); print('container-got-lock')\n"
              "except OSError as e: print('container-blocked', e)\n")
    with open(lock, "w", encoding="utf-8") as f, portable.locked(f):
        r = podman("run", "--rm", "--mount", f"type=bind,source={d},target=/x", "--entrypoint", "python", image,
                   "-c", script)
    record("host lock seen by the container's flock", None, result=r.stdout.strip(), stderr=r.stderr.strip()[-200:])


@check
def probe_sqlite_and_speed(image, tmp):
    d = tmp / "sqlite"
    d.mkdir()
    for i in range(500):
        (d / f"f{i}.txt").write_text("x" * 2000, encoding="utf-8")
    vol = f"adhoc-probe-{uuid.uuid4().hex[:8]}"
    script = ("import sqlite3,time,os,glob\n"
              "def bench(path):\n"
              " t=time.time(); db=sqlite3.connect(path); db.execute('PRAGMA journal_mode=WAL')\n"
              " db.execute('create table if not exists t(a,b)')\n"
              " for i in range(2000):\n  db.execute('insert into t values(?,?)',(i,'x'*100))\n  i%100 or db.commit()\n"
              " db.commit(); n=db.execute('select count(*) from t').fetchone()[0]; db.close(); return round(time.time()-t,2), n\n"
              "print('bind_sqlite', *bench('/x/test.db'))\n"
              "print('volume_sqlite', *bench('/v/test.db'))\n"
              "t=time.time(); [open(p).read() for p in glob.glob('/x/*.txt')]; print('bind_read_500_files', round(time.time()-t,2))\n")
    try:
        r = podman("run", "--rm", "--mount", f"type=bind,source={d},target=/x", "-v", f"{vol}:/v",
                   "--entrypoint", "python", image, "-c", script)
        record("SQLite (WAL) and file reads on a bind mount vs a volume", r.returncode == 0, stdout=r.stdout.strip(),
               stderr=r.stderr.strip()[-300:])
    finally:
        podman("volume", "rm", "-f", vol)


@check
def probe_host_python(image, tmp):
    import asyncio
    from adhocdocs import endpoint
    os.environ["ADHOC_TRANSPORT"] = "tcp"

    async def run():
        async def echo(r, w):
            w.write(b"ok:" + await r.read(10))
            await w.drain()
            w.close()
        srv, cleanup = await endpoint.start_server(echo, tmp / "ep.sock")
        r, w = await endpoint.open_connection(tmp / "ep.sock")
        w.write(b"ping")
        await w.drain()
        got = await r.read(100)
        srv.close()
        cleanup()
        return got
    got = asyncio.run(run())
    p = portable.spawn([sys.executable, "-c", "import time; time.sleep(30)"], tmp / "spawn.log")
    portable.stop(p)
    record("host side: endpoint, spawn/stop, tesseract", got == b"ok:ping", endpoint_reply=got,
           stopped=p.wait(10) is not None, tesseract=portable.tesseract(), python=sys.version.split()[0],
           stdout_encoding=sys.stdout.encoding, utf8_mode=sys.flags.utf8_mode)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", help="default: the adhocdocs image if built, else python:3.14-slim")
    ap.add_argument("--out", default="probe-report.json")
    a = ap.parse_args()
    if not shutil.which("podman"):
        sys.exit("podman is not on PATH")
    image = a.image
    if not image:
        image = "localhost/adhocdocs:latest" if podman("image", "exists", "localhost/adhocdocs:latest").returncode == 0 \
            else "docker.io/library/python:3.14-slim"
    print(f"image: {image}", flush=True)
    podman("pull", "-q", image) if not image.startswith("localhost/") else None
    with tempfile.TemporaryDirectory(prefix="adhoc-probe-") as t:
        tmp = Path(t)
        probe_machine(image)
        probe_host_python(image, tmp)
        probe_bind_mounts(image, tmp)
        probe_v_with_drive_letter(image, tmp)
        probe_volume_sockets(image)
        probe_published_port(image)
        probe_host_from_container(image)
        probe_locks_across_vm(image, tmp)
        probe_sqlite_and_speed(image, tmp)
    Path(a.out).write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8", newline="\n")
    print(f"\nreport: {a.out}")


if __name__ == "__main__":
    main()
