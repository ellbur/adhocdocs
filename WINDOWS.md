# Windows

*Status: in progress. The host code is portable and the bridge is built,
but none of it has run on Windows yet. Everything was checked on Linux, in
the ways listed under "Checked so far". Next: run
[`tools/windows/probe.py`](tools/windows/probe.py) on a Windows machine,
then follow the test plan at the end.*

adhocdocs has two halves. The session is a Linux container, and stays one:
on Windows, Podman runs it in a Linux VM (`podman machine`, on WSL 2 or
Hyper-V). The host half is the launcher, the viewer, the proxy, the
annotation and audit APIs, and ingest. That half has to run on Windows
itself. Most of the work is in the host half, and one problem is bigger
than the rest put together: how the session reaches the host.

## Reaching the session

On Linux the host's services listen on Unix sockets in a private
directory, and the session mounts that directory. On Windows this fails
twice over:

- Python on Windows has no `AF_UNIX`.
- A host socket doesn't reach into Podman's VM anyway. File sharing into
  the VM (9p or virtiofs) carries files, not sockets.

### Design: the host dials in

([`endpoint.py`](src/adhocdocs/endpoint.py), [`bridge.py`](src/adhocdocs/bridge.py),
[`viewer/src/endpoint.rs`](viewer/src/endpoint.rs))

```
 Windows host                                │ Podman VM
                                             │
 proxy ─┐                                    │  bridge container            session container
 ann API├─ 127.0.0.1:random   ◄── link ──────┼─► 127.0.0.1:P ─► :7100       (--network none)
 audit  │  + token,                (dials in,│   │                          /run/adhoc/*.sock ─┐
 viewer ┘  endpoint files          one port) │   └─ listens on the sockets ◄─────────────────┘
                                             │      in the shared volumes  /run/adhoc-audit (ro)
```

- **Endpoints.** A service is still named by a path. On Windows the service
  listens on a random port on 127.0.0.1 and writes an endpoint file at that
  path: `{"tcp": "127.0.0.1:PORT", "token": "…"}`. Every connection must
  begin with `ADHOC1 <token>`, or the service drops it. Any local program
  can reach a port, so the token does the job the private directory did.
  Clients call `endpoint.connect(path)`, which handles both kinds, so
  `ADHOC_VIEW_SOCKET`, `--socket` and the rest stay as they are. Linux keeps
  its Unix sockets. `ADHOC_TRANSPORT=tcp` forces the Windows behaviour, for
  testing on Linux.
- **The bridge** is a second container from the same image, started
  alongside the session. It shares the session's socket volumes and listens
  on the sockets the session already uses (`/run/adhoc/proxy.sock`, …,
  `/run/adhoc-audit/audit.sock`). It publishes one port, on the host's
  127.0.0.1 only. The session still runs with `--network none`, and its
  image and paths don't change.
- **The link** is a host process. It holds a control connection into the
  bridge's port. When something in the session connects to a socket, the
  bridge sends `open <id> <service>`. The link dials in again with
  `data <id>` and joins that connection to the host service's endpoint.
- **Claude's own services** (`view serve`, `svc-NAME.sock`) work the other
  way: the viewer dials the bridge's port with `svc NAME`. The launcher
  writes the port and token to `svc.endpoint` beside the viewer's socket.

*Why the host dials in:* the VM never needs a route to the host. That
avoids the least certain part of Podman on Windows:
`host.containers.internal` behaves differently on WSL, Hyper-V and
user-mode networking, and the Windows firewall can block it. Port
publishing to 127.0.0.1 is Podman Desktop's core use case on both Windows
and macOS. *Rejected:* host services on TCP that the bridge dials (depends
on the above), Windows `AF_UNIX` (Python lacks it, and it doesn't cross
into the VM anyway), and named pipes (no way in from the VM).

*What changes for security:*
- Inside the session, nothing changes: same mounts, no network.
- The bridge has a network, but runs only our code, and the session can't
  use the bridge's network namespace.
