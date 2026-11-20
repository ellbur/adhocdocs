"""Choosing the passages the memo must quote: writes tasks.json. See docs/QUOTEBENCH.md.

    uv run python tools/quotebench/passages.py --matter MATTER [--out tools/quotebench/tasks.json]

The passages come from the sample matter's long, born-digital public
documents, whose text has no OCR or ligature damage:

- the FMCSA's property-broker transparency NPRM (HTML, about 23,500 words):
  prose paragraphs, one or two at a time, named by section and opening words;
- the Federal Rules of Civil Procedure (PDF, about 65,000 words): whole
  subdivisions, paragraphs and subparagraphs, named by citation, only where
  no page break (running head, page number) falls inside;
- Title 9 of the U.S. Code (HTML): sections, named by citation, without
  their notes.

Each task keeps the passage's text, so a checked memo can say how much of
the requested passage each quotation reproduces. The choice is seeded, so
the same matter gives the same tasks.
"""

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = Path(sys.executable).parent

FR = "fr-2024-27115-property-broker-transparency.html"
FRCP = "uscourts-federal-rules-of-civil-procedure.pdf"
USC9 = "usc-title9-arbitration-2023.htm"
COUNTS = {FR: 24, FRCP: 18, USC9: 6}
MIN_WORDS, MAX_WORDS = 80, 320
SEED = 20261120


def doc_texts(matter: str) -> dict[str, tuple[str, str]]:
    """name -> (locator, text) for the documents above."""
    out = {}
    r = subprocess.run([str(BIN / "search"), "--matter", matter, "all"], capture_output=True, text=True, check=True)
    for line in r.stdout.splitlines()[1:]:
        d = json.loads(line)
        name = d["doc"]["name"]
        if name in COUNTS:
            t = subprocess.run([str(BIN / "text"), "--matter", matter, d["loc"]], capture_output=True, text=True,
                               check=True).stdout
            out[name] = (d["loc"], t)
    missing = set(COUNTS) - set(out)
    if missing:
        sys.exit(f"not in the matter: {', '.join(sorted(missing))}")
    return out


def words(s: str) -> int:
    return len(s.split())


def opening(s: str, n=7) -> str:
    return " ".join(s.split()[:n])


# -- the NPRM: paragraphs under their headings ---------------------------------------------------------

HEADING = re.compile(r"^(?P<n>[IVX]+|[A-Z]|\d+)\.\s+(?P<title>.+)$", re.S)


def fr_candidates(text: str) -> list[dict]:
    blocks, pos = [], 0
    for b in text.split("\n\n"):
        start = text.index(b, pos)
        pos = start + len(b)
        blocks.append((start, b))
    # the table of contents repeats the headings: start at the second "I. Public Participation"
    firsts = [i for i, (_, b) in enumerate(blocks) if b.strip().startswith("I. Public Participation")]
    body = blocks[firsts[1]:] if len(firsts) > 1 else blocks
    path: list[tuple[str, str]] = []
    romans = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
    nxt = 0         # the next top-level part: "I." is also the ninth lettered section
    paras = []      # (section path, start, end, text, prose)
    in_list = False     # after "…including, but not limited to:", numbered blocks are items, not headings
    for start, b in body:
        s = b.strip()
        if not s:
            continue
        m = HEADING.match(s)
        title = " ".join(m.group("title").split()) if m else ""
        heading = (m and not (in_list and m.group("n").isdigit()) and words(s) <= 40 and not title.endswith((".", ";", ":", ",", " and", " or"))
                   and not re.search(r"[a-z]\. [A-Z]", title))
        if heading:
            n = m.group("n")
            if nxt < len(romans) and n == romans[nxt]:
                level, nxt = 0, nxt + 1
            else:
                level = 1 if n.isalpha() else 2
            path = path[:level] + [(n, title.rstrip("."))]
            in_list = False
            continue
        if s.endswith(":"):
            in_list = True
        elif in_list and s[0].isupper() and words(s) >= 8 and not m:
            in_list = False
        if s.startswith("List of Subjects") or s.startswith("PART 371"):
            break
        prose = s[0].isupper() and s.endswith(".") and words(s) >= 25 and "\n" not in s.strip()[:40]
        paras.append((tuple(path), start, start + len(b), s, prose))
    out = []
    for i, (sec, a, b, s, prose) in enumerate(paras):
        if not prose or not sec:
            continue
        cite = ".".join(n for n, _ in sec)
        name = sec[-1][1]
        if MIN_WORDS <= words(s) <= MAX_WORDS:
            out.append({"section": cite, "title": name, "start": a, "end": b, "paras": 1})
        if i + 1 < len(paras):
            sec2, a2, b2, s2, prose2 = paras[i + 1]
            if sec2 == sec and prose2 and words(s) >= 40 and 150 <= words(s) + words(s2) <= MAX_WORDS + 60:
                out.append({"section": cite, "title": name, "start": a, "end": b2, "paras": 2})
    return out


def fr_task(c: dict, text: str) -> str:
    # opening words that occur once in the document ("The proposed rule does not include an …" twice)
    para, flat = text[c["start"]:c["end"]], " ".join(text.split())
    n = 7
    while flat.count(opening(para, n)) > 1:
        n += 1
    first = opening(para, n)
    what = "the paragraph" if c["paras"] == 1 else "the paragraph"
    tail = "" if c["paras"] == 1 else ", and the paragraph that follows it"
    return (f"Section {c['section']} (“{c['title']}”): {what} beginning “{first} …”{tail}, in full.")


# -- the Federal Rules: subdivisions by citation --------------------------------------------------------

