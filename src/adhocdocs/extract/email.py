"""RFC 822 email (.eml).

The text starts with a header block (``From:``, ``To:``, ``Cc:``, ``Bcc:``,
``Date:``, ``Subject:``, one line each), then a blank line and the body:
the text/plain part, or the HTML part rendered to text. Quoted replies and
signatures get segments. Attachments become child documents.

Inline images (``Content-ID`` parts, usually in a ``multipart/related``)
are child documents too, even when nested where ``iter_attachments``
doesn't look. Where the body shows one (``<img src="cid:…">`` in HTML, or
Outlook's ``[cid:…]`` in plain text) the layer has an ``image`` segment:
zero-width where an HTML image was, or over the ``[cid:…]`` marker. It
names the ``cid`` and, when the message holds that part, its ``content``
(the image's hash), which the presentation shows.
"""

import email
import email.policy
import re
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import unquote

from ..objects import sha256

from . import Child, Extraction, TextBuilder, text_presentation
from .simple import html_to_text

HEADERS = ("From", "To", "Cc", "Bcc", "Date", "Subject")
CID_MARKER = re.compile(r"\[cid:([^\]\s]+)\]")
QUOTE_START = re.compile(r"^(-{2,}\s*Original Message\s*-{2,}|On .{5,200} wrote:|From: .+)$")


def _addresses(values) -> list[str]:
    return [addr.lower() for _, addr in getaddresses([str(v) for v in values]) if addr]


def _mark_body(b: TextBuilder, start: int, body: str):
    lines, pos = [], start
    for line in body.split("\n"):
        lines.append((pos, line))
        pos += len(line) + 1
    end = start + len(body)
    quoted_from = None
    for i, (lpos, line) in enumerate(lines):
        if QUOTE_START.match(line.strip()) or line.startswith(">"):
            quoted_from = i
            break
    main_end = lines[quoted_from][0] if quoted_from is not None else end
    sig = next((lpos for lpos, line in lines if line.rstrip() == "--" and lpos < main_end), None)
    b.segment(start, end, "body")
    if quoted_from is not None:
        b.segment(main_end, end, "quoted")
    if sig is not None:
        b.segment(sig, main_end, "signature")


def _cid(value: str) -> str:
    return unquote(value.strip().strip("<>")).lower()


def _multiparts(part):
    """The multiparts in this message, not in messages attached to it."""
    if part.is_multipart() and part.get_content_maintype() != "message":
        yield part
        for sub in part.get_payload():
            yield from _multiparts(sub)


def _children(msg):
    """The attachments, then inline parts nested out of ``iter_attachments``'
    reach (numbered after them, so earlier parts keep their numbers), and
    each Content-ID's content hash."""
    parts = list(msg.iter_attachments())
    seen = {id(p) for p in parts}
    for part in _multiparts(msg):
        if part.get_content_type() != "multipart/related" or part is msg:
            continue
        subs = part.get_payload()
        start = (part.get_param("start") or "").strip()
        root = next((s for s in subs if start and str(s.get("Content-ID", "")).strip() == start), subs[0] if subs else None)
        for sub in subs:
            if sub is not root and id(sub) not in seen and not sub.is_multipart():
                parts.append(sub)
                seen.add(id(sub))
    children, cids = [], {}
    for i, part in enumerate(parts, 1):
        payload = part.get_payload(decode=True)
        if payload is None and part.get_content_type() == "message/rfc822":
            payload = part.get_payload(0).as_bytes()
        if payload is not None:
            children.append(Child(part=str(i), name=part.get_filename(), data=payload))
            if part.get("Content-ID"):
                cids.setdefault(_cid(str(part["Content-ID"])), sha256(payload))
    return children, cids


def extract_email(data: bytes) -> Extraction:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    b = TextBuilder()
    for name in HEADERS:
        value = msg.get(name)
        if value is None:
            continue
        s, _ = b.add(f"{name}: ")
        _, e = b.add(str(value).replace("\n", " "))
        b.segment(s, e, "header", name=name.lower())
        b.add("\n")
    b.add("\n")
    children, cids = _children(msg)
    body = msg.get_body(preferencelist=("plain", "html"))
    start = b.length
    images = []
    if body is not None:
        content = body.get_content()
        if body.get_content_subtype() == "html":
            html_to_text(content, b, images)
            images = [(at, at, cid) for at, cid in images]
        else:
            b.add(content.rstrip("\n"))
            images = [(start + mt.start(), start + mt.end(), mt.group(1))
                      for mt in CID_MARKER.finditer(b.text[start:])]
    _mark_body(b, start, b.text[start:])
    for s, e, cid in images:
        cid = _cid(cid)
        extra = {"content": cids[cid]} if cid in cids else {}
        b.segment(s, e, "image", cid=cid, **extra)

    meta = {"subject": msg.get("Subject"), "message_id": (msg.get("Message-ID") or "").strip() or None,
            "in_reply_to": (msg.get("In-Reply-To") or "").strip() or None,
            "references": re.findall(r"<[^>]+>", msg.get("References") or "")}
    for name in ("from", "to", "cc", "bcc"):
        values = [str(v) for v in msg.get_all(name, [])]
        addrs = _addresses(values)
        if addrs:
            meta[name] = addrs
            meta[name + "_names"] = [n or a for n, a in getaddresses(values) if a]
    if msg.get("Date"):
        try:
            meta["date"] = parsedate_to_datetime(msg["Date"]).isoformat()
        except (TypeError, ValueError):
            pass
    meta = {k: v for k, v in meta.items() if v}
    layer = b.layer("native", primary=True)
    layer.presentation = text_presentation(layer.text, layer.segments)
    return Extraction(kind="email", layers=[layer], metadata=meta, children=children)
