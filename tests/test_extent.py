"""Finding a passage from roughly its opening (extent.py, page ref add --from)."""

import json
from pathlib import Path

import pytest

from adhocdocs import extent, pages, readlog
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter

RULES = """Rule 11. Signing Pleadings
(a) SIGNATURE. Every pleading must be signed.
(b) REPRESENTATIONS TO THE COURT. By presenting to the court
a pleading, written motion, or other paper, an attorney or unrep-
resented party certifies that:
(1) it is not being presented for any improper purpose;
(2) the claims are warranted by existing law; and
(A) the first lettered item, with a
sub-item under it;
(3) the factual contentions have evidentiary support.
(c) SANCTIONS.
(1) In General. If, after notice, the court determines that Rule 11(b) has been violated.
(As amended Apr. 28, 1983, eff. Aug. 1, 1983.)
"""

PROSE = """II. Background

The Agency proposes to amend the rule so that brokers keep records in an
electronic format, as many commenters asked
49 U.S.C. 13904
, and as the petitions argued.

The proposed rule does not include an automatic disclosure provision. FMCSA believes
the request process is enough.

The proposed rule does not include an automatic disclosure provision. Instead, parties
may ask for the records within 48 hours.

A third paragraph, to end the section.
"""


def layer(text, name="doc"):
    return extent.Layer("sha256:" + name, "sha256:l" + name, name, text)


def found(scopes, *a, **k):
    r = extent.locate(scopes, *a, **k)
    assert r["status"] == "found", r
    L = next(L for _, rs in scopes for L, _, _ in rs)
    return L.text[r["span"][0]:r["span"][1]], r


def whole(L):
    return [("the matter", [(L, 0, len(L.keys))])]


def test_a_paragraph_from_its_rough_opening():
    L = layer(PROSE)
    text, r = found(whole(L), "the agency proposes to amend the rule so brokers")      # "that" left out
    assert text.startswith("The Agency proposes") and text.endswith("as the petitions argued.")
    assert "13904" in text                      # a line break inside a paragraph doesn't end it
    assert r["preview"]["paragraphs"] == 1 and r["preview"]["after"].startswith("The proposed rule")


def test_paragraphs_and_end_words():
    L = layer(PROSE)
    text, _ = found(whole(L), "The Agency proposes to amend the rule", paras=2)
    assert text.endswith("the request process is enough.")
    text, _ = found(whole(L), "The Agency proposes to amend the rule", end="as many commenters asked")
    assert text.endswith("as many commenters asked")


def test_a_shared_opening_asks_which():
    L = layer(PROSE)
    r = extent.locate(whole(L), "The proposed rule does not include an automatic disclosure provision")
    assert r["status"] == "confirm" and len(r["candidates"]) == 2
    assert r["candidates"][1]["preview"]["starts"].endswith("Instead, parties")
    text, _ = found(whole(L), "The proposed rule does not include an automatic disclosure provision", pick=2)
    assert "48 hours" in text
    # more words make it unique
    text, _ = found(whole(L), "The proposed rule does not include an automatic disclosure provision. Instead")
    assert "48 hours" in text


def test_what_was_read_last_settles_it():
    L = layer(PROSE)
    second = PROSE.index("Instead")
    lo, hi = L.index_at(second - 120), L.index_at(second + 60)
    scopes = [("what you read last", [(L, lo, hi)])] + whole(L)
    text, r = found(scopes, "The proposed rule does not include an automatic disclosure provision")
    assert "48 hours" in text and r["found_in"] == "what you read last"


def test_a_loose_match_needs_confirming_and_a_wrong_one_isnt_found():
    L = layer(PROSE)
    r = extent.locate(whole(L), "The Agency proposes amending the rule so that brokers keep files")
    assert r["status"] == "confirm" and r["why"] == "the best match is loose"
    with pytest.raises(extent.ExtentError):
        extent.locate(whole(L), "nothing like this is anywhere in the text at all")


def test_numbered_items():
    L = layer(RULES)
    text, r = found(whole(L), "(b) Representations to the court. By presenting", item=True)
    assert text.startswith("(b) REPRESENTATIONS") and text.endswith("evidentiary support.")
    assert "(A) the first lettered item" in text            # the items under it come along
    text, _ = found(whole(L), "(2) the claims are warranted", item=True)
    assert text.endswith("sub-item under it;")              # (A) is under (2); (3) ends it
    text, _ = found(whole(L), "(1) In General. If, after notice", item=True)
    assert text.endswith("has been violated.")              # the rule's history isn't part of it
    # a word broken at a line end is one word
    text, _ = found(whole(L), "an attorney or unrepresented party certifies", item=True)
    assert text.startswith("an attorney")


CORPUS = Path(__file__).parent.parent / "corpus"
EMAIL = "2026-03-09_RE_INV-2026-0347_past-due.eml"


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "m")
    ingest_paths(m, [CORPUS / "muller-z" / EMAIL], "x", "c")
    return Matter(m.root)


def test_ref_add_from(m, tmp_path):
    doc = m.state.live_documents()[0]
    lay = m.state.primary_layer(m.state.documents[doc])
    text = m.objects.get_text(lay.text)
    s = text.index("the shipment was never received")
    para_start = text.rfind("\n\n", 0, s) + 2
    opening = " ".join(text[para_start:].split()[:7]).upper()          # case doesn't matter
    readlog.record(m, doc, lay.id, (para_start, para_start + 300))
    p = tmp_path / "page.md"
    p.write_text("# T\n\n::q{#a}\n", encoding="utf-8")
    res = pages.find_passage(m, None, opening)
    assert res["status"] == "found" and res["found_in"] == "what you read last"
    pages.add_ref(m, p, "a", [res["loc"]])
    html, results = pages.render(pages.Page(m, p))
    assert results == [{"id": "a", "status": "exact"}] and "never received" in html
    rows = tmp_path / "rows.jsonl"
    out = pages.add_passages(m, p, [{"id": "b", "from": opening, "to": "never received"},
                                    {"id": "c", "from": "words that are nowhere in it at all"}])
    assert out[0]["written"] and out[1]["status"] == "error"
    assert "b" in json.dumps(pages.Page(m, p).refs)


def test_citations_say_when_a_page_number_is_the_files():
    base = {"doc": "sha256:" + "0" * 64, "kind": "pdf", "name": "rules.pdf", "title": "Rules"}
    assert pages.default_cite({**base, "pages": [37, 37], "page_labels": ["18", "18"], "has_page_labels": True}) == "Rules at 18"
    assert pages.default_cite({**base, "pages": [37, 37], "has_page_labels": True}) == "Rules at PDF p. 37"
    assert pages.default_cite({**base, "pages": [37, 38], "has_page_labels": True}) == "Rules at PDF pp. 37–38"
    assert pages.default_cite({**base, "pages": [3, 3]}) == "Rules at 3"       # no printed numbers anywhere


def test_filename_cite_style(m, tmp_path):
    doc = m.state.live_documents()[0]
    lay = m.state.primary_layer(m.state.documents[doc])
    text = m.objects.get_text(lay.text)
    s = text.index("the shipment was never received")
    from adhocdocs import locators
    p = tmp_path / "page.md"
    p.write_text("---\ncite-style: filename\n---\n:q{#a}\n", encoding="utf-8")
    pages.add_ref(m, p, "a", [locators.char_locator(doc, lay.id, text, s, s + 31)])
    html, _ = pages.render(pages.Page(m, p))
    assert EMAIL in html
