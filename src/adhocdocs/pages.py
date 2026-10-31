"""Literate pages: render, check and export (see literate.py for the format).

A page is a Markdown file with optional YAML front matter (``title``,
``cite-style``, ``cite-exhibits``), directives for quotations, and a ``refs`` block at the
end. The renderer fills in every quotation from the document; the source
has no way to supply quotation text.

Citations come from the reference's locator, the document's cite-as name
and the page's ``cite-style``. Cite-as names live in
``workspace/cite-as.jsonl`` (``{"doc": ..., "cite": "Müller Email"}``),
outside the document's identity; without one, a name is made from the
metadata. A cite style is ``default`` or the path of a Python file defining
``cite(info: dict) -> str``.
"""

import html
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin

from . import literate, locators
from .literate import Resolved
from .matter import Matter
from .objects import sha256

MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


class PageError(Exception):
    pass


class Page:
    def __init__(self, m: Matter, path):
        self.m = m
        self.path = Path(path)
        self.source = self.path.read_text()
        fm = FRONT.match(self.source)
        self.meta = (yaml.safe_load(fm.group(1)) or {}) if fm else {}
        body = self.source[fm.end():] if fm else self.source
        self.prose, refs, self.ref_problems, _ = literate.split(body)
        self.refs = {r["id"]: r for r in refs}
        self._resolved = {}
        self._cite_as = None
        self._meta = {}
        self._styles = {}
        self._shown = {}
        self.images = {}        # name -> PNG, for pictures of pages in the last render

    def resolved(self, rid) -> Resolved:
        if rid not in self._resolved:
            ref = self.refs.get(rid)
            self._resolved[rid] = literate.resolve(self.m, ref) if ref else Resolved(rid, "broken", error="no such reference")
        return self._resolved[rid]

    def styles(self, r: Resolved) -> tuple[list, str | None]:
        if r.layer.id not in self._styles:
            from .quoting import styles_for
            self._styles[r.layer.id] = styles_for(self.m, r.doc, r.layer)
        return self._styles[r.layer.id]

    def shown(self, d, r: Resolved) -> dict:
        """How a quotation directive shows its reference: ``as``, an error,
        and for pictures their crops, for formatted text its runs and source."""
        key = (d.id, tuple(sorted(d.opts.items())))
        if key not in self._shown:
            want, err = literate.shown_as(self.m, d, r)
            out = {"as": want, "error": err}
            if not err and want == "image":
                from .quoting import crops
                out["crops"] = crops(self.m, r, highlight=d.opts.get("highlight") in ("yes", "true", "1"))
            if not err and want == "rich":
                out["styles"], out["source"] = self.styles(r)
            self._shown[key] = out
        return self._shown[key]

    def check(self) -> list[dict]:
        return literate.check(self.m, self.source[FRONT.match(self.source).end():] if FRONT.match(self.source) else self.source)

    # -- citations -----------------------------------------------------------------------

    def cite_as(self, doc: str) -> str | None:
        if self._cite_as is None:
            self._cite_as = {}
            f = self.m.workspace / "cite-as.jsonl"
            if f.exists():
                for line in f.read_text().splitlines():
                    if line.strip():
                        row = json.loads(line)
                        self._cite_as[locators.parse("doc:" + row["doc"].removeprefix("doc:"), self.m.objects)["doc"]] = row["cite"]
        return self._cite_as.get(doc)

    def doc_meta(self, doc: str) -> dict:
        if doc not in self._meta:
            h = self.m.state.metadata.get(doc)
            self._meta[doc] = self.m.objects.get_object(h) if h else {}
        return self._meta[doc]

    def cite_info(self, r: Resolved, signal=None) -> dict:
        """What a cite style gets: the document's name and kind, date, pages,
        page:line range, printed page labels, and whether emphasis was added."""
        meta = self.doc_meta(r.doc)
        info = {"doc": r.doc, "cite_as": self.cite_as(r.doc), "kind": meta.get("kind"), "name": meta.get("name"),
                "title": meta.get("title"), "date": meta.get("date"), "from": meta.get("from_names") or meta.get("from"),
                "to": meta.get("to_names") or meta.get("to"), "signal": signal,
                "emphasis_added": any("emphasis" in a for a in r.alter)}
        if r.quoted:
            segs = self.m.objects.get_jsonl(self.m.objects.get_object(r.layer.id).get("segments"))
            lines = [s["label"] for s in segs if s["kind"] == "line" and any(s["start"] < p.end and p.start < s["end"] for p in r.parts)]
            if lines:
                info["lines"] = [lines[0], lines[-1]]
            if "\f" in r.text:
                info["pages"] = [locators.page_of(r.text, r.parts[0].start), locators.page_of(r.text, r.parts[-1].end - 1)]
                labels = {}
                for s in segs:
                    if s["kind"] == "page-label":
                        labels[locators.page_of(r.text, s["start"])] = s["label"]
                if all(p in labels for p in info["pages"]):
                    info["page_labels"] = [labels[p] for p in info["pages"]]
        else:
            sel = (self.refs.get(r.id) or {}).get("select") or {}
            if "cells" in sel:
                info["cells"], info["sheet"] = sel["cells"], sel.get("sheet")
            if "page" in sel:
                info["pages"] = sel["page"] if isinstance(sel["page"], list) else [sel["page"], sel["page"]]
        from .produce import bates_for
        pages = list(range(info["pages"][0], info["pages"][1] + 1)) if info.get("pages") else None
        info["bates"] = bates_for(self.m, r.doc, pages)
        want = self.meta.get("cite-exhibits")
        if want:
            from .exhibits import exhibit_for
            info["exhibit"] = exhibit_for(self.m, r.doc, pages, None if want is True else str(want))
        return info

    def cite(self, r: Resolved, signal=None) -> str:
        style = self.meta.get("cite-style", "default")
        info = self.cite_info(r, signal)
        if style == "default":
            return default_cite(info)
        spec = importlib.util.spec_from_file_location("cite_style", (self.path.parent / style).resolve())
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return html.escape(mod.cite(info))


