"""Outlook messages (from a PST or a .msg file) as RFC 822 email.

Both formats store a message as MAPI properties: the message's own, one set
per recipient, and one per attachment (whose data is bytes or another
message). Readers fill in a :class:`Message`; :func:`to_eml` writes it as
an email, so threading, people, the viewer and productions treat it like
any other.

What the email holds:

- **Headers.** A message that arrived over the internet keeps the headers
  it arrived with (``PidTagTransportMessageHeaders``), minus the MIME ones,
  which are rebuilt. Others (internal Exchange mail, sent items, drafts)
  get headers made from their properties: From, To, Cc, Bcc, Date,
  Subject, Message-ID, In-Reply-To and References. An Exchange address
  with no SMTP address is given as the display name alone.
- **Body.** The plain-text body and the HTML body, as alternatives, when
  there are both. HTML that exists only inside the compressed RTF body is
  recovered from it (MS-OXRTFEX); RTF with no HTML becomes plain text.
- **Attachments**, by value, with their names and types. An attached
  message becomes a ``message/rfc822`` part, converted the same way.
  Attachments by reference (a path on someone's computer) have no data,
  and are left out.
- ``X-Adhoc-Converted-From``, naming what the email was made from, and
  ``X-Adhoc-Unread-Attachment`` for each attached message the reader
  couldn't get at.
"""

import email
import email.policy
import hashlib
import mimetypes
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime

# Property IDs (MS-OXPROPS)
SUBJECT = 0x0037
CLIENT_SUBMIT_TIME = 0x0039
SENT_REPRESENTING_NAME = 0x0042
SENT_REPRESENTING_ADDRTYPE = 0x0064
SENT_REPRESENTING_EMAIL = 0x0065
TRANSPORT_HEADERS = 0x007D
SENDER_NAME = 0x0C1A
SENDER_ADDRTYPE = 0x0C1E
SENDER_EMAIL = 0x0C1F
RECIPIENT_TYPE = 0x0C15
DELIVERY_TIME = 0x0E06
BODY = 0x1000
RTF_COMPRESSED = 0x1009
HTML = 0x1013
INTERNET_MESSAGE_ID = 0x1035
REFERENCES = 0x1039
IN_REPLY_TO = 0x1042
DISPLAY_NAME = 0x3001
ADDRTYPE = 0x3002
EMAIL_ADDRESS = 0x3003
CREATION_TIME = 0x3007
ATTACH_DATA = 0x3701
ATTACH_FILENAME = 0x3704
ATTACH_METHOD = 0x3705
ATTACH_LONG_FILENAME = 0x3707
ATTACH_MIME_TAG = 0x370E
ATTACH_CONTENT_ID = 0x3712
SMTP_ADDRESS = 0x39FE
INTERNET_CPID = 0x3FDE
MESSAGE_CODEPAGE = 0x3FFD
SENDER_SMTP = 0x5D01
SENT_REPRESENTING_SMTP = 0x5D02
MESSAGE_CLASS = 0x001A

# Property types (MS-OXCDATA)
PT_I2, PT_LONG, PT_R4, PT_DOUBLE, PT_CURRENCY, PT_APPTIME = 0x0002, 0x0003, 0x0004, 0x0005, 0x0006, 0x0007
PT_ERROR, PT_BOOLEAN, PT_OBJECT, PT_I8 = 0x000A, 0x000B, 0x000D, 0x0014
PT_STRING8, PT_UNICODE, PT_SYSTIME, PT_CLSID, PT_BINARY = 0x001E, 0x001F, 0x0040, 0x0048, 0x0102

ATTACH_BY_VALUE, ATTACH_EMBEDDED_MSG = 1, 5
MIME_HEADERS = {"content-type", "content-transfer-encoding", "mime-version", "content-disposition"}


@dataclass
class Attachment:
    props: dict
    data: bytes | None = None
    message: "Message | None" = None
    unread: bool = False            # an attached message the reader couldn't get


