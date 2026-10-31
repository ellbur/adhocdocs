"""The timeline display: dates, order, lanes, breaks in the axis, and what
`view timeline` sends. (How it looks is checked by hand in the viewer.)"""

import base64
import html
import json
import re
from datetime import date
from pathlib import Path

import pytest

from adhocdocs import lists, timeline, view
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter

CORPUS = Path(__file__).parent.parent / "corpus"


@pytest.fixture(scope="module")
def m(tmp_path_factory):
    m = Matter.create(tmp_path_factory.mktemp("timeline") / "m")
    ingest_paths(m, sorted((CORPUS / "okafor-d").glob("*.eml")), "okafor-d", "c")
    return Matter(m.root)


def page(files):
    return base64.b64decode(files["index.html"]).decode()


def test_parse_date():
    assert timeline.parse_date("2026-03-09T10:58:00-04:00") == date(2026, 3, 9)
    assert timeline.parse_date("2026-03") == date(2026, 3, 1) and timeline.parse_date("2007") == date(2007, 1, 1)
    assert timeline.parse_date("soon") is None and timeline.parse_date(None) is None


def test_chronology(m, tmp_path):
    docs = sorted(m.state.documents)
    path = tmp_path / "chron.list.jsonl"
    header, _ = lists.new("Chronology", [])
    items = [
        {"loc": "doc:" + docs[0], "group": "B", "fields": {"date": "2026-03-20", "event": "Later <event>"}},
        {"loc": "doc:" + docs[1], "group": "A", "fields": {"date": "2019-01", "event": "Long before"}},
        {"loc": "doc:" + docs[2], "group": "A", "fields": {"date": "2026-03-20", "event": "Same day"}},
        {"loc": "doc:" + docs[3], "fields": {"date": "someday", "event": "No date"}},
    ]
    lists.write(path, header, items)
    files, seq, s = timeline.timeline_html(m, path)

    out = page(files)
    labels = [html.unescape(x) for x in re.findall(r'data-locator="[^"]+"[^>]*>([^<]*)<', out)]
    assert labels.index("Long before") < labels.index("Later <event>") < labels.index("Same day")   # date order; ties keep list order
    assert seq[0] == "doc:" + docs[1] and len(seq) == 4
    assert s["lanes"] == "group" and s["lane_names"][:2] == ["A", "B"]
    assert len(s["segments"]) >= 2 and s["segments"][0] == ["2019-01-01", "2019-01-01"]        # 7 years cut out of the axis
    assert 'class="adhoc-tl-break"' in out and "7 yr" in out
    assert 'approx" data-locator="doc:' + docs[1] in out                                       # known only to the month
    assert "&lt;event&gt;" in out and "<event>" not in out                                     # labels are escaped

    # one-row lanes stack items that would overlap: the two on Mar 20 are in different lanes here
    tops = dict(re.findall(r'data-locator="doc:(sha256:\w+)"[^>]*style="left:\d+px;top:(\d+)px"', out))
    assert tops[docs[0]] != tops[docs[2]]


def test_lanes_and_undated(m, tmp_path):
    from adhocdocs.ingest import ingest_bytes
    note = ingest_bytes(m, b"call notes, no date anywhere", "notes.txt", {"custodian": "cust:a", "source": {"collection": "c", "path": "notes.txt"}})
    m = Matter(m.root)
    path = tmp_path / "all.list.jsonl"
    header, items = lists.new("All", ["doc:" + d for d in sorted(m.state.documents)])
    lists.write(path, header, items)
    files, seq, s = timeline.timeline_html(m, path, lanes="kind")
    assert s["lanes"] == "kind" and "email" in s["lane_names"] and "text" not in s["lane_names"]
    assert s["dated"] + s["undated"] == s["items"] == len(items) and s["undated"] >= 1
    out = page(files)
    assert "Undated (" in out and out.index("Undated (") < out.index(f'data-locator="doc:{note}"')   # listed after the timeline
    assert seq[-1] == "doc:" + note
    files, _, s = timeline.timeline_html(m, path, lanes="none", scale="week")
    assert s["px_per_day"] == timeline.SCALES["week"] and "lanes:" not in page(files)


def test_scale_keeps_distances():
    """Six entries weeks apart: one stretch of axis, not six islands."""
    dates = [date(2025, 11, 3), date(2026, 2, 9), date(2026, 2, 24), date(2026, 3, 1), date(2026, 3, 25), date(2026, 4, 28)]
    px = timeline.choose_scale(dates, "auto")
    assert len(timeline.segments(dates, px)) <= 3
    assert timeline.width_of(timeline.segments(dates, px), px) <= timeline.MAX_WIDTH
    assert timeline.choose_scale(dates, "day") == timeline.SCALES["day"]


def test_view_timeline_command(m, tmp_path, monkeypatch, capsys):
    from adhocdocs import cli
    m = Matter(m.root)                   # as the command will see it (another test adds a document)
    sent = []
    monkeypatch.setattr(view, "command", lambda _m, c: sent.append(c) or {"pane": "p1", "preview": "p2"})
    path = tmp_path / "t.list.jsonl"
    lists.write(path, *lists.new("Mine", ["doc:" + d for d in sorted(m.state.documents)]))
    cli.view(["--matter", str(m.root), "timeline", str(path), "--lanes", "custodian", "--new-window"])
    [c] = sent
    assert c["cmd"] == "pane" and c["kind"] == "timeline" and c["title"] == "Mine" and c["preview_new"] and c["new_window"]
    assert len(c["sequence"]) == len(m.state.documents)
    result = json.loads(capsys.readouterr().out)
    assert result["pane"] == "p1" and result["timeline"]["lanes"] == "custodian"