def _date(d: str | None) -> str | None:
    if not d:
        return None
    try:
        t = datetime.fromisoformat(d)
    except ValueError:
        return None
    return f"{MONTHS[t.month - 1]} {t.day}, {t.year}"


def _range(a: str, b: str) -> str:
    if a == b:
        return a
    pa, _, la = a.partition(":")
    pb, _, lb = b.partition(":")
    if la and pa == pb:
        return f"{a}–{lb}"
    return f"{a}–{b}"


def default_name(info: dict) -> str:
    if info.get("cite_as"):
        return info["cite_as"]
    title = info.get("title") or ""
    m = re.match(r"Deposition of ([^-–,(]+)", title)
    if m:
        return f"{m.group(1).split()[-1]} Dep."
    if info.get("kind") == "email":
        who = info.get("from") or []
        to = info.get("to") or []
        s = "Email"
        if who:
            s += f" from {who[0]}"
        if to:
            s += f" to {to[0]}"
        return s
    if title:
        return title
    name = info.get("name") or info["doc"][7:19]
    return name.rsplit(".", 1)[0]


def bates_range(a: str, b: str) -> str:
    """HFS000124–25: the end keeps only the digits that change (at least two)."""
    if a == b:
        return a
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return f"{a}–{b[min(i, len(b) - 2):]}"