@dataclass
class Message:
    props: dict = field(default_factory=dict)
    recipients: list[dict] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)


def filetime(n: int) -> datetime | None:
    if not n:
        return None
    try:
        return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=n // 10)
    except OverflowError:
        return None


def codepage(props: dict) -> str:
    """The Python codec for the message's 8-bit strings."""
    for tag in (MESSAGE_CODEPAGE, INTERNET_CPID):
        cp = props.get(tag)
        if isinstance(cp, int) and cp:
            name = {65001: "utf-8", 20127: "ascii", 28591: "latin-1"}.get(cp, f"cp{cp}")
            try:
                "".encode(name)
                return name
            except LookupError:
                pass
    return "cp1252"


def decode(ptype: int, data: bytes, cp: str = "cp1252"):
    """A property's value from its stored bytes. 8-bit strings stay bytes
    until :func:`finish_strings` knows the message's code page."""
    if ptype == PT_UNICODE:
        return data.decode("utf-16-le", "replace").rstrip("\x00")
    if ptype == PT_STRING8:
        return data.rstrip(b"\x00").decode(cp, "replace")
    if ptype == PT_BOOLEAN:
        return bool(data[:1] and data[0])
    if ptype == PT_I2:
        return struct.unpack("<h", data[:2])[0]
    if ptype in (PT_LONG, PT_ERROR):
        return struct.unpack("<i", data[:4])[0]
    if ptype in (PT_I8, PT_CURRENCY):
        return struct.unpack("<q", data[:8])[0]
    if ptype == PT_SYSTIME:
        return filetime(struct.unpack("<Q", data[:8])[0])
    if ptype in (PT_DOUBLE, PT_APPTIME):
        return struct.unpack("<d", data[:8])[0]
    if ptype == PT_R4:
        return struct.unpack("<f", data[:4])[0]
    return data


def finish_strings(props: dict, raw8: dict, cp: str):
    """Decode 8-bit strings (``raw8``: id → bytes) with the code page, and
    drop the prefix marker PSTs can store before a subject (``\x01`` and
    the prefix's length)."""
    for pid, data in raw8.items():
        props.setdefault(pid, data.rstrip(b"\x00").decode(cp, "replace"))
    s = props.get(SUBJECT)
    if isinstance(s, str) and len(s) >= 2 and s[0] == "\x01":
        props[SUBJECT] = s[2:]


# ---------------------------------------------------------------- RTF

_RTF_PREBUF = (b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern "
               b"\\fscript \\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0"
               b"\\blue0\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx")


def decompress_rtf(data: bytes) -> bytes:
    """PidTagRtfCompressed to RTF (MS-OXRTFCP)."""
    if len(data) < 16:
        raise ValueError("compressed RTF too short")
    comp_size, raw_size, ctype, _crc = struct.unpack_from("<IIII", data)
    body = data[16:4 + comp_size]
    if ctype == 0x414C454D:                       # "MELA": stored uncompressed
        return body[:raw_size]
    if ctype != 0x75465A4C:                       # "LZFu"
        raise ValueError(f"unknown RTF compression {ctype:#x}")
    window = bytearray(4096)
    window[:len(_RTF_PREBUF)] = _RTF_PREBUF
    wpos = len(_RTF_PREBUF)
    out = bytearray()
    i = 0
    while i < len(body):
        control = body[i]
        i += 1
        for bit in range(8):
            if i >= len(body):
                break
            if control & (1 << bit):
                if i + 1 >= len(body):
                    return bytes(out)
                ref = (body[i] << 8) | body[i + 1]
                i += 2
                offset, length = ref >> 4, (ref & 0xF) + 2
                if offset == wpos:
                    return bytes(out)
                for k in range(length):
                    b = window[(offset + k) % 4096]
                    out.append(b)
                    window[wpos] = b
                    wpos = (wpos + 1) % 4096
            else:
                b = body[i]
                i += 1
                out.append(b)
                window[wpos] = b
                wpos = (wpos + 1) % 4096
    return bytes(out)


