"""Interpretations: Claude's own readings of documents whose format or
schema nothing in adhocdocs knows (see DATA.md).

A text layer is the document's own text, so it can be quoted. A file of an
unknown binary format has no such text, and a JSON export's meaning isn't
in its syntax. What Claude works out about them (that bytes 0x40–0x80 are
a little-endian table of timestamps, that ``/r/7/t`` is a sender) is an
**interpretation**: useful, often right, and never the document's words.
So an interpretation is kept apart from quotation everywhere:

- It is made by a **decoder**, a command Claude writes and runs with
  ``interp run``, which reads the original's bytes and prints its reading:
  JSON (a table, a tree or a value), CSV, text, or a chart as SVG or PNG.
  The files the command names are copied in beside the output, with their
  hashes, so the record says exactly what produced it, and ``interp
  check`` can run it again to show it is reproducible.
- It lives in the workspace, ``interpretations/<name>/``, never in the
  store. It is not a text layer and isn't indexed, so no search hit or
  quotation can come from it by accident. (Its output is a plain file, so
  ``grep`` works.)
- Pages show it with ``:interp``/``::interp``, not ``:q``, labeled as an
  interpretation by Claude with its decoder and output hash; export lists it
  apart from quotations. A ``:q`` of an interpretation is a broken
  reference.
- An interpretation can point back at its evidence: a JSON object in the
  output with ``"_bytes": [start, end]`` (a range of the first input's
  bytes), ``"_ptr": "/a/b"`` (a JSON pointer into it) or ``"_loc"`` (any
  locator) links to the bytes or text it was read from, which *can* be
  quoted. Those keys aren't shown as data.

``record.json`` holds: ``name``, ``title``, ``about`` (what Claude thinks
the file is, and how sure), ``inputs`` (doc, content, size), ``decoder``
(``command`` as run, ``files`` with hashes), ``output`` (file, type,
sha256, size) and ``made``.
"""

import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import locators

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TYPES = ("json", "csv", "text", "svg", "png")
EXT = {"json": "json", "csv": "csv", "text": "txt", "svg": "svg", "png": "png"}
SHOWN_AS = ("table", "tree", "value", "text", "image")
MAX_ROWS = 2000
MAX_NODES = 5000
MAX_TEXT = 200_000
TIMEOUT = 600
MAX_DECODER_FILE = 10 << 20   # files on the decoder's command line bigger than this aren't copied
EVIDENCE_KEYS = ("_bytes", "_ptr", "_loc")


class InterpError(ValueError):
    pass


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def root(m) -> Path:
    return m.workspace / "interpretations"


def folder(m, name: str) -> Path:
    if not NAME.match(name or ""):
        raise InterpError(f"bad name {name!r}: use lowercase letters, digits and hyphens")
    return root(m) / name


def load(m, name: str) -> dict:
    f = folder(m, name) / "record.json"
    if not f.exists():
        raise InterpError(f"no interpretation named {name!r} (interp ls lists them)")
    return json.loads(f.read_text(encoding="utf-8"))


def output_bytes(m, rec: dict) -> bytes:
    return (folder(m, rec["name"]) / rec["output"]["file"]).read_bytes()


def names(m) -> list[str]:
    r = root(m)
    return sorted(p.name for p in r.iterdir() if (p / "record.json").exists()) if r.exists() else []


# -- running ---------------------------------------------------------------------------------

def guess_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    head = data[:512].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048]):
        return "svg"
    try:
        json.loads(data)
        return "json"
    except ValueError:
        return "text"


def _validate(data: bytes, type_: str):
    try:
        if type_ == "json":
            json.loads(data)
        elif type_ in ("csv", "text", "svg"):
            data.decode("utf-8")
        elif type_ == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("not a PNG")
    except (ValueError, UnicodeDecodeError) as e:
        raise InterpError(f"the decoder's output isn't {type_}: {e}")


def _inputs(m, docs: list[str]) -> list[dict]:
    if not docs:
        raise InterpError("name at least one document to interpret")
    state = m.state
    out = []
    for d in docs:
        doc = locators.parse("doc:" + d.removeprefix("doc:"), m.objects)["doc"]
        if doc not in state.documents or doc in state.withdrawn:
            raise InterpError(f"{d}: no such live document")
        content = state.documents[doc]
        out.append({"doc": doc, "content": content, "size": m.objects.path(content).stat().st_size})
    return out


