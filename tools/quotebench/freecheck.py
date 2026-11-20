"""Checking the quotations in a free-form memo (a chronology, an analysis). See docs/QUOTEBENCH.md.

    uv run python tools/quotebench/freecheck.py MEMO.md --matter MATTER [-o result.json]

check.py answers a list of requested passages. Here nothing was requested:
the memo quotes what it likes, in block quotations, inline “…” of three
words or more, or page references, and cites documents by file name. Each
quotation is judged against the documents its line or list item names by
file name (the whole name, extension and all), then those its entry names:

- **verbatim**: its words are in the cited document (align.py's rules);
- **misattributed**: they aren't in the documents named in its own line or
  list item, but they are in another document;
- **misquoted**: they are close to a passage (half or more of the words
  match) but not the same, with the word errors;
- **not found**: nothing in the matter is close: a paraphrase in quotation
  marks, or words from somewhere else;
- **uncited**: its line names no document, and the words aren't in the
  documents its entry names (they are somewhere else);
- **unverifiable**: it is cited to a document with no text here (a scan
  not OCR'd), and nothing with text has it.

A chronology cites by entry: a "Sources:" line under an entry covers every
quotation in it, so a quotation counts as cited to any document its entry
names; failing that, to the documents the memo names anywhere (a reference
memo may name its sources once). Leaving out a page's running head or
number that falls inside a passage is not a misquotation.
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
import check  # noqa: E402

INLINE = re.compile(r"“([^”\n]+)”|(?<![\w=])\"([^\"\n]+)\"(?!\w)")
MIN_WORDS = 3


def documents(matter: str) -> list[dict]:
    return check.all_documents(matter)


class Corpus:
    """Every document's text, read when a quotation might be in it."""

    def __init__(self, matter: str):
        self.matter = matter
        self.docs = documents(matter)
        self.names = {d["doc"]["name"]: d["loc"] for d in self.docs}
        self._raw: dict[str, str] = {}
        self._src: dict[str, align.Source] = {}

    def raw(self, name: str) -> str:
        """The document's primary text (read straight from the store: `text` for each of a
        matter's documents would take minutes)."""
        if name not in self._raw:
            from adhocdocs.matter import Matter
            if not hasattr(self, "_m"):
                self._m = Matter(Path(self.matter))
            doc = self.names[name].removeprefix("doc:")
            content = self._m.state.documents.get(doc)
            layer = self._m.state.primary_layer(content) if content else None
            self._raw[name] = self._m.objects.get_text(layer.text) if layer else ""
        return self._raw[name]

    def source(self, name: str) -> align.Source:
        if name not in self._src:
            self._src[name] = align.Source(name, self.raw(name))
        return self._src[name]

    def candidates(self, quotation: str) -> list[str]:
        """Documents that have the quotation's longest word (all of them, for a short one)."""
        ws = sorted({w.lower() for w in re.findall(r"[^\W\d_]{4,}", quotation)}, key=len, reverse=True)
        out = []
        for n in self.names:
            t = self.raw(n).lower()
            if not ws or ws[0] in t or (len(ws) > 1 and ws[1] in t):
                out.append(n)
        return out


def paragraphs(prose: str):
    """(start, text) of each entry: a chronology's entries (a heading, a bold lead
    line, or a rule starts one) or, in prose without them, each paragraph. An
    entry's "Sources:" line covers every quotation in it."""
    starts = [0] + [m.start() for m in re.finditer(r"^(?:#{1,6} |\*\*[^*\n]{3,}\*\*|---+\s*$|\|)", prose, re.M)]
    if len(starts) < 3:
        starts = [0] + [m.end() for m in re.finditer(r"\n\s*\n", prose)]
    starts = sorted(set(starts)) + [len(prose)]
    for a, b in zip(starts, starts[1:]):
        block = prose[a:b]
        if block.strip():
            yield a, block


def _norm(s: str) -> str:
    """For finding file names in Markdown: no emphasis or code marks (“RE_ Unclaimed…” keeps its words)."""
    return re.sub(r"[*_`]", " ", s.lower())


