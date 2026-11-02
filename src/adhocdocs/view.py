"""Talking to the viewer, and making its list panes.

The viewer (``viewer/``, a native program) runs on the host, launched by
``adhoc view``; the ``view`` CLI is a thin client for its API on a Unix
socket. :func:`list_html` renders a list file as static HTML for a list
pane: rows are ``data-locator`` elements in a ``data-sequence``, and the
``tags`` and ``notes`` columns are filled in live by the viewer.
"""

import base64
import hashlib
import html
import http.client
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import endpoint, lists, locators
from .matter import Matter
from .portable import WINDOWS, runtime_dir, spawn

MAX_SOCKET_PATH = 100
ROWS_PER_PAGE = 500
REPO = Path(__file__).resolve().parents[2]


def socket_path(m: Matter) -> Path:
    """The same rule as the viewer's: ``$ADHOC_VIEW_SOCKET``, else
    ``<matter>/run/viewer.sock``, else a per-matter path in the runtime dir."""
    if os.environ.get("ADHOC_VIEW_SOCKET"):
        return Path(os.environ["ADHOC_VIEW_SOCKET"])
    p = m.root / "run" / "viewer.sock"
    if len(str(p)) <= MAX_SOCKET_PATH:
        return p
    return runtime_dir() / "adhocdocs" / hashlib.sha256(str(m.root).encode()).hexdigest()[:16] / "viewer.sock"


class NotRunning(Exception):
    pass


class ViewerError(Exception):
    pass


class _Conn(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=30)
        self._path = path

    def connect(self):
        self.sock = endpoint.connect(self._path, 30)


def command(m: Matter, cmd: dict) -> dict:
    path = socket_path(m)
    conn = _Conn(path)
    try:
        conn.request("POST", "/command", json.dumps(cmd), {"Content-Type": "application/json"})
        out = json.loads(conn.getresponse().read())
    except (FileNotFoundError, ConnectionRefusedError):
        raise NotRunning("the viewer isn't running; it is started on the host (adhoc view), so ask the user")
    finally:
        conn.close()
    if "error" in out:
        raise ViewerError(out["error"])
    return out


# -- loose files ---------------------------------------------------------------------------------
# Files the user hands Claude (dropped on the viewer, or its terminal) and
# files Claude hands back: plain files in ``workspace/files/``, not discovery
# documents (not ingested, no IDs, not synced or tracked). The viewer's Files
# pane shows both folders and does the copying in and saving out
# (viewer/src/files.rs).

LOOSE = "files"


def loose_dir(m: Matter, kind: str) -> Path:
    return m.workspace / LOOSE / kind


def _unique(d: Path, name: str) -> Path:
    p = d / name
    stem, ext = (name[:name.rindex(".")], name[name.rindex("."):]) if "." in name.lstrip(".") else (name, "")
    n = 2
    while p.exists() or p.is_symlink():
        p = d / f"{stem} ({n}){ext}"
        n += 1
    return p


