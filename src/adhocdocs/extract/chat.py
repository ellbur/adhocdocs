"""Chat messages: Slack exports, WhatsApp chat exports and RSMF.

An export is a container, like a mailbox. Its children are **chat days**:
one document per conversation per day, each made from the export the way
a PST's messages are made into emails (see docs/CHATS.md). A chat day is a small
JSON file (``<day>.chat.json``, format ``adhocdocs-chat-day``) holding the
conversation (its name, platform and members) and that day's messages, with
the attachments the export has, base64, so a day is self-contained: it
reads alike wherever it is, and its attachments are its children, as an
email's are.

A chat day's text starts with a header block (``Conversation:``,
``Platform:``, ``Members:``, ``Date:``), then one message per line::

    09:03 Alice Smith: Morning, did the shipment go out?
      ↳ 09:07 Bob Jones: Yes, this morning.
        (edited)
        [attachment: packing-slip.pdf]
        :thumbsup: Alice Smith

Each message is a ``message`` segment (``id``, ``author``, ``name``,
``time``, and ``parent`` for a reply in a thread) over its whole block;
inside it, ``sender`` is the time and name, ``message-text`` what was
written, and ``chat-note`` what the platform says about it (edited,
deleted, reactions); ``attachment`` names a file. The time and name, the
arrow and the notes are ours, as an email's header lines are; what was
written is literal. ``msg=<id>`` locators name messages (docs/LOCATORS.md).

Readers:

- **Slack** (a workspace export zip: ``users.json``, ``channels.json``,
  ``groups.json``, ``dms.json``, ``mpims.json`` and a folder of day files
  for each conversation). Times are UTC. Mentions (``<@U123>``) are shown
  as the person's name and links as their text; the files a message shared
  are named but not in a standard export.
- **WhatsApp** ("Export chat": ``_chat.txt`` or ``WhatsApp Chat with
  ….txt``, alone or zipped with its media). Times are the phone's local
  time, as written. Whether ``3/4/24`` is March or April is read from the
  chat itself (a day over 12 settles it); if nothing does, a 12-hour clock
  reads month first and a 24-hour clock day first, and the day says so.
- **RSMF** (Relativity Short Message Format: an ``.rsmf`` email carrying
  ``rsmf.zip``, or that zip alone, with ``rsmf_manifest.json``), which is
  how chats (Teams, Slack, iMessage, SMS, …) are usually produced. Times
  are as written, with their offsets.
"""

import base64
import email
import email.policy
import hashlib
import html
import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath

from . import Child, Extraction, Layer, TextBuilder, text_presentation

FORMAT = "adhocdocs-chat-day"
DAY_SUFFIX = ".chat.json"
MAX_MEMBERS_SHOWN = 30
PLATFORMS = {"slack": "Slack", "whatsapp": "WhatsApp", "teams": "Microsoft Teams", "imessage": "iMessage",
             "sms": "SMS", "rsmf": "RSMF"}


# -- chat days ---------------------------------------------------------------------------------

def conversation_key(platform: str, native_id: str | None, name: str, people: list[str]) -> str:
    """What makes two chat days the same conversation's: the platform's own
    ID where it is unique (a Slack channel), or else the name and who took
    part, so two people's chats "with Alice" stay apart, and two phones'
    copies of one group chat come together."""
    if native_id:
        return f"chat:{platform}/{native_id}"
    h = hashlib.sha256(json.dumps(sorted(set(people)), ensure_ascii=False).encode()).hexdigest()[:12]
    return f"chat:{platform}/{name}#{h}"