def _execute(m, command: list[str], inputs: list[dict], cwd: Path | None = None) -> bytes:
    paths = [str(m.objects.path(i["content"])) for i in inputs]
    env = {**os.environ, "ADHOC_INPUT": paths[0], "ADHOC_INPUTS": json.dumps(paths)}
    with open(paths[0], "rb") as stdin, tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run(command, stdin=stdin, capture_output=True, env=env, cwd=cwd or tmp, timeout=TIMEOUT)
        except FileNotFoundError as e:
            raise InterpError(f"can't run the decoder: {e}")
        except subprocess.TimeoutExpired:
            raise InterpError(f"the decoder ran for more than {TIMEOUT} s")
    if r.returncode != 0:
        raise InterpError(f"the decoder failed (exit {r.returncode}): {r.stderr.decode(errors='replace')[-2000:]}")
    return r.stdout


def run(m, docs: list[str], name: str, command: list[str], title: str, about: str | None = None,
        type_: str | None = None, replace: bool = False) -> dict:
    """Run a decoder on documents' originals and record its output as an
    interpretation. The decoder gets the first original on stdin, and every
    original's path in ``$ADHOC_INPUT`` (the first) and ``$ADHOC_INPUTS``
    (a JSON list); it prints its reading on stdout. Files named on its
    command line are copied in first and run from the copies, so what is
    recorded is what ran."""
    if not command:
        raise InterpError("give the decoder's command after --")
    if type_ and type_ not in TYPES:
        raise InterpError(f"--type is one of {', '.join(TYPES)}")
    dest = folder(m, name)
    if dest.exists() and not replace:
        raise InterpError(f"interpretation {name} exists; pass --replace (pages that show it will then say it changed)")
    inputs = _inputs(m, docs)
    stores = {str(Path(m.objects.path(i["content"])).resolve()) for i in inputs}
    root(m).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root(m)))
    workspace = Path(m.workspace).resolve()
    try:
        (tmp / "decoder").mkdir(parents=True)
        cmd, files = [], []
        for i, arg in enumerate(command):
            p = Path(arg)
            keep = p.is_file() and str(p.resolve()) not in stores and p.stat().st_size <= MAX_DECODER_FILE and (
                i > 0 or p.resolve().is_relative_to(workspace))      # not the interpreter itself
            if keep:
                rel = f"decoder/{p.name}"
                if any(f["path"] == rel for f in files):
                    rel = f"decoder/{i}-{p.name}"
                shutil.copy2(p, tmp / rel)
                files.append({"path": rel, "from": str(p), "sha256": sha256((tmp / rel).read_bytes())})
                cmd.append(rel)
            else:
                cmd.append(arg)
        data = _execute(m, [str(tmp / c) if any(f["path"] == c for f in files) else c for c in cmd], inputs)
        type_ = type_ or guess_type(data)
        _validate(data, type_)
        out_file = f"output.{EXT[type_]}"
        (tmp / out_file).write_bytes(data)
        rec = {"type": "interpretation", "v": 1, "name": name, "title": title, **({"about": about} if about else {}),
               "inputs": inputs, "decoder": {"command": cmd, "files": files},
               "output": {"file": out_file, "type": type_, "sha256": sha256(data), "size": len(data)},
               "made": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        (tmp / "record.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    m.logs["workspace"].append(op="interpret", name=name, output=rec["output"]["sha256"],
                               inputs=[i["doc"] for i in inputs], decoder=[f["sha256"] for f in files])
    return rec


def check(m, name: str, rerun: bool = True) -> dict:
    """Whether an interpretation still holds: its inputs are live with the
    same content, its files have their recorded hashes, and (``rerun``) its
    decoder, run again from the copies, prints the same output."""
    rec = load(m, name)
    d = folder(m, name)
    problems = []
    state = m.state
    for i in rec["inputs"]:
        if i["doc"] not in state.documents or i["doc"] in state.withdrawn:
            problems.append(f"input {i['doc'][:15]}… is no longer in the matter")
        elif state.documents[i["doc"]] != i["content"]:
            problems.append(f"input {i['doc'][:15]}… has other content")
    for f in rec["decoder"]["files"]:
        p = d / f["path"]
        if not p.exists() or sha256(p.read_bytes()) != f["sha256"]:
            problems.append(f"{f['path']} has changed since it ran")
    out = d / rec["output"]["file"]
    if not out.exists() or sha256(out.read_bytes()) != rec["output"]["sha256"]:
        problems.append(f"{rec['output']['file']} has changed since it was made")
    result = {"name": name, "ok": not problems, "problems": problems}
    if rerun and not problems:
        cmd = [str(d / c) if any(f["path"] == c for f in rec["decoder"]["files"]) else c for c in rec["decoder"]["command"]]
        try:
            again = _execute(m, cmd, rec["inputs"])
            result["reproducible"] = sha256(again) == rec["output"]["sha256"]
            if not result["reproducible"]:
                result["ok"] = False
                problems.append("running the decoder again gave different output")
        except InterpError as e:
            result["ok"], result["reproducible"] = False, False
            problems.append(str(e))
    return result


# -- reading the output -----------------------------------------------------------------------

def value_of(m, rec: dict, ptr: str | None = None):
    """The output as data: JSON decoded (then the value at ``ptr``), CSV as
    rows, text as a string. Images have no value."""
    from . import structured
    data = output_bytes(m, rec)
    t = rec["output"]["type"]
    if t == "json":
        v = json.loads(data)
        for tok in structured.parse_pointer(ptr or ""):
            if isinstance(v, list) and re.fullmatch(r"0|[1-9][0-9]*", tok) and int(tok) < len(v):
                v = v[int(tok)]
            elif isinstance(v, dict) and tok in v:
                v = v[tok]
            else:
                raise InterpError(f"nothing at {ptr} in the output of {rec['name']}")
        return v
    if ptr:
        raise InterpError(f"ptr is for JSON output; {rec['name']}'s is {t}")
    if t == "csv":
        return list(csv.reader(io.StringIO(data.decode("utf-8"))))
    if t == "text":
        return data.decode("utf-8")
    return None


def default_as(rec: dict, value, block: bool) -> str:
    t = rec["output"]["type"]
    if t in ("svg", "png"):
        return "image"
    if t == "csv":
        return "table" if block else "text"
    if t == "text":
        return "text"
    if not isinstance(value, (dict, list)):
        return "value"
    if not block:
        return "value"
    if isinstance(value, list) and value and all(isinstance(x, (dict, list)) for x in value):
        return "table"
    return "tree"


def shown_as(rec: dict, value, want: str | None, block: bool) -> tuple[str, str | None]:
    """How an interpretation is shown, or why it can't be."""
    want = want or default_as(rec, value, block)
    t = rec["output"]["type"]
    if want not in SHOWN_AS:
        return want, f"as={want}: use one of {', '.join(SHOWN_AS)}"
    if want == "image" and t not in ("svg", "png"):
        return want, "as=image is for SVG or PNG output"
    if t in ("svg", "png") and want != "image":
        return want, f"{t} output can only be shown as an image"
    if want in ("table", "tree", "image") and not block:
        return want, f"as={want} is for blocks (::interp)"
    if want == "table" and not (isinstance(value, list) and all(isinstance(x, (dict, list)) for x in value)
                                or isinstance(value, dict) and value and all(isinstance(x, dict) for x in value.values())):
        return want, "as=table needs a list of objects or of lists (or an object of objects)"
    if want == "tree" and t != "json":
        return want, "as=tree is for JSON output"
    return want, None


def _evidence(m, rec: dict, row) -> str | None:
    """A locator for what a row of the output was read from, if it says."""
    if not isinstance(row, dict):
        return None
    doc = rec["inputs"][0]["doc"]
    try:
        if "_loc" in row:
            return row["_loc"] if isinstance(row["_loc"], str) else locators.format(row["_loc"])
        if "_bytes" in row:
            a, b = row["_bytes"]
            return f"doc:{doc}#bytes={int(a)},{int(b)}"
        if "_ptr" in row:
            r = locators.resolve(m, {"doc": doc, "select": {"ptr": row["_ptr"]}})
            return locators.format(r["loc"]) if r["status"] != "broken" else None
    except (ValueError, TypeError, KeyError):
        return None
    return None


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def table_rows(rec: dict, value) -> tuple[list[str] | None, list[list[str]], list, int]:
    """(header, rows, each row's source object, rows left out)."""
    if rec["output"]["type"] == "csv":
        header, rows = (value[0], value[1:]) if value else (None, [])
        return header, rows[:MAX_ROWS], [None] * min(len(rows), MAX_ROWS), max(0, len(rows) - MAX_ROWS)
    keyed = isinstance(value, dict)
    items = list(value.items()) if keyed else list(enumerate(value))
    more = max(0, len(items) - MAX_ROWS)
    items = items[:MAX_ROWS]
    if all(isinstance(x, list) for _, x in items):
        return None, [[_cell(c) for c in x] for _, x in items], [None] * len(items), more
    cols = [] if not keyed else [""]
    for _, x in items:
        for k in x:
            if k not in cols and k not in EVIDENCE_KEYS:
                cols.append(k)
    rows = [([str(k)] if keyed else []) + [_cell(x.get(c)) for c in cols[1 if keyed else 0:]] for k, x in items]
    return cols, rows, [x for _, x in items], more


def as_text(m, rec: dict, value, how: str) -> str:
    """The plain text an export writes."""
    if how == "table":
        header, rows, _, more = table_rows(rec, value)
        lines = (["\t".join(header)] if header else []) + ["\t".join(r) for r in rows]
        return "\n".join(lines + ([f"[{more} more rows]"] if more else []))
    if how == "tree":
        return json.dumps(_strip_evidence(value), indent=2, ensure_ascii=False)
    if how == "value":
        return _cell(_strip_evidence(value))
    if how == "text":
        return value if isinstance(value, str) else as_text(m, rec, value, "table" if isinstance(value, list) else "value")
    return ""


def _strip_evidence(v):
    if isinstance(v, dict):
        return {k: _strip_evidence(x) for k, x in v.items() if k not in EVIDENCE_KEYS}
    if isinstance(v, list):
        return [_strip_evidence(x) for x in v]
    return v


# -- HTML --------------------------------------------------------------------------------------

def caption(m, rec: dict, ptr: str | None = None, names: dict | None = None) -> str:
    """What every rendering says about itself: whose reading it is, of what, by what."""
    names = names or {}
    srcs = []
    for i in rec["inputs"]:
        h = m.state.metadata.get(i["doc"])
        nm = names.get(i["doc"]) or (m.objects.get_object(h).get("name") if h else None) or i["doc"][7:19]
        srcs.append(f'<a href="loc:doc:{i["doc"]}">{html.escape(nm)}</a>')
    files = ", ".join(f'{html.escape(Path(f["path"]).name)} ({f["sha256"][7:15]})' for f in rec["decoder"]["files"])
    by = f" by {files}" if files else f" by <code>{html.escape(' '.join(rec['decoder']['command']))}</code>"
    at = f", at <code>{html.escape(ptr)}</code>" if ptr else ""
    return (f'<span class="adhoc-interp-label">Interpretation by Claude, not the document’s own content:</span> '
            f'{html.escape(rec["title"])}. Decoded from {", ".join(srcs)}{by}{at}; output {rec["output"]["sha256"][7:15]}.')


def _tree_html(v, budget: list) -> str:
    budget[0] -= 1
    if budget[0] < 0:
        return "…"
    if isinstance(v, dict):
        items = [(k, x) for k, x in v.items() if k not in EVIDENCE_KEYS]
        inner = "".join(f"<li><span class=\"k\">{html.escape(str(k))}</span>: {_tree_html(x, budget)}</li>" for k, x in items)
        return f"<details open><summary>{{{len(items)}}}</summary><ul>{inner}</ul></details>" if items else "{}"
    if isinstance(v, list):
        inner = "".join(f"<li><span class=\"k\">{i}</span>: {_tree_html(x, budget)}</li>" for i, x in enumerate(v))
        return f"<details open><summary>[{len(v)}]</summary><ul>{inner}</ul></details>" if v else "[]"
    return f"<span class=\"v\">{html.escape(_cell(v) if not isinstance(v, str) else json.dumps(v, ensure_ascii=False))}</span>"


def body_html(m, rec: dict, value, how: str, images: dict | None = None, inline_images: bool = True) -> str:
    """The rendering itself. Pictures go inline as data URIs, or (for a
    viewer bundle, whose panes allow no data: URLs) into ``images``."""
    if how == "image":
        data = output_bytes(m, rec)
        t = rec["output"]["type"]
        mime = "image/svg+xml" if t == "svg" else "image/png"
        if inline_images:
            import base64
            src = f"data:{mime};base64," + base64.b64encode(data).decode()
        else:
            src = f"img/{rec['output']['sha256'][7:23]}.{t}"
            if images is not None:
                images[src] = data
        return f'<img src="{src}" alt="{html.escape(rec["title"])}">'
    if how == "table":
        header, rows, objs, more = table_rows(rec, value)
        links = [_evidence(m, rec, o) for o in objs]
        has_links = any(links)
        head = ""
        if header:
            head = "<thead><tr>" + "".join(f"<th>{html.escape(str(h))}</th>" for h in header) + \
                   ("<th>source</th>" if has_links else "") + "</tr></thead>"
        width = max([len(header or [])] + [len(r) for r in rows])
        body = []
        for r, link in zip(rows, links):
            cells = "".join(f"<td>{html.escape(c)}</td>" for c in r + [""] * (width - len(r)))
            if has_links:
                cells += f'<td><a href="loc:{html.escape(link)}">↗</a></td>' if link else "<td></td>"
            body.append(f"<tr>{cells}</tr>")
        if more:
            body.append(f'<tr><td colspan="{width + has_links}" class="adhoc-mark">{more} more rows not shown</td></tr>')
        return f"<table>{head}<tbody>{''.join(body)}</tbody></table>"
    if how == "tree":
        return f'<div class="adhoc-tree">{_tree_html(value, [MAX_NODES])}</div>'
    if how == "value":
        return f"<span class=\"v\">{html.escape(as_text(m, rec, value, 'value'))}</span>"
    text = as_text(m, rec, value, "text")
    cut = len(text) > MAX_TEXT
    return f"<pre>{html.escape(text[:MAX_TEXT])}</pre>" + (f'<p class="adhoc-mark">{len(text) - MAX_TEXT} more characters not shown</p>' if cut else "")


STYLE = """
figure.adhoc-interp { margin: 1em 0; border: 1px dashed #b07d00; background: #fffbf0; padding: .5em .8em; }
figure.adhoc-interp figcaption { font: 12px/1.4 system-ui, sans-serif; color: #6b4e00; margin-bottom: .4em; }
.adhoc-interp-label { font-weight: bold; }
figure.adhoc-interp table { border-collapse: collapse; font: 12px/1.3 system-ui, sans-serif; }
figure.adhoc-interp td, figure.adhoc-interp th { border: 1px solid #d9c690; padding: 2px 6px; vertical-align: top; }
figure.adhoc-interp pre { white-space: pre-wrap; font: 12px/1.4 ui-monospace, monospace; margin: 0; }
figure.adhoc-interp img { max-width: 100%; }
.adhoc-tree ul { list-style: none; margin: 0; padding-left: 1.2em; font: 12px/1.4 ui-monospace, monospace; }
.adhoc-tree .k { color: #555; }
span.adhoc-interp { border-bottom: 1px dashed #b07d00; background: #fffbf0; }
.adhoc-hex { font: 12px/1.4 ui-monospace, monospace; white-space: pre; margin: 1em 2em; }
"""


def figure_html(m, rec: dict, ptr: str | None, how: str, images=None, inline_images=True, names=None) -> str:
    value = value_of(m, rec, ptr)
    return (f'<figure class="adhoc-interp" data-interp="{html.escape(rec["name"])}">'
            f"<figcaption>{caption(m, rec, ptr, names)}</figcaption>"
            f"{body_html(m, rec, value, how, images, inline_images)}</figure>")


def page_html(m, name: str, ptr: str | None = None, how: str | None = None, inline_images=True) -> tuple[str, dict]:
    """A standalone HTML page showing an interpretation (for ``interp
    render`` and a viewer pane), and its picture files for a bundle."""
    rec = load(m, name)
    value = value_of(m, rec, ptr)
    how, err = shown_as(rec, value, how, True)
    if err:
        raise InterpError(err)
    images = {}
    fig = figure_html(m, rec, ptr, how, images, inline_images)
    about = f'<p class="adhoc-about">{html.escape(rec["about"])}</p>' if rec.get("about") else ""
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(rec["title"])}</title>'
           f"<style>body {{ font: 15px/1.5 system-ui, sans-serif; margin: 1em; }}{STYLE}</style></head><body>"
           f"<h2>{html.escape(rec['title'])}</h2>{about}{fig}</body></html>")
    return doc, images


