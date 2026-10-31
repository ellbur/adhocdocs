from pathlib import Path

import pytest

from adhocdocs import analysis, locators
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter

CORPUS = Path(__file__).parent.parent / "corpus"


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("a") / "m")
    for cust in ("muller-z", "okafor-d"):
        ingest_paths(m, sorted((CORPUS / cust).glob("*.eml")), cust, cust)
    ingest_paths(m, sorted((CORPUS / "voss-r").glob("memo-*.pdf")) + [CORPUS / "voss-r" / "INV-2026-0347.pdf"], "voss-r", "v")
    ingest_paths(m, [CORPUS / "muller-z" / "INV-2026-0347.pdf"], "muller-z", "muller-z")
    return Matter(m.root)


def named(m, name, custodian=None):
    for d in m.state.live_documents():
        meta = m.objects.get_object(m.state.metadata[d])
        obj = m.objects.get_object(d)
        if meta.get("name") == name and (custodian is None or obj.get("custodian") == f"cust:{custodian}"):
            return d


def test_threads(m):
    ts = analysis.threads(m)
    past_due = next(t for t in ts if "past due" in (t["subject"] or ""))
    assert past_due["size"] == 3
    first = past_due["messages"][0]
    assert len(first["copies"]) == 1                      # the same message from two custodians
    assert set(first["custodians"]) == {"muller-z", "okafor-d"}
    assert [x["depth"] for x in past_due["messages"]] == [0, 1, 2]
    assert len(past_due["missing_parents"]) == 1         # Dana's original isn't in the matter


def test_near_finds_versions_and_dupes(m):
    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    v2 = named(m, "memo-collection-bellwether-2026-03-18-rev.pdf")
    r = analysis.near(m, v1)
    assert r["near"][0]["loc"] == "doc:" + v2 and r["near"][0]["containment"] > 0.7
    inv = named(m, "INV-2026-0347.pdf", "voss-r")
    assert "doc:" + named(m, "INV-2026-0347.pdf", "muller-z") in analysis.near(m, inv)["exact_duplicates"]
    groups = analysis.groups(m, threshold=0.4)
    memo = next(g for g in groups if any("doc:" + v1 in t["docs"] for t in g["texts"]))
    assert {d for t in memo["texts"] for d in t["docs"]} == {"doc:" + v1, "doc:" + v2}
    # replies that quote earlier messages cluster with them too
    assert any(g["size"] >= 3 for g in groups)


def test_redline(m):
    v1 = named(m, "memo-collection-bellwether-2026-03-16.pdf")
    v2 = named(m, "memo-collection-bellwether-2026-03-18-rev.pdf")
    header, changes = analysis.redline(m, v1, v2)
    assert header["changes"] == len(changes) > 0
    inserted = " ".join(c["b"]["quote"]["exact"] for c in changes if c["op"] == "insert")
    assert "unclaimed" in inserted and "Pruitt" in inserted
    for c in changes:                                   # every side is a locator that checks out
        for side in ("a", "b"):
            if "quote" in c[side]:
                assert locators.resolve(m, c[side])["status"] == "exact"
    page = analysis.redline_html(m, v1, v2)
    assert "<ins>" in page and "<del>" in page and "data-text=" in page
    # line breaks and hyphenation are not changes
    _, same = analysis.redline(m, v1, v1)
    assert same == []


def test_people_merges_addresses_by_name(tmp_path):
    from adhocdocs.ingest import ingest_bytes
    m2 = Matter.create(tmp_path / "p")
    mails = [
        "From: Zoë Müller <zoe@work.example>\nTo: Dana Okafor <dana@vendor.example>\nDate: Mon, 09 Mar 2026 10:00:00 -0400\nSubject: a\n\nZoe Muller here.\n",
        "From: \"Muller, Zoe\" <zmuller@home.example>\nTo: Dana Okafor <dana@vendor.example>\nDate: Tue, 10 Mar 2026 10:00:00 -0400\nSubject: b\n\nhi\n",
        "From: Dana Okafor <dana@vendor.example>\nTo: Info <info@vendor.example>\nDate: Wed, 11 Mar 2026 10:00:00 -0400\nSubject: c\n\nhi\n",
    ]
    for i, t in enumerate(mails):
        ingest_bytes(m2, t.encode(), f"{i}.eml", {"source": {"collection": "c", "path": f"{i}.eml"}})
    ps = {p["name"]: p for p in analysis.people(Matter(m2.root), mentions=True)}
    zoe = ps["Zoë Müller"]
    assert zoe["addresses"] == ["zmuller@home.example", "zoe@work.example"]
    assert set(zoe["names"]) == {"Zoë Müller", "Muller, Zoe"} and zoe["sent"] == 2
    assert zoe["first"].startswith("2026-03-09") and zoe["last"].startswith("2026-03-10")
    assert zoe["mentioned_in"] >= 2                     # its own header lines and "Zoe Muller here"
    assert ps["Dana Okafor"]["sent"] == 1 and ps["Dana Okafor"]["received"] == 2
    assert ps["Info"]["addresses"] == ["info@vendor.example"]     # one-word names never merge