def make_day(platform: str, conversation: dict, day: str, messages: list, files: list, *,
             source: str | None = None, zone: str | None = None, notes: list | None = None) -> bytes:
    """A chat day's bytes. ``conversation`` has ``key``, ``name``, ``kind``
    and ``participants`` ({id, name, email, phone}); each message ``id``,
    ``author``, ``time`` (ISO), ``text``, and maybe ``parent``, ``event``,
    ``edited``, ``deleted``, ``reactions`` ([{emoji, by}]) and
    ``attachments`` ([{name, file}], ``file`` numbering ``files`` from 1);
    each file ``name`` and ``data`` (bytes)."""
    out = {"format": FORMAT, "version": 1, "platform": platform, "conversation": conversation, "day": day,
           "messages": messages,
           "files": [{"name": f["name"], "data": base64.b64encode(f["data"]).decode("ascii")} for f in files]}
    if source:
        out["source"] = source
    if zone:
        out["zone"] = zone
    if notes:
        out["notes"] = notes
    return json.dumps(out, ensure_ascii=False, indent=1).encode("utf-8")


def _label(p: dict | None, pid) -> str:
    if not p:
        return str(pid) if pid else "(unknown)"
    return p.get("name") or p.get("email") or p.get("phone") or str(pid)


def extract_chat_day(data: bytes) -> Extraction:
    day = json.loads(data.decode("utf-8"))
    if day.get("format") != FORMAT:
        raise ValueError("not a chat day")
    conv = day["conversation"]
    platform = day.get("platform") or "chat"
    people = {p["id"]: p for p in conv.get("participants", [])}
    who = lambda pid: _label(people.get(pid), pid)
    msgs = day.get("messages", [])
    b = TextBuilder()

    def header(label, value, name):
        s, _ = b.add(f"{label}: ")
        _, e = b.add(value)
        b.segment(s, e, "header", name=name)
        b.add("\n")
    header("Conversation", conv.get("name") or conv["key"], "conversation")
    shown = PLATFORMS.get(platform, platform)
    if day.get("source") and day["source"] != platform:
        shown += f" (from {PLATFORMS.get(day['source'], day['source'])})"
    header("Platform", shown, "platform")
    members = [who(p["id"]) for p in conv.get("participants", [])]
    if members:
        header("Members", ", ".join(members) if len(members) <= MAX_MEMBERS_SHOWN else f"{len(members)} people",
               "members")
    header("Date", day["day"] + (f" (times {day['zone']})" if day.get("zone") else ""), "date")
    for note in day.get("notes") or []:
        header("Note", note, "note")
    b.add("\n")

    times = {m["id"]: m.get("time") for m in msgs}
    children = [Child(part=str(i), name=f["name"], data=base64.b64decode(f["data"]))
                for i, f in enumerate(day.get("files", []), 1)]
    authors: list[str] = []
    for k, msg in enumerate(msgs):
        start = b.length
        parent = msg.get("parent")
        reply = parent and parent != msg["id"]
        t = msg.get("time") or ""
        hhmm = t[11:16] if len(t) >= 16 else "--:--"
        if msg.get("event"):
            s, e = b.add(f"{'  ↳ ' if reply else ''}{hhmm} ")
        else:
            s, e = b.add(f"{'  ↳ ' if reply else ''}{hhmm} {who(msg.get('author'))}: ")
            if msg.get("author") not in authors:
                authors.append(msg.get("author"))
        b.segment(s, e, "sender")
        if reply and parent not in times:
            s, e = b.add("(in a thread from earlier) ")
            b.segment(s, e, "chat-note")
        text = msg.get("text") or ""
        ts, te = b.add(text)
        b.segment(ts, te, "message-text")

        def note(s):
            b.add("\n")
            a, z = b.add("    " + s)
            b.segment(a, z, "chat-note")
        if msg.get("deleted") and not text.strip():
            note("(deleted)")
        if msg.get("edited"):
            note("(edited" + (f" {msg['edited'][11:16]}" if len(msg["edited"]) >= 16 else "") + ")")
        for att in msg.get("attachments") or []:
            b.add("\n")
            have = att.get("file")
            a, z = b.add(f"    [attachment: {att.get('name') or 'unnamed'}{'' if have else ', not in the export'}]")
            b.segment(a, z, "attachment", name=att.get("name"), **({"part": str(have)} if have else {}))
        reactions = msg.get("reactions") or []
        if reactions:
            note("; ".join(f"{r.get('emoji')} {', '.join(who(x) for x in r.get('by') or [])}".strip()
                           for r in reactions))
        extra = {"author": msg.get("author"), "name": who(msg.get("author")) if msg.get("author") else None,
                 "time": t or None, "parent": parent if reply else None, "event": msg.get("event") or None}
        b.segment(start, b.length, "message", id=str(msg["id"]), **{k: v for k, v in extra.items() if v})
        if k < len(msgs) - 1:
            b.add("\n")

    layer = b.layer("native", primary=True)
    layer.presentation = text_presentation(layer.text, layer.segments)
    name = conv.get("name") or conv["key"]
    others = [p for p in conv.get("participants", []) if p["id"] not in authors]
    meta = {"platform": platform, "conversation": name, "conversation_kind": conv.get("kind"),
            "subject": name, "title": f"{name}, {day['day']}", "day": day["day"],
            "message_id": f"{conv['key']}/{day['day']}", "thread_key": conv["key"],
            "messages": len(msgs), "attachments": len(children), "zone": day.get("zone"),
            "participants": [who(a) for a in authors if a],
            "date": next((m["time"] for m in msgs if m.get("time")), None),
            "end_date": next((m["time"] for m in reversed(msgs) if m.get("time")), None)}
    if day.get("source"):
        meta["source_format"] = day["source"]
    for field, ids in (("from", [a for a in authors if a]), ("to", [p["id"] for p in others])):
        pairs = [(people[i]["email"].lower(), who(i)) for i in ids if (people.get(i) or {}).get("email")]
        if pairs:
            meta[field] = [a for a, _ in pairs]
            meta[field + "_names"] = [n for _, n in pairs]
    meta = {k: v for k, v in meta.items() if v not in (None, [], "")}
    return Extraction(kind="chat", layers=[layer], metadata=meta, children=children)