def default_cite(info: dict) -> str:
    """Jones Dep. 14:3–15:7 · Ex. 12 at 3 (with ``cite-exhibits: true``, or
    the name of an exhibit set) · HFS000124–25 (a produced document's pages) ·
    Warehouse Log at 2 · Email from Zoë Müller to Dana Okafor (Mar. 9, 2026),
    with "(emphasis added)" when needed."""
    s = default_name(info)
    ex = info.get("exhibit")
    if ex and not info.get("lines"):
        from .exhibits import short
        s = short(ex["label"])
        if ex.get("pages"):
            a, b = ex["pages"]
            s += f" at {a}" if a == b else f" at {a}–{b}"
        if info.get("emphasis_added"):
            s += " (emphasis added)"
        return f"<i>{html.escape(info['signal'])}</i> " + html.escape(s) if info.get("signal") else html.escape(s)
    if info.get("bates") and not info.get("lines"):
        s = bates_range(*info["bates"])
        if info.get("emphasis_added"):
            s += " (emphasis added)"
        return f"<i>{html.escape(info['signal'])}</i> " + html.escape(s) if info.get("signal") else html.escape(s)
    if info.get("lines"):
        s += " " + _range(*info["lines"])
    elif info.get("pages") and (info.get("kind") in ("pdf", "image") or info.get("page_labels")):
        a, b = info.get("page_labels") or info["pages"]
        s += f" at {a}" if a == b else f" at {a}–{b}"
    elif info.get("cells"):
        s += f" ({'sheet ' + info['sheet'] + ', ' if info.get('sheet') else ''}cells {info['cells']})"
    elif info.get("kind") == "email" and _date(info.get("date")):
        s += f" ({_date(info['date'])})"
    if info.get("emphasis_added"):
        s += " (emphasis added)"
    if info.get("signal"):
        s = f"<i>{html.escape(info['signal'])}</i> " + html.escape(s)
    else:
        s = html.escape(s)
    return s


# -- rendering -------------------------------------------------------------------------------

def _loc(r: Resolved, s=None, e=None) -> str:
    if s is None:
        return "doc:" + r.doc
    return locators.format({"doc": r.doc, "layer": r.layer.id, "select": {"char": [s, e]}})


STYLE_TAGS = (("b", "b"), ("i", "i"), ("u", "u"), ("s", "s"), ("sup", "sup"), ("sub", "sub"))


def quote_html(page: Page, r: Resolved, block: bool, layout: str, styles=()) -> str:
    """Each run of document text is a ``data-text`` span the viewer fills in
    from the store; the text inside is the renderer's own copy, for display
    outside the viewer. Marks are the renderer's, styled apart. With
    ``styles`` (as=rich), runs carry their formatting from the original."""
    out = []
    for p in literate.pieces(r, layout=layout, inline=not block, styles=styles):
        if "mark" in p:
            out.append(f'<span class="adhoc-mark">{html.escape(p["mark"])}</span>')
        else:
            t = html.escape(p["text"])
            if layout == "lines":
                t = t.replace("\n", "<br>\n")
            span = f'<span data-text="{_loc(r, p["start"], p["end"])}" data-normalize="{"lines" if layout == "lines" else ("block" if block else "inline")}">{t}</span>'
            for key, tag in STYLE_TAGS:
                if key in p.get("style", ()):
                    span = f"<{tag}>{span}</{tag}>"
            out.append(f"<em>{span}</em>" if p["em"] else span)
    classes = "adhoc-quote" + (" adhoc-rich" if styles else "") + (" adhoc-low-confidence" if r.low_confidence else "") + (" adhoc-reanchored" if r.status == "re-anchored" else "")
    body = "".join(out)
    href = _loc(r, r.parts[0].start, r.parts[-1].end)
    if block:
        return f'<blockquote class="{classes}" data-ref="{r.id}" data-status="{href}"><a href="loc:{href}">{body}</a></blockquote>'
    return f'<q class="{classes}" data-ref="{r.id}" data-status="{href}"><a href="loc:{href}">{body}</a></q>'


def _sel_loc(r: Resolved) -> str:
    return locators.format({"doc": r.doc, **({"layer": r.layer.id} if r.layer else {}), "select": r.select})


