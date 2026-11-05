"""Chat messages (CHATS.md): Slack exports, WhatsApp exports and RSMF read
into a document per conversation per day, messages citable by msg=."""

import email.message
import io
import json
import zipfile

import pytest

from adhocdocs import analysis, locators, metadocs
from adhocdocs.extract import extract, kind_for
from adhocdocs.extract.chat import extract_chat_day, looks_like_whatsapp, slack_text
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter


def _zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, v in files.items():
            z.writestr(n, v if isinstance(v, (str, bytes)) else json.dumps(v))
    return b.getvalue()


def slack_export(root="") -> bytes:
    return _zip({
        root + "users.json": [{"id": "U1", "name": "alice", "profile": {"real_name": "Alice Smith", "email": "Alice@Acme.com"}},
                              {"id": "U2", "name": "bob", "profile": {"real_name": "Bob Jones", "email": "bob@acme.com"}}],
        root + "channels.json": [{"id": "C1", "name": "shipping", "members": ["U1", "U2"]}],
        root + "dms.json": [{"id": "D1", "members": ["U1", "U2"]}],
        root + "shipping/2024-01-15.json": [
            {"type": "message", "user": "U1", "ts": "1705309380.000100",
             "text": "Morning <@U2>, did the shipment go out? <https://x.example/t|tracker> &amp; <#C1|shipping>",
             "reactions": [{"name": "eyes", "users": ["U2"]}]},
            {"type": "message", "user": "U2", "text": "Yes, this morning.", "ts": "1705309620.000200",
             "thread_ts": "1705309380.000100", "edited": {"user": "U2", "ts": "1705309700.0"},
             "files": [{"name": "slip.pdf"}]},
            {"type": "message", "subtype": "channel_join", "user": "U2", "text": "<@U2> has joined the channel",
             "ts": "1705300000.000001"}],
        root + "shipping/2024-01-16.json": [
            {"type": "message", "user": "U2", "text": "One more thing", "ts": "1705395600.000300",
             "thread_ts": "1705309380.000100"}],
        root + "D1/2024-01-15.json": [{"type": "message", "user": "U1", "text": "private note", "ts": "1705309999.0"}],
    })


WHATSAPP_IOS = """[15/01/2024, 09:03:12] Alice Smith: Morning
[15/01/2024, 09:04:01] Bob Jones: Shipped.
Tracking 1Z999
[15/01/2024, 09:05:00] Bob Jones: ‎<attached: 00000012-PHOTO-2024-01-15.jpg>
[16/01/2024, 21:00:00] Alice Smith: This message was deleted
"""

WHATSAPP_ANDROID = """1/2/24, 9:03 PM - Messages and calls are end-to-end encrypted.
1/2/24, 9:04 PM - Alice Smith: Did it arrive?
1/2/24, 9:05 PM - +1 555 010 0199: Not yet
"""


def rsmf(as_email=True) -> bytes:
    manifest = {
        "version": "2.0.0",
        "participants": [{"id": "P1", "display": "Alice Smith", "email": "alice@acme.com"},
                         {"id": "P2", "display": "Carol Diaz"}],
        "conversations": [{"id": "C1", "platform": "Microsoft Teams", "type": "channel", "display": "Logistics",
                           "participants": ["P1", "P2"]}],
        "events": [
            {"id": "E1", "type": "join", "conversation": "C1", "participant": "P2",
             "timestamp": "2024-01-15T09:00:00-05:00", "body": "Carol Diaz joined"},
            {"id": "E2", "type": "message", "conversation": "C1", "participant": "P1",
             "timestamp": "2024-01-15T09:01:00-05:00", "body": "Please send the invoice",
             "attachments": [{"id": "inv.txt", "display": "inv.txt"}]},
            {"id": "E3", "type": "message", "conversation": "C1", "participant": "P2",
             "timestamp": "2024-01-15T09:02:00-05:00", "body": "Sent", "parent": "E2",
             "reactions": [{"value": "👍", "participants": ["P1"]}],
             "edits": [{"timestamp": "2024-01-15T09:03:00-05:00"}]},
            {"id": "E4", "type": "message", "conversation": "C1", "participant": "P1",
             "timestamp": "2024-01-16T08:00:00-05:00", "body": "Thanks, paid."}]}
    z = _zip({"rsmf_manifest.json": manifest, "inv.txt": "INVOICE 42 for 1,044.50"})
    if not as_email:
        return z
    m = email.message.EmailMessage()
    m["X-RSMF-Version"] = "2.0.0"
    m["From"] = "export@acme.com"
    m["Subject"] = "Logistics"
    m.set_content("A conversation in RSMF.")
    m.add_attachment(z, maintype="application", subtype="zip", filename="rsmf.zip")
    return bytes(m)


