"""Checking a memo's quotations against the documents. See docs/QUOTEBENCH.md.

    uv run python tools/quotebench/check.py MEMO.md --matter MATTER [--tasks tasks.json] [-o result.json]

The memo answers tasks.json: a heading per item (``## Item 7``), then the
passage as a block quotation and its citation. A quotation is either typed
(Markdown ``>`` lines, or a long “…” run in the prose) or a page reference
(``::q{#id}``, made with ``page ref add``), which is read the way the
renderer shows it, marks and all. Each is compared word by word with the
document it best matches (align.py); the citation is right when it names
that document.

Per item: the quotations, whether each is verbatim, its word errors, and
how much of the requested passage they reproduce. Overall: counts a reader
cares about, most importantly the items with any misquotation.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
BIN = Path(sys.executable).parent

import align  # noqa: E402

ITEM = re.compile(r"^#{1,6}\s*(?:Item\s+)?(\d+)\b", re.M | re.I)
INLINE = re.compile(r"“([^”]{60,})”|\"([^\"\n]{60,})\"")
INLINE_MIN_WORDS = 12
# the same work, two files (Title 9 as HTML and as PDF)
SAME_WORK = [{"usc-title9-arbitration-2023.htm", "usc-title9-arbitration-2023.pdf"}]


def all_documents(matter: str) -> list[dict]:
    r = subprocess.run([str(BIN / "search"), "--matter", matter, "all"], capture_output=True, text=True, check=True)
    return [json.loads(l) for l in r.stdout.splitlines()[1:]]


def doc_text(matter: str, loc: str) -> str:
    return subprocess.run([str(BIN / "text"), "--matter", matter, loc], capture_output=True, text=True,
                          check=True).stdout


def same_work(a: str, b: str) -> bool:
    return a == b or any(a in s and b in s for s in SAME_WORK)


def sources(matter: str, tasks: dict) -> dict[str, align.Source]:
    """The documents a quotation can come from: the tasks' documents and their other copies."""
    names = set(tasks["documents"]) | set().union(*[s for s in SAME_WORK if s & set(tasks["documents"])])
    out = {}
    for d in all_documents(matter):
        n = d["doc"]["name"]
        if n in names and n not in out:
            out[n] = align.Source(n, doc_text(matter, d["loc"]))
    return out


# -- finding the quotations -------------------------------------------------------------------------------

def items_of(prose: str) -> dict[int, str]:
    heads = list(ITEM.finditer(prose))
    return {int(h.group(1)): prose[h.end():(heads[k + 1].start() if k + 1 < len(heads) else len(prose))]
            for k, h in enumerate(heads)}


def strip_markdown(s: str) -> str:
    s = re.sub(r"(?<!\w)[*_]{1,3}(?=\S)|(?<=\S)[*_]{1,3}(?!\w)", "", s)
    return s


def typed_quotations(section: str) -> list[dict]:
    """Block quotations (``>`` lines) and long inline quotations, with the text after each (its citation)."""
    out = []
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith(">"):
            j = i
            body = []
            while j < len(lines) and lines[j].lstrip().startswith(">"):
                body.append(re.sub(r"^\s*>\s?", "", lines[j]))
                j += 1
            # a citation line inside the block quote ("— 9 U.S.C. § 4") is not part of the quotation
            cite_in = []
            while body and re.match(r"^\s*(?:—|--|-|\(|Source:|\[Source|Cited)", body[-1].strip() or "x") and \
                    len(body[-1].split()) < 40 and not body[-1].strip().startswith(("(1)", "(a)", "(A)", "(i)")):
                cite_in.insert(0, body.pop())
            while body and not body[-1].strip():
                body.pop()
            after = "\n".join(cite_in + lines[j:j + 4])
            text = strip_markdown("\n".join(body))
            if text.strip():
                out.append({"kind": "typed", "text": text, "citation": after})
            i = j
        else:
            i += 1
    if not out:
        # no block quotation: a long inline one
        for m in INLINE.finditer(section):
            q = m.group(1) or m.group(2)
            if len(q.split()) >= INLINE_MIN_WORDS:
                out.append({"kind": "typed-inline", "text": q, "citation": section[m.end():m.end() + 300]})
    return out