- On the host, TCP ports with tokens replace a private directory. The
  tokens are only in endpoint files, in `%LOCALAPPDATA%\adhocdocs\run\…`
  (per-user ACL) or the matter's `run\`. The bridge gets its token by
  environment variable name, never in a command line.
- The bridge's port answers only on 127.0.0.1, and only with the token, so
  another program on the host can't use the session's services, or pose as
  the host's.

macOS has the same problem and gets the same solution.

## Other host differences

Done on this branch:

- **Text files** are opened with `encoding="utf-8"` everywhere, and written
  with LF. Python 3.14 on Windows still defaults to the ANSI code page and
  CRLF; UTF-8 mode only becomes the default in 3.15. Without this, the
  matter's logs written on the host would not read back in the session,
  and hashes would differ. `tests/test_portable.py` finds any open that
  doesn't name an encoding.
- **Locks** ([`portable.py`](src/adhocdocs/portable.py)): `flock` on POSIX,
  `msvcrt.locking` on Windows. Windows locks are mandatory, so the lock
  covers one byte far past the end of the file. Readers aren't blocked,
  and appends still land at the end.
- **Background processes**: `spawn` and `stop` replace
  `start_new_session` and `killpg`. On Windows that means a new process
  group, no console window, and `terminate()`.
- **Runtime directory**: `%LOCALAPPDATA%\adhocdocs\run` instead of
  `$XDG_RUNTIME_DIR` or `/tmp/adhocdocs-$UID`. Python and Rust share the
  rule.
- **Line endings in git** ([`.gitattributes`](.gitattributes)): LF in every
  checkout. Otherwise Git for Windows would turn `container/adhoc-session`
  into CRLF and break the image. The sample documents are `-text`: many of
  the emails are CRLF, and they must arrive byte for byte.
- **The viewer**:
  - `wry` uses WebView2 on Windows. main.rs already used
    `http://adhoc.localhost/` there.
  - The API listens through `endpoint.rs`. A small HTTP reader replaces
    `tiny_http`, which can't serve a stream after a token line.
  - `--annotate` takes a JSON list, because the Python path is often
    `C:\Users\First Last\…`.
  - `adhoc-viewer.exe` is found.
- **`adhoc setup`** checks the Windows prerequisites: Podman with a running
  machine, Rust, Visual Studio C++ build tools (via `vswhere`), LLVM clang,
  the WebView2 runtime (registry) and tesseract. Tesseract is also found in
  `Program Files\Tesseract-OCR`, where its usual installer puts it without
  adding it to PATH.
- **Podman arguments**: on Windows the session's bind mounts use
  `--mount type=bind,source=C:\…,target=…`, because `-v` splits on colons.
  Its socket directories are two named volumes, shared with the bridge.

Not done yet, smaller:

- **Locks across the VM.** A lock taken on the host likely doesn't stop
  `flock` in the session, so host and session could both update the same
  index at once. The probe measures this. If it's true, the launcher should
  refuse host-side `idx update` while a session is open (a marker in
  `run/`), and the session's own lock keeps working.
- **SQLite and speed over the VM's file sharing.** The session reads the
  store and writes indexes in the workspace through 9p on WSL. SQLite over
  9p has a history of locking trouble, and many small reads are slow. The
  probe times both. Fallbacks, in order:
  - `journal_mode=DELETE` instead of WAL on Windows.
  - Indexes in a per-matter Podman volume, with host tools reaching them
    through the session.
  - Recommend matters on a WSL path (`\\wsl$\…`).
- **Windows file names.** Claude, in the Linux session, can name a
  workspace file `aux.md`, `a:b`, or two names differing only in case. The
  host can't open those. Lists and pages written by our tools are fine.
  Possibly warn in the session guide.
- **Long paths.** `<matter>\workspace\.adhoc\indexes\…` stays well under
  260 characters for ordinary locations; deep OneDrive paths may not.