def image_html(page: Page, r: Resolved, crops, images: str) -> str:
    """Pictures of the page; ``images`` is "data" (inline, for a file that
    stands alone) or "files" (img/<hash>.png, for a bundle)."""
    import base64
    from .objects import sha256
    out = []
    for c in crops:
        if images == "data":
            src = "data:image/png;base64," + base64.b64encode(c.png).decode()
        else:
            src = f"img/{sha256(c.png)[7:23]}.png"
            page.images[src] = c.png
        href = _loc(r, r.parts[0].start, r.parts[-1].end) if r.quoted else _sel_loc(r)
        out.append(f'<a href="loc:{href}"><img src="{src}" alt="{html.escape(c.alt)}" title="page {c.page}"></a>')
    if any(c.rendered for c in crops):
        out.append(f'<figcaption class="adhoc-mark">{RENDERED_NOTE}</figcaption>')
    return f'<figure class="adhoc-quote adhoc-image" data-ref="{r.id}">{"".join(out)}</figure>'


RENDERED_NOTE = "Set from the document's text by adhocdocs, not its original layout."


def table_html(r: Resolved) -> str:
    width = max(len(row) for row in r.cells)
    rows = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row + [""] * (width - len(row))) + "</tr>"
                   for row in r.cells)
    return (f'<figure class="adhoc-quote adhoc-cells" data-ref="{r.id}"><a href="loc:{_sel_loc(r)}">'
            f'<table>{rows}</table></a></figure>')


def cells_text(r: Resolved) -> str:
    """Cells shown inline: each row's cells joined by "; ", rows by " / "."""
    return " / ".join("; ".join(c for c in row if c) for row in r.cells)


def broken_html(rid: str, error: str | None) -> str:
    return f'<span class="adhoc-broken" data-ref="{html.escape(rid)}">[broken reference: {html.escape(rid)}' + \
        (f" ({html.escape(error)})" if error else "") + "]</span>"


def prepare(page: Page) -> tuple[str, dict]:
    """The prose with each directive replaced by a placeholder token, and
    token -> (kind, directive, resolution)."""
    prose = page.prose
    fills = {}
    ds = literate.directives(prose)
    out, pos = [], 0
    for i, d in enumerate(ds):
        out.append(prose[pos:d.start])
        pos = d.end
        token = f"ADHOCFILL{i}X"
        r = page.resolved(d.id)
        if d.kind == "link":
            fills[token] = ("link", d, r)
            out.append(f"](ADHOCLINK{i}X)")
            continue
        fills[token] = ("node", d, r)
        out.append(f"\n\n{token}\n\n" if d.kind == "block" else token)
    out.append(prose[pos:])
    return "".join(out), fills


def markdown() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False}).use(footnote_plugin).enable("table")


def layout_of(d, r, page=None) -> str:
    """Block quotations keep line breaks for transcripts, and for formatted
    Word text, where each paragraph is a line."""
    if d.opts.get("layout"):
        return d.opts["layout"]
    if d.kind != "block" or not r.layer:
        return "flow"
    if r.layer.view == "transcript":
        return "lines"
    if d.opts.get("as") == "rich" and page and page.doc_meta(r.doc).get("kind") == "docx":
        return "lines"
    return "flow"