def hex_html(m, loc: dict, data: bytes) -> str:
    """A byte range as a hex dump (the official display of bytes)."""
    from . import raw
    a = loc["select"]["bytes"][0]
    text = raw.hexdump_text(data, a)
    return f'<pre class="adhoc-hex">{html.escape(text)}</pre>'


# -- searching ---------------------------------------------------------------------------------

def searchable(m, rec: dict) -> tuple[str, list[tuple[int, dict]]] | None:
    """An interpretation's output as text to search, and where each line of
    it came from: ``(text, [(line start, where)])``. JSON becomes one line
    per value (keys aren't searched), with a blank line between records
    (the items of the innermost array), so ``/p`` means "in the same
    record"; ``where`` is the value's ``ptr`` and its ``record``. CSV is one
    line per cell (``row``, ``column``), text its own lines (``line``).
    Pictures have no text: None."""
    t = rec["output"]["type"]
    if t in ("svg", "png"):
        return None
    value = value_of(m, rec)
    lines: list[tuple[str, dict]] = []
    if t == "json":
        def walk(v, path):
            if isinstance(v, dict):
                for k, x in v.items():
                    if k not in EVIDENCE_KEYS:
                        walk(x, path + [k])
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    walk(x, path + [i])
            elif v is not None:
                ints = [i for i, p in enumerate(path) if isinstance(p, int)]
                record = format_ptr(path[:ints[-1] + 1]) if ints else ""
                lines.append((_cell(v), {"ptr": format_ptr(path), "record": record}))
        walk(value, [])
    elif t == "csv":
        header = value[0] if value else []
        for r, row in enumerate(value[1:], 1):
            for c, cell in enumerate(row):
                lines.append((cell, {"row": r, "column": header[c] if c < len(header) else c}))
    else:
        lines = [(line, {"line": n}) for n, line in enumerate(value.split("\n"), 1)]
    out, where, pos, last = [], [], 0, None
    for text, w in lines:
        text = text.replace("\n", " ")
        if out and w.get("record", last) != last:
            out.append("")                   # a blank line: a new record, so a new paragraph
            pos += 1
        last = w.get("record")
        where.append((pos, w))
        out.append(text)
        pos += len(text) + 1
    return "\n".join(out), where