_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|(\r\n|\r|\n)|([^\\{}\r\n]+)", re.S)
_SKIP = {b"fonttbl", b"colortbl", b"stylesheet", b"info", b"pict", b"object", b"header", b"footer",
         b"headerl", b"headerr", b"footerl", b"footerr", b"listtable", b"listoverridetable",
         b"rsidtbl", b"generator", b"xmlnstbl", b"themedata", b"colorschememapping", b"latentstyles",
         b"datastore", b"filetbl", b"revtbl", b"mmathPr", b"pgdsctbl"}
_CHARS = {b"par": "\n", b"line": "\n", b"tab": "\t", b"emdash": "\u2014", b"endash": "\u2013",
          b"lquote": "\u2018", b"rquote": "\u2019", b"ldblquote": "\u201c", b"rdblquote": "\u201d",
          b"bullet": "\u2022", b"emspace": "\u2003", b"enspace": "\u2002", b"~": "\u00a0", b"_": "\u2011"}


def rtf_text(rtf: bytes) -> tuple[str, bool]:
    """(text, is_html): the HTML an RTF body encapsulates (MS-OXRTFEX), or,
    for RTF that wasn't made from HTML, its plain text."""
    from_html = b"\\fromhtml" in rtf[:1000]
    cp = "cp1252"
    m = re.search(rb"\\ansicpg(\d+)", rtf[:1000])
    if m:
        try:
            "".encode(f"cp{int(m.group(1))}")
            cp = f"cp{int(m.group(1))}"
        except LookupError:
            pass
    out: list[str] = []
    pending = bytearray()          # \'hh bytes, decoded together (multi-byte code pages)
    # Group state: (skip, in_htmltag, htmlrtf, uc)
    stack = []
    skip, in_tag, htmlrtf, uc = False, False, False, 1
    star = False
    to_skip = 0                    # fallback characters still to drop after \uN

    def flush():
        if pending:
            out.append(pending.decode(cp, "replace"))
            pending.clear()

    def emit(s: str):
        if skip:
            return
        if from_html and htmlrtf and not in_tag:
            return
        if from_html and not in_tag and not out and not s.strip():
            return
        out.append(s)

    for tok in _TOKEN.finditer(rtf):
        word, param, hexb, sym, brace, nl, text = tok.groups()
        if to_skip and (hexb or text):
            if hexb:
                to_skip -= 1
                continue
            n = min(to_skip, len(text))
            to_skip -= n
            text = text[n:]
            if not text:
                continue
        if hexb is None:
            flush()
        if brace == b"{":
            stack.append((skip, in_tag, htmlrtf, uc))
            star = False
        elif brace == b"}":
            if stack:
                skip, in_tag, htmlrtf, uc = stack.pop()
            star = False
        elif nl:
            continue
        elif hexb:
            if not skip and not (from_html and htmlrtf and not in_tag):
                pending.append(int(hexb, 16))
        elif sym:
            if sym == b"*":
                star = True
            elif sym in (b"\\", b"{", b"}"):
                emit(sym.decode())
            elif sym in _CHARS:
                emit(_CHARS[sym])
        elif word:
            if star:
                star = False
                if word == b"htmltag" and from_html:
                    in_tag = True
                    continue
                skip = True
                continue
            if word in _SKIP:
                skip = True
            elif word == b"htmlrtf":
                htmlrtf = param != b"0"
            elif word == b"uc":
                uc = int(param or 1)
            elif word == b"u":
                n = int(param or 0)
                emit(chr(n + 65536 if n < 0 else n))
                to_skip = uc
            elif word in _CHARS:
                emit(_CHARS[word])
            elif word == b"htmlbase":
                pass
        elif text:
            emit(text.decode(cp, "replace"))
    flush()
    s = "".join(out)
    if not from_html:
        s = re.sub(r"[ \t]+\n", "\n", s).strip("\n") + "\n"
    return s, from_html


