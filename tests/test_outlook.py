"""Outlook files: PSTs (read with libpff) and .msg files, both converted to
email. The corpus has one of each, made by tools/corpus/outlook.py."""

import email
import email.policy
from pathlib import Path

import pytest

from adhocdocs import analysis
from adhocdocs.extract import extract, kind_for, mapi
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter
from corpus_files import story

CORPUS = Path(__file__).parent.parent / "corpus"
PST = CORPUS / "kettering-t" / "kettering-t-mailbox-2026-04.pst"
MSG = CORPUS / "delacroix-m" / "RE LITIGATION HOLD - Harrowgate.msg"


@pytest.fixture(scope="module")
def pst():
    return extract(PST.name, PST.read_bytes())


def child(ext, part):
    return next(c for c in ext.children if c.part == part)


def test_detected_without_a_name():
    assert kind_for(None, PST.read_bytes()) == "pst"
    assert kind_for(None, MSG.read_bytes()) == "msg"
    assert kind_for("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600) != "msg"


def test_pst_folders_and_messages(pst):
    assert pst.kind == "mailbox"
    assert pst.metadata["folders"] == ["Deleted Items", "Inbox", "Inbox/Tri-County", "Sent Items"]
    assert sorted(c.part for c in pst.children) == [
        "Deleted Items/1", "Inbox/1", "Inbox/Tri-County/1", "Sent Items/1", "Sent Items/2"]
    assert all(c.name.endswith(".eml") for c in pst.children)


def test_pst_message_that_came_over_the_internet(pst):
    """It keeps the headers it arrived with; MIME headers are rebuilt."""
    c = child(pst, "Inbox/Tri-County/1")
    raw = email.message_from_bytes(c.data, policy=email.policy.default)
    assert raw["Received"].startswith("from mx2.tricountyfreight.example")
    assert raw["Content-Type"].startswith("multipart/mixed") and "=_tcfl_0302" not in raw["Content-Type"]
    assert raw["X-Adhoc-Converted-From"] == "Outlook PST, folder Inbox/Tri-County"
    e = extract(c.name, c.data)
    assert e.metadata["in_reply_to"] == "<20260302130500.1e5f@bellwethermarine.example>"
    assert e.metadata["date"] == "2026-03-02T15:10:00-05:00"
    [pdf] = e.children
    assert pdf.name == "pickup-order-PU-0304-118.pdf"
    assert "PU-0304-118" in extract(pdf.name, pdf.data).layers[0].text


def test_pst_internal_message(pst):
    """Exchange mail has no transport headers: they're made from its
    properties, with SMTP addresses for Exchange ones."""
    e = extract("x.eml", child(pst, "Inbox/1").data)
    assert e.metadata["message_id"] == "<20260327174500.ab02@bellwethermarine.example>"
    assert e.metadata["from"] == ["mdelacroix-hale@bellwethermarine.example"]
    assert e.metadata["to"] == ["zoe.muller@bellwethermarine.example", "tkettering@bellwethermarine.example",
                                "ap@bellwethermarine.example"]
    assert e.metadata["date"] == "2026-03-27T21:45:00+00:00"          # MAPI times are UTC
    assert "Normal deletion schedules are suspended" in e.layers[0].text


def test_pst_attached_message(pst):
    e = extract("x.eml", child(pst, "Sent Items/2").data)
    [inner] = e.children
    assert inner.name == "Unclaimed pallet at Bellwether Dock 4 - please pick up.eml"
    ie = extract(inner.name, inner.data)
    assert ie.kind == "email"
    assert ie.metadata["message_id"] == "<20260302130500.1e5f@bellwethermarine.example>"
    assert "No PO, no packing list" in ie.layers[0].text


def test_conversion_is_deterministic(pst):
    again = extract(PST.name, PST.read_bytes())
    assert [c.data for c in again.children] == [c.data for c in pst.children]


def test_msg():
    e = extract(MSG.name, MSG.read_bytes())
    assert e.kind == "email" and e.metadata["format"] == "msg"
    assert e.metadata["from"] == ["tkettering@bellwethermarine.example"]
    assert e.metadata["in_reply_to"] == "<20260327174500.ab02@bellwethermarine.example>"
    assert "records over itself after 60 days" in e.layers[0].text


def test_msg_html_from_compressed_rtf():
    """The .msg keeps its HTML only inside the compressed RTF body."""
    import io

    import olefile

    from adhocdocs.extract import msg
    with olefile.OleFileIO(io.BytesIO(MSG.read_bytes())) as ole:
        m = msg.read_message(ole)
    assert mapi.HTML not in m.props and mapi.RTF_COMPRESSED in m.props
    parsed = email.message_from_bytes(mapi.to_eml(m, "Outlook .msg"), policy=email.policy.default)
    assert "after <b>60 days</b>" in parsed.get_body(("html",)).get_content()


def test_copies_thread_across_formats(tmp_path):
    """The hold notice as an .eml (Delacroix-Hale's) and in Kettering's PST
    is one message; the .msg reply joins its thread."""
    m = Matter.create(tmp_path / "m")
    ingest_paths(m, story("delacroix-m", "*.eml") + [MSG], "delacroix-m", "d")
    ingest_paths(m, [PST, CORPUS / "kettering-t" / "2026-03-02_unclaimed-pallet-Q3.eml"], "kettering-t", "k")
    ts = analysis.threads(Matter(m.root))
    hold = next(t for t in ts if (t["subject"] or "").startswith("LITIGATION HOLD"))
    assert hold["size"] == 2
    first = hold["messages"][0]
    assert set(first["custodians"]) == {"delacroix-m", "kettering-t"} and len(first["copies"]) == 1
    pallet = next(t for t in ts if "Unclaimed pallet" in (t["subject"] or ""))
    assert pallet["size"] == 2                     # Tom's note and Tri-County's reply
    root = next(x for x in pallet["messages"] if x["depth"] == 0)
    assert len(root["copies"]) == 2                # the .eml, the PST's Sent Items, the one attached to his 3/26 reply
    demand = next(t for t in ts if (t["subject"] or "").startswith("FW: Harrowgate"))
    assert demand["size"] == 2                     # Tom's reply in the PST answers the forward


# ---------------------------------------------------------------- the conversion itself


def test_rtf_decompression_spec_example():
    """The worked example in MS-OXRTFCP."""
    data = bytes.fromhex("2d0000002b0000004c5a4675f1c5c7a703000a007263706731323542320af32068656c09"
                         "0020627705b06c647d0a800fa0")
    assert mapi.decompress_rtf(data) == b"{\\rtf1\\ansi\\ansicpg1252\\pard hello world}\r\n"


def test_rtf_to_text():
    rtf = (b"{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0 Arial;}}{\\colortbl;\\red0\\green0\\blue0;}"
           b"\\f0\\fs20 Hello Zo\\'eb,\\par Second\\u8212?line\\tab x\\par}")
    assert mapi.rtf_text(rtf) == ("Hello Zoë,\nSecond—line\tx\n", False)


def test_rtf_encapsulated_html():
    rtf = (b"{\\rtf1\\ansi\\fromhtml1 {\\*\\htmltag64 <p>}\\htmlrtf {\\b \\htmlrtf0 {\\*\\htmltag84 <b>}"
           b"Bold\\htmlrtf }\\htmlrtf0 {\\*\\htmltag92 </b>} caf\\'e9{\\*\\htmltag72 </p>}}")
    assert mapi.rtf_text(rtf) == ("<p><b>Bold</b> café</p>", True)


def test_addresses_and_unread_attachments():
    """An Exchange address with no SMTP one is the display name alone,
    and an attached message the reader couldn't get is named in a header."""
    m = mapi.Message({mapi.SUBJECT: "Hi", mapi.BODY: "text", mapi.SENDER_NAME: "Old Exchange User",
                      mapi.SENDER_ADDRTYPE: "EX", mapi.SENDER_EMAIL: "/O=ORG/CN=RECIPIENTS/CN=OLDUSER"},
                     [{mapi.DISPLAY_NAME: "Ann", mapi.ADDRTYPE: "SMTP", mapi.EMAIL_ADDRESS: "ann@x.example",
                       mapi.RECIPIENT_TYPE: 2}],
                     [mapi.Attachment({mapi.DISPLAY_NAME: "Fwd: lost"}, unread=True)])
    data = mapi.to_eml(m, "test")
    parsed = email.message_from_bytes(data, policy=email.policy.default)
    assert parsed["X-Adhoc-Unread-Attachment"] == "Fwd: lost"
    e = extract("x.eml", data)
    assert e.layers[0].text.startswith("From: Old Exchange User <>\nCc: Ann <ann@x.example>\n")
    assert "from" not in e.metadata and e.metadata["cc"] == ["ann@x.example"]