def days(name, data):
    ext = extract(name, data)
    return ext, {c.part: extract(c.name, c.data) for c in ext.children}


# -- reading -----------------------------------------------------------------------------------

def test_kinds_are_recognized():
    assert kind_for("export.zip", slack_export()) == "slack"
    assert kind_for("export.zip", slack_export("Acme Slack export/")) == "slack"
    assert kind_for("chat.txt", WHATSAPP_IOS.encode()) == "whatsapp"
    assert kind_for(None, WHATSAPP_ANDROID.encode()) == "whatsapp"
    assert kind_for("x.zip", _zip({"_chat.txt": WHATSAPP_IOS})) == "whatsapp"
    assert kind_for("logistics.rsmf", rsmf()) == "rsmf"
    assert kind_for(None, rsmf()) == "rsmf"
    assert kind_for("rsmf.zip", rsmf(as_email=False)) == "rsmf"
    # a log with times isn't a chat; an email talking about RSMF isn't one
    assert kind_for("log.txt", b"2024-01-15 09:03 started\n2024-01-15 09:04 done\n") == "text"
    assert not looks_like_whatsapp("[15/01/2024, 09:03:12] one line only")
    m = email.message.EmailMessage()
    m["From"] = "a@b.c"
    m["Subject"] = "x"
    m.set_content("\nX-RSMF-Version: 2.0.0\n")
    assert kind_for(None, bytes(m)) == "email"


def test_slack_days():
    ext, out = days("export.zip", slack_export())
    assert ext.kind == "chat-export"
    assert ext.metadata["conversations"] == 2 and ext.metadata["days"] == 3 and ext.metadata["messages"] == 5
    assert set(out) == {"shipping/2024-01-15", "shipping/2024-01-16", "D1/2024-01-15"}
    d = out["shipping/2024-01-15"]
    text = d.layers[0].text
    assert d.kind == "chat"
    assert text.startswith("Conversation: #shipping\nPlatform: Slack\nMembers: Alice Smith, Bob Jones\n"
                           "Date: 2024-01-15 (times UTC)\n\n")
    assert "09:03 Alice Smith: Morning @Bob Jones, did the shipment go out? tracker & #shipping\n" \
           "    :eyes: Bob Jones\n" in text
    assert "  ↳ 09:07 Bob Jones: Yes, this morning.\n    (edited 09:08)\n" \
           "    [attachment: slip.pdf, not in the export]" in text
    assert text.index("has joined") < text.index("Morning")            # in time order
    msgs = [s for s in d.layers[0].segments if s["kind"] == "message"]
    assert [s["id"] for s in msgs] == ["1705300000.000001", "1705309380.000100", "1705309620.000200"]
    reply = msgs[2]
    assert reply["parent"] == "1705309380.000100" and reply["name"] == "Bob Jones"
    assert reply["time"] == "2024-01-15T09:07:00Z"
    assert msgs[0]["event"] == "channel_join"
    meta = d.metadata
    assert meta["title"] == "#shipping, 2024-01-15" and meta["thread_key"] == "chat:slack/C1"
    assert meta["from"] == ["alice@acme.com", "bob@acme.com"]
    later = out["shipping/2024-01-16"].layers[0].text
    assert "  ↳ 09:00 Bob Jones: (in a thread from earlier) One more thing" in later
    assert out["D1/2024-01-15"].metadata["conversation"] == "DM: Alice Smith, Bob Jones"
    # the same export zipped inside its folder reads the same
    _, again = days("export.zip", slack_export("Acme Slack export/"))
    assert again["shipping/2024-01-15"].layers[0].text == text


