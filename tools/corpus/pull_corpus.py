"""Download a fetched test corpus from its public bucket instead of from the
agency, with its OCR cache. See docs/QUOTEBENCH.md.

    uv run python tools/corpus/pull_corpus.py            # the FTC docket into corpus-ftc/
    ADHOC_OCR_CACHE=corpus-ftc/ocr-cache uv run adhoc ...  # then ingest reads OCR from the cache

The bucket is public, so this needs no account or gcloud: it lists the
bucket and downloads over HTTPS, skipping files already here with the same
MD5, then checks every document against the SHA-256 that ``sources.jsonl``
recorded when it was fetched from the agency. ``--bucket`` (or
``$ADHOC_CORPUS_BUCKET``) points it at another copy.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUCKET = "ellbur-adhocdocs-corpora"
PREFIX = "ftc/"                       # the docket (benco-9379/) and its OCR cache (ocr-cache/)
API = "https://storage.googleapis.com"


def listing(bucket: str, prefix: str):
    """(name, size, md5 base64) for each object under ``prefix``."""
    token = None
    while True:
        q = {"prefix": prefix, "fields": "items(name,size,md5Hash),nextPageToken"}
        if token:
            q["pageToken"] = token
        with urllib.request.urlopen(f"{API}/storage/v1/b/{bucket}/o?{urllib.parse.urlencode(q)}", timeout=60) as r:
            page = json.load(r)
        for it in page.get("items", []):
            yield it["name"], int(it["size"]), it.get("md5Hash")
        token = page.get("nextPageToken")
        if not token:
            return


def digest(path: Path, algo: str) -> bytes:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.digest()


def download(bucket: str, name: str, dest: Path, md5: str | None, tries: int = 3) -> bool:
    """False if it was already here; raises if it can't be had intact."""
    if dest.exists() and md5 and base64.b64encode(digest(dest, "md5")).decode() == md5:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    url = f"{API}/{bucket}/{urllib.parse.quote(name)}"
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
                for block in iter(lambda: r.read(1 << 20), b""):
                    f.write(block)
            if md5 and base64.b64encode(digest(tmp, "md5")).decode() != md5:
                raise OSError("MD5 differs from the bucket's")
            os.replace(tmp, dest)
            return True
        except OSError as e:
            if attempt == tries:
                tmp.unlink(missing_ok=True)
                raise OSError(f"{name}: {e}") from e
    return True


def verify(folder: Path) -> list[str]:
    """Problems with the files ``sources.jsonl`` lists: missing, or not what was fetched."""
    log = folder / "sources.jsonl"
    if not log.exists():
        return [f"{log}: missing"]
    bad = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        p = folder / row["file"]
        if not p.exists():
            bad.append(f"{row['file']}: missing")
        elif digest(p, "sha256").hex() != row["sha256"]:
            bad.append(f"{row['file']}: SHA-256 differs from when it was fetched")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", default=os.environ.get("ADHOC_CORPUS_BUCKET", BUCKET))
    ap.add_argument("--out", default=str(REPO / "corpus-ftc"), help="where to put it (default corpus-ftc/)")
    ap.add_argument("--no-ocr-cache", action="store_true", help="only the documents")
    ap.add_argument("--jobs", type=int, default=8, help="downloads at once")
    a = ap.parse_args()
    bucket = a.bucket.removeprefix("gs://").rstrip("/")
    out = Path(a.out)
    objs = [o for o in listing(bucket, PREFIX) if not (a.no_ocr_cache and o[0].startswith(PREFIX + "ocr-cache/"))]
    if not objs:
        sys.exit(f"nothing under gs://{bucket}/{PREFIX}")
    total = sum(size for _, size, _ in objs)
    print(f"{len(objs)} files, {total / 1e6:.0f} MB in gs://{bucket}/{PREFIX}", file=sys.stderr, flush=True)

    def one(o):
        name, _, md5 = o
        return download(bucket, name, out / name[len(PREFIX):], md5)
    with ThreadPoolExecutor(a.jobs) as ex:
        got = sum(ex.map(one, objs))
    print(f"downloaded {got}, already here {len(objs) - got}", file=sys.stderr, flush=True)
    bad = verify(out / "benco-9379")
    for b in bad:
        print(b, file=sys.stderr)
    if bad:
        sys.exit(1)
    n = sum(1 for l in (out / "benco-9379" / "sources.jsonl").read_text(encoding="utf-8").splitlines() if l)
    print(json.dumps({"docket": str(out / "benco-9379"), "files": n, "verified": True,
                      "ocr_cache": None if a.no_ocr_cache else str(out / "ocr-cache")}))


if __name__ == "__main__":
    main()
