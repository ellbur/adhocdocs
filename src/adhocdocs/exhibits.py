"""Exhibits: documents stamped as numbered exhibits, and the exhibit list.

``produce exhibits`` takes a list file (one exhibit per item, in its order)
and writes, under ``workspace/exhibits/<name>/``:

- ``PDF/<label>.pdf`` per exhibit: a sticker on the first page (the label,
  and the witness and date if given) and the label at the top of every
  page. A document that has been produced is used as produced, so its Bates
  numbers show; otherwise its original pages (or its text set as pages). An
  item whose locator names pages (``#page=3-5``) makes just those pages the
  exhibit.
- ``exhibits.jsonl``: a header, then per exhibit ``number``, ``label``,
  ``doc``, ``description``, ``date``, ``bates`` (first and last, if
  produced), ``pages``, ``page_map`` (exhibit page n is the original's page
  ``page_map[n-1]``, or null when rendered from text), ``file``, ``sha256``.
- ``exhibit-list.csv`` and ``exhibit-list.docx``: number, date,
  description, Bates range, pages.
- ``exhibits.list.jsonl``: the same as a list file, for ``view list`` and
  ``view timeline``.

An item's description is its list field ``description`` (or ``event``),
else the document's title, subject or name; its date is the field
``date``, else the document's.

Numbers run on across sets with the same prefix (a deposition's exhibits
continue from the last one's), and a number is never used twice. ``exh:``
locators resolve through every set: ``exh:12``, ``exh:DX-12``, and
``exh:12#page=3`` for a page of the exhibit (see :func:`resolve_exhibit`).
"""

import csv
import hashlib
import json
import re
from datetime import date as Date
from pathlib import Path

import pymupdf

from . import lists, locators
from .matter import Matter
from .produce import ProductionError, _fields, _productions, pages_for

EXH = re.compile(r"^exh:(?P<label>[^#]+?)(?:#page=(?P<p1>\d+)(?:-(?P<p2>\d+))?)?$")


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", label).strip("_")


def short(label: str) -> str:
    """"Exhibit 12" is cited as "Ex. 12"; other labels (DX-12, PX 4) as they are."""
    return re.sub(r"^Exhibit\s+", "Ex. ", label)


def _sets(m: Matter) -> list[dict]:
    out = []
    for f in sorted((m.workspace / "exhibits").glob("*/exhibits.jsonl")):
        rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        if rows:
            out.append({**rows[0], "records": rows[1:]})
    return out


def _text(page, rect, text, **kw):
    """insert_textbox drops text that doesn't fit, quietly: don't let it."""
    if page.insert_textbox(rect, text, **kw) < 0:
        raise ProductionError(f"{text!r} doesn't fit its stamp")


def sticker(page: pymupdf.Page, label: str, witness: str | None, when: str | None):
    """An exhibit sticker in the bottom right, above where a Bates number goes."""
    r = page.rect
    lines = [x for x in (witness, when) if x]
    height = 4 + 22 + 17 * len(lines) + 4
    box = pymupdf.Rect(r.x1 - 168, r.y1 - 40 - height, r.x1 - 18, r.y1 - 40)
    page.draw_rect(box, color=(0, 0, 0), fill=(1, 1, 1), width=1.2, overlay=True)
    _text(page, pymupdf.Rect(box.x0 + 4, box.y0 + 4, box.x1 - 4, box.y0 + 26), label.upper(),
          fontsize=13, fontname="hebo", align=pymupdf.TEXT_ALIGN_CENTER)
    for i, line in enumerate(lines):
        y = box.y0 + 26 + 17 * i
        _text(page, pymupdf.Rect(box.x0 + 4, y, box.x1 - 4, y + 17), line, fontsize=9, fontname="helv",
              align=pymupdf.TEXT_ALIGN_CENTER)


def page_label(page: pymupdf.Page, label: str, n: int, total: int):
    r = page.rect
    box = pymupdf.Rect(r.x1 - 198, r.y0 + 10, r.x1 - 18, r.y0 + 24)
    page.draw_rect(box, color=None, fill=(1, 1, 1), overlay=True)
    _text(page, box, f"{label} · page {n} of {total}", fontsize=8, fontname="helv", align=pymupdf.TEXT_ALIGN_RIGHT)


def _produced(m: Matter, doc: str):
    """The latest production's copy of a document: (PDF, Bates-page -> original page map, record)."""
    for p in reversed(_productions(m)):
        for r in p["records"]:
            if r["doc"] == doc:
                return m.workspace / "productions" / p["name"] / r["file"], r, p
    return None