# ---------------------------------------------------------------- to email

def _address(props: dict, name_id, type_id, email_id, smtp_id) -> Address | str | None:
    name = props.get(name_id) or ""
    addr = props.get(smtp_id) or ""
    if not addr and str(props.get(type_id) or "SMTP").upper() == "SMTP":
        addr = props.get(email_id) or ""
    addr = addr.strip()
    if addr and "@" in addr and " " not in addr:
        try:
            user, domain = addr.rsplit("@", 1)
            return Address(display_name=name if name != addr else "", username=user, domain=domain)
        except (ValueError, IndexError):
            pass
    return name or None


def _set_addresses(m: EmailMessage, header: str, addrs):
    addrs = [a for a in addrs if a]
    if not addrs:
        return
    # A display name with no usable address is written as "Name" <>.
    parts = [str(a) if isinstance(a, Address) else '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '" <>'
             for a in addrs]
    try:
        m[header] = ", ".join(parts)
    except Exception:
        m[header] = ", ".join(a.display_name or a.addr_spec if isinstance(a, Address) else a for a in addrs)


def _bodies(props: dict) -> tuple[str | None, str | None]:
    cp = codepage(props)
    plain = props.get(BODY)
    html = props.get(HTML)
    if isinstance(html, bytes):
        html = html.decode(_html_charset(html) or cp, "replace")
    if isinstance(plain, bytes):
        plain = plain.decode(cp, "replace")
    if html is None or plain is None:
        rtf = props.get(RTF_COMPRESSED)
        if isinstance(rtf, bytes) and rtf:
            try:
                text, is_html = rtf_text(decompress_rtf(rtf))
            except Exception:
                text, is_html = None, False
            if text:
                if is_html and html is None:
                    html = text
                elif not is_html and plain is None:
                    plain = text
    plain = plain.replace("\r\n", "\n") if plain else plain
    html = html.replace("\r\n", "\n") if html else html
    return plain or None, html or None


def _html_charset(html: bytes) -> str | None:
    m = re.search(rb"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)", html[:2000])
    if m:
        name = m.group(1).decode("ascii").lower()
        try:
            "".encode(name)
            return name
        except LookupError:
            return None
    return None


def _date(props: dict) -> datetime | None:
    for tag in (CLIENT_SUBMIT_TIME, DELIVERY_TIME, CREATION_TIME):
        if isinstance(props.get(tag), datetime):
            return props[tag]
    return None


