"""Inline images in email: ingested as children, placed in the body by
``image`` segments, shown by the presentation."""

import email
import email.policy
from email.message import EmailMessage

from adhocdocs.extract import extract, kind_for, mapi
from adhocdocs.extract.email import extract_email
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter
from adhocdocs.objects import sha256

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
HTML = '<p>Damage at the dock:</p><img src="cid:dock@x"><p>See the pallet.</p><img src="cid:gone@x">'


def message(plain=None):
    """mixed[alternative[plain, related[html, png]], pdf, message/rfc822]: the
    image is nested where iter_attachments doesn't look."""
    m = EmailMessage()
    m["From"], m["Subject"] = "a@x.example", "dock"
    m.set_content(plain or "Damage at the dock:\nSee the pallet.")
    m.add_alternative(HTML, subtype="html")
    m.get_payload()[1].add_related(PNG, "image", "png", cid="<dock@x>")
    m.add_attachment(b"%PDF-1.4", "application", "pdf", filename="bol.pdf")
    inner = EmailMessage()
    inner["Subject"] = "inner"
    inner.set_content('<img src="cid:inner@y">', subtype="html")
    inner.make_related()
    inner.add_related(PNG + b"inner", "image", "png", cid="<inner@y>")
    m.add_attachment(inner)
    return m


def html_only():
    m = EmailMessage()
    m["Subject"] = "dock"
    m.set_content(HTML, subtype="html")
    m.make_related()
    m.add_related(PNG, "image", "png", cid="<dock@x>")
    return m


def images(layer):
    return [s for s in layer.segments if s["kind"] == "image"]


def test_nested_inline_images_are_children_numbered_after_attachments():
    e = extract_email(message().as_bytes())
    parts = {c.part: c for c in e.children}
    assert parts["1"].name == "bol.pdf"                    # as before: attachments keep their numbers
    assert parts["2"].data.startswith(b"Subject: inner")   # the attached message
    assert parts["3"].data == PNG                          # the inline image, new
    assert len(parts) == 3                                 # the attached message's image stays with it


def test_html_images_are_placed_without_changing_the_text():
    e = extract_email(html_only().as_bytes())
    layer = e.layers[0]
    plain = EmailMessage()
    plain["Subject"] = "dock"
    plain.set_content(HTML.replace('<img src="cid:dock@x">', "").replace('<img src="cid:gone@x">', ""),
                      subtype="html")
    assert layer.text == extract_email(plain.as_bytes()).layers[0].text
    dock, gone = images(layer)
    assert dock["start"] == dock["end"] == layer.text.index("See the pallet.")
    assert dock["cid"] == "dock@x" and dock["content"] == sha256(PNG)
    assert gone["start"] == gone["end"] == len(layer.text) and "content" not in gone
    p = layer.presentation
    assert p.index("Damage at the dock") < p.index(f'src="/image/{sha256(PNG)}"') < p.index("See the pallet")
    assert 'class="adhoc-inline-image missing" data-cid="gone@x"' in p
    assert p.rstrip().endswith('data-cid="gone@x"></figure>\n</article>')


def test_outlook_plain_markers():
    e = extract_email(message("Damage [cid:dock@x] here [cid:Missing@X]\nthanks").as_bytes())
    layer = e.layers[0]
    dock, missing = images(layer)
    assert layer.text[dock["start"]:dock["end"]] == "[cid:dock@x]" and dock["content"] == sha256(PNG)
    assert missing["cid"] == "missing@x" and "content" not in missing
    p = layer.presentation
    assert p.index("[cid:dock@x]") < p.index("/image/") < p.index("thanks")


def test_outlook_attachments_keep_their_content_id():
    m = mapi.Message({mapi.SUBJECT: "Hi", mapi.HTML: '<p>Logo</p><img src="cid:image001.png@01DA">'}, [],
                     [mapi.Attachment({mapi.ATTACH_LONG_FILENAME: "image001.png",
                                       mapi.ATTACH_CONTENT_ID: "image001.png@01DA"}, data=PNG)])
    data = mapi.to_eml(m, "test")
    att = next(email.message_from_bytes(data, policy=email.policy.default).iter_attachments())
    assert att["Content-ID"] == "<image001.png@01DA>"
    (seg,) = images(extract("x.eml", data).layers[0])
    assert seg["cid"] == "image001.png@01da" and seg["content"] == sha256(PNG)


def test_images_are_known_by_their_bytes():
    assert kind_for(None, PNG) == "image"
    assert kind_for(None, b"\xff\xd8\xff\xe0rest") == "image"
    assert kind_for("notes.txt", b"GIF89a is a format") == "text"


def test_ingested_image_is_the_segment_content(tmp_path):
    m = Matter.create(tmp_path / "m")
    doc = ingest_bytes(m, html_only().as_bytes(), "dock.eml", {"custodian": "a"})
    state = m.state
    (child,) = state.children[doc]
    assert state.documents[child] == sha256(PNG)
    assert m.objects.get_object(state.metadata[child])["kind"] == "image"
