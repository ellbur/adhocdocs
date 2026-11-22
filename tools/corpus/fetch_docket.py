"""Download a public agency docket by following its own links. See tools/corpus/ftc_matter.sh.

    uv run python tools/corpus/fetch_docket.py --out corpus-ftc/benco \\
        --start https://www.ftc.gov/legal-library/browse/cases-proceedings/151-0190-bencoscheinpatterson-matter \\
        --start https://www.ftc.gov/legal-library/browse/frequently-requested-foia-records/transcripts-adjudicative-proceedings \\
        --follow /legal-library/browse/cases-proceedings/ --follow /transcripts-adjudicative-proceedings/benco \\
        --keep benco --keep d09379 --keep 9379

Only what the start pages link to is fetched: no URL is made up or guessed,
so nothing is reached that the agency doesn't link to publicly. From each
page, it takes:

- **files** (PDF, DOC, DOCX, TXT, HTML under a files path) linked from it,
  when their URL or link text has one of the --keep words (all of them, with
  no --keep);
- **pages** linked from it on the same host whose path contains one of the
  --follow prefixes and one of the --keep words (the case page's timeline
  items, the pages of its listing, a transcript's own page), up to --depth
  links from a start page.

It honours robots.txt, waits --delay seconds between requests, names itself
in the User-Agent, and writes ``sources.jsonl`` beside the files: for each
file, the URL, the page that linked to it, the link text, the size and the
SHA-256. Files already there (same name) are not fetched again, so a run can
be resumed. Nothing here runs JavaScript: a page that builds its links in the
browser shows only the links in its HTML.
"""

import argparse
import hashlib
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path

UA = "adhocdocs-corpus-fetch/1 (research; follows public links only)"
FILE_EXT = re.compile(r"\.(pdf|docx?|txt|rtf|xlsx?|csv)$", re.I)


class Links(HTMLParser):
    """The <a href> links of a page, with their text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def links_of(html: str, base: str) -> list[tuple[str, str]]:
    p = Links()
    p.feed(html)
    out = []
    for href, text in p.links:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urllib.parse.urldefrag(urllib.parse.urljoin(base, href))[0]
        out.append((url, text))
    return out


def is_file(url: str) -> bool:
    return bool(FILE_EXT.search(urllib.parse.urlparse(url).path))


def wanted(url: str, text: str, keep: list[str]) -> bool:
    hay = (urllib.parse.unquote(url) + " " + text).lower()
    return not keep or any(k.lower() in hay for k in keep)


def classify(links: list[tuple[str, str]], host: str, follow: list[str], keep: list[str]):
    """(files, pages) to take from a page's links."""
    files, pages = [], []
    for url, text in links:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or u.netloc != host:
            continue
        if is_file(url):
            if wanted(url, text, keep):
                files.append((url, text))
        elif any(f in u.path for f in follow) and wanted(url, text, keep):
            pages.append((url, text))
    return files, pages


def file_name(url: str, taken: set[str]) -> str:
    name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name) or "file"
    name = re.sub(r"[^\w.\-()]+", "_", name)
    base, dot, ext = name.rpartition(".")
    n, out = 1, name
    while out in taken:
        n += 1
        out = f"{base}-{n}.{ext}" if dot else f"{name}-{n}"
    taken.add(out)
    return out


class FetchError(OSError):
    """A download that kept failing part way."""