def render(page: Page, standalone=True, images="data") -> tuple[str, list[dict]]:
    """Static HTML for a page pane (or a browser), and the check results.
    Pictures of pages are inline (``images="data"``) or, for a bundle the
    viewer can show, files named in ``page.images`` (``"files"``)."""
    page.images = {}
    source, fills = prepare(page)
    footnotes = []
    body = markdown().render(source)

    for token, (kind, d, r) in fills.items():
        i = token[len("ADHOCFILL"):-1]
        if kind == "link":
            href = f"loc:{_loc(r) if not r.quoted else _loc(r, r.parts[0].start, r.parts[-1].end)}" if r.status != "broken" else "#broken"
            body = body.replace(f'href="ADHOCLINK{i}X"', f'href="{html.escape(href)}"')
            continue
        if r.status == "broken":
            frag = broken_html(d.id, r.error)
        else:
            cite_mode = d.opts.get("cite", "inline")
            citation = page.cite(r, d.opts.get("signal"))
            if d.kind == "cite":
                frag = f'<cite data-ref="{d.id}">{citation}</cite>'
            else:
                shown = page.shown(d, r)
                if shown["error"]:
                    frag = broken_html(d.id, shown["error"])
                elif shown["as"] == "image":
                    frag = image_html(page, r, shown["crops"], images)
                elif shown["as"] == "table":
                    frag = table_html(r)
                elif r.kind == "cells":
                    frag = f'<q class="adhoc-quote" data-ref="{r.id}"><a href="loc:{_sel_loc(r)}">{html.escape(cells_text(r))}</a></q>'
                else:
                    frag = quote_html(page, r, d.kind == "block", layout_of(d, r, page),
                                      (shown.get("styles") or ()) if shown["as"] == "rich" else ())
                if not shown["error"]:
                    if cite_mode == "footnote":
                        footnotes.append(citation)
                        n = len(footnotes)
                        frag += f'<sup class="adhoc-fn"><a href="#adhoc-fn{n}" id="adhoc-fnref{n}">{n}</a></sup>'
                    elif cite_mode != "none":
                        cite_el = f'<cite data-ref="{d.id}">{citation}</cite>'
                        frag = frag + f'<p class="adhoc-cite">{cite_el}</p>' if d.kind == "block" else f"{frag} ({cite_el})"
        if d.kind == "block":
            body = body.replace(f"<p>{token}</p>", frag)
        body = body.replace(token, frag)

    if footnotes:
        body += '<section class="adhoc-footnotes"><ol>' + "".join(
            f'<li id="adhoc-fn{n}">{c} <a href="#adhoc-fnref{n}">↩</a></li>' for n, c in enumerate(footnotes, 1)) + "</ol></section>"
    title = page.meta.get("title")
    if title:
        body = f"<h1 class=\"adhoc-title\">{html.escape(str(title))}</h1>\n" + body
    results = page.check()
    if standalone:
        body = STANDALONE.format(title=html.escape(str(title or page.path.stem)), body=body)
    return body, results


