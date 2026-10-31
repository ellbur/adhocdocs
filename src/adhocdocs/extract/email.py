"""RFC 822 email (.eml).

The text starts with a header block (``From:``, ``To:``, ``Cc:``, ``Bcc:``,
``Date:``, ``Subject:``, one line each), then a blank line and the body:
the text/plain part, or the HTML part rendered to text. Quoted replies and
signatures get segments. Attachments become child documents.
"""

import email
import email.policy
import re
from email.utils import getaddresses, parsedate_to_datetime

from . import Child, Extraction, TextBuilder, text_presentation
from .simple import html_to_text

HEADERS = ("From", "To", "Cc", "Bcc", "Date", "Subject")
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
    body = msg.get_body(preferencelist=("plain", "html"))
    start = b.length
    if body is not None:
        content = body.get_content()
        if body.get_content_subtype() == "html":
            html_to_text(content, b)
        else:
            b.add(content.rstrip("\n"))
    _mark_body(b, start, b.text[start:])

    children = []
    for i, part in enumerate(msg.iter_attachments(), 1):
        payload = part.get_payload(decode=True)
        if payload is None and part.get_content_type() == "message/rfc822":
            payload = part.get_payload(0).as_bytes()
        if payload is not None:
            children.append(Child(part=str(i), name=part.get_filename(), data=payload))

    meta = {"subject": msg.get("Subject"), "message_id": msg.get("Message-ID"),
            "in_reply_to": msg.get("In-Reply-To")}
    for name in ("from", "to", "cc", "bcc"):
        addrs = _addresses(msg.get_all(name, []))
        if addrs:
            meta[name] = addrs
    if msg.get("Date"):
        try:
            meta["date"] = parsedate_to_datetime(msg["Date"]).isoformat()
        except (TypeError, ValueError):
            pass
    meta = {k: v for k, v in meta.items() if v}
    layer = b.layer("native", primary=True)
    layer.presentation = text_presentation(layer.text, layer.segments)
    return Extraction(kind="email", layers=[layer], metadata=meta, children=children)