- **Custom indexes**: the `updater` command runs with `shell=True`, so
  `cmd.exe` on Windows.
- **The terminal**: `podman run -it` from Windows Terminal or PowerShell,
  then Ctrl+C, Ctrl+D and resizing. Podman supports it, but it hasn't been
  tried with Claude Code's interface.
- **Claude Code updates and the image build**: these run in the VM, so they
  should be the same as on Linux.
- **Defender** scans every file ingest writes. Expect slower ingests;
  excluding the matter directory is the user's call.
- **The viewer's WebView2 data** goes in a folder beside the exe by
  default. That's fine in `target\release`; set `with_data_directory` if it
  gets installed somewhere read-only.

## Checked so far (on Linux)

- **Tests:** all 180 pass, both normally and with `ADHOC_TRANSPORT=tcp`.
  That includes the bridge and link end to end:
  - session socket → bridge → link → host endpoint
  - viewer → bridge → session service
  - wrong tokens, bad service names and stray `data` ids refused
- **The real viewer** in TCP mode under Xvfb: the `view` CLI found it by
  its endpoint file and token, and opened a list in it.
- **Static checks with Windows in mind:**
  - `pyright --pythonplatform Windows`: every remaining Windows-only
    finding sits behind a runtime platform check, or is code that runs only
    in the Linux VM (the bridge, the session's relay).
  - `cargo check --target x86_64-pc-windows-gnu`, with mingw: the viewer
    and its C dependencies (MuPDF, via `mupdf-sys`) compile for Windows.
    Real builds will use the MSVC target, which needs Windows.

## What the probe answers

`uv run python tools/windows/probe.py`, with the machine started, reports:

- **The machine:** WSL or Hyper-V, rootful or not, Podman's version.
- **Bind mounts:** whether `--mount` with Windows paths works, `ro` is
  enforced, and `keep-id` file ownership is right. Also whether `-v C:\…`
  works.
- **Volume sockets:** whether a Unix socket in a shared volume works
  between two containers, the client read-only and without a network. This
  is the session ↔ bridge path.
- **Published port:** whether `-p 127.0.0.1::7100` answers on the host, and
  not on the LAN address. This is the link's path.
- **Container → host:** whether `host.containers.internal` reaches the host
  (for information; the design doesn't use it).
- **Locks:** whether a host lock stops `flock` in a container.
- **SQLite and speed:** SQLite (WAL) and file reads, bind mount vs volume.
- **The host side:** endpoint, spawn/stop, where tesseract is, Python's
  encodings.

It writes `probe-report.json`, which is the useful thing to bring back.

## Test plan on Windows

1. Install Git, uv, Podman (`podman machine init`, `podman machine start`),
   Rust with the MSVC build tools, LLVM and tesseract.
2. Clone the repo, run `git ls-files --eol container/` (expect `w/lf`),
   then run the probe.
3. `uv run pytest`, then `cd viewer; cargo test`.
4. `uv run adhoc setup`: the checks first, then the viewer build (MSVC +
   MuPDF is the likeliest build problem), then the image.
5. `uv run adhoc init`, then `ingest --manifest corpus/manifest.toml`.
   Check OCR, and spot-check hashes against a Linux ingest of the same
   files: they should match.
6. `uv run adhoc view` with `uv run view …` from the host: the viewer over
   an endpoint, without a session.
7. `uv run adhoc open <matter> -- adhoc status`: mounts, no direct
   internet, and the viewer, annotation API and audit API all running.
   That's the bridge, the link and the volumes. Then `-- search …`,
   `-- ann tag …`, `-- view list …`.
8. `uv run adhoc open <matter>`: sign in, then work through the README's
   prompts. Check the audit log (`adhoc audit verify`), and that Ctrl+D
   cleans up the bridge container and volumes (`podman ps -a`,
   `podman volume ls`).
9. `view serve` from the session, and a form submitted in the viewer: the
   svc path through the bridge.