def _by_day(messages: list) -> dict:
    out = defaultdict(list)
    for msg in sorted(messages, key=lambda m: (m.get("time") or "", m.get("_order", 0))):
        out[(msg.get("time") or "0000-00-00")[:10]].append(msg)
    return out


def _zone(t: str) -> str | None:
    """How an ISO time names its zone: UTC, UTC-05:00, or None."""
    if t.endswith("Z"):
        return "UTC"
    m = re.search(r"([+-]\d{2}:\d{2})$", t)
    return None if not m else "UTC" if m.group(1) in ("+00:00", "-00:00") else "UTC" + m.group(1)


def _clean_msg(msg: dict) -> dict:
    return {k: v for k, v in msg.items() if not k.startswith("_") and v not in (None, [], "", False)}


def _safe(s: str) -> str:
    return re.sub(r"[/\\\x00-\x1f]", "_", s).strip() or "_"


def _container(platform: str, convs, source=None) -> Extraction:
    """An export's extraction: its children are made as they're read.
    ``convs`` yields (conversation, messages, read_file, zone, notes)."""
    meta: dict = {"platform": platform}
    if source:
        meta["source_format"] = source

    def children():
        n_convs = n_days = n_msgs = 0
        first = last = None
        try:
            for conv, messages, read_file, zone, notes in convs:
                n_convs += 1
                for day, msgs in sorted(_by_day(messages).items()):
                    files, out = [], []
                    for msg in msgs:
                        atts = []
                        for att in msg.get("attachments") or []:
                            data = read_file(att["_path"]) if att.get("_path") and read_file else None
                            if data is not None:
                                files.append({"name": att["name"], "data": data})
                                atts.append({"name": att["name"], "file": len(files)})
                            else:
                                atts.append({"name": att["name"]})
                        out.append(_clean_msg({**msg, "attachments": atts}))
                    n_days += 1
                    n_msgs += len(out)
                    times = [m["time"] for m in out if m.get("time")]
                    if times:
                        first = min(first or times[0], times[0])
                        last = max(last or times[-1], times[-1])
                    data = make_day(platform if platform != "rsmf" else conv.get("platform", "rsmf"),
                                    {k: v for k, v in conv.items() if k != "platform"}, day, out, files,
                                    source=source, zone=zone or (_zone(times[0]) if times else None), notes=notes)
                    yield Child(part=f"{_safe(conv.get('folder') or conv['name'])}/{day}", name=day + DAY_SUFFIX,
                                data=data)
        finally:
            meta.update({"conversations": n_convs, "days": n_days, "messages": n_msgs, "members": n_days})
            if first:
                meta["date"], meta["end_date"] = first, last
    return Extraction(kind="chat-export", layers=[Layer("native", "", primary=True)], metadata=meta,
                      children=children())


