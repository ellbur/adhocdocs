"""The story documents in corpus/, without the irrelevant ones that
tools/corpus/build_noise.py writes beside them (it lists them in
tools/corpus/noise-files.txt)."""

from pathlib import Path

ROOT = Path(__file__).parent.parent
CORPUS = ROOT / "corpus"
NOISE = {CORPUS / l for l in (ROOT / "tools" / "corpus" / "noise-files.txt").read_text().splitlines()
         if l.strip() and not l.startswith("#")}


def story(custodian: str, pattern: str = "*") -> list[Path]:
    """custodian's story files matching pattern, sorted."""
    return sorted(p for p in (CORPUS / custodian).glob(pattern) if p not in NOISE)
