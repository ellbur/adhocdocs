"""Dates (DATES.md): finding dates in text, placing relative ones, a
document's own date, the dates index, event entities, and `analyze around`."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from adhocdocs import dates, lists
from adhocdocs.annotations import Annotations, Refused, write
from adhocdocs.index import open_index
from adhocdocs.ingest import ingest_bytes
from adhocdocs.matter import Matter

ROOT = Path(__file__).parent.parent
CASES = json.loads((ROOT / "tests" / "date_cases.json").read_text())["cases"]


def found(text):
    return [(d["form"], d["kind"], d.get("date")) for d in dates.detect(text)]


# -- detection -------------------------------------------------------------------------------

def test_written_forms():
    assert found("signed March 9, 2026 and Mar. 10 2026; on Monday, March 9th, 2026.") == [
        ("March 9, 2026", "absolute", "2026-03-09"), ("Mar. 10 2026", "absolute", "2026-03-10"),
        ("Monday, March 9th, 2026", "absolute", "2026-03-09")]
    assert found("served 9 March 2026, the 9th of March, 2026") == [
        ("9 March 2026", "absolute", "2026-03-09"), ("9th of March, 2026", "absolute", "2026-03-09")]
    assert found("Date: Wed, 18 Feb 2026 09:12:44 -0500") == [("Wed, 18 Feb 2026", "absolute", "2026-02-18")]


def test_numeric_forms():
    assert found("on 2026-03-09 and 3/19/2026 and 03/09/26") == [
        ("2026-03-09", "absolute", "2026-03-09"), ("3/19/2026", "absolute", "2026-03-19"), ("03/09/26", "absolute", "2026-03-09")]
    d, = dates.detect("invoiced 3/4/2026")
    assert d["date"] == "2026-03-04" and d["ambiguous"] and d["alt"] == "2026-04-03"
    assert found("19/3/2026") == [("19/3/2026", "absolute", "2026-03-19")]          # only day-first makes sense
    assert found("9.3.2026") == [("9.3.2026", "absolute", "2026-03-09")]           # dots are day first
    assert found("see section 1.2.26, case 12/45/2026, and v2026-13-01") == []


def test_partial_and_relative():
    assert found("In March 2026 we shipped on March 2, and Feb. 19") == [
        ("March 2026", "month", "2026-03"), ("March 2", "noyear", "--03-02"), ("Feb. 19", "noyear", "--02-19")]
    assert found("you may 5 times") == []                                             # "may" the verb
    assert found("it was due 3/7, and 1/2 of it by 12/31") == [("3/7", "noyear", "--03-07"), ("12/31", "noyear", "--12-31")]
    rel = dates.detect("Yesterday I called; last Friday too, and next Tuesday. Today is fine.")
    assert [(d["form"], d["rel"]) for d in rel] == [
        ("Yesterday", {"days": -1}), ("last Friday", {"weekday": 4, "dir": -1}), ("next Tuesday", {"weekday": 1, "dir": 1})]


def test_bare_weekdays_and_days_of_the_month():
    got = [(d["form"], d["rel"]) for d in dates.detect("Call me on Friday, or by Tue. Nothing since Monday. Due the 15th.")]
    assert got == [("Friday", {"weekday": 4, "bare": "near"}), ("Tue", {"weekday": 1, "bare": "future"}),
                   ("Monday", {"weekday": 0, "bare": "past"}), ("the 15th", {"monthday": 15, "bare": "future"})]
    assert found("we work on Fridays; the sun rose on sun; on the 2nd floor; by the 3rd quarter") == []
    assert found("send it on friday") == []                       # bare weekdays only capitalized
    assert found("Then on the 2nd, we") == [("the 2nd", "relative", None)]


def test_weeks_months_years():
    got = [(d["form"], d["rel"], d["precision"]) for d in dates.detect("last week, this week, next month and last year")]
    assert got == [("last week", {"weeks": -1}, "week"), ("this week", {"weeks": 0}, "week"),
                   ("next month", {"months": 1}, "month"), ("last year", {"years": -1}, "year")]


def test_ranges():
    def rng(t):
        return [(d["form"], d["kind"], d["date"], d.get("until")) for d in dates.detect(t)]
    assert rng("on site March 2–6, 2026, then March 30 to April 2") == [
        ("March 2–6, 2026", "absolute", "2026-03-02", "2026-03-06"), ("March 30 to April 2", "noyear", "--03-30", "--04-02")]
    assert rng("2–6 March 2026; 30 March – 2 April 2026; Dec 28 – Jan 3, 2027") == [
        ("2–6 March 2026", "absolute", "2026-03-02", "2026-03-06"),
        ("30 March – 2 April 2026", "absolute", "2026-03-30", "2026-04-02"),
        ("Dec 28 – Jan 3, 2027", "absolute", "2026-12-28", "2027-01-03")]
    assert rng("March 6-2") == [("March 6", "noyear", "--03-06", None)]        # backwards: not a range
    assert rng("we sell 2 to 6 May") == [] and rng("may 2-6 times") == []
    assert rng("pages 2-6 of March 2026") == [("March 2026", "month", "2026-03", None)]


def test_a_longer_form_wins():
    assert found("March 9, 2026") == [("March 9, 2026", "absolute", "2026-03-09")]  # not also "March 9"


def test_quoted_headers_name_the_sender():
    text = ("Thanks.\n\nOn Mon, Mar 9, 2026 at 10:14 AM, R. Voss <rvoss@hfs.example> wrote:\n> Pallet went out March 2.\n"
            "\n-----Original Message-----\nFrom: Ames, Rita [mailto:ra@bms.example]\nSent: Tuesday, March 10, 2026 4:01 PM\n")
    ds = dates.detect(text)
    assert [d.get("header") for d in ds] == [{"who": "R. Voss"}, None, {"who": "Ames, Rita"}]
    for line, who in [("On Mon, Mar 9, 2026 at 10:14 AM Dana Okafor <d@x.example> wrote:", "Dana Okafor"),
                      ('On 3/9/2026 4:01 PM EST, "Ames, Rita" <ra@x.example> wrote:', "Ames, Rita")]:
        assert dates.detect(line)[0]["header"] == {"who": who}


@pytest.mark.parametrize("case", CASES)
def test_resolve(case):
    assert dates.resolve(case["mention"], case["anchor"]) == case["want"]
    assert dates.resolve_until(case["mention"], case["anchor"]) == case.get("until")


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_viewer_resolves_the_same():
    """viewer/shell/dates.js gives the same answers as dates.resolve."""
    script = """