def _open_zip(src):
    return zipfile.ZipFile(io.BytesIO(src) if isinstance(src, bytes) else src)


def _root_of(names, marker: str) -> str | None:
    """The folder holding ``marker`` in a zip (``""`` at the top, or one
    folder down, as when an export's folder was zipped), or None."""
    for n in names:
        p = PurePosixPath(n)
        if p.name == marker and len(p.parts) <= 2 and "__MACOSX" not in p.parts:
            return "" if len(p.parts) == 1 else p.parts[0] + "/"
    return None


# -- Slack -------------------------------------------------------------------------------------

SLACK_DAY = re.compile(r"(?:^|/)[^/]+/\d{4}-\d{2}-\d{2}\.json$")
SLACK_LISTS = (("channels.json", "channel"), ("groups.json", "private channel"),
               ("mpims.json", "group DM"), ("dms.json", "DM"))
SLACK_EVENTS = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name",
                "channel_archive", "channel_unarchive", "group_join", "group_leave", "group_topic",
                "group_purpose", "group_name", "pinned_item", "reminder_add"}


def is_slack(names) -> bool:
    names = list(names)
    root = next((r for f, _ in SLACK_LISTS if (r := _root_of(names, f)) is not None), None)
    return root is not None and any(SLACK_DAY.search(n) for n in names)


def slack_text(s: str, users: dict, channels: dict) -> str:
    """Slack's markup as it shows in Slack: ``<@U1>`` as @name, ``<#C1|x>``
    as #x, ``<https://…|text>`` as text, ``<!here>`` as @here, and its
    escapes (&amp; &lt; &gt;) undone."""
    def tag(m):
        body = m.group(1)
        target, _, label = body.partition("|")
        if target.startswith("@"):
            return "@" + (label or _label(users.get(target[1:]), target[1:]))
        if target.startswith("#"):
            return "#" + (label or channels.get(target[1:], target[1:]))
        if target.startswith("!"):
            return "@" + (label or target[1:].split("^")[0])
        return label or target
    return html.unescape(re.sub(r"<([^<>]+)>", tag, s or ""))


def read_slack(src) -> Extraction:
    z = _open_zip(src)
    names = z.namelist()
    root = next(r for f, _ in SLACK_LISTS if (r := _root_of(names, f)) is not None)

    def load(n):
        try:
            return json.loads(z.read(root + n).decode("utf-8-sig"))
        except KeyError:
            return []
    users = {}
    for u in load("users.json"):
        prof = u.get("profile") or {}
        users[u["id"]] = {"id": u["id"], "name": prof.get("real_name") or u.get("real_name") or prof.get("display_name")
                          or u.get("name"), "email": prof.get("email")}
    listed = []
    for fname, kind in SLACK_LISTS:
        for c in load(fname):
            listed.append((c, kind))
    channel_names = {c["id"]: c.get("name") or c["id"] for c, _ in listed}
    days = defaultdict(list)
    for n in names:
        if n.startswith(root) and SLACK_DAY.search(n):
            rel = PurePosixPath(n[len(root):])
            if len(rel.parts) == 2:
                days[rel.parts[0]].append(n)
    seen = set()

    def convs():
        try:
            for c, kind in listed + [({"id": None, "name": f}, "channel") for f in sorted(days)]:
                folder = c.get("name") or c.get("id")
                if folder in seen or folder not in days:
                    continue
                seen.add(folder)
                members = [m for m in c.get("members") or [] if m]
                people = dict(users)
                messages = []
                for path in sorted(days[folder]):
                    try:
                        raw = json.loads(z.read(path).decode("utf-8-sig"))
                    except ValueError:
                        continue
                    for i, m in enumerate(raw if isinstance(raw, list) else []):
                        if m.get("type") != "message" or not m.get("ts"):
                            continue
                        msgs = _slack_message(m, users, people, channel_names)
                        for x in msgs:
                            x["_order"] = (path, i)
                        messages += msgs
                authors = [m["author"] for m in messages if m.get("author")]
                ids = list(dict.fromkeys(members + authors))
                if kind == "DM" or kind == "group DM":
                    shown = ", ".join(_label(people.get(i), i) for i in ids)
                    name = f"{kind}: {shown}" if shown else folder
                else:
                    name = "#" + (c.get("name") or folder)
                conv = {"key": conversation_key("slack", c.get("id") or folder, name, []), "name": name,
                        "kind": kind, "folder": folder,
                        "participants": [{k: v for k, v in (people.get(i) or {"id": i}).items() if v} for i in ids]}
                yield conv, messages, None, "UTC", None
        finally:
            z.close()
    return _container("slack", convs())


