"""A screen for possible privilege, before Claude reads a document.

Watching Claude summarize a matter, the other side's privileged emails were
read before they were recognized: the flag (``ann flag-privilege``) came
after the reading. This screen comes before it. ``text`` refuses a screened
document unless told to read past the screen with a reason (which the audit
log records), and search results say a document is screened and leave out
its snippet. Whether anything *is* privileged, and whether it may be read,
stays a lawyer's call; the screen only says why a document looks like it
might be, and how it came into the matter.

A document is screened when:

- **marked**: its subject, title or first page says privileged, attorney-
  client, work product, legal advice, or prepared at counsel's direction;
- **counsel**: it is an email within one organization (every address on it
  shares a domain) that counsel sent, or that went to counsel directly (To)
  among at most four recipients (not counsel's copy of an all-staff
  notice), and whose subject or opening is about a legal matter (a claim, a
  contract, a hold, compliance, employment…): not the lunch order.
  Counsel are addresses whose own
  emails are mostly signed as counsel (general counsel, Esq., attorney,
  legal department), plus any listed in ``workspace/privilege-screen.json``
  (``{"counsel": [...], "not_counsel": [...]}``);
- **flagged**: anyone tagged it ``check-privilege``.

An attachment is screened for its own content, not its email's: a letter
forwarded to counsel isn't privileged for having been forwarded.

A user's ``privilege-cleared`` tag lifts the screen for that document.
"""

import json
import re
from collections import defaultdict

from .annotations import CLAUDE, PRIVILEGE_CHECK, Annotations

CLEARED = "privilege-cleared"
MARKING = re.compile(r"\b(privileged(?:\s*(?:&|and)\s*confidential)?|attorney[-\s]client|work[-\s]product|"
                     r"prepared (?:at|by) the (?:request|direction) of counsel|legal advice)\b", re.I)
SIGNED_AS_COUNSEL = re.compile(r"\b(general counsel|associate counsel|in-house counsel|legal counsel|esq\.?|"
                               r"attorney at law|legal department)\b", re.I)
QUOTE_START = re.compile(r"\n(On .{0,200}wrote:|-----\s*Original Message|From: )")
LEGAL_TOPIC = re.compile(r"\b(legal|law|laws|advice|advise|claims?|disputes?|litigat\w*|lawsuits?|suit|demand|"
                         r"contracts?|agreements?|hold|compliance|attorneys?|privilege\w*|liabilit\w*|settle\w*|"
                         r"subpoena|regulat\w*|fmla|terminat\w*|employment|harass\w*|trademark|lease|nda|"
                         r"indemn\w*|breach|arbitrat\w*|deposition|discovery|investigat\w*)\b", re.I)
HEAD = 1500             # how much of a document's text a marking is looked for in: about a page
SIGNATURE = 400         # the end of a sender's own words, where a signature is

_cache: dict = {}


def _meta(m, doc):
    mid = m.state.metadata.get(doc)
    return m.objects.get_object(mid) if mid else {}


def _text(m, doc) -> str:
    content = m.state.documents.get(doc)
    layer = m.state.primary_layer(content) if content else None
    return m.objects.get_text(layer.text) if layer else ""


def _domain(addr: str) -> str:
    return ".".join(addr.rsplit("@", 1)[-1].lower().split(".")[-2:])


def _config(m) -> dict:
    f = m.workspace / "privilege-screen.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        return {}


def counsel(m) -> dict[str, str]:
    """Address -> why it is taken for counsel."""
    key = (str(m.root), len(m.state.metadata), (m.workspace / "privilege-screen.json").exists() and
           (m.workspace / "privilege-screen.json").stat().st_mtime)
    if _cache.get("counsel_key") == key:
        return _cache["counsel"]
    sent = defaultdict(lambda: [0, 0])
    for d in m.state.live_documents():
        md = _meta(m, d)
        if md.get("kind") != "email" or not md.get("from"):
            continue
        body = _text(m, d)
        q = QUOTE_START.search(body)
        own = body[:q.start()] if q else body
        s = sent[md["from"][0].lower()]
        s[0] += 1
        s[1] += bool(SIGNED_AS_COUNSEL.search(own[-SIGNATURE:]))
    out = {a: f"signs {k} of the {n} emails they sent as counsel" for a, (n, k) in sent.items()
           if k >= 2 and k / n >= 0.4}
    cfg = _config(m)
    for a in cfg.get("counsel", []):
        out[a.lower()] = "listed as counsel in workspace/privilege-screen.json"
    for a in cfg.get("not_counsel", []):
        out.pop(a.lower(), None)
    _cache.update(counsel_key=key, counsel=out)
    return out


