"""Productions: Bates-stamped PDFs, a production record, and load files.

``produce`` takes a list file and writes, under
``workspace/productions/<name>/``:

- ``PDF/<BEGBATES>.pdf`` per document: the original pages for PDFs and
  images, or the text layer rendered to pages for anything else, each page
  stamped with its Bates number (and an optional legend)
- ``production.jsonl``: a header, then per document ``doc``, ``begin``,
  ``end``, ``pages``, ``file``, ``sha256``, and ``page_map`` (Bates page n
  is the original's physical page ``page_map[n]``, or null when the pages
  were rendered from text)
- ``<name>.dat`` and ``<name>.opt``: Concordance and Opticon load files
- ``privlog.csv`` and ``privlog.jsonl``: withheld and redacted documents

Documents tagged ``privileged`` (by default) are withheld: a slip sheet
takes their Bates number. Ranges and page regions tagged with a tag starting
``redact`` (``redact``, ``redact-privilege``…) are redacted: MuPDF removes
the text beneath on pages, and rendered text has the range replaced.

A production is work product in the workspace. The Bates numbers it assigns
attach to documents from outside, and ``bates:`` locators resolve through
every production record (see :func:`resolve_bates`).
"""

import hashlib
import io
import json
import re
from datetime import date
from pathlib import Path

import pymupdf

from . import lists, locators
from .matter import Matter

LETTER = pymupdf.paper_rect("letter")
DAT_FIELDS = ("BEGBATES", "ENDBATES", "BEGATTACH", "ENDATTACH", "CUSTODIAN", "FILENAME", "DATE", "FROM", "TO",
              "SUBJECT", "PAGES", "MD5HASH", "NATIVEPATH")
BATES = re.compile(r"^([A-Za-z_\-]*?)(\d+)$")


class ProductionError(Exception):
    pass


def bates(prefix: str, n: int, digits: int) -> str:
    return f"{prefix}{n:0{digits}d}"


def _render_text(text: str) -> pymupdf.Document:
    """A layer's text set as pages, page breaks kept."""
    out = pymupdf.open()
    for page_text in text.split("\f"):
        story = pymupdf.Story(html="<pre style='font-family: monospace; font-size: 9pt; white-space: pre-wrap'>"
                              + (page_text.replace("&", "&amp;").replace("<", "&lt;") or " ") + "</pre>")
        buf = io.BytesIO()
        writer = pymupdf.DocumentWriter(buf)
        more = True
        while more:
            dev = writer.begin_page(LETTER)
            more, _ = story.place(LETTER + (54, 54, -54, -72))
            story.draw(dev)
            writer.end_page()
        writer.close()
        out.insert_pdf(pymupdf.open("pdf", buf.getvalue()))
    return out


def geometry(m: Matter, layer_id: str) -> list[dict]:
    """A layer's word boxes; a derived layer's come from its source through
    the offset map."""
    try:
        obj = m.objects.get_object(layer_id)
    except KeyError:
        return []
    if obj.get("geometry"):
        return m.objects.get_jsonl(obj["geometry"])
    if not obj.get("derived_from"):
        return []
    runs = m.objects.get_jsonl(obj["offset_map"])
    out = []
    for g in geometry(m, obj["derived_from"]):
        for r in runs:
            if r["source_start"] <= g["start"] and g["end"] <= r["source_start"] + r["end"] - r["start"]:
                d = r["start"] - r["source_start"]
                out.append({**g, "start": g["start"] + d, "end": g["end"] + d})
                break
    return out


def _label(tag: str) -> str:
    reason = tag.removeprefix("redact").strip("-:_ ")
    return f"REDACTED – {reason.replace('-', ' ').title()}" if reason else "REDACTED"