def test_slack_markup():
    users = {"U1": {"id": "U1", "name": "Alice Smith"}}
    assert slack_text("hi <@U1> and <@U9>", users, {}) == "hi @Alice Smith and @U9"
    assert slack_text("<!here> see <https://a.b/c> or <mailto:x@y.z|x@y.z>", users, {}) == \
        "@here see https://a.b/c or x@y.z"
    assert slack_text("1 &lt; 2 &amp;&amp; <#C2>", users, {"C2": "general"}) == "1 < 2 && #general"


def test_whatsapp_ios_and_its_media():
    data = _zip({"_chat.txt": WHATSAPP_IOS, "00000012-PHOTO-2024-01-15.jpg": b"\xff\xd8\xffJPEG"})
    ext, out = days("WhatsApp Chat - Bob Jones.zip", data)
    assert set(out) == {"Bob Jones/2024-01-15", "Bob Jones/2024-01-16"}
    d = out["Bob Jones/2024-01-15"]
    text = d.layers[0].text
    assert "Note: dates are read day first: a day over 12 comes first" in text
    assert "09:04 Bob Jones: Shipped.\nTracking 1Z999\n" in text          # a message over two lines
    assert "    [attachment: 00000012-PHOTO-2024-01-15.jpg]" in text
    kids = list(d.children)
    assert [(c.part, c.name, c.data) for c in kids] == [("1", "00000012-PHOTO-2024-01-15.jpg", b"\xff\xd8\xffJPEG")]
    att = next(s for s in d.layers[0].segments if s["kind"] == "attachment")
    assert att["part"] == "1"
    assert [s["id"] for s in d.layers[0].segments if s["kind"] == "message"] == ["1", "2", "3"]
    assert d.metadata["zone"] == "local" and d.metadata["date"] == "2024-01-15T09:03:12"
    # the text alone: the media is named, not there
    _, alone = days("WhatsApp Chat with Bob Jones.txt", WHATSAPP_IOS.encode())
    assert "[attachment: 00000012-PHOTO-2024-01-15.jpg, not in the export]" in \
        alone["Bob Jones/2024-01-15"].layers[0].text
    last = alone["Bob Jones/2024-01-16"].layers[0].text
    assert last.endswith("21:00 Alice Smith: This message was deleted")


def test_whatsapp_android_reads_month_first_on_a_12_hour_clock():
    _, out = days("WhatsApp Chat with Alice Smith.txt", WHATSAPP_ANDROID.encode())
    [(part, d)] = out.items()
    assert part == "Alice Smith/2024-01-02"
    text = d.layers[0].text
    assert "month first" in text
    assert "21:03 Messages and calls are end-to-end encrypted." in text
    assert "21:05 +1 555 010 0199: Not yet" in text
    msgs = [s for s in d.layers[0].segments if s["kind"] == "message"]
    assert msgs[0]["event"] == "system" and "author" not in msgs[0]
    # day over 12 in the second place settles it
    _, out = days("c.txt", b"1/13/24, 9:03 - A: x\n1/14/24, 9:04 - B: y\n")
    assert set(out) == {"c/2024-01-13", "c/2024-01-14"}


def test_rsmf():
    for data in (rsmf(), rsmf(as_email=False)):
        ext, out = days("logistics.rsmf", data)
        assert ext.metadata["source_format"] == "rsmf"
        assert set(out) == {"C1 Logistics/2024-01-15", "C1 Logistics/2024-01-16"}
        d = out["C1 Logistics/2024-01-15"]
        text = d.layers[0].text
        assert "Platform: Microsoft Teams (from RSMF)\n" in text
        assert "Date: 2024-01-15 (times UTC-05:00)" in text
        assert "09:00 Carol Diaz joined\n09:01 Alice Smith: Please send the invoice\n    [attachment: inv.txt]\n" \
               "  ↳ 09:02 Carol Diaz: Sent\n    (edited 09:03)\n    👍 Alice Smith" in text
        assert [(c.name, c.data) for c in d.children] == [("inv.txt", b"INVOICE 42 for 1,044.50")]
        assert d.metadata["platform"] == "teams"