def _slack_time(ts: str) -> str:
    return datetime.fromtimestamp(int(float(ts)), timezone.utc).isoformat().replace("+00:00", "Z")


def _slack_message(m: dict, users: dict, people: dict, channels: dict) -> list:
    author = m.get("user") or m.get("bot_id") or m.get("username")
    if author and author not in people:
        prof = m.get("user_profile") or m.get("bot_profile") or {}
        people[author] = {"id": author, "name": prof.get("real_name") or prof.get("name") or m.get("username")}
    sub = m.get("subtype")
    text = slack_text(m.get("text") or "", users, channels)
    msg = {"id": m["ts"], "author": author, "time": _slack_time(m["ts"]), "text": text,
           "parent": m.get("thread_ts") if m.get("thread_ts") and m.get("thread_ts") != m["ts"] else None}
    if sub in SLACK_EVENTS:
        msg["event"] = sub
    if sub in ("tombstone", "message_deleted"):
        msg["deleted"] = True
    if (m.get("edited") or {}).get("ts"):
        msg["edited"] = _slack_time(m["edited"]["ts"])
    msg["reactions"] = [{"emoji": f":{r.get('name')}:", "by": list(r.get("users") or [])}
                        for r in m.get("reactions") or []]
    msg["attachments"] = [{"name": f.get("name") or f.get("title") or f.get("id")}
                          for f in m.get("files") or [] if f.get("mode") != "tombstone"]
    return [msg]


# -- WhatsApp ----------------------------------------------------------------------------------

_WA_TIME = r"(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{2,4}),? (\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?(?:[\s ]*([AaPp])\.?\s?[Mm]\.?)?"
WA_IOS = re.compile(r"^[‎‏]?\[" + _WA_TIME + r"\] (.*)$")
WA_ANDROID = re.compile(r"^[‎‏]?" + _WA_TIME + r" [-–] (.*)$")
WA_ATTACHED = re.compile(r"<attached: ([^>]+)>|([^\s<>:]+\.\w{2,5}) \(file attached\)")


def _wa_match(line: str):
    return WA_IOS.match(line) or WA_ANDROID.match(line)


def looks_like_whatsapp(text: str) -> bool:
    lines = [l for l in text.split("\n")[:40] if l.strip()]
    hits = [m for l in lines if (m := _wa_match(l.rstrip("\r")))]
    return len(hits) >= 2 and len(hits) * 2 >= min(len(lines), 10)


def is_whatsapp_zip(names) -> bool:
    return any(_wa_chat_member(n) for n in names)


def _wa_chat_member(n: str) -> bool:
    p = PurePosixPath(n)
    return p.suffix.lower() == ".txt" and (p.name == "_chat.txt" or p.name.startswith("WhatsApp Chat")) \
        and "__MACOSX" not in p.parts


def _wa_name(name: str | None) -> str | None:
    if not name:
        return None
    stem = PurePosixPath(name).stem
    m = re.match(r"WhatsApp Chat (?:with |- |– )?(.+)$", stem)
    return m.group(1).strip() if m else (None if stem in ("_chat", "") else stem)