RULE = re.compile(r"^Rule (\d+(?:\.\d+)?)\. ")
FURNITURE = re.compile(r"FEDERAL RULES OF CIVIL PROCEDURE|\d+\s*Rule \d+(?:\.\d+)?\s*$|^\d+$|^\(As amended|Committee Note")
# a subdivision's heading is in capitals and may run onto the next line; paragraphs and
# subparagraphs start with a capital (their items don't); a heading is a title and a period
SUBDIV = re.compile(r"^\(([a-z])\) ([A-Z]{2}[A-Z ,;’'\-]*)")
PARA = re.compile(r"^\((\d+)\) [A-Z]")
SUBPARA = re.compile(r"^\(([A-Z])\) [A-Z]")
TITLED = re.compile(r"^\([0-9A-Za-z]+\) ([A-Z][A-Za-z ,;’'\-]{2,80}?)\.(?:\s|$)")
CAPS_TITLE = re.compile(r"^\([a-z]\) ([A-Z][A-Z ,;’'\-]{2,80}[A-Z])\.(?:\s|$)")


def frcp_candidates(text: str) -> list[dict]:
    lines, offs, pos = text.split("\n"), [], 0
    for l in lines:
        offs.append(pos)
        pos += len(l) + 1
    start_body = next(i for i, l in enumerate(lines) if l.startswith("Rule 1. Scope and Purpose") and "...." not in l)
    heads = []      # (line, level, designation, heading)
    rule, sub, par = None, None, None
    for i in range(start_body, len(lines)):
        l = lines[i]
        if re.match(r"^Rule [A-G]\. ", l):
            break           # the Supplemental Rules number differently: stop at Rule A
        if m := RULE.match(l):
            rule, sub, par = m.group(1), None, None
            heads.append((i, 0, None, None))
            continue
        if rule is None:
            continue
        if m := SUBDIV.match(l):
            sub, par = m.group(1), None
            t = CAPS_TITLE.match(l)
            heads.append((i, 1, f"Rule {rule}({sub})", t.group(1).title() if t else None))
        elif sub and (m := PARA.match(l)):
            par = m.group(1)
            t = TITLED.match(l)
            heads.append((i, 2, f"Rule {rule}({sub})({par})", t.group(1) if t else None))
        elif par and (m := SUBPARA.match(l)):
            t = TITLED.match(l)
            heads.append((i, 3, f"Rule {rule}({sub})({par})({m.group(1)})", t.group(1) if t else None))
        elif l.startswith("(As amended"):
            heads.append((i, 0, None, None))
    out = []
    for k, (i, level, desig, heading) in enumerate(heads):
        if not desig or not heading:
            continue
        j = next((h[0] for h in heads[k + 1:] if h[1] <= level), len(lines))
        body = lines[i:j]
        if any(FURNITURE.search(l) for l in body):
            continue
        s = "\n".join(body).strip()
        if MIN_WORDS <= words(s) <= MAX_WORDS:
            out.append({"cite": desig, "heading": heading, "start": offs[i], "end": offs[i] + len(s)})
    return out


def frcp_task(c: dict) -> str:
    return f"Fed. R. Civ. P. {c['cite'][5:]} (“{c['heading']}”), in full, with any items under it."


# -- Title 9: sections without their notes -----------------------------------------------------------------

SECTION = re.compile(r"^§(\d+)\. (.+)$", re.M)


def usc_candidates(text: str) -> list[dict]:
    out = []
    for m in SECTION.finditer(text):
        a = m.end()
        nxt = re.search(r"^\((?:July|Added|Pub\. L\.|Feb\.|Sept\.|Oct\.|Nov\.|Dec\.)", text[a:], re.M)
        if not nxt:
            continue
        s = text[a:a + nxt.start()].strip()
        start = text.index(s, a)
        if MIN_WORDS <= words(s) <= MAX_WORDS + 80:
            out.append({"section": m.group(1), "title": m.group(2).strip(), "start": start, "end": start + len(s)})
    return out


def usc_task(c: dict) -> str:
    return f"9 U.S.C. § {c['section']} (“{c['title']}”): the text of the section, in full (not its notes)."


# -- choosing ---------------------------------------------------------------------------------------------

def spread(cands: list[dict], n: int, rnd: random.Random) -> list[dict]:
    """n candidates spread through the document, none overlapping."""
    cands = sorted(cands, key=lambda c: c["start"])
    chosen = []
    pool = cands[:]
    rnd.shuffle(pool)
    for c in pool:
        if len(chosen) == n:
            break
        if all(c["end"] <= d["start"] or d["end"] <= c["start"] for d in chosen):
            chosen.append(c)
    if len(chosen) < n:
        sys.exit(f"only {len(chosen)} passages, wanted {n}")
    return sorted(chosen, key=lambda c: c["start"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matter", required=True)
    ap.add_argument("--out", default=str(HERE / "tasks.json"))
    a = ap.parse_args()
    texts = doc_texts(a.matter)
    rnd = random.Random(SEED)
    items = []
    for name, cands, task in ((FR, fr_candidates, lambda c, t: fr_task(c, t)),
                              (FRCP, frcp_candidates, lambda c, t: frcp_task(c)),
                              (USC9, usc_candidates, lambda c, t: usc_task(c))):
        loc, text = texts[name]
        for c in spread(cands(text), COUNTS[name], rnd):
            passage = text[c["start"]:c["end"]]
            items.append({"doc": name, "loc": loc, "request": task(c, text), "start": c["start"], "end": c["end"],
                          "words": words(passage), "passage": passage})
    for n, it in enumerate(items, 1):
        it["item"] = n
    out = {"version": 1, "seed": SEED, "documents": {n: texts[n][0] for n in COUNTS},
           "items": [{"item": it.pop("item"), **it} for it in items]}
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(it["words"] for it in items)
    print(json.dumps({"written": a.out, "items": len(items), "words": total}))


if __name__ == "__main__":
    main()