def deliver(m: Matter, paths, note: str | None = None) -> list[dict]:
    """Copy files (or folders) into ``files/from-claude/`` for the user,
    never over one there, and keep the note on each. A file already there is
    left where it is. Returns ``[{name, path}]``."""
    d = loose_dir(m, "from-claude")
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for p in map(Path, paths):
        if not p.exists():
            raise FileNotFoundError(f"no file {p}")
        if p.resolve().parent == d.resolve():
            dest = p.resolve()
        else:
            name = p.resolve().name
            dest = _unique(d, name)
            tmp = d / f".adding-{os.getpid()}-{name}"
            (shutil.copytree(p, tmp, symlinks=True) if p.is_dir() else shutil.copyfile(p, tmp))
            tmp.rename(dest)           # whole, or not there at all
        out.append({"name": dest.name, "path": str(dest)})
    if note:
        nf = d / ".notes.json"
        try:
            notes = json.loads(nf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            notes = {}
        notes.update({x["name"]: note for x in out})
        nf.write_text(json.dumps(notes, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def files_of(path: Path) -> dict:
    """A pane's files, base64: one HTML file, or a directory bundle."""
    path = Path(path)
    if path.is_dir():
        found = {str(f.relative_to(path)): f for f in path.rglob("*") if f.is_file()}
        if "index.html" not in found:
            raise ViewerError(f"{path} has no index.html")
    else:
        found = {"index.html": path}
    return {k: base64.b64encode(f.read_bytes()).decode() for k, f in found.items()}


# -- lists ---------------------------------------------------------------------------------

def _cell(col: str, item: dict, row: dict, loc: str) -> str:
    fields = item.get("fields") or {}
    if col in fields:
        v = fields[col]
    elif col == "tags":
        return f'<span data-tags="{html.escape(loc)}"></span>'
    elif col == "notes":
        return f'<span data-notes="{html.escape(loc)}"></span>'
    elif col == "id":
        v = locators.parse(loc)["doc"][7:17]
    elif col == "date":
        v = (row.get("date") or "")[:10]
    elif col == "snippet" and "char" in (locators.parse(item["loc"]).get("select") or {}):
        return f'<span data-snippet="{html.escape(loc)}" data-context="60"></span>'
    else:
        v = row.get(col)
    if isinstance(v, list):
        v = ", ".join(str(x) for x in v)
    return html.escape("" if v is None else str(v))


def list_html(m: Matter, path: Path) -> tuple[dict, list[str]]:
    """(files, sequence): an HTML bundle for a list pane, split into pages of
    ROWS_PER_PAGE rows, and every row's locator in order."""
    from .index import open_index
    header, items = lists.read(path)
    fx = open_index(m, "fields")
    fx.update()
    rows = fx.rows()
    columns = header.get("columns") or lists.DEFAULT_COLUMNS
    flat = []
    for it in items:
        flat.append((it, False))
        flat += [(c, True) for c in it.get("children", [])]
    sequence = []
    for it, _ in flat:
        loc = it["loc"] if isinstance(it["loc"], str) else locators.format(it["loc"])
        sequence.append(loc)
    pages = [flat[i:i + ROWS_PER_PAGE] for i in range(0, len(flat), ROWS_PER_PAGE)] or [[]]
    files = {}
    title = html.escape(header.get("title") or Path(path).stem)
    note = header.get("query") or ""
    for n, page in enumerate(pages):
        out = [f"<h2>{title}</h2>"]
        if note:
            out.append(f'<p class="adhoc-query">{html.escape(str(note))} · {len(items)} items</p>')
        if header.get("unofficial"):         # interp search --list: places in Claude's readings
            out.append('<p class="adhoc-interp-label">Interpretation by Claude, not the documents’ own content. '
                       'Each row opens the evidence its reading points at; quote that, not the row.</p>')
        nav = []
        if n > 0:
            nav.append(f'<a href="{"index" if n == 1 else f"page{n}"}.html">← rows {(n - 1) * ROWS_PER_PAGE + 1}–{n * ROWS_PER_PAGE}</a>')
        if n < len(pages) - 1:
            nav.append(f'<a href="page{n + 2}.html">rows {(n + 1) * ROWS_PER_PAGE + 1}– →</a>')
        out.append(f'<p>{" · ".join(nav)}</p>' if nav else "")
        out.append('<table class="adhoc-list"><thead><tr>' + "".join(f"<th>{html.escape(c)}</th>" for c in columns) + "</tr></thead>")
        out.append("<tbody data-sequence>")
        group = None
        for it, child in page:
            if not child and it.get("group") and it["group"] != group:
                group = it["group"]
                count = sum(1 for x in items if x.get("group") == group)
                out.append(f'<tr class="group"><td colspan="{len(columns)}">{html.escape(group)} ({count})</td></tr>')
            loc = it["loc"] if isinstance(it["loc"], str) else locators.format(it["loc"])
            row = rows.get(locators.parse(loc)["doc"], {})
            cls = ' class="child"' if child else ""
            out.append(f'<tr{cls} data-locator="{html.escape(loc)}">' + "".join(f"<td>{_cell(c, it, row, loc)}</td>" for c in columns) + "</tr>")
        out.append("</tbody></table>")
        files["index.html" if n == 0 else f"page{n + 1}.html"] = base64.b64encode("\n".join(out).encode()).decode()
    return files, sequence


# -- launching (host side) -------------------------------------------------------------------

def viewer_binary() -> str:
    if os.environ.get("ADHOC_VIEWER_BIN"):
        return os.environ["ADHOC_VIEWER_BIN"]
    exe = "adhoc-viewer.exe" if WINDOWS else "adhoc-viewer"
    for profile in ("release", "debug"):
        p = REPO / "viewer" / "target" / profile / exe
        if p.exists():
            return str(p)
    found = shutil.which("adhoc-viewer")
    if found:
        return found
    raise NotRunning("adhoc-viewer not found: build it with `uv run adhoc setup`, or set ADHOC_VIEWER_BIN")


def annotate_command() -> str:
    """How the viewer runs this Python's ``adhoc`` to write the user's
    annotations: a JSON list, since the path may have spaces (on Windows, often)."""
    return json.dumps([sys.executable, "-m", "adhocdocs.cli", "adhoc"])


def launch(m: Matter, user: str, wait=False):
    """Start the viewer for this matter. This is part of connecting a matter,
    done by the user on the host; nothing in the container can do it."""
    cmd = [viewer_binary(), "--matter", str(m.root), "--user", user,
           "--annotate", annotate_command()]
    if wait:
        return subprocess.call(cmd)
    (m.root / "run").mkdir(exist_ok=True)
    return spawn(cmd, m.root / "run" / "viewer.log").pid


# -- services ------------------------------------------------------------------------------------

def service_socket(m: Matter, name: str) -> Path:
    """Services listen next to the viewer's socket: the directory the
    session shares with the host."""
    return socket_path(m).parent / f"svc-{name}.sock"


def serve(m: Matter, name: str, command: list[str]):
    """Answer a pane's ``<form action="svc:NAME">``: for each submission, run
    ``command`` with ``{"form", "selection", "pane"}`` as JSON on stdin; its
    stdout is the pane's new HTML. Runs until interrupted."""
    import re
    from http.server import BaseHTTPRequestHandler
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ViewerError("service names are lowercase letters, digits and hyphens")
    path = service_socket(m, name)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            r = subprocess.run(command, input=body, capture_output=True, timeout=55)
            ok = r.returncode == 0
            data = r.stdout if ok else (r.stderr or b"failed")
            self.send_response(200 if ok else 500)
            self.send_header("Content-Type", "text/html; charset=utf-8" if ok else "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    # In the session: a Unix socket the viewer reaches (through the bridge on Windows and macOS).
    with endpoint.http_server(path, Handler, "service", mode=0o660) as srv:
        print(json.dumps({"serving": name, "socket": str(path)}), file=sys.stderr, flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