def format_ptr(path) -> str:
    from .structured import format_pointer
    return format_pointer(path)


def _check_query(node):
    from . import query as Q
    if isinstance(node, Q.Field):
        raise InterpError(f"{node.name}: is a field of documents; interpretations are searched by their text only")
    for attr in ("items",):
        for x in getattr(node, attr, ()):
            _check_query(x)
    if isinstance(node, Q.Not):
        _check_query(node.item)


def _eval(node, toks, offs, units):
    from . import query as Q
    if isinstance(node, Q.All):
        return True, []
    if isinstance(node, (Q.Words, Q.Near)):
        sp = [(offs[i][0], offs[j - 1][1]) for i, j in Q.spans(node, toks, units)]
        return bool(sp), sp
    if isinstance(node, Q.And):
        out = []
        for x in node.items:
            ok, sp = _eval(x, toks, offs, units)
            if not ok:
                return False, []
            out += sp
        return True, out
    if isinstance(node, Q.Or):
        res, out = False, []
        for x in node.items:
            ok, sp = _eval(x, toks, offs, units)
            res, out = res or ok, out + sp
        return res, out
    if isinstance(node, Q.Not):
        return not _eval(node.item, toks, offs, units)[0], []
    raise TypeError(node)


def _evidence_at(m, rec: dict, ptr: str) -> str | None:
    """The evidence link of the innermost object around a value that has one."""
    from .structured import parse_pointer
    v, found = value_of(m, rec), None
    for tok in [None] + parse_pointer(ptr):
        if tok is not None:
            v = v[int(tok)] if isinstance(v, list) else v.get(tok) if isinstance(v, dict) else None
        if isinstance(v, dict) and any(k in v for k in EVIDENCE_KEYS):
            found = v
    return _evidence(m, rec, found) if found else None