def pages_for(m: Matter, doc: str, redactions=()) -> tuple[pymupdf.Document, list | None]:
    """(PDF to produce, page map). Originals with pages keep them; anything
    else is rendered from its primary layer. ``redactions`` are tag
    applications on ranges or regions of this document: on pages they are
    applied with MuPDF's redaction, which removes the text beneath, and in
    rendered text the range is replaced. A redaction that can't be placed
    stops the production rather than letting the text through."""
    state = m.state
    content = state.documents[doc]
    meta = m.objects.get_object(state.metadata[doc]) if doc in state.metadata else {}
    data = m.objects.get_bytes(content)
    if meta.get("kind") in ("pdf", "image"):
        if meta["kind"] == "pdf":
            d = pymupdf.open("pdf", data)
        else:
            img = pymupdf.open(stream=data, filetype=(meta.get("name") or "x.png").rsplit(".", 1)[-1])
            d = pymupdf.open("pdf", img.convert_to_pdf())
        for app in redactions:
            sel = app["target"].get("select") or {}
            rects = []
            if "xywh" in sel:
                x, y, w, h = sel["xywh"]
                page = sel["page"] if isinstance(sel["page"], int) else sel["page"][0]
                rects.append((page, (x, y, x + w, y + h)))
            elif "char" in sel:
                s_, e_ = sel["char"]
                rects += [(g["page"], g["box"]) for g in geometry(m, app["target"]["layer"])
                          if g["start"] < e_ and s_ < g["end"]]
            if not rects:
                raise ProductionError(f"can't place redaction {app['tag']!r} on {meta.get('name')}'s pages")
            for page_no, (x0, y0, x1, y1) in rects:
                page = d[page_no - 1]
                r = page.rect
                page.add_redact_annot(pymupdf.Rect(r.x0 + x0 * r.width, r.y0 + y0 * r.height,
                                                   r.x0 + x1 * r.width, r.y0 + y1 * r.height),
                                      text=_label(app["tag"]), fill=(0, 0, 0), text_color=(1, 1, 1), fontsize=6)
        for page in d:
            page.apply_redactions()
        return d, list(range(1, d.page_count + 1))
    layer = state.primary_layer(content)
    text = m.objects.get_text(layer.text) if layer else ""
    cuts = []
    for app in redactions:
        t = app["target"]
        sel = t.get("select") or {}
        same_text = t.get("layer") and m.objects.get_object(t["layer"])["text"] == (layer.text if layer else None)
        if "char" not in sel or not same_text:
            raise ProductionError(f"can't place redaction {app['tag']!r} in {meta.get('name')}'s text")
        cuts.append((sel["char"][0], sel["char"][1], _label(app["tag"])))
    for s_, e_, lbl in sorted(cuts, reverse=True):
        text = text[:s_] + f"[{lbl}]" + text[e_:]
    if not text.strip():
        text = f"[{meta.get('name') or 'document'}: no text; produced natively]"
    return _render_text(text), None


def slip_sheet(reason: str) -> pymupdf.Document:
    d = pymupdf.open()
    page = d.new_page(width=LETTER.width, height=LETTER.height)
    page.insert_textbox(pymupdf.Rect(72, 300, LETTER.width - 72, 420), f"Document withheld\n{reason}",
                        fontsize=18, fontname="helv", align=pymupdf.TEXT_ALIGN_CENTER)
    return d


def stamp(page: pymupdf.Page, number: str, legend: str | None):
    r = page.rect
    box = pymupdf.Rect(r.x1 - 200, r.y1 - 28, r.x1 - 18, r.y1 - 10)
    page.draw_rect(box, color=None, fill=(1, 1, 1), overlay=True)
    page.insert_textbox(box, number, fontsize=10, fontname="helv", align=pymupdf.TEXT_ALIGN_RIGHT)
    if legend:
        lbox = pymupdf.Rect(r.x0 + 18, r.y1 - 28, r.x0 + 300, r.y1 - 10)
        page.draw_rect(lbox, color=None, fill=(1, 1, 1), overlay=True)
        page.insert_textbox(lbox, legend, fontsize=10, fontname="helv")