def test_a_chat_day_is_only_read_as_one():
    with pytest.raises(ValueError):
        extract_chat_day(b'{"format": "something else"}')
    assert kind_for("notes.chat.json", b'{"a": 1}') == "json"


# -- in a matter -------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def matter(tmp_path_factory):
    root = tmp_path_factory.mktemp("chat")
    src = root / "src"
    (src / "a").mkdir(parents=True)
    (src / "b").mkdir()
    (src / "a" / "slack.zip").write_bytes(slack_export())
    (src / "a" / "logistics.rsmf").write_bytes(rsmf())
    (src / "a" / "WhatsApp Chat with Bob Jones.txt").write_text(WHATSAPP_IOS)
    (src / "b" / "slack.zip").write_bytes(slack_export())          # another custodian's copy
    m = Matter.create(root / "m")
    ingest_paths(m, [src / "a"], "smith-a", "a")
    ingest_paths(m, [src / "b"], "jones-b", "b")
    return m


def _docs(m, **want):
    out = []
    for d in m.state.live_documents():
        meta = m.objects.get_object(m.state.metadata[d])
        if all(meta.get(k) == v for k, v in want.items()):
            out.append(d)
    return out


def test_ingested_days_and_attachments(matter):
    m = matter
    exports = _docs(m, kind="chat-export")
    assert len(exports) == 4
    day = _docs(m, kind="chat", title="Logistics, 2024-01-15")
    [d] = day
    inv = [x for x in m.state.live_documents() if m.objects.get_object(x).get("parent") == d]
    assert len(inv) == 1 and m.objects.get_object(m.state.metadata[inv[0]])["name"] == "inv.txt"


def test_msg_locators(matter):
    m = matter
    [d] = _docs(m, kind="chat", title="Logistics, 2024-01-15")
    r = locators.resolve(m, f"doc:{d}#msg=E2")
    assert r["status"] == "exact"
    assert r["text"] == "09:01 Alice Smith: Please send the invoice\n    [attachment: inv.txt]"
    r = locators.resolve(m, f"doc:{d}#msg=E2,E3")
    assert r["text"].startswith("09:01 Alice Smith") and r["text"].endswith("👍 Alice Smith")
    with pytest.raises(locators.LocatorError):
        locators.resolve(m, f"doc:{d}#msg=E9")
    with pytest.raises(locators.LocatorError):
        locators.resolve(m, f"doc:{d}#msg=E3,E2")
    loc = locators.parse(f"doc:{d}#msg=1705309380.000100")
    assert locators.format(loc).endswith("#msg=1705309380.000100")
    assert locators.expand(m, f"doc:{d}#msg=E3")["select"]["char"]


def test_a_conversation_is_a_thread(matter):
    m = matter
    ts = analysis.threads(m)
    shipping = next(t for t in ts if (t["messages"][0]["title"] or "").startswith("#shipping"))
    assert [x["title"] for x in shipping["messages"]] == ["#shipping, 2024-01-15", "#shipping, 2024-01-16"]
    assert all(x["depth"] == 0 for x in shipping["messages"])
    assert shipping["missing_parents"] == []
    assert all(len(x["copies"]) == 1 for x in shipping["messages"])          # the other custodian's
    assert shipping["custodians"] == ["jones-b", "smith-a"]
    u = metadocs.Units(m)
    first = shipping["messages"][0]["loc"][4:]
    assert len(u.docs(u.unit(first, "thread"))) == 4
    assert u.name(first) == "#shipping, 2024-01-15"
    # the DM is its own conversation, one day long: no thread
    [dm] = _docs(m, kind="chat", title="DM: Alice Smith, Bob Jones, 2024-01-15", **{})[:1]
    assert u.docs(u.unit(dm, "thread")) == [dm]


def test_people_from_chats(matter):
    people = analysis.people(matter)
    alice = next(p for p in people if "alice@acme.com" in p.get("addresses", []))
    assert "Alice Smith" in json.dumps(alice)