def _tags(m) -> dict[str, list[dict]]:
    by_doc = defaultdict(list)
    for a in Annotations.load(m).applications(parts=True):
        if a["tag"] in (PRIVILEGE_CHECK, CLEARED) and "doc" in a["target"]:
            by_doc[a["target"]["doc"]].append(a)
    return by_doc


def screen(m, doc: str, tags=None) -> list[dict]:
    """Why ``doc`` is screened (empty: it isn't)."""
    tags = _tags(m) if tags is None else tags
    if any(a["tag"] == CLEARED and a["by"] != CLAUDE for a in tags.get(doc, [])):
        return []
    md = _meta(m, doc)
    why = []
    head = "\n".join(x for x in (md.get("subject"), md.get("title")) if x) + "\n" + _text(m, doc)[:HEAD]
    marks = sorted({x.lower() for x in MARKING.findall(head)})
    if marks:
        why.append({"reason": "marked", "words": marks})
    if md.get("kind") == "email":
        addrs = [a.lower() for k in ("from", "to", "cc", "bcc") for a in (md.get(k) or [])]
        doms = {_domain(a) for a in addrs if "@" in a}
        c = counsel(m)
        sender = [a.lower() for a in (md.get("from") or [])]
        to = [a.lower() for a in (md.get("to") or [])]
        recipients = len(set(addrs) - set(sender))
        on = [a for a in dict.fromkeys(sender + (to if recipients <= 4 else [])) if a in c]
        topic = LEGAL_TOPIC.findall(head)
        if on and len(doms) == 1 and topic:
            why.append({"reason": "counsel", "who": on, "basis": [c[a] for a in on],
                        "topic": sorted({t.lower() for t in topic})[:5],
                        "detail": f"an email within {next(iter(doms))} with counsel on it, about "
                                  + ", ".join(sorted({t.lower() for t in topic})[:3])})
    flags = [a for a in tags.get(doc, []) if a["tag"] == PRIVILEGE_CHECK]
    if flags:
        why.append({"reason": "flagged", "by": sorted({a["by"] for a in flags})})
    return why


def arrived(m, doc: str) -> dict:
    """How the document came into the matter: a production received from a
    party (volume, Bates), or a collection (custodian, path), or attached to
    another document that did."""
    md = _meta(m, doc)
    if md.get("produced"):
        p = md["produced"]
        return {"how": "produced", "party": p.get("party"), "volume": p.get("volume"),
                "bates": [p.get("begin"), p.get("end") or p.get("begin")]}
    obj = m.objects.get_object(doc)
    if obj.get("parent"):
        return {"how": "attached", "to": obj["parent"], "parent": arrived(m, obj["parent"])}
    src = obj.get("source") or {}
    return {"how": "collected", "collection": src.get("collection"),
            "custodian": (obj.get("custodian") or "").removeprefix("cust:") or None, "path": src.get("path")}


def screened(m, docs) -> dict[str, list[dict]]:
    """Doc -> reasons, for those of ``docs`` that are screened."""
    tags = _tags(m)
    out = {}
    for d in docs:
        why = screen(m, d, tags)
        if why:
            out[d] = why
    return out


def describe(why: list[dict]) -> str:
    parts = []
    for w in why:
        if w["reason"] == "marked":
            parts.append("marked " + ", ".join(f"“{x}”" for x in w["words"]))
        elif w["reason"] == "counsel":
            parts.append(w["detail"] + " (" + ", ".join(w["who"]) + ")")
        else:
            parts.append("tagged check-privilege by " + ", ".join(w["by"]))
    return "; ".join(parts)