def cited_near(block: str, at: int, names: list[str]) -> str | None:
    """The nearest document name after ``at`` in the block, else before it."""
    found = []
    low = _norm(block)
    for n in names:
        # the whole file name: without its extension, a name can be ordinary text ("PO-BMS-26-0112")
        for key in {_norm(n)}:
            if len(key) < 6:
                continue
            for m in re.finditer(re.escape(key), low):
                found.append((m.start(), n))
    if not found:
        return None
    # a name cites the quotation only if no other quotation stands between them
    quotes = [m.start() for m in INLINE.finditer(block)]
    after = [f for f in found if f[0] >= at and not any(at < q < f[0] for q in quotes)]
    before = [f for f in found if f[0] < at and not any(f[0] < q < at - 1 for q in quotes)]
    return min(after)[1] if after else max(before)[1] if before else None


def cited_in(block: str, names: list[str]) -> list[str]:
    """Every document named in the block."""
    low = _norm(block)
    out = []
    for n in names:
        if len(n) >= 6 and re.search(r"(?<![\w-])" + re.escape(_norm(n)), low):
            out.append(n)
    # a name inside a longer one ("Unclaimed pallet …" in "RE  Unclaimed pallet …") isn't cited by it
    return [n for n in out if not any(o != n and _norm(n) in _norm(o) for o in out)]


def bullet_at(block: str, at: int) -> str:
    """The list item or line of ``block`` that position ``at`` is in, with its continuation lines."""
    starts = [0] + [m.start() for m in re.finditer(r"^\s*(?:[-*+]|\d+\.)\s", block, re.M)] + [len(block)]
    a = max(x for x in starts if x <= at)
    b = min(x for x in starts if x > at) if any(x > at for x in starts) else len(block)
    return block[a:b]


def quotations(memo: str, m, corpus: Corpus) -> list[dict]:
    from adhocdocs import literate
    prose, refs, _, _ = literate.split(memo)
    refs = {r["id"]: r for r in refs}
    names = list(corpus.names)
    loc_names = {d["loc"].removeprefix("doc:"): d["doc"]["name"] for d in corpus.docs}
    out = []
    memo_sources = cited_in(check.strip_markdown(prose), names)
    for start, block in paragraphs(prose):
        entry_sources = cited_in(check.strip_markdown(block), names) or memo_sources
        # page references
        for q in check.ref_quotations(block, refs, m):
            q["cited"] = loc_names.get(str(q.get("doc", "")).removeprefix("doc:"))
            q["where"] = start
            out.append(q)
        # block quotations
        if block.lstrip().startswith(">"):
            text = check.strip_markdown("\n".join(re.sub(r"^\s*>\s?", "", l) for l in block.splitlines()
                                                  if l.lstrip().startswith(">")))
            out.append({"kind": "typed-block", "text": text, "where": start,
                        "cited": cited_near(block, len(block), names), "sources": entry_sources})
            continue
        # inline quotations
        plain = check.strip_markdown(block)
        for mm in INLINE.finditer(plain):
            q = mm.group(1) or mm.group(2)
            if len(re.findall(r"[^\W_]+", q)) < MIN_WORDS:
                continue
            own = bullet_at(plain, mm.start())
            near = cited_near(own, own.find(q) + len(q) + 1, names)
            out.append({"kind": "typed-inline", "text": q, "where": start, "cited": near,
                        "own_sources": [near] if near else [], "sources": entry_sources})
    return out


FURNITURE = re.compile(r"^(?:\d+ )?(?:Rule \d+(?:\.\d+)? )?FEDERAL RULES OF CIVIL PROCEDURE(?: Rule \d+(?:\.\d+)?)?(?: \d+)?$"
                       r"|^(?:.* )?Internal (?:- )?Page \d+$|^\d+$|^Page \d+(?: of \d+)?$", re.I)


def furniture(e: dict) -> bool:
    """A dropped run of words that is a page's running head, footer or number."""
    return e.get("type") == "dropped" and bool(FURNITURE.match(" ".join(e.get("source", "").split())))


