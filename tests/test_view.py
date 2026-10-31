import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from adhocdocs import lists, view
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter
from corpus_files import story

CORPUS = Path(__file__).parent.parent / "corpus"


@pytest.fixture
def m(tmp_path):
    m = Matter.create(tmp_path / "m")
    ingest_paths(m, story("okafor-d", "*.eml"), "okafor-d", "c")
    return Matter(m.root)


def test_socket_path_rule(tmp_path, monkeypatch):
    monkeypatch.delenv("ADHOC_VIEW_SOCKET", raising=False)
    m = Matter.create(tmp_path / "m")
    assert view.socket_path(m) == m.root / "run" / "viewer.sock"
    long = Matter.create(tmp_path / ("x" * 120))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1")
    # the same hash the viewer computes (see viewer/src/api.rs)
    assert view.socket_path(long).parent.parent == Path("/run/user/1/adhocdocs")
    assert view.socket_path(long).name == "viewer.sock"
    assert view.service_socket(long, "x").parent == view.socket_path(long).parent      # services are per matter


def test_list_html(m, tmp_path):
    docs = [d for d in m.state.live_documents()]
    p = tmp_path / "a.list.jsonl"
    header, items = lists.new("Okafor", ["doc:" + d for d in docs], columns=["date", "name", "tags", "why"])
    items[0]["fields"] = {"why": "<b>first</b>"}
    items[0]["group"] = "G1"
    lists.write(p, header, items)
    files, seq = view.list_html(m, p)
    page = base64.b64decode(files["index.html"]).decode()
    assert seq == ["doc:" + d for d in docs]
    assert page.count("data-locator=") == len(docs) and "data-sequence" in page
    assert "&lt;b&gt;first&lt;/b&gt;" in page            # list fields are text, not HTML
    assert 'data-tags="doc:' in page and "G1 (1)" in page


def test_list_html_pages(m, tmp_path, monkeypatch):
    monkeypatch.setattr(view, "ROWS_PER_PAGE", 3)
    docs = m.state.live_documents()
    p = tmp_path / "a.list.jsonl"
    lists.write(p, *lists.new("All", ["doc:" + d for d in docs]))
    files, seq = view.list_html(m, p)
    assert len(files) == -(-len(docs) // 3) and len(seq) == len(docs)
    assert 'href="page2.html"' in base64.b64decode(files["index.html"]).decode()


def test_annotate_as_user(m):
    def run(req):
        return subprocess.run([sys.executable, "-m", "adhocdocs.cli", "adhoc", "--matter", str(m.root), "annotate-as-user",
                               "--user", "owen"], input=json.dumps(req), capture_output=True, text=True)
    r = run({"op": "define-tag", "tag": "hot", "hotkey": "h"})
    assert r.returncode == 0 and json.loads(r.stdout)["events"][0]["author"] == "user:owen"
    r = run({"op": "define-tag", "tag": "hot"})
    assert r.returncode == 1 and "already defined" in json.loads(r.stderr)["refused"]


def test_view_not_running(m, monkeypatch, tmp_path):
    monkeypatch.setenv("ADHOC_VIEW_SOCKET", str(tmp_path / "none.sock"))
    with pytest.raises(view.NotRunning):
        view.command(m, {"cmd": "state"})


def test_window_commands(m, monkeypatch, capsys):
    """What the view CLI sends for windows (the viewer itself is tested by hand: VIEWER.md)."""
    from adhocdocs import cli
    sent = []
    monkeypatch.setattr(view, "command", lambda _m, c: sent.append(c) or {})
    doc = next(iter(m.state.documents))
    run = lambda *args: cli.view(["--matter", str(m.root), *args])
    run("window", "new", "--pane", "p2", "--layout", "split-v")
    run("window", "move", "--pane", "p2", "--window", "w1")
    run("window", "close", "--window", "w2")
    run("open", doc, "--new-window")
    run("open", doc, "--window", "w2", "--mode", "text")
    run("layout", "grid", "--window", "w2")
    assert sent[:3] == [{"cmd": "window-new", "pane": "p2", "layout": "split-v"},
                        {"cmd": "window-move", "pane": "p2", "window": "w1"},
                        {"cmd": "window-close", "window": "w2"}]
    assert sent[3]["new_window"] is True and "window" not in sent[3]
    assert sent[4]["window"] == "w2" and sent[4]["mode"] == "text" and "new_window" not in sent[4]
    assert sent[5] == {"cmd": "layout", "layout": "grid", "window": "w2"}
    with pytest.raises(SystemExit):
        run("window", "close")                       # which window?
    with pytest.raises(SystemExit):
        run("open", doc, "--window", "w2", "--new-window")
    assert len(sent) == 6
