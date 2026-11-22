"""Download a fetched test corpus from its bucket instead of from the agency,
with its OCR cache. See docs/QUOTEBENCH.md.

    uv run python tools/corpus/pull_corpus.py            # the FTC docket into corpus-ftc/
    ADHOC_OCR_CACHE=corpus-ftc/ocr-cache uv run adhoc ...  # then ingest reads OCR from the cache

It runs ``gcloud storage rsync`` (so gcloud must be signed in, with read
access to the bucket: it is private), then checks every file against the
SHA-256 that ``sources.jsonl`` recorded when it was fetched from the agency.
``--bucket`` (or ``$ADHOC_CORPUS_BUCKET``) points it at another copy.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUCKET = "gs://ellbur-adhocdocs-corpora"
# what is in the bucket, under <bucket>/ftc/: the docket and the OCR cache for it
PARTS = ["benco-9379", "ocr-cache"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


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
        elif sha256(p) != row["sha256"]:
            bad.append(f"{row['file']}: SHA-256 differs from when it was fetched")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", default=os.environ.get("ADHOC_CORPUS_BUCKET", BUCKET))
    ap.add_argument("--out", default=str(REPO / "corpus-ftc"), help="where to put it (default corpus-ftc/)")
    ap.add_argument("--no-ocr-cache", action="store_true", help="only the documents")
    a = ap.parse_args()
    gcloud = shutil.which("gcloud")
    if not gcloud:
        sys.exit("gcloud not found: install the Google Cloud CLI and run `gcloud auth login`")
    out = Path(a.out)
    for part in PARTS:
        if part == "ocr-cache" and a.no_ocr_cache:
            continue
        (out / part).mkdir(parents=True, exist_ok=True)
        print(f"== {a.bucket}/ftc/{part} -> {out / part}", file=sys.stderr, flush=True)
        r = subprocess.run([gcloud, "storage", "rsync", "-r", f"{a.bucket.rstrip('/')}/ftc/{part}", str(out / part)])
        if r.returncode:
            sys.exit(f"gcloud storage rsync failed for {part} (signed in? `gcloud auth login`)")
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