def search(m, q: str, only: list[str] | None = None, limit: int | None = None) -> tuple[dict, list[dict]]:
    """Search interpretations' outputs with the query language of ``search``
    (words, phrases, prefixes, Boolean, w/N, pre/N, /s, /p; no field
    filters). Exhaustive over what it reports searching. Boolean operators
    apply per interpretation, proximity within one. Every hit is a place
    in Claude's reading, never in a document: it says which reading, where
    in its output, and the evidence locator the decoder gave, if any."""
    from bisect import bisect_right
    from . import query as Q, textnorm
    node = Q.parse(q)
    _check_query(node)
    chosen = only or names(m)
    coverage = {"interpretations": len(chosen), "searched": 0, "pictures": 0, "stale": []}
    items = []
    for name in chosen:
        rec = load(m, name)
        got = searchable(m, rec)
        if got is None:
            coverage["pictures"] += 1
            continue
        coverage["searched"] += 1
        stale = not check(m, name, rerun=False)["ok"]
        if stale:
            coverage["stale"].append(name)
        text, where = got
        tokens = textnorm.tokenize(text)
        toks = [t for t, _, _ in tokens]
        offs = [(s, e) for _, s, e in tokens]
        units = None
        if any(isinstance(n, Q.Near) and n.unit != "w" for n in _nodes(node)):
            flat = [x for se in offs for x in se]
            units = Q.Units(text, flat)
        ok, spans = _eval(node, toks, offs, units)
        if not ok:
            continue
        from .search import snippet
        starts = [p for p, _ in where]
        hits = []
        for s, e in sorted(set(spans)):
            w = where[bisect_right(starts, s) - 1][1]
            hit = {**w, "text": text[s:e], "snippet": snippet(text, s, e)}
            if "ptr" in w:
                ev = _evidence_at(m, rec, w["ptr"])
                if ev:
                    hit["evidence"] = ev
            hits.append(hit)
        items.append({"interp": name, "title": rec["title"], "unofficial": True,
                      "inputs": [i["doc"] for i in rec["inputs"]], **({"stale": True} if stale else {}),
                      "hits": hits[:limit] if limit else hits, **({"more_hits": len(hits) - limit} if limit and len(hits) > limit else {})})
    header = {"type": "search", "of": "interpretations", "query": q, "parsed": Q.to_text(node), "exhaustive": True,
              "coverage": coverage, "matches": {"interpretations": len(items), "hits": sum(len(i["hits"]) for i in items)},
              "note": "hits are in Claude's readings, not in documents: quote the evidence, not the hit"}
    return header, items