STANDALONE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<style>
body {{ font: 16px/1.5 Georgia, serif; max-width: 42em; margin: 2em auto; padding: 0 1em; color: #222; }}
.adhoc-quote span[data-text] {{ background: #eef4ff; }}
.adhoc-quote a {{ color: inherit; text-decoration: none; }}
blockquote.adhoc-quote {{ margin: 1em 2em; }}
.adhoc-mark {{ color: #555; }}
.adhoc-cite {{ margin: -0.5em 2em 1em; }}
.adhoc-reanchored {{ outline: 1px dashed #c90; }}
.adhoc-low-confidence {{ text-decoration: underline wavy #c90; }}
.adhoc-broken {{ color: #fff; background: #c00; padding: 0 .3em; }}
figure.adhoc-quote {{ margin: 1em 2em; }}
figure.adhoc-image img {{ max-width: 100%; border: 1px solid #ccc; display: block; margin-bottom: .3em; }}
figure.adhoc-cells {{ margin: 1em 0; }}
figure.adhoc-cells table {{ border-collapse: collapse; width: 100%; font: 11px/1.3 system-ui, sans-serif; }}
figure.adhoc-cells td {{ border: 1px solid #ccc; padding: 2px 6px; }}
</style></head><body>
{body}
</body></html>
"""


# -- export ------------------------------------------------------------------------------------

def provenance(page: Page) -> list[dict]:
    rows = []
    for d in literate.directives(page.prose):
        if d.kind not in ("q", "block"):
            continue
        r = page.resolved(d.id)
        shown = page.shown(d, r)
        row = {"id": d.id, "status": r.status, "as": shown["as"]}
        if r.kind == "text":
            row.update(locators=[{"doc": r.doc, "layer": r.layer.id, "select": {"char": [p.start, p.end]},
                                  "quote": locators.make_quote(r.text, p.start, p.end)} for p in r.parts],
                       rendered=literate.plain(literate.pieces(r, inline=d.kind != "block")), alterations=r.alter)
            if shown["as"] == "rich":
                row["formatting"] = shown.get("source") or "none found in the original"
        elif r.kind == "cells":
            text = "\n".join("\t".join(row_) for row_ in r.cells)
            row.update(locators=[{"doc": r.doc, "layer": r.layer.id, "select": r.select, "quote": {"exact": text}}], rendered=text)
        else:
            row.update(locators=[{"doc": r.doc, "select": r.select}], rendered=" ".join(c.alt for c in shown.get("crops", [])))
        if shown.get("crops"):
            row["images"] = [{"page": c.page, "rect": c.rect, "sha256": sha256(c.png), "words": c.alt,
                              "of": "the text set as pages by adhocdocs" if c.rendered else "the document's pages"}
                             for c in shown["crops"]]
        row["citation"] = re.sub("<[^>]+>", "", page.cite(r, d.opts.get("signal")))
        rows.append(row)
    return rows


def export(page: Page, fmt: str, out: Path, allow_reanchored=False) -> dict:
    """Write the page with quotations as literal text, plus a provenance
    record. Refuses while any reference is broken, and, unless allowed,
    while any is re-anchored."""
    results = page.check()
    broken = [r for r in results if r.get("status") == "broken" or r.get("lint") in ("missing-ref", "bad-ref", "duplicate-ref")]
    if broken:
        raise PageError(f"not exporting: {len(broken)} broken reference(s): " + ", ".join(r.get("id", "?") for r in broken))
    moved = [r["id"] for r in results if r.get("status") == "re-anchored"]
    if moved and not allow_reanchored:
        raise PageError(f"not exporting: re-anchored reference(s) {', '.join(moved)}; "
                        "run page refs update, or pass --allow-reanchored")
    out = Path(out)
    html_doc, _ = render(page, standalone=True)
    if fmt == "html":
        out.write_text(html_doc)
    elif fmt == "pdf":
        import pymupdf
        html_doc, _ = render(page, standalone=True, images="files")
        archive = pymupdf.Archive()
        for name, png in page.images.items():
            archive.add((png, name))
        story = pymupdf.Story(html=html_doc, archive=archive)
        writer = pymupdf.DocumentWriter(str(out))
        more = True
        while more:
            dev = writer.begin_page(pymupdf.paper_rect("letter"))
            more, _ = story.place(pymupdf.paper_rect("letter") + (72, 72, -72, -72))
            story.draw(dev)
            writer.end_page()
        writer.close()
    elif fmt == "docx":
        from .pagedocx import write_docx
        write_docx(page, out)
    else:
        raise PageError(f"unknown format {fmt}")
    prov_path = out.with_name(out.name + ".provenance.json")
    prov = {"page": str(page.path), "page_hash": sha256(page.source.encode()), "output": str(out),
            "output_hash": sha256(out.read_bytes()), "format": fmt, "quotations": provenance(page)}
    prov_path.write_text(json.dumps(prov, indent=2, ensure_ascii=False) + "\n")
    ev = page.m.logs["workspace"].append(op="export", page=str(page.path), output=str(out), output_hash=prov["output_hash"],
                                         provenance=str(prov_path), provenance_hash=sha256(prov_path.read_bytes()))
    return {"output": str(out), "provenance": str(prov_path), "event": ev["id"], "reanchored": moved}


# -- editing references --------------------------------------------------------------------

_QUOTE_MARKS = {"'": "'’‘", "’": "'’‘", "‘": "'’‘", '"': '"“”', "“": '"“”', "”": '"“”'}


def _words_pattern(words: str) -> str:
    """Words as typed, matched exactly except that any run of whitespace
    matches any run (line breaks included) and straight and curly quote
    marks match each other."""
    tokens = words.split()
    if not tokens:
        raise PageError("--words is empty")
    def tok(t):
        return "".join(f"[{re.escape(_QUOTE_MARKS[c])}]" if c in _QUOTE_MARKS else re.escape(c) for c in t)
    return r"\s+".join(tok(t) for t in tokens)


def _doc_name(m: Matter, doc: str) -> str:
    md = m.state.metadata.get(doc)
    return (m.objects.get_object(md).get("name") if md else None) or ""


def _docs_for(m: Matter, where: str | None) -> list[str]:
    """--in: a doc locator or hash prefix, or part of a file name (unique)."""
    if where is None:
        return m.state.live_documents()
    w = where.removeprefix("doc:")
    if re.fullmatch(r"(sha256:)?[0-9a-f]{6,64}", w):
        return [locators.parse("doc:" + w, m.objects)["doc"]]
    docs = [d for d in m.state.live_documents() if where.lower() in _doc_name(m, d).lower()]
    if not docs:
        raise PageError(f"no document's name contains {where!r}")
    return docs


def _describe(m: Matter, doc: str) -> str:
    cust = (m.objects.get_object(doc) or {}).get("custodian", "")
    return f"{doc[7:19]} {_doc_name(m, doc)}" + (f" ({cust.removeprefix('cust:')})" if cust else "")


def find_words(m: Matter, where: str | None, words: list[str], view: str | None = None) -> list[dict]:
    """Locators for quoting the given words: one text range per entry of
    ``words`` (several make an elided quotation), all in one document,
    found in its primary layer (or the layer with ``view``). Each must occur
    exactly once there, after the previous one."""
    found, repeated = {}, None
    for doc in _docs_for(m, where):
        content = m.state.documents[doc]
        if view:
            layers = [l for l in m.state.layers.get(content, []) if l.view == view]
            layer = layers[-1] if layers else None
        else:
            layer = m.state.primary_layer(content)
        if layer is None:
            continue
        text = m.objects.get_text(layer.text)
        spans, pos, ok = [], 0, True
        for w in words:
            hits = [h for h in re.finditer(_words_pattern(w), text) if h.start() >= pos]
            if len(hits) > 1 and repeated is None:
                ctx = "; ".join(f"…{text[max(0, h.start() - 30):h.end() + 30]}…".replace("\n", " ") for h in hits[:5])
                repeated = f"{w!r} occurs {len(hits)} times in {_doc_name(m, doc)}; add words to make it unique: {ctx}"
            if len(hits) != 1:
                ok = False
                break
            spans.append(hits[0].span())
            pos = hits[0].end()
        if ok:
            found[doc] = (layer, text, spans)
    if not found and repeated:
        raise PageError(repeated)
    if not found:
        hint = ""
        for doc in _docs_for(m, where)[:50]:
            layer = m.state.primary_layer(m.state.documents[doc])
            if layer and re.search(_words_pattern(words[0]), m.objects.get_text(layer.text), re.I):
                hint = f" ({words[0]!r} matches in {_doc_name(m, doc)} if case is ignored)"
                break
        scope = f"in {where}" if where else "in any one document"
        raise PageError(f"the words weren't found {scope}, exactly once and in order{hint}. "
                        "They must be the document's own words; whitespace and quote marks may differ.")
    if len(found) > 1:
        names = "; ".join(_describe(m, d) for d in list(found)[:10])
        raise PageError(f"the words occur in {len(found)} documents; pick one with --in: {names}")
    doc, (layer, text, spans) = next(iter(found.items()))
    return [{"doc": doc, "layer": layer.id, "select": {"char": [a, b]}, "quote": locators.make_quote(text, a, b)}
            for a, b in spans]


def add_ref(m: Matter, path: Path, rid: str, locs: list, replace=False) -> dict:
    """Add a reference made from locators (several make an elided quotation),
    with layer and quote fingerprints taken from the store."""
    if not literate.ID.match(rid):
        raise PageError(f"bad id {rid!r}: use lowercase letters, digits and hyphens")
    source = Path(path).read_text()
    _, refs, _, _ = literate.split(source)
    if any(r["id"] == rid for r in refs) and not replace:
        raise PageError(f"reference {rid} exists; pass --replace")
    parts = []
    for s in locs:
        if isinstance(s, str) and s.startswith(("bates:", "exh:")):          # the document (and pages) they name
            from .exhibits import resolve_exhibit
            from .produce import resolve_bates
            s = (resolve_bates if s.startswith("bates:") else resolve_exhibit)(m, s)[0]["loc"]
        r = locators.resolve(m, s if isinstance(s, dict) else (s if s.lstrip().startswith("{") else "doc:" + s.removeprefix("doc:")))
        if r["status"] == "broken":
            raise PageError(f"locator is broken: {r.get('error')}")
        parts.append(r["loc"])
    first = parts[0]
    sel = first.get("select") or {}
    if "char" in sel:
        layer = m.state.layer(first["layer"])
        text = m.objects.get_text(layer.text)
        for p in parts:
            if p["doc"] != first["doc"] or p.get("layer") != first["layer"] or "char" not in (p.get("select") or {}):
                raise PageError("parts of one reference must be text ranges in the same document and layer")
        spans = [tuple(p["select"]["char"]) for p in parts]
        if spans != sorted(spans) or any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
            raise PageError("parts must be in order and not overlap")
        if len(parts) == 1:
            ref = {"id": rid, "doc": first["doc"], "layer": first["layer"], "select": {"char": list(spans[0])},
                   "quote": locators.make_quote(text, *spans[0])}
        else:
            ref = {"id": rid, "doc": first["doc"], "layer": first["layer"],
                   "parts": [{"select": {"char": list(sp)}, "quote": locators.make_quote(text, *sp)} for sp in spans]}
    elif "cells" in sel:
        if len(parts) > 1:
            raise PageError("only text ranges can be elided")
        ref = {"id": rid, "doc": first["doc"], "layer": first["layer"], "select": sel, "quote": first["quote"]}
    else:
        if len(parts) > 1:
            raise PageError("only text ranges can be elided")
        ref = {"id": rid, "doc": first["doc"], **({"select": sel} if sel else {})}
    refs = [r for r in refs if r["id"] != rid] + [ref]
    Path(path).write_text(literate.replace_refs(source, refs))
    return ref


def update_refs(m: Matter, path: Path) -> list[dict]:
    """Rewrite re-anchored references in place. Returns what changed."""
    source = Path(path).read_text()
    _, refs, _, _ = literate.split(source)
    changed, new_refs = [], []
    for ref in refs:
        r = literate.resolve(m, ref)
        if r.status == "re-anchored" and r.new:
            new_refs.append(r.new)
            changed.append({"id": ref["id"], "new": r.new})
        else:
            new_refs.append(ref)
    if changed:
        Path(path).write_text(literate.replace_refs(source, new_refs))
    return changed


def cited_docs(page: Page) -> list[tuple[str, list[str]]]:
    """The documents a page quotes, cites or links to, in the order the prose
    first does, each with the reference IDs that name it. References the
    prose doesn't use are left out (``page check`` reports them)."""
    out = {}
    for d in literate.directives(page.prose):
        ref = page.refs.get(d.id)
        if not ref or not ref.get("doc"):
            continue
        doc = locators.parse({"doc": ref["doc"]}, page.m.objects)["doc"]
        ids = out.setdefault(doc, [])
        if d.id not in ids:
            ids.append(d.id)
    return list(out.items())