def produce(m: Matter, list_file: Path, name: str, prefix: str, start: int = 1, digits: int = 6,
            legend: str | None = None, families=True, withhold=("privileged",), redact="redact") -> dict:
    """Produce the documents of a list, in its order; with ``families``,
    each document's attachments follow it, as is usual. Documents tagged
    with a ``withhold`` tag become a slip sheet; ranges and regions tagged
    with a tag starting ``redact`` are redacted. Both go in the privilege
    log."""
    out = m.workspace / "productions" / name
    if out.exists():
        raise ProductionError(f"production {name} exists; choose another name")
    used = [p["end_n"] for p in _productions(m) if p["prefix"] == prefix]
    if used and start <= max(used):
        raise ProductionError(f"{prefix} numbers up to {bates(prefix, max(used), digits)} are already used; "
                              f"start at {max(used) + 1}")
    _, items = lists.read(list_file)
    state = m.state
    listed = [state.follow(locators.parse(it["loc"], m.objects)["doc"]) for it in items]
    listed_set = set(listed)
    order = []
    for doc in listed:
        if families and any(_is_descendant(state, doc, other) for other in listed_set if other != doc):
            continue                          # produced with its parent, right after it
        for d in [doc] + (_descendants(state, doc) if families else []):
            if d not in order and d not in state.withdrawn:
                order.append(d)
    from .annotations import Annotations
    ann = Annotations.load(m)
    whole = {}                                  # doc -> withhold tag
    ranged = {}                                 # doc -> redaction applications
    for app in ann.applications():
        t = app["target"]
        if not t.get("select") and app["tag"] in withhold:
            whole.setdefault(t["doc"], app["tag"])
        elif t.get("select") and redact and app["tag"].startswith(redact):
            ranged.setdefault(t["doc"], []).append(app)
    (out / "PDF").mkdir(parents=True)
    fields = _fields(m)
    n = start
    records = []
    for doc in order:
        if doc in whole:
            desc = (ann.definitions.get(whole[doc]) or {}).get("description")
            pdf, page_map = slip_sheet(f"{whole[doc].title()}" + (f": {desc}" if desc else "")), None
        else:
            pdf, page_map = pages_for(m, doc, ranged.get(doc, ()))
        begin = bates(prefix, n, digits)
        for page in pdf:
            stamp(page, bates(prefix, n, digits), legend)
            n += 1
        end = bates(prefix, n - 1, digits)
        path = out / "PDF" / f"{begin}.pdf"
        pdf.save(path, garbage=3, deflate=True)
        rec = {"doc": doc, "begin": begin, "end": end, "pages": pdf.page_count, "file": f"PDF/{begin}.pdf",
               "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), "page_map": page_map,
               "parent": _parent(state, doc)}
        if doc in whole:
            rec["withheld"] = whole[doc]
        if doc in ranged and doc not in whole:
            rec["redactions"] = sorted({a["tag"] for a in ranged[doc]})
        records.append(rec)
    for r in records:                       # families: BEGATTACH/ENDATTACH span the top parent's family
        top = r["doc"]
        while _parent(state, top) in {x["doc"] for x in records}:
            top = _parent(state, top)
        fam = [x for x in records if x["doc"] == top or _is_descendant(state, x["doc"], top)]
        r["begattach"], r["endattach"] = fam[0]["begin"], fam[-1]["end"]
    header = {"type": "production", "v": 1, "name": name, "prefix": prefix, "digits": digits,
              "begin": records[0]["begin"] if records else None, "end": records[-1]["end"] if records else None,
              "documents": len(records), "pages": n - start, "legend": legend, "list": str(list_file),
              "date": date.today().isoformat()}
    (out / "production.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [header, *records]))
    _load_files(out, name, records, fields)
    _privilege_log(m, out, records, fields, ann)
    ev = m.logs["workspace"].append(op="export", kind="production", name=name, begin=header["begin"], end=header["end"],
                                    record="sha256:" + hashlib.sha256((out / "production.jsonl").read_bytes()).hexdigest())
    return {**header, "path": str(out), "event": ev["id"]}


def _fields(m):
    from .index import open_index
    ix = open_index(m, "fields")
    ix.update()
    return ix.rows()


def _parent(state, doc):
    for p, kids in state.children.items():
        if doc in kids:
            return p
    return None


def _descendants(state, doc) -> list:
    out = []
    for c in state.children.get(doc, []):
        out += [c] + _descendants(state, c)
    return out


def _is_descendant(state, doc, top) -> bool:
    p = _parent(state, doc)
    while p:
        if p == top:
            return True
        p = _parent(state, p)
    return False


def _load_files(out: Path, name: str, records: list, fields: dict):
    """Concordance .dat (þ quote, ¶ separator… as the format expects: \\x14
    between fields, þ around them) and Opticon .opt (one line per page)."""
    q, sep = "þ", "\u0014"
    lines = [sep.join(q + f + q for f in DAT_FIELDS)]
    opt = []
    for r in records:
        row = fields.get(r["doc"], {})
        values = {"BEGBATES": r["begin"], "ENDBATES": r["end"], "BEGATTACH": r["begattach"], "ENDATTACH": r["endattach"],
                  "CUSTODIAN": row.get("custodian") or "", "FILENAME": row.get("name") or "",
                  "DATE": (row.get("date") or "")[:10], "FROM": "; ".join(row.get("from") or []),
                  "TO": "; ".join(row.get("to") or []), "SUBJECT": row.get("subject") or "", "PAGES": str(r["pages"]),
                  "MD5HASH": "", "NATIVEPATH": ""}
        lines.append(sep.join(q + values[f].replace(q, "") + q for f in DAT_FIELDS))
        m_ = BATES.match(r["begin"])
        prefix, n = m_.group(1), int(m_.group(2))
        for i in range(r["pages"]):
            first = "Y" if i == 0 else ""
            opt.append(f"{bates(prefix, n + i, len(m_.group(2)))},{name},{r['file']},{first},,,{r['pages'] if i == 0 else ''}")
    (out / f"{name}.dat").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    (out / f"{name}.opt").write_text("\r\n".join(opt) + "\r\n", encoding="utf-8")


PRIVLOG_FIELDS = ("bates", "date", "author", "recipients", "cc", "type", "description", "treatment", "privilege", "basis")


def _privilege_log(m, out: Path, records: list, fields: dict, ann):
    """One row per withheld or redacted document. The basis is the tag's
    description plus the user's notes on the document; review it before
    serving."""
    import csv
    rows = []
    for r in records:
        tags = [r["withheld"]] if r.get("withheld") else r.get("redactions", [])
        if not tags:
            continue
        f = fields.get(r["doc"], {})
        notes = [n["body"].strip() for n in ann.notes.values()
                 if n["target"].get("doc") == r["doc"] and n["by"].startswith("user:")]
        basis = "; ".join(filter(None, [*(ann.definitions.get(t, {}).get("description") for t in tags), *notes]))
        rows.append({"bates": r["begin"] if r["begin"] == r["end"] else f"{r['begin']}–{r['end']}",
                     "date": (f.get("date") or "")[:10], "author": "; ".join(f.get("from") or []) or (f.get("author") or ""),
                     "recipients": "; ".join(f.get("to") or []), "cc": "; ".join(f.get("cc") or []),
                     "type": f.get("kind") or "", "description": f.get("subject") or f.get("title") or f.get("name") or "",
                     "treatment": "withheld" if r.get("withheld") else "redacted",
                     "privilege": ", ".join(t.removeprefix("redact").strip("-:_ ") or t for t in tags), "basis": basis})
    with open(out / "privlog.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PRIVLOG_FIELDS)
        w.writeheader()
        w.writerows(rows)
    (out / "privlog.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


# -- resolving Bates numbers -----------------------------------------------------------------------

def _productions(m: Matter) -> list[dict]:
    out = []
    for f in sorted((m.workspace / "productions").glob("*/production.jsonl")):
        rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        if not rows or not rows[0].get("begin"):
            continue
        head = rows[0]
        mb, me = BATES.match(head["begin"]), BATES.match(head["end"])
        out.append({**head, "begin_n": int(mb.group(2)), "end_n": int(me.group(2)), "records": rows[1:]})
    return out


def resolve_bates(m: Matter, s: str) -> list[dict]:
    """``bates:ABC000123`` or ``bates:ABC000123-ABC000130`` to locators, one
    per document the range touches. Pages map back to the original's pages
    where it had them; documents rendered from text resolve whole."""
    spec = s.removeprefix("bates:")
    pair = re.match(r"^([A-Za-z_\-]*?\d+)-([A-Za-z_\-]*?\d+)$", spec)
    first, last = (pair.group(1), pair.group(2)) if pair else (spec, spec)
    a, b = BATES.match(first), BATES.match(last)
    if not a or not b or a.group(1) != b.group(1):
        raise locators.LocatorError(f"not a Bates number or range: {s}")
    prefix, lo, hi = a.group(1), int(a.group(2)), int(b.group(2))
    out = []
    for p in _productions(m):
        if p["prefix"] != prefix or hi < p["begin_n"] or lo > p["end_n"]:
            continue
        for r in p["records"]:
            rb, re_ = int(BATES.match(r["begin"]).group(2)), int(BATES.match(r["end"]).group(2))
            if hi < rb or lo > re_:
                continue
            loc = {"doc": r["doc"]}
            if r["page_map"]:
                first_page = r["page_map"][max(lo, rb) - rb]
                last_page = r["page_map"][min(hi, re_) - rb]
                loc["select"] = {"page": first_page if first_page == last_page else [first_page, last_page]}
            out.append({"loc": loc, "bates": [bates(prefix, max(lo, rb), p["digits"]), bates(prefix, min(hi, re_), p["digits"])],
                        "production": p["name"]})
    if not out:
        raise locators.LocatorError(f"{s} is not in any production")
    return out


def bates_for(m: Matter, doc: str, pages: list[int] | None) -> list[str] | None:
    """The Bates numbers a document's pages were produced as (first and
    last), from the latest production that has it."""
    for p in reversed(_productions(m)):
        for r in p["records"]:
            if r["doc"] != doc:
                continue
            m_ = BATES.match(r["begin"])
            base, digits = int(m_.group(2)), len(m_.group(2))
            if pages is None or not r["page_map"]:
                return [r["begin"], r["end"]]
            idx = [r["page_map"].index(pg) for pg in pages if pg in r["page_map"]]
            if idx:
                return [bates(p["prefix"], base + min(idx), digits), bates(p["prefix"], base + max(idx), digits)]
    return None
