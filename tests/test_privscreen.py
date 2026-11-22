"""The privilege screen: documents held back from Claude's reading until a
lawyer says otherwise (privscreen.py)."""

import json

import pytest

from adhocdocs import annotations, cli, privscreen
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter
from corpus_files import CORPUS

GC = "mdelacroix-hale@bellwethermarine.example"


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("screen") / "m")
    ingest_paths(m, [CORPUS / "delacroix-m"], "delacroix-m", "delacroix-m")
    ingest_paths(m, [CORPUS / "voss-r" / "demand-letter-2026-03-25.pdf"], "voss-r", "voss-r")
    m = Matter(m.root)
    privscreen.set_level(m, "suspected")
    return m


def named(m, name):
    return next(d for d in m.state.live_documents() if privscreen._meta(m, d).get("name") == name)


def test_who_is_counsel(m):
    c = privscreen.counsel(m)
    assert list(c) == [GC] and "emails they sent as counsel" in c[GC]


def test_what_is_screened_and_why(m):
    memo = privscreen.screen(m, named(m, "privileged-memo-harrowgate-2026-03-27.docx"))
    assert memo == [{"reason": "worded", "words": ["privileged"]}]
    hold = {w["reason"] for w in privscreen.screen(m, named(m, "2026-03-27_litigation-hold.eml"))}
    assert hold == {"worded", "counsel"}
    reply = privscreen.screen(m, named(m, "RE LITIGATION HOLD - Harrowgate.msg"))        # unmarked, to counsel
    assert [w["reason"] for w in reply] == ["counsel"] and reply[0]["who"] == [GC]
    # counsel on an email isn't enough: not the lunch order, not an all-staff notice, not a letter from outside
    for name in ("2026-06-24_Lunch.eml", "2025-10-25_Blood-drive.eml", "2025-12-30_Thermostat.eml"):
        assert privscreen.screen(m, named(m, name)) == [], name
    assert privscreen.screen(m, named(m, "demand-letter-2026-03-25.pdf")) == []
    # an attachment is screened for its own content: the demand forwarded to counsel isn't privileged for it
    fw = named(m, "2026-03-26_FW_demand-for-payment.eml")
    att = next(d for d in m.state.children.get(fw, []))
    assert privscreen.screen(m, fw) and privscreen.screen(m, att) == []


def test_how_it_arrived(m):
    a = privscreen.arrived(m, named(m, "privileged-memo-harrowgate-2026-03-27.docx"))
    assert a == {"how": "collected", "collection": "delacroix-m", "custodian": "delacroix-m",
                 "path": "privileged-memo-harrowgate-2026-03-27.docx"}
    fw = named(m, "2026-03-26_FW_demand-for-payment.eml")
    att = m.state.children[fw][0]
    assert privscreen.arrived(m, att)["how"] == "attached" and privscreen.arrived(m, att)["to"] == fw


def test_text_holds_back_and_search_leaves_out(m, capsys, monkeypatch):
    monkeypatch.setenv("ADHOC_MATTER", str(m.root))
    memo = named(m, "privileged-memo-harrowgate-2026-03-27.docx")
    with pytest.raises(SystemExit) as e:
        cli.text(["doc:" + memo])
    assert e.value.code == 3
    err = json.loads(capsys.readouterr().err)
    assert err["screened"] == memo and err["arrived"]["custodian"] == "delacroix-m" and "ask" in err["next"]
    assert err["what"]["author"] == "Marcus Delacroix-Hale" and err["what"]["title"].startswith("Privileged memo")
    cli.text(["doc:" + memo, "--past-screen", "the user said to read it"])
    got = capsys.readouterr()
    assert "Memorandum" in got.out and json.loads(got.err)["reason_given"] == "the user said to read it"
    cli.search(['"litigation hold"'])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[0]["screened"]["documents"] >= 2
    held = [x for x in lines[1:] if x.get("screened")]
    assert held and all(h["snippet"]["text"].startswith("[screened") for x in held for h in x["hits"])
    assert all(set(h["loc"]["quote"]) == {"exact"} for x in held for h in x["hits"])     # no words around the hit