def _pages_wanted(sel: dict) -> tuple[int, int] | None:
    if "page" not in sel:
        return None
    pg = sel["page"]
    return (pg, pg) if isinstance(pg, int) else (pg[0], pg[1])


def exhibits(m: Matter, list_file: Path, name: str, prefix: str = "Exhibit ", start: int | None = None,
             witness: str | None = None, when: str | None = None, originals: bool = False) -> dict:
    out = m.workspace / "exhibits" / name
    if out.exists():
        raise ProductionError(f"exhibit set {name} exists; choose another name")
    try:
        return _exhibits(m, out, list_file, name, prefix, start, witness, when, originals)
    except BaseException:
        import shutil
        shutil.rmtree(out, ignore_errors=True)        # all or nothing: no half-made set to trip over
        raise


def _exhibits(m, out, list_file, name, prefix, start, witness, when, originals) -> dict:
    used = sorted(r["number"] for s in _sets(m) if s["prefix"] == prefix for r in s["records"])
    if start is None:
        start = (used[-1] + 1) if used else 1
    _, items = lists.read(list_file)
    clash = [n for n in range(start, start + len(items)) if n in used]
    if clash:
        raise ProductionError(f"{prefix}{clash[0]} is already used; start at {used[-1] + 1}")
    state = m.state
    fields = _fields(m)
    (out / "PDF").mkdir(parents=True)
    records = []
    for i, it in enumerate(items):
        loc = locators.parse(it["loc"], m.objects)
        doc = state.follow(loc["doc"])
        if doc not in state.documents or doc in state.withdrawn:
            raise ProductionError(f"{it['loc']}: not a live document")
        want = _pages_wanted(loc.get("select") or {})
        produced = None if originals else _produced(m, doc)
        bates_pages = None
        if produced:
            path, rec, prod = produced
            if rec.get("withheld"):
                raise ProductionError(f"{fields.get(doc, {}).get('name') or doc} was withheld from production "
                                      f"{prod['name']}; pass --originals to use the original")
            src = pymupdf.open(path)
            page_map = rec["page_map"]
            bm = re.match(r"^(.*?)(\d+)$", rec["begin"])
            bates_pages = [f"{bm.group(1)}{int(bm.group(2)) + k:0{len(bm.group(2))}d}" for k in range(src.page_count)]
        else:
            src, page_map = pages_for(m, doc)
        keep = list(range(src.page_count))
        if want:
            if not page_map:
                raise ProductionError(f"{it['loc']}: this document has no pages of its own to choose from")
            keep = [k for k in keep if want[0] <= page_map[k] <= want[1]]
            if not keep:
                raise ProductionError(f"{it['loc']}: no such pages")
        pdf = pymupdf.open()
        for k in keep:
            pdf.insert_pdf(src, from_page=k, to_page=k)
        n = start + i
        label = f"{prefix}{n}"
        sticker(pdf[0], label, witness, when)
        for k, page in enumerate(pdf, 1):
            page_label(page, label, k, pdf.page_count)
        file = f"PDF/{_slug(label)}.pdf"
        pdf.save(out / file, garbage=3, deflate=True)
        f = it.get("fields") or {}
        row = fields.get(doc, {})
        records.append({
            "number": n, "label": label, "doc": doc,
            "description": f.get("description") or f.get("event") or row.get("title") or row.get("subject") or row.get("name"),
            "date": (f.get("date") or row.get("date") or "")[:10] or None,
            "bates": [bates_pages[keep[0]], bates_pages[keep[-1]]] if bates_pages else None,
            "pages": len(keep), "page_map": [page_map[k] for k in keep] if page_map else None,
            "file": file, "sha256": "sha256:" + hashlib.sha256((out / file).read_bytes()).hexdigest(),
        })
    header = {"type": "exhibits", "v": 1, "name": name, "prefix": prefix, "witness": witness, "date": when,
              "first": records[0]["label"] if records else None, "last": records[-1]["label"] if records else None,
              "exhibits": len(records), "list": str(list_file), "made": Date.today().isoformat()}
    (out / "exhibits.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [header, *records]))
    _exhibit_list(out, header, records)
    ev = m.logs["workspace"].append(op="export", kind="exhibits", name=name, first=header["first"], last=header["last"],
                                    record="sha256:" + hashlib.sha256((out / "exhibits.jsonl").read_bytes()).hexdigest())
    return {**header, "path": str(out), "event": ev["id"]}


def _bates_range(b) -> str:
    if not b:
        return ""
    from .pages import bates_range
    return bates_range(*b)


def _exhibit_list(out: Path, header: dict, records: list):
    cols = ("Exhibit", "Date", "Description", "Bates", "Pages")
    rows = [(r["label"], r["date"] or "", r["description"] or "", _bates_range(r["bates"]), str(r["pages"])) for r in records]
    with open(out / "exhibit-list.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)

    import docx
    d = docx.Document()
    d.add_heading("Exhibit list", level=1)
    sub = " · ".join(x for x in (header.get("witness"), header.get("date"), header["name"]) if x)
    d.add_paragraph(sub)
    t = d.add_table(rows=1 + len(rows), cols=len(cols))
    t.style = "Table Grid"
    for j, c in enumerate(cols):
        t.cell(0, j).text = c
        t.cell(0, j).paragraphs[0].runs[0].bold = True
    for i, row in enumerate(rows, 1):
        for j, v in enumerate(row):
            t.cell(i, j).text = v
    d.save(str(out / "exhibit-list.docx"))

    items = []
    for r in records:
        loc = {"doc": r["doc"]}
        if r["page_map"]:
            loc["select"] = {"page": r["page_map"][0] if len(set(r["page_map"])) == 1 else [r["page_map"][0], r["page_map"][-1]]}
        items.append({"loc": locators.format(loc), "fields": {"exhibit": r["label"], "date": r["date"], "description": r["description"],
                                                              "event": f"{r['label']}: {r['description']}", "bates": _bates_range(r["bates"])}})
    lists.write(out / "exhibits.list.jsonl", {"type": "list", "v": 1, "title": f"Exhibits: {header['name']}",
                                              "columns": ["exhibit", "date", "description", "bates"], "made_by": "produce exhibits"}, items)


def _match(label: str, want: str) -> bool:
    norm = lambda s: re.sub(r"[\s.]+", "", s).lower()
    if want.isdigit():
        return re.sub(r"^\D*", "", label) == want
    return norm(label) == norm(want) or norm(short(label)) == norm(want)


def resolve_exhibit(m: Matter, s: str) -> list[dict]:
    """``exh:12``, ``exh:DX-12`` or ``exh:Ex.12`` (spaces and dots don't
    matter), with ``#page=3`` or ``#page=3-4`` for pages of the exhibit, to
    a locator on the original document's pages."""
    mt = EXH.match(s.strip())
    if not mt:
        raise locators.LocatorError(f"not an exhibit locator: {s}")
    want = mt.group("label").strip()
    hits = [(st, r) for st in _sets(m) for r in st["records"] if _match(r["label"], want)]
    labels = sorted({r["label"] for _, r in hits})
    if not hits:
        raise locators.LocatorError(f"no exhibit {want}")
    if len(labels) > 1:
        raise locators.LocatorError(f"exh:{want} could be {', '.join(labels)}; name it in full, e.g. exh:{labels[0].replace(' ', '')}")
    st, r = hits[-1]
    loc = {"doc": r["doc"]}
    p1 = int(mt.group("p1")) if mt.group("p1") else None
    p2 = int(mt.group("p2")) if mt.group("p2") else p1
    if p1:
        if not 1 <= p1 <= p2 <= r["pages"]:
            raise locators.LocatorError(f"{r['label']} has {r['pages']} pages")
        if r["page_map"]:
            a, b = r["page_map"][p1 - 1], r["page_map"][p2 - 1]
            loc["select"] = {"page": a if a == b else [a, b]}
    elif r["page_map"]:
        a, b = r["page_map"][0], r["page_map"][-1]
        loc["select"] = {"page": a if a == b else [a, b]}
    return [{"loc": loc, "exhibit": r["label"], "set": st["name"], **({"pages": [p1, p2]} if p1 else {})}]


def exhibit_for(m: Matter, doc: str, pages: list[int] | None, only: str | None = None) -> dict | None:
    """The exhibit a document (and these original pages) became, with the
    exhibit's own page numbers: from the latest set that has it (or ``only``)."""
    for st in reversed(_sets(m)):
        if only and st["name"] != only:
            continue
        for r in st["records"]:
            if r["doc"] != doc:
                continue
            if pages and r["page_map"]:
                idx = [r["page_map"].index(p) + 1 for p in pages if p in r["page_map"]]
                if not idx:
                    continue
                return {"label": r["label"], "pages": [min(idx), max(idx)], "set": st["name"]}
            return {"label": r["label"], "pages": None, "set": st["name"]}
    return None