def search_list(header: dict, items: list[dict]) -> tuple[dict, list[dict]]:
    """``interp search`` results as a list file (LISTS.md), one row per hit,
    grouped by interpretation. A row's locator is the evidence its record
    points at, so the linked preview opens what can be quoted; a hit with no
    evidence opens the interpretation's first input, and says so. The header
    is marked ``"unofficial": true``, and the list display captions it as
    Claude's readings."""
    head = {"type": "list", "v": 1, "title": f"Claude's readings: {header['query']}",
            "columns": ["where", "snippet", "evidence"], "made_by": "interp search", "query": header["query"],
            "unofficial": True, "note": header["note"],
            "search": {k: header[k] for k in ("of", "parsed", "exhaustive", "coverage") if k in header}}
    rows = []
    for it in items:
        group = f"{it['title']} ({it['interp']})" + (" · stale" if it.get("stale") else "")
        for h in it["hits"]:
            where = (h["ptr"] if "ptr" in h else f"row {h['row']}, {h['column']}" if "row" in h else f"line {h['line']}")
            ev = h.get("evidence")
            rows.append({"loc": ev or f"doc:{it['inputs'][0]}", "group": group,
                         "fields": {"reading": it["interp"], "where": where, "snippet": h["snippet"]["text"],
                                    "evidence": "linked" if ev else "none: opens the input", "unofficial": True}})
    return head, rows


def _nodes(node):
    yield node
    for x in getattr(node, "items", ()):
        yield from _nodes(x)
    for attr in ("item", "left", "right"):
        if hasattr(node, attr):
            yield from _nodes(getattr(node, attr))