def read_whatsapp(src, name: str | None = None) -> Extraction:
    """A WhatsApp export: its text (bytes of a ``.txt``), or a zip of it and
    its media (bytes or a path)."""
    if isinstance(src, bytes) and not src.startswith(b"PK\x03\x04"):
        return _container("whatsapp", _wa_convs(src.decode("utf-8-sig", errors="replace"), _wa_name(name), None))
    z = _open_zip(src)
    member = next(n for n in z.namelist() if _wa_chat_member(n))
    text = z.read(member).decode("utf-8-sig", errors="replace")
    by_name = {}
    for n in z.namelist():
        by_name.setdefault(PurePosixPath(n).name, n)

    def read_file(fname):
        try:
            return z.read(by_name[fname]) if fname in by_name else None
        except (KeyError, zipfile.BadZipFile, ValueError):
            return None

    def convs():
        try:
            yield from _wa_convs(text, _wa_name(member) or _wa_name(name), read_file)
        finally:
            z.close()
    return _container("whatsapp", convs())


def _wa_order(rows) -> tuple[str, str]:
    """Day first or month first, and why."""
    firsts = [int(r[0]) for r in rows if len(r[0]) <= 2]
    seconds = [int(r[1]) for r in rows if len(r[0]) <= 2]
    if any(len(r[0]) == 4 for r in rows):
        return "ymd", "the year is written first"
    if any(a > 12 for a in firsts):
        return "dmy", "a day over 12 comes first"
    if any(b > 12 for b in seconds):
        return "mdy", "a day over 12 comes second"
    if any(r[6] for r in rows):
        return "mdy", "nothing in the chat says; read month first, as a 12-hour clock suggests"
    return "dmy", "nothing in the chat says; read day first, as a 24-hour clock suggests"


def _wa_convs(text: str, name: str | None, read_file):
    entries = []        # (fields, rest)
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _wa_match(line)
        if m:
            entries.append([m.groups()[:7], m.group(8)])
        elif entries:
            entries[-1][1] += "\n" + line
    if not entries:
        return
    order, basis = _wa_order([e[0] for e in entries])
    notes = [f"dates are read {'day first' if order == 'dmy' else 'month first' if order == 'mdy' else 'year first'}: {basis}"] \
        if order != "ymd" else None
    messages, authors = [], []
    for k, (f, rest) in enumerate(entries):
        a, b, c, hh, mm, ss, ampm = f
        y, mo, d = (a, b, c) if order == "ymd" else (c, a, b) if order == "mdy" else (c, b, a)
        y = int(y) + (2000 if len(y) <= 2 else 0)
        h = int(hh)
        if ampm:
            h = h % 12 + (12 if ampm.lower() == "p" else 0)
        try:
            t = datetime(y, int(mo), int(d), h, int(mm), int(ss or 0)).isoformat()
        except ValueError:
            t = None
        rest = rest.lstrip("‎‏").rstrip("\n")
        who, sep, body = rest.partition(": ")
        msg = {"time": t, "_order": k}
        if sep and "\n" not in who and len(who) <= 80:
            who = who.strip("‎‏ ~ ")
            msg.update(author=who, text=body.lstrip("‎"))
            if who not in authors:
                authors.append(who)
        else:
            msg.update(event="system", text=rest)
        msg["attachments"] = [{"name": n, "_path": n} for g in WA_ATTACHED.findall(msg["text"]) for n in g if n]
        if msg["text"].strip() in ("This message was deleted", "You deleted this message", "‎This message was deleted"):
            msg["deleted"] = True
        messages.append(msg)
    for day, msgs in _by_day(messages).items():
        for i, msg in enumerate(msgs, 1):
            msg["id"] = str(i)
    people = [{"id": a, **({"phone": a} if re.fullmatch(r"\+?[\d\s\-()]{7,}", a) else {"name": a})} for a in authors]
    name = name or ", ".join(authors[:4]) or "WhatsApp chat"
    conv = {"key": conversation_key("whatsapp", None, name, authors), "name": name,
            "kind": "group" if len(authors) > 2 else "chat", "participants": people}
    yield conv, messages, read_file, "local", notes


