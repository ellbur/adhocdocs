"""Checking the quotations in a free-form memo (a chronology, an analysis). See docs/QUOTEBENCH.md.

    uv run python tools/quotebench/freecheck.py MEMO.md --matter MATTER [-o result.json]

check.py answers a list of requested passages. Here nothing was requested:
the memo quotes what it likes, in block quotations, inline “…” of three
words or more, or page references, and cites documents by file name. Each
quotation is judged against the document cited next to it (the nearest file
name after it in the same paragraph, else before it):

- **verbatim**: its words are in the cited document (align.py's rules);
- **misattributed**: they aren't, but they are in another document (a
  forward's copy is the same words: any document they occur in that is the
  cited one, or a copy of the same message, counts as right);
- **misquoted**: they are close to a passage (half or more of the words
  match) but not the same, with the word errors;
- **not found**: nothing in the matter is close: a paraphrase in quotation
  marks, or words from somewhere else;
- **uncited**: no document is named near it.
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
    """(start, text) of each paragraph or list item."""
    pos = 0
    for block in re.split(r"(\n\s*\n|\n(?=\s*(?:[-*+]|\d+\.)\s))", prose):
        if block and not re.fullmatch(r"\s*", block):
            yield prose.index(block, pos), block
        pos += len(block or "")


def cited_near(block: str, at: int, names: list[str]) -> str | None:
    """The nearest document name after ``at`` in the block, else before it."""
    found = []
    low = block.lower()
    for n in names:
        stem = n.rsplit(".", 1)[0].lower()
        for key in {n.lower(), stem}:
            if len(key) < 6:
                continue
            for m in re.finditer(re.escape(key), low):
                found.append((m.start(), n))
    if not found:
        return None
    after = [f for f in found if f[0] >= at]
    return min(after)[1] if after else max(found)[1]


def quotations(memo: str, m, corpus: Corpus) -> list[dict]:
    from adhocdocs import literate
    prose, refs, _, _ = literate.split(memo)
    refs = {r["id"]: r for r in refs}
    names = list(corpus.names)
    loc_names = {d["loc"].removeprefix("doc:"): d["doc"]["name"] for d in corpus.docs}
    out = []
    for start, block in paragraphs(prose):
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
                        "cited": cited_near(block, len(block), names), "_block": block})
            continue
        # inline quotations
        plain = check.strip_markdown(block)
        for mm in INLINE.finditer(plain):
            q = mm.group(1) or mm.group(2)
            if len(re.findall(r"[^\W_]+", q)) < MIN_WORDS:
                continue
            out.append({"kind": "typed-inline", "text": q, "where": start,
                        "cited": cited_near(plain, mm.end(), names)})
    return out


def same_message(corpus: Corpus, a: str, b: str) -> bool:
    return a == b or any(a in s and b in s for s in check.SAME_WORK)


def judge(q: dict, corpus: Corpus) -> dict:
    q = {k: v for k, v in q.items() if not k.startswith("_")}
    if q.get("kind") == "ref":
        if not q.get("text"):
            return {**q, "verdict": "broken"}
        return {**q, "verdict": "verbatim"}       # the renderer's words are the layer's
    text = q["text"]
    cited = q.get("cited")
    if cited:
        r = align.compare(corpus.source(cited), text)
        if r["verbatim"]:
            return {**q, "verdict": "verbatim", "case": r["case"], "punctuation": r["punctuation"]}
    # anywhere else?
    best, best_name = None, None
    for n in corpus.candidates(text):
        if n == cited:
            continue
        r = align.compare(corpus.source(n), text)
        if r["verbatim"]:
            return {**q, "verdict": "misattributed" if cited else "uncited", "found_in": n}
        if best is None or r["matched"] > best["matched"]:
            best, best_name = r, n
    if cited:
        rc = align.compare(corpus.source(cited), text)
        if not best or rc["matched"] >= best["matched"]:
            best, best_name = rc, cited
    if not cited and not best:
        return {**q, "verdict": "uncited"}
    if best and best["words"] and best["matched"] >= best["words"] / 2:
        return {**q, "verdict": "misquoted" if cited else "uncited", "closest": best_name,
                "errors": best["errors"], "words_wrong": best["words_wrong"]}
    return {**q, "verdict": "not found" if cited else "uncited", "closest": best_name}


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
