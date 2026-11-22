"""Fill an OCR cache for a folder of PDFs, so ingesting them later (a test
corpus, into a fresh matter) needn't run tesseract. See tools/corpus/ftc_matter.sh.

    uv run python tools/corpus/ocr_cache.py corpus-ftc/benco-9379 --cache corpus-ftc/ocr-cache

The pages OCRed are the ones ingest OCRs (no native text, an image on the
page: adhocdocs.extract.pdf), rendered and read by the same functions, so
ingest with ``ADHOC_OCR_CACHE`` set to the same folder finds every one of
them there. The pages are spread over processes, since one long scanned
exhibit would otherwise keep a single core busy for an hour. ``--check``
then extracts each file with tesseract disabled, to show nothing misses.
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("OMP_THREAD_LIMIT", "1")   # one tesseract thread per process; the pool is the parallelism

import pymupdf  # noqa: E402

from adhocdocs.extract import pdf  # noqa: E402


def pages_to_ocr(path: Path) -> list[int]:
    doc = pymupdf.open(path)
    out = []
    for i, page in enumerate(doc):
        chars = sum(len(w) for l in pdf.native_lines(page) for w, _, _ in l)
        if chars < pdf.MIN_PAGE_CHARS and page.get_images():
            out.append(i)
    return out


def ocr_one(path: str, i: int) -> None:
    pdf.ocr_page(pymupdf.open(path)[i])


def check(path: Path) -> None:
    def missed(png, psm):
        raise RuntimeError("not in the OCR cache")
    real, pdf._tesseract_lines = pdf._tesseract_lines, missed
    try:
        pdf.extract_pdf(path.read_bytes())
    finally:
        pdf._tesseract_lines = real


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="the PDFs (not recursive)")
    ap.add_argument("--cache", required=True, help="the OCR cache folder (ADHOC_OCR_CACHE)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--check", action="store_true", help="afterwards, extract each file with tesseract disabled")
    a = ap.parse_args()
    os.environ["ADHOC_OCR_CACHE"] = str(Path(a.cache).resolve())
    files = sorted(Path(a.folder).glob("*.pdf"))
    tasks = [(str(f), i) for f in files for i in pages_to_ocr(f)]
    print(f"{len(tasks)} pages to OCR in {len({t[0] for t in tasks})} of {len(files)} files", file=sys.stderr, flush=True)
    start, done, failed = time.time(), 0, []
    with ProcessPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(ocr_one, *t): t for t in tasks}
        for fut in as_completed(futs):
            done += 1
            if fut.exception():
                failed.append((futs[fut], repr(fut.exception())))
            if done % 50 == 0 or done == len(tasks):
                rate = done / (time.time() - start)
                print(f"{done}/{len(tasks)} pages, {rate:.1f}/s, ~{(len(tasks) - done) / rate / 60:.0f} min left",
                      file=sys.stderr, flush=True)
    for (f, i), e in failed:
        print(f"failed: {Path(f).name} page {i + 1}: {e}", file=sys.stderr)
    if a.check:
        for f in files:
            check(f)
        print(f"check: all {len(files)} files extract from the cache alone", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