const { resolveDate, resolveUntil } = require(process.argv[1]);
const cases = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")).cases;
console.log(JSON.stringify(cases.map((c) => [resolveDate(c.mention, c.anchor), resolveUntil(c.mention, c.anchor)])));
"""
    out = subprocess.run(["node", "-e", script, str(ROOT / "viewer/shell/dates.js"), str(ROOT / "tests/date_cases.json")],
                         capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [[c["want"], c.get("until")] for c in CASES]


# -- a document's own date ----------------------------------------------------------------------

def test_doc_date_sources():
    assert dates.doc_date({"kind": "email", "date": "2026-03-09T10:14:00-05:00"}) == {"date": "2026-03-09T10:14:00-05:00", "source": "sent"}
    assert dates.doc_date({"kind": "pdf", "date": "2020-01-01"}, {"kind": "email", "date": "2026-03-09"}) == {"date": "2026-03-09", "source": "attached"}
    assert dates.doc_date({"kind": "docx", "date": "2019-05-01", "date_source": "created"}) == {"date": "2019-05-01", "source": "created"}
    assert dates.doc_date({"kind": "pdf", "date": "2019-05-01"}) == {"date": "2019-05-01", "source": "metadata"}
    assert dates.doc_date({"kind": "text"}) is None


EMAIL = b"""From: Rita Ames <ra@bms.example>
To: Raymond Voss <rvoss@hfs.example>
Subject: Pallet
Date: Wed, 11 Mar 2026 09:00:00 -0500
Message-ID: <b@bms.example>
Content-Type: multipart/mixed; boundary=XX

--XX
Content-Type: text/plain

We rejected it March 9. Yesterday Voss called. See the log.

--XX
Content-Type: text/plain; name=log.txt
Content-Disposition: attachment; filename=log.txt

Dock 4 log, 2026-03-09: pallet refused.
--XX--
"""


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "m")
    ingest_bytes(m, EMAIL, "mail.eml", {"custodian": "cust:a", "source": {"collection": "c", "path": "mail.eml"}})
    return Matter(m.root)


def test_index_files_and_analyze(m):
    ix = open_index(m, "dates")
    ix.update()
    state = m.state
    email = next(d for d in state.live_documents() if d not in {c for cs in state.children.values() for c in cs})
    attachment = state.children[email][0]
    layer = state.primary_layer(state.documents[email])
    side = json.loads((ix.dir / "texts" / (layer.text.removeprefix("sha256:") + ".json")).read_text())
    assert {d["form"] for d in side["dates"]} >= {"March 9", "Yesterday"}
    assert json.loads((ix.dir / "threads.json").read_text()) == {"of": {}, "threads": {}}
    got = dates.doc_mentions(m, email)
    assert got["dated"]["source"] == "sent"
    by_form = {x["form"]: x["date"] for x in got["mentions"]}
    assert by_form["March 9"] == "2026-03-09" and by_form["Yesterday"] == "2026-03-10"
    assert dates.doc_dates(m)[attachment]["source"] == "attached"


def test_event_entities_and_around(m):
    e = write(m, "claude", {"op": "define-entity", "name": "Dock 4 rejection", "kind": "event", "date": "2026-03-09"})[0]["entity"]
    assert Annotations.load(m).entities[e]["date"] == "2026-03-09"
    with pytest.raises(Refused):
        write(m, "claude", {"op": "edit-entity", "entity": e, "date": "March 9"})
    email = next(iter(dates.doc_dates(m)))
    header, items = lists.new("Chronology", ["doc:" + email])
    items[0]["fields"] = {"date": "2026-03-11", "event": "Ames writes to Voss"}
    lists.write(m.workspace / "chron.list.jsonl", header, items)
    got = dates.around(m, "2026-03-10", 3)
    assert [e["name"] for e in got["events"]] == ["Dock 4 rejection"]
    assert [c["event"] for c in got["chronology"]] == ["Ames writes to Voss"]
    assert len(got["documents"]) == 1                                  # the email; its attachment goes with it
    assert dates.around(m, "2026-04-10", 3)["events"] == []
