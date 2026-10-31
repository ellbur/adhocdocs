"""Checking Markdown that quotes documents by reference (Principle 5).

Shared by Claude's notes now and literate pages later. Quotations are
directives (``:q{#id}``, ``::q{#id}``, ``:cite{#id}``, ``[text](ref:id)``)
naming entries in a fenced ``refs`` block of JSON locators, one per line,
each with an ``id``. :func:`check` resolves every reference and flags text
that looks like a quotation typed by hand.
"""

import json
import re

from . import locators

REFS_BLOCK = re.compile(r"^```refs[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
DIRECTIVE = re.compile(r":{1,2}(?:q|cite)\{#([a-z0-9-]+)[^}]*\}|\]\(ref:([a-z0-9-]+)\)")
QUOTED = re.compile(r"“[^”]*”|\"[^\"\n]*\"")
UNBACKED_WORDS = 5


def split(body: str) -> tuple[str, list[dict], list[dict]]:
    """(prose, refs, problems): the body without its refs block, and the refs."""
    refs, problems = [], []
    m = REFS_BLOCK.search(body)
    if not m:
        return body, refs, problems
    for n, line in enumerate(m.group(1).splitlines()):
        if not line.strip():
            continue
        try:
            ref = json.loads(line)
            if not isinstance(ref, dict) or "id" not in ref:
                raise ValueError("needs an id")
            refs.append(ref)
        except ValueError as e:
            problems.append({"lint": "bad-ref", "line": n + 1, "error": str(e)})
    return body[:m.start()] + body[m.end():], refs, problems


def check(m, body: str) -> list[dict]:
    """One dict per reference (``id``, ``status``) and per lint (``lint``)."""
    prose, refs, out = split(body)
    used = set()
    for mt in DIRECTIVE.finditer(prose):
        used.add(mt.group(1) or mt.group(2))
    ids = {r["id"] for r in refs}
    for ref in refs:
        if "parts" in ref:
            parts = [{**ref, **p} for p in ref["parts"]]
        else:
            parts = [ref]
        statuses = []
        for p in parts:
            loc = {k: v for k, v in p.items() if k in ("doc", "layer", "select", "quote")}
            try:
                r = locators.resolve(m, loc)
            except (locators.LocatorError, KeyError, ValueError) as e:
                r = {"status": "broken", "error": str(e)}
            statuses.append(r)
        worst = next((s for s in statuses if s["status"] == "broken"), None) \
            or next((s for s in statuses if s["status"] == "re-anchored"), statuses[0])
        item = {"id": ref["id"], "status": worst["status"]}
        if worst.get("error"):
            item["error"] = worst["error"]
        out.append(item)
    for i in sorted(used - ids):
        out.append({"lint": "missing-ref", "id": i})
    for i in sorted(ids - used):
        out.append({"lint": "unused-ref", "id": i})
    stripped = DIRECTIVE.sub("", prose)
    for n, line in enumerate(stripped.splitlines(), 1):
        if line.lstrip().startswith(">"):
            out.append({"lint": "blockquote", "line": n, "text": line.strip()[:80]})
        for q in QUOTED.findall(line):
            if len(q.split()) >= UNBACKED_WORDS:
                out.append({"lint": "unbacked-quote", "line": n, "text": q[:80]})
    return out


def problems(results: list[dict]) -> list[dict]:
    """The results that need attention."""
    return [r for r in results if "lint" in r or r.get("status") != "exact"]
