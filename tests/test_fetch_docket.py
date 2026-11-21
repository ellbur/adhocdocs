"""tools/corpus/fetch_docket.py: only what the start pages link to, filtered, with provenance."""

import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "corpus"))

import fetch_docket  # noqa: E402

CASE = """<html><body>
<a href="/files/d09379_complaint.pdf">Complaint</a>
<a href="/legal-library/browse/cases-proceedings/benco-timeline-1">Benco timeline item</a>
<a href="/legal-library/browse/cases-proceedings/other-matter">Another matter</a>
<a href="/files/unrelated.pdf">Unrelated</a>
<a href="https://elsewhere.example/files/d09379_copy.pdf">Off-site copy</a>
<a href="#top">Top</a>
</body></html>"""
TIMELINE = """<html><body><a href="/files/d09379_order.pdf">Order</a>
<a href="/private/d09379_secret.pdf">Disallowed by robots</a></body></html>"""


def serve(root: Path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    srv.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_fetches_only_linked_wanted_files(tmp_path):
    site = tmp_path / "site"
    (site / "legal-library/browse/cases-proceedings").mkdir(parents=True)
    (site / "files").mkdir()
    (site / "private").mkdir()
    (site / "case.html").write_text(CASE)
    (site / "legal-library/browse/cases-proceedings/benco-timeline-1").write_text(TIMELINE)
    (site / "legal-library/browse/cases-proceedings/other-matter").write_text('<a href="/files/d09379_x.pdf">x</a>')
    for n in ("d09379_complaint.pdf", "d09379_order.pdf", "unrelated.pdf", "d09379_x.pdf"):
        (site / "files" / n).write_bytes(b"%PDF-1.4 " + n.encode())
    (site / "private" / "d09379_secret.pdf").write_bytes(b"%PDF")
    (site / "robots.txt").write_text("User-agent: *\nDisallow: /private/\n")
    srv = serve(site)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    out = tmp_path / "out"
    res = fetch_docket.crawl([base + "/case.html"], out, ["/cases-proceedings/"], ["d09379", "benco"],
                             depth=2, delay=0, limit=100)
    srv.shutdown()
    got = sorted(p.name for p in out.iterdir() if p.suffix == ".pdf")
    # the complaint (linked from the case page) and the order (from its timeline page); not the
    # unrelated file, another matter's page, an off-site copy, or what robots.txt disallows
    assert got == ["d09379_complaint.pdf", "d09379_order.pdf"], got
    rows = [json.loads(l) for l in (out / "sources.jsonl").read_text().splitlines()]
    order = next(r for r in rows if r["file"] == "d09379_order.pdf")
    assert order["linked_from"].endswith("benco-timeline-1") and order["link_text"] == "Order"
    assert any(s["why"] == "robots.txt" for s in res["skipped"])
    # a second run fetches nothing again
    srv = serve(site)
    base2 = f"http://127.0.0.1:{srv.server_address[1]}"
    res2 = fetch_docket.crawl([base2 + "/case.html"], out, ["/cases-proceedings/"], ["d09379", "benco"],
                              depth=2, delay=0, limit=100)
    srv.shutdown()
    assert res2["already"] == 2