def same_message(corpus: Corpus, a: str, b: str) -> bool:
    return a == b or any(a in s and b in s for s in check.SAME_WORK)


def judge(q: dict, corpus: Corpus) -> dict:
    """A quotation's verdict, against the documents its entry cites (the one
    named nearest it first)."""
    q = {k: v for k, v in q.items() if not k.startswith("_")}
    if q.get("kind") == "ref":
        if not q.get("text"):
            return {**q, "verdict": "broken"}
        return {**q, "verdict": "verbatim"}       # the renderer's words are the layer's
    text = q["text"]
    own = ([q["cited"]] if q.get("cited") else []) + [n for n in q.get("own_sources", []) if n != q.get("cited")]
    cited = own + [n for n in q.get("sources", []) if n not in own]
    results = {}
    for n in cited:
        r = align.compare(corpus.source(n), text)
        results[n] = r
        if r["verbatim"]:
            return {**q, "verdict": "verbatim", "source": n, "case": r["case"], "punctuation": r["punctuation"]}
    # anywhere else?
    best, best_name = None, None
    for n in corpus.candidates(text):
        if n in results:
            continue
        r = align.compare(corpus.source(n), text)
        if r["verbatim"]:
            # a textless document cited next to it may hold these words too: can't tell
            if any(not corpus.raw(c).strip() for c in (own or cited)):
                return {**q, "verdict": "unverifiable", "found_in": n,
                        "reason": "cited to a document with no text; the words are also in " + n}
            return {**q, "verdict": "misattributed" if own else "uncited", "found_in": n}
        if best is None or r["matched"] > best["matched"]:
            best, best_name = r, n
    for n, r in results.items():
        if not best or r["matched"] >= best["matched"]:
            best, best_name = r, n
    if not cited:
        return {**q, "verdict": "uncited", **({"closest": best_name} if best_name else {})}
    # a document with no text (a scan nothing read) can't be checked against
    textless = [n for n in (own or cited) if not corpus.raw(n).strip()]
    if textless:
        return {**q, "verdict": "unverifiable", "reason": f"no text for {', '.join(textless)}"}
    if best and best["words"] and best["matched"] >= best["words"] / 2:
        errors = [e for e in best["errors"] if not furniture(e)]
        if not errors:
            # all it leaves out is a page's running head or number, inside the passage
            return {**q, "verdict": "verbatim", "source": best_name, "furniture_left_out": len(best["errors"])}
        return {**q, "verdict": "misquoted", "closest": best_name, "errors": errors,
                "words_wrong": sum(max(len(e.get("source", "").split()), len(e.get("quote", "").split())) for e in errors)}
    return {**q, "verdict": "not found", "closest": best_name}


def run(memo: str, matter: str) -> dict:
    from adhocdocs.matter import Matter
    m = Matter(Path(matter))
    corpus = Corpus(matter)
    qs = [judge(q, corpus) for q in quotations(memo, m, corpus)]
    words = sum(len(q["text"].split()) for q in qs if q.get("text"))
    verdicts = {}
    for q in qs:
        verdicts[q["verdict"]] = verdicts.get(q["verdict"], 0) + 1
    return {"quotations": qs, "summary": {
        "quotations": len(qs), "words_quoted": words, "verdicts": verdicts,
        "by_kind": {k: sum(q["kind"] == k for q in qs) for k in sorted({q["kind"] for q in qs})},
        "words_wrong": sum(q.get("words_wrong", 0) for q in qs)}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("memo")
    ap.add_argument("--matter", required=True)
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    res = run(Path(a.memo).read_text(encoding="utf-8"), a.matter)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(res["summary"]))
    for q in res["quotations"]:
        if q["verdict"] not in ("verbatim",):
            print(f"  {q['verdict']}: {q['text'][:90]!r} cited {q.get('cited')} "
                  f"{('closest ' + str(q.get('closest'))) if q.get('closest') else ''}"
                  f"{('found in ' + q['found_in']) if q.get('found_in') else ''}", file=sys.stderr)
            for e in q.get("errors", [])[:3]:
                print(f"      {e['type']}: {e.get('source', '')!r} -> {e.get('quote', '')!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