# -- RSMF --------------------------------------------------------------------------------------

def is_rsmf_zip(names) -> bool:
    return _root_of(names, "rsmf_manifest.json") is not None


def looks_like_rsmf(head: bytes) -> bool:
    """An email whose own headers (not its body) are RSMF's."""
    headers = re.split(rb"\r?\n\r?\n", head, maxsplit=1)[0]
    return b"\nX-RSMF-" in headers or headers.startswith(b"X-RSMF-")


def _rsmf_zip(data: bytes) -> bytes:
    """The rsmf.zip an .rsmf email carries."""
    msg = email.message_from_bytes(data, policy=email.policy.default)
    zips = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload and payload.startswith(b"PK\x03\x04"):
            zips.append(((part.get_filename() or "").lower() == "rsmf.zip", payload))
    if not zips:
        raise ValueError("no rsmf.zip in this RSMF file")
    return max(zips, key=lambda x: x[0])[1]


def read_rsmf(src) -> Extraction:
    """An .rsmf file (bytes or a path) or its zip."""
    if not isinstance(src, bytes):
        if zipfile.is_zipfile(src):
            z = zipfile.ZipFile(src)
        else:
            with open(src, "rb") as f:
                z = _open_zip(_rsmf_zip(f.read()))
    else:
        z = _open_zip(src if src.startswith(b"PK\x03\x04") else _rsmf_zip(src))
    root = _root_of(z.namelist(), "rsmf_manifest.json")
    manifest = json.loads(z.read(root + "rsmf_manifest.json").decode("utf-8-sig"))
    names = set(z.namelist())

    def read_file(path):
        for n in (root + path, root + "attachments/" + path, path):
            if n in names:
                return z.read(n)
        return None
    people = {}
    for p in manifest.get("participants") or []:
        people[p["id"]] = {k: v for k, v in {"id": p["id"], "name": p.get("display"), "email": p.get("email"),
                                             "phone": p.get("phone") or p.get("number")}.items() if v}
    events = defaultdict(list)
    for k, ev in enumerate(manifest.get("events") or []):
        events[ev.get("conversation")].append((k, ev))

    def convs():
        try:
            for c in manifest.get("conversations") or []:
                platform = (c.get("platform") or "rsmf").strip().lower().replace("microsoft teams", "teams")
                ids = list(dict.fromkeys(list(c.get("participants") or []) +
                                         [ev.get("participant") for _, ev in events.get(c.get("id"), [])
                                          if ev.get("participant")]))
                name = c.get("display") or c.get("id")
                key = conversation_key(platform, None, name, [_label(people.get(i), i) for i in ids])
                conv = {"key": key, "name": name, "kind": c.get("type"), "platform": platform,
                        "folder": f"{c.get('id')} {name}" if c.get("id") != name else name,
                        "participants": [people.get(i, {"id": i}) for i in ids]}
                yield conv, [_rsmf_message(k, ev) for k, ev in events.get(c.get("id"), [])], read_file, None, None
        finally:
            z.close()
    return _container("rsmf", convs(), source="rsmf")


def _rsmf_message(k: int, ev: dict) -> dict:
    msg = {"id": ev.get("id") or f"event-{k + 1}", "author": ev.get("participant"), "time": ev.get("timestamp"),
           "text": ev.get("body") or "", "parent": ev.get("parent"), "_order": k}
    if (ev.get("type") or "message") != "message":
        msg["event"] = ev.get("type")
    if ev.get("deleted"):
        msg["deleted"] = True
    edits = [e.get("timestamp") for e in ev.get("edits") or [] if e.get("timestamp")]
    if edits or ev.get("edited"):
        msg["edited"] = max(edits) if edits else "edited"
    msg["reactions"] = [{"emoji": r.get("value"), "by": list(r.get("participants") or [])}
                        for r in ev.get("reactions") or []]
    msg["attachments"] = [{"name": a.get("display") or a.get("id"), "_path": a.get("id")}
                          for a in ev.get("attachments") or []]
    return msg