def test_a_lawyer_lifts_or_widens_the_screen(tmp_path):
    m2 = Matter.create(tmp_path / "m2")
    ingest_paths(m2, [CORPUS / "delacroix-m" / "privileged-memo-harrowgate-2026-03-27.docx",
                      CORPUS / "delacroix-m" / "2025-10-08_Board-deck-legal-section.eml"], "delacroix-m", "c")
    m2 = Matter(m2.root)
    memo, deck = named(m2, "privileged-memo-harrowgate-2026-03-27.docx"), named(m2, "2025-10-08_Board-deck-legal-section.eml")
    assert not privscreen.screen(m2, memo)                                    # the default: only what a person tagged
    privscreen.set_level(m2, "suspected")
    assert privscreen.screen(m2, memo) and not privscreen.screen(m2, deck)   # one email: counsel not found
    # the lawyer names counsel, and an internal email to counsel about a legal matter is screened
    (m2.workspace / "privilege-screen.json").write_text(json.dumps({"level": "suspected", "counsel": [GC]}))
    assert [w["reason"] for w in privscreen.screen(m2, deck)] == ["counsel"]
    # clearing is a lawyer's call: Claude can't, a user can
    annotations.write(m2, "user:lawyer", {"op": "define-tag", "tag": privscreen.CLEARED})
    with pytest.raises(annotations.Refused):
        annotations.write(m2, annotations.CLAUDE, {"op": "tag", "tag": privscreen.CLEARED, "target": "doc:" + memo})
    annotations.write(m2, "user:lawyer", {"op": "tag", "tag": privscreen.CLEARED, "target": "doc:" + memo})
    assert privscreen.screen(Matter(m2.root), memo) == []


def test_analyze_leaves_out_held_back_passages(m, capsys, monkeypatch):
    monkeypatch.setenv("ADHOC_MATTER", str(m.root))
    cli.analyze(["trace", "--query", '"litigation hold"'])
    r = json.loads(capsys.readouterr().out.splitlines()[0])
    held = [x for x in r["appearances"] if x.get("screened")]
    assert held and all(x["excerpt"].startswith("[screened") for x in held)
    assert all(not x["route"]["text"].startswith("[screened") for x in held)       # the tool's words stay
    assert all(x["name"] and x["date"] for x in held)                              # so do names and dates


def test_the_levels(tmp_path, capsys, monkeypatch):
    m2 = Matter.create(tmp_path / "m3")
    ingest_paths(m2, [CORPUS / "delacroix-m" / "privileged-memo-harrowgate-2026-03-27.docx",
                      CORPUS / "voss-r" / "demand-letter-2026-03-25.pdf"], "x", "c")
    m2 = Matter(m2.root)
    memo, letter = named(m2, "privileged-memo-harrowgate-2026-03-27.docx"), named(m2, "demand-letter-2026-03-25.pdf")
    assert privscreen.level(m2) == "marked" and privscreen.screened(m2, [memo, letter]) == {}
    # a person's privileged tag holds a document back at marked and suspected, not off; Claude's can't be made
    annotations.write(m2, "user:lawyer", {"op": "define-tag", "tag": "privileged"})
    annotations.write(m2, "user:lawyer", {"op": "tag", "tag": "privileged", "target": "doc:" + letter})
    assert privscreen.screen(m2, letter) == [{"reason": "tagged", "by": ["user:lawyer"]}]
    privscreen.set_level(m2, "suspected")
    assert set(privscreen.screened(m2, [memo, letter])) == {memo, letter}
    privscreen.set_level(m2, "off")
    assert privscreen.screened(m2, [memo, letter]) == {}
    with pytest.raises(ValueError):
        privscreen.set_level(m2, "paranoid")
    monkeypatch.setenv("ADHOC_MATTER", str(m2.root))
    cli.ann(["privilege-screen", "marked"])
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"level": "marked", "held_back": 1}
    cli.ann(["privilege-screen", "--list"])
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rows[1]["doc"] == letter and rows[1]["why"] == "tagged privileged by user:lawyer" and rows[1]["arrived"]["how"] == "collected"