def ref_quotations(section: str, refs: dict, m) -> list[dict]:
    from adhocdocs import literate
    out = []
    for d in literate.directives(section):
        if d.kind not in ("q", "block"):
            continue
        ref = refs.get(d.id)
        if ref is None:
            out.append({"kind": "ref", "id": d.id, "status": "missing", "text": ""})
            continue
        r = literate.resolve(m, ref)
        if r.status == "broken" or not r.parts:
            out.append({"kind": "ref", "id": d.id, "status": r.status, "error": r.error, "text": ""})
            continue
        text = literate.plain(literate.pieces(r, layout="flow", inline=False))
        out.append({"kind": "ref", "id": d.id, "status": r.status, "text": text, "doc": r.doc})
    return out


# -- checking ---------------------------------------------------------------------------------------------

def cited_name(citation: str, names: list[str]) -> str | None:
    c = citation.lower()
    for n in sorted(names, key=len, reverse=True):
        stem = n.rsplit(".", 1)[0].lower()
        if n.lower() in c or stem in c:
            return n
    return None


def check_quotation(q: dict, srcs: dict[str, align.Source], doc_names: dict) -> dict:
    if not q["text"].strip():
        return {**q, "verbatim": False, "broken": True}
    results = [align.compare(s, q["text"]) for s in srcs.values()]
    best = max(results, key=lambda r: (r["matched"], -r["words_wrong"]))
    out = {**q, **best}
    if q["kind"] == "ref":
        out["cited"] = doc_names.get(str(q.get("doc", "")).removeprefix("doc:"))
    else:
        out["cited"] = cited_name(q.get("citation", ""), list(srcs) + [n for n in doc_names.values() if n])
    out["citation_right"] = bool(out["cited"]) and same_work(out["cited"], best["source"])
    out.pop("citation", None)
    return out


def check(memo: str, matter_path: str, tasks: dict) -> dict:
    from adhocdocs import literate
    from adhocdocs.matter import Matter
    m = Matter(Path(matter_path))
    srcs = sources(matter_path, tasks)
    doc_names = {d["loc"].removeprefix("doc:"): d["doc"]["name"] for d in all_documents(matter_path)}
    prose, refs, problems, _ = literate.split(memo)
    refs = {r["id"]: r for r in refs}
    sections = items_of(prose)
    items = []
    for t in tasks["items"]:
        sec = sections.get(t["item"], "")
        qs = ref_quotations(sec, refs, m) + typed_quotations(sec)
        checked = [check_quotation(q, srcs, doc_names) for q in qs]
        quoted = " … ".join(c["text"] for c in checked if c.get("text"))
        items.append({
            "item": t["item"], "doc": t["doc"], "requested_words": t["words"],
            "answered": any(c.get("text") for c in checked),
            "quotations": checked,
            "verbatim": bool(checked) and all(c.get("verbatim") for c in checked),
            "misquoted": any(c.get("errors") for c in checked),
            "coverage": align.coverage(t["passage"], quoted) if quoted else 0.0,
        })
    return {"items": items, "summary": summarize(items), "lint": problems}


def summarize(items: list[dict]) -> dict:
    qs = [q for it in items for q in it["quotations"]]
    words = sum(q.get("words", 0) for q in qs)
    wrong = sum(q.get("words_wrong", 0) for q in qs)
    return {
        "items": len(items),
        "answered": sum(it["answered"] for it in items),
        "items_misquoted": sum(it["misquoted"] for it in items),
        "items_verbatim": sum(it["verbatim"] for it in items),
        "quotations": len(qs),
        "by_kind": {k: sum(q["kind"] == k for q in qs) for k in sorted({q["kind"] for q in qs})},
        "quotations_misquoted": sum(bool(q.get("errors")) for q in qs),
        "broken_references": sum(bool(q.get("broken")) for q in qs),
        "words_quoted": words,
        "words_wrong": wrong,
        "words_wrong_per_1000": round(1000 * wrong / words, 2) if words else None,
        "citations_wrong": sum(not q.get("citation_right") for q in qs if not q.get("broken")),
        "case_differences": sum(len(q.get("case", [])) for q in qs),
        "punctuation_differences": sum(q.get("punctuation", 0) for q in qs),
        "mean_coverage": round(sum(it["coverage"] for it in items) / len(items), 4) if items else None,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("memo")
    ap.add_argument("--matter", required=True)
    ap.add_argument("--tasks", default=str(HERE / "tasks.json"))
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    tasks = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    res = check(Path(a.memo).read_text(encoding="utf-8"), a.matter, tasks)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(res["summary"]))
    for it in res["items"]:
        for q in it["quotations"]:
            for e in q.get("errors", []):
                print(f"  item {it['item']}: {e['type']}: source {e.get('source', '')!r} quote {e.get('quote', '')!r}",
                      file=sys.stderr)


if __name__ == "__main__":
    main()