class Fetcher:
    def __init__(self, delay: float):
        self.delay, self.last, self.robots = delay, 0.0, {}

    def allowed(self, url: str) -> bool:
        u = urllib.parse.urlparse(url)
        root = f"{u.scheme}://{u.netloc}"
        if root not in self.robots:
            # fetched with our own User-Agent, not RobotFileParser.read()'s
            # Python-urllib one: some sites (www.ftc.gov) answer that with 403,
            # which the parser takes as "disallow everything"
            rp = urllib.robotparser.RobotFileParser(root + "/robots.txt")
            try:
                req = urllib.request.Request(root + "/robots.txt", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r:
                    rp.parse(r.read().decode("utf-8", "replace").splitlines())
                # the site's Crawl-delay, when it asks for longer than --delay
                self.delay = max(self.delay, float(rp.crawl_delay(UA) or 0))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.allow_all = True    # no robots.txt (404 etc.): as RobotFileParser.read() does
            except OSError:
                rp = None          # no robots.txt reachable: the start pages were named by a person
            self.robots[root] = rp
        rp = self.robots[root]
        return rp is None or rp.can_fetch(UA, url)

    def get(self, url: str, tries: int = 3, max_requests: int = 30) -> tuple[bytes, str]:
        """The body and content type. A dropped connection (a large file cut
        off part way, a reset, a timeout) is tried again; when the server takes
        byte ranges (www.ftc.gov does, and cuts off long downloads), from where
        it stopped. It gives up after ``tries`` attempts in a row that get no
        further, or ``max_requests`` in all. An HTTP error status isn't retried."""
        body, ctype, total = b"", "", None
        stalled = 0
        for n in range(1, max_requests + 1):
            wait = self.last + self.delay * (stalled + 1) - time.time()
            if wait > 0:
                time.sleep(wait)
            headers = {"User-Agent": UA}
            if body:
                headers["Range"] = f"bytes={len(body)}-"
            before = len(body)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
                    if body and r.status != 206:
                        body = b""                      # the range was ignored: this is the whole file again
                    ctype = ctype or r.headers.get("content-type", "")
                    if total is None:
                        cr = r.headers.get("content-range", "")
                        total = int(cr.rsplit("/", 1)[1]) if "/" in cr and not cr.endswith("*") else \
                            (int(r.headers["content-length"]) if r.headers.get("content-length") else None)
                    try:
                        body += r.read()
                    except http.client.IncompleteRead as e:
                        body += e.partial
                        raise
                if total is None or len(body) >= total:
                    return body, ctype
                raise FetchError(f"short body: {len(body)} of {total} bytes")
            except urllib.error.HTTPError:
                raise
            except (OSError, http.client.HTTPException) as e:
                stalled = 0 if len(body) > before else stalled + 1
                if stalled >= tries or n == max_requests:
                    raise FetchError(f"{type(e).__name__}: {e}") from e
                where = f", resuming at byte {len(body)}" if body else ""
                print(f"retrying {url} ({type(e).__name__}{where})", file=sys.stderr, flush=True)
            finally:
                self.last = time.time()


def crawl(start: list[str], out: Path, follow: list[str], keep: list[str], depth: int, delay: float,
          limit: int, dry_run: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    log = out / "sources.jsonl"
    done = {json.loads(l)["url"]: json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l} \
        if log.exists() else {}
    taken = {r["file"] for r in done.values()}
    f = Fetcher(delay)
    queue = [(u, 0, None) for u in start]
    seen_pages, files, skipped = set(), [], []
    while queue:
        url, d, via = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        if not f.allowed(url):
            skipped.append({"url": url, "why": "robots.txt"})
            continue
        try:
            body, ctype = f.get(url)
        except OSError as e:
            skipped.append({"url": url, "why": str(e)})
            continue
        html = body.decode("utf-8", "replace")
        fl, pg = classify(links_of(html, url), urllib.parse.urlparse(url).netloc, follow, keep)
        for u, text in fl:
            if u not in {x[0] for x in files}:
                files.append((u, text, url))
        if d < depth:
            queue.extend((u, d + 1, url) for u, _ in pg if u not in seen_pages)
        print(f"page {url}: {len(fl)} files, {len(pg)} pages", file=sys.stderr, flush=True)
    got = 0
    for u, text, via in files[:limit]:
        if u in done:
            continue
        if dry_run:
            print(f"would fetch {u}", file=sys.stderr)
            continue
        if not f.allowed(u):
            skipped.append({"url": u, "why": "robots.txt"})
            continue
        try:
            body, ctype = f.get(u)
        except OSError as e:
            skipped.append({"url": u, "why": str(e)})
            continue
        name = file_name(u, taken)
        (out / name).write_bytes(body)
        row = {"file": name, "url": u, "linked_from": via, "link_text": text, "bytes": len(body),
               "sha256": hashlib.sha256(body).hexdigest(), "content_type": ctype,
               "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with log.open("a", encoding="utf-8") as lf:
            lf.write(json.dumps(row) + "\n")
        got += 1
        print(f"file {name} ({len(body)} bytes)", file=sys.stderr, flush=True)
    return {"pages": len(seen_pages), "files_linked": len(files), "fetched": got, "already": len(done),
            "skipped": skipped}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", action="append", required=True, help="a page to start from (repeatable)")
    ap.add_argument("--out", required=True, help="the directory for the files and sources.jsonl")
    ap.add_argument("--follow", action="append", default=[], help="follow same-host pages whose path has this (repeatable)")
    ap.add_argument("--keep", action="append", default=[], help="only files and pages whose URL or link text has this")
    ap.add_argument("--depth", type=int, default=2, help="links to follow from a start page (default 2)")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between requests (default 2)")
    ap.add_argument("--limit", type=int, default=2000, help="most files to fetch")
    ap.add_argument("--dry-run", action="store_true", help="list the files, fetch none")
    a = ap.parse_args()
    print(json.dumps(crawl(a.start, Path(a.out), a.follow, a.keep, a.depth, a.delay, a.limit, a.dry_run)))


if __name__ == "__main__":
    main()