def _transport_headers(text: str) -> list[tuple[str, str]]:
    """The headers a message arrived with, minus the MIME ones, unfolded."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            if out:
                break
            continue
        if line[0] in " \t" and out:
            out[-1] = (out[-1][0], out[-1][1] + "\n" + line)
        elif ":" in line:
            k, v = line.split(":", 1)
            out.append((k.strip(), v.lstrip()))
    return [(k, v) for k, v in out if k.lower() not in MIME_HEADERS and k]


def _fix_boundaries(m: EmailMessage):
    """Boundaries from the content, not at random, so the same message
    always makes the same bytes (and the same document)."""
    parts = [p for p in m.walk() if p.is_multipart()]
    for n, part in enumerate(parts, 1):
        part.set_boundary(f"=_adhoc_{n}")
    digest = hashlib.sha256(m.as_bytes(policy=email.policy.default)).hexdigest()[:24]
    for n, part in enumerate(parts, 1):
        part.set_boundary(f"=_adhoc_{digest}_{n}")


def to_eml(msg: Message, source: str, depth: int = 0) -> bytes:
    """The message as RFC 822 bytes. ``source`` goes in
    ``X-Adhoc-Converted-From`` (e.g. ``Outlook .msg``)."""
    p = msg.props
    m = EmailMessage(policy=email.policy.default)
    plain, html = _bodies(p)
    if plain is not None:
        m.set_content(plain, cte="quoted-printable" if not plain.isascii() else "7bit")
        if html is not None:
            m.add_alternative(html, subtype="html", cte="quoted-printable")
    elif html is not None:
        m.set_content(html, subtype="html", cte="quoted-printable")
    else:
        m.set_content("")
    for i, a in enumerate(msg.attachments, 1):
        ap = a.props
        name = ap.get(ATTACH_LONG_FILENAME) or ap.get(ATTACH_FILENAME) or ap.get(DISPLAY_NAME)
        if a.message is not None and depth < 20:
            inner = email.message_from_bytes(to_eml(a.message, source, depth + 1), policy=email.policy.default)
            subject = a.message.props.get(SUBJECT)
            fname = name or f"{subject or f'message-{i}'}.eml"
            if not fname.lower().endswith((".eml", ".msg")):
                fname += ".eml"
            m.add_attachment(inner, filename=fname)
        elif a.data is not None:
            mime = (ap.get(ATTACH_MIME_TAG) or mimetypes.guess_type(name or "")[0] or "application/octet-stream")
            maintype, _, subtype = mime.strip().lower().partition("/")
            if not subtype or maintype in ("multipart", "message"):
                maintype, subtype = "application", "octet-stream"
            cid = ap.get(ATTACH_CONTENT_ID)
            cid = f"<{cid.strip().strip('<>')}>" if isinstance(cid, str) and cid.strip() else None
            m.add_attachment(a.data, maintype=maintype, subtype=subtype, filename=name or f"attachment-{i}", cid=cid)
    _fix_boundaries(m)
    m["X-Adhoc-Converted-From"] = source
    for a in msg.attachments:
        if a.unread:
            name = a.props.get(ATTACH_LONG_FILENAME) or a.props.get(DISPLAY_NAME) or "an attached message"
            m["X-Adhoc-Unread-Attachment"] = name
    content_headers = [(k, v) for k, v in m.items()]
    for k in list(m.keys()):
        del m[k]

    transport = p.get(TRANSPORT_HEADERS)
    top = _transport_headers(transport) if isinstance(transport, str) else []
    if top:
        head = "".join(f"{k}: {v}\n" for k, v in top)
    else:
        h = EmailMessage(policy=email.policy.default)
        frm = _address(p, SENT_REPRESENTING_NAME, SENT_REPRESENTING_ADDRTYPE, SENT_REPRESENTING_EMAIL,
                       SENT_REPRESENTING_SMTP) or _address(p, SENDER_NAME, SENDER_ADDRTYPE, SENDER_EMAIL, SENDER_SMTP)
        _set_addresses(h, "From", [frm])
        by_type = {1: [], 2: [], 3: []}
        for r in msg.recipients:
            kind = (r.get(RECIPIENT_TYPE) or 1) & 0xF        # unset: To
            by_type.setdefault(kind, []).append(
                _address(r, DISPLAY_NAME, ADDRTYPE, EMAIL_ADDRESS, SMTP_ADDRESS))
        for header, t in (("To", 1), ("Cc", 2), ("Bcc", 3)):
            _set_addresses(h, header, by_type.get(t, []))
        d = _date(p)
        if d:
            h["Date"] = format_datetime(d)
        if p.get(SUBJECT):
            h["Subject"] = p[SUBJECT]
        for header, tag in (("Message-ID", INTERNET_MESSAGE_ID), ("In-Reply-To", IN_REPLY_TO),
                            ("References", REFERENCES)):
            v = p.get(tag)
            if isinstance(v, str) and v.strip():
                h[header] = " ".join(v.split())
        head = h.as_string().split("\n\n", 1)[0].rstrip("\n")
        head = head + "\n" if head else ""
    for k, v in content_headers:
        m[k] = v
    return head.encode("utf-8") + m.as_bytes(policy=email.policy.default.clone(linesep="\n"))
