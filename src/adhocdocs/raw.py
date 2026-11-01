"""Originals as bytes: for files no extractor reads, and for looking under
any extractor's text.

An original is stored exactly as given and never changes, so a range of
its bytes is as fixed a thing to quote as text in a layer. A **byte
locator** (``doc:<id>#bytes=0,16``) names one; its fingerprint is the
bytes' SHA-256 (and, for short ranges, the bytes in hex), so a superseded
or mistaken document can't quietly show other bytes. Bytes are shown as a
hex dump, a fixed display rule like the whitespace rule for text: offset,
sixteen bytes in hex, and the printable ASCII among them (``.`` for the
rest). That is the only *official* reading of an unknown format. What the
bytes mean is interpretation (adhocdocs.interp).

:func:`info` says what can be told without knowing the format: size,
hash, a signature if the first bytes match a known one, entropy, how much
looks like text, and for text whether it is JSON, JSON Lines or XML.
"""

import hashlib
import math
import re
from collections import Counter

from . import locators

SAMPLE = 1 << 20          # info reads at most this much for its statistics
HEX_QUOTE = 64            # byte fingerprints include the hex of ranges up to this long
DUMP_LIMIT = 1 << 16      # a hex dump shows at most this many bytes

# (offset, magic, what it is). Only facts about the first bytes: a match
# says what a file claims to be, and is reported as a signature, not a kind.
SIGNATURES = [
    (0, b"SQLite format 3\x00", "SQLite database"),
    (0, b"\x1f\x8b", "gzip"),
    (0, b"BZh", "bzip2"),
    (0, b"\xfd7zXZ\x00", "xz"),
    (0, b"\x28\xb5\x2f\xfd", "zstd"),
    (0, b"\x04\x22\x4d\x18", "lz4 frame"),
    (0, b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    (0, b"Rar!\x1a\x07", "RAR archive"),
    (0, b"PK\x03\x04", "zip (or a format built on zip)"),
    (0, b"PK\x05\x06", "zip (empty)"),
    (257, b"ustar", "tar archive"),
    (0, b"%PDF", "PDF"),
    (0, b"\x89PNG\r\n\x1a\n", "PNG image"),
    (0, b"\xff\xd8\xff", "JPEG image"),
    (0, b"GIF8", "GIF image"),
    (0, b"II*\x00", "TIFF image (little-endian)"),
    (0, b"MM\x00*", "TIFF image (big-endian)"),
    (0, b"BM", "BMP image (if the size field agrees)"),
    (0, b"RIFF", "RIFF container (WAV, AVI, WebP, …)"),
    (4, b"ftyp", "ISO media (MP4, MOV, HEIC, …)"),
    (0, b"OggS", "Ogg media"),
    (0, b"fLaC", "FLAC audio"),
    (0, b"ID3", "MP3 audio (ID3 tag)"),
    (0, b"\x1aE\xdf\xa3", "Matroska / WebM media"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE compound file (old Office, .msg, …)"),
    (0, b"!BDN", "Outlook PST/OST"),
    (0, b"{\\rtf", "RTF"),
    (0, b"bplist00", "binary property list (Apple)"),
    (0, b"PAR1", "Parquet"),
    (0, b"Obj\x01", "Avro object container"),
    (0, b"ORC", "ORC"),
    (0, b"ARROW1", "Arrow IPC"),
    (0, b"\x93NUMPY", "NumPy array"),
    (0, b"\x89HDF\r\n\x1a\n", "HDF5"),
    (0, b"\x7fELF", "ELF executable"),
    (0, b"MZ", "Windows executable (MZ)"),
    (0, b"\xca\xfe\xba\xbe", "Java class or Mach-O fat binary"),
    (0, b"\xcf\xfa\xed\xfe", "Mach-O executable"),
    (0, b"\x00asm", "WebAssembly"),
    (0, b"-----BEGIN ", "PEM (keys or certificates)"),
    (0, b"regf", "Windows registry hive"),
    (0, b"ElfFile\x00", "Windows event log (EVTX)"),
    (0, b"\x4c\x00\x00\x00\x01\x14\x02\x00", "Windows shortcut (.lnk)"),
    (0, b"BEGIN:VCARD", "vCard"),
    (0, b"BEGIN:VCALENDAR", "iCalendar"),
    (0, b"\xef\xbb\xbf", "UTF-8 text (with BOM)"),
    (0, b"\xff\xfe", "UTF-16 little-endian text (BOM), or other"),
    (0, b"\xfe\xff", "UTF-16 big-endian text (BOM), or other"),
]


def content_of(m, doc: str) -> str:
    state = m.state
    doc = locators.parse({"doc": doc}, m.objects)["doc"]
    if doc not in state.documents:
        raise locators.LocatorError("no such document")
    return state.documents[doc]


def size(m, content: str) -> int:
    return m.objects.path(content).stat().st_size


def read(m, content: str, start: int = 0, end: int | None = None) -> bytes:
    """Bytes [start, end) of stored content, without reading the rest."""
    with open(m.objects.path(content), "rb") as f:
        f.seek(start)
        return f.read(-1 if end is None else end - start)


def fingerprint(data: bytes) -> dict:
    q = {"sha256": "sha256:" + hashlib.sha256(data).hexdigest()}
    if len(data) <= HEX_QUOTE:
        q["hex"] = data.hex()
    return q


def byte_locator(doc: str, data_or_m, start: int, end: int, content: str | None = None) -> dict:
    """A durable byte-range locator, fingerprinted from the bytes. Pass the
    original's bytes, or the matter and the content hash."""
    data = data_or_m[start:end] if isinstance(data_or_m, (bytes, bytearray)) else read(data_or_m, content, start, end)
    return {"doc": doc, "select": {"bytes": [start, end]}, "quote": fingerprint(bytes(data))}


def check_bytes(m, content: str, sel: dict, quote: dict | None) -> tuple[bytes | None, str | None]:
    """(the bytes, None) or (None, why the byte locator is broken)."""
    a, b = sel["bytes"]
    n = size(m, content)
    if not (0 <= a <= b <= n):
        return None, f"bytes {a}–{b} are outside the original ({n} bytes)"
    data = read(m, content, a, b)
    if quote:
        want = quote.get("sha256")
        if want and fingerprint(data)["sha256"] != want:
            return None, "the bytes at this range don't match the locator's fingerprint"
        if "hex" in quote and quote["hex"] != data.hex():
            return None, "the bytes at this range don't match the locator's fingerprint"
    return data, None


def hexdump(data: bytes, base: int = 0, width: int = 16) -> list[dict]:
    """Rows of a hex dump: ``offset`` (of the row's first byte in the
    original), ``hex`` and ``ascii``. Rows start at multiples of ``width``,
    so a range that starts mid-row is padded on the left."""
    rows = []
    first = base - base % width
    for row in range(first, base + len(data), width):
        cells, chars = [], []
        for off in range(row, row + width):
            i = off - base
            if 0 <= i < len(data):
                cells.append(f"{data[i]:02x}")
                chars.append(chr(data[i]) if 0x20 <= data[i] < 0x7f else ".")
            else:
                cells.append("  ")
                chars.append(" ")
        rows.append({"offset": row, "hex": " ".join(cells[:8]) + "  " + " ".join(cells[8:]), "ascii": "".join(chars)})
    return rows


def hexdump_text(data: bytes, base: int = 0) -> str:
    return "\n".join(f"{r['offset']:08x}  {r['hex']}  |{r['ascii']}|" for r in hexdump(data, base))


def entropy(data: bytes) -> float:
    """Bits per byte (0 to 8). Near 8: compressed or encrypted."""
    if not data:
        return 0.0
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in Counter(data).values())


def signature(head: bytes) -> list[str]:
    return [what for off, magic, what in SIGNATURES if head[off:off + len(magic)] == magic]


_UTF8_RUN = rb"(?:[\x20-\x7e\t]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3})+"


def strings(data: bytes, min_len: int = 6, base: int = 0, limit: int | None = None):
    """Runs of printable text in binary data, as ``(start, end, text,
    encoding)`` with byte offsets in the original: ASCII or UTF-8 runs, and
    UTF-16LE runs (every other byte zero), the way Windows files store text."""
    found = []
    for x in re.finditer(_UTF8_RUN, data):
        try:
            t = x.group().decode("utf-8")
        except UnicodeDecodeError:           # an overlong or surrogate form: not text after all
            continue
        if len(t) >= min_len:
            found.append((base + x.start(), base + x.end(), t, "ascii" if t.isascii() else "utf-8"))
    found += [(base + x.start(), base + x.end(), x.group().decode("utf-16-le"), "utf-16le")
              for x in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len, data)]
    found.sort()
    return found[:limit] if limit else found


def info(m, doc: str) -> dict:
    """What the original is, as far as its bytes say."""
    from . import structured
    doc = locators.parse({"doc": doc}, m.objects)["doc"]
    content = content_of(m, doc)
    n = size(m, content)
    sample = read(m, content, 0, SAMPLE)
    meta = m.objects.get_object(m.state.metadata[doc]) if doc in m.state.metadata else {}
    layers = m.state.layers.get(content, [])
    out = {"doc": doc, "name": meta.get("name"), "kind": meta.get("kind"), "size": n, "content": content,
           "path": str(m.objects.path(content)), "signature": signature(sample[:4096]),
           "entropy": round(entropy(sample), 3), "sampled": min(n, SAMPLE),
           "layers": [{"layer": l.id, "view": l.view} for l in layers]}
    if sample:
        printable = sum(1 for b in sample if 0x20 <= b < 0x7f or b in (9, 10, 13))
        out["printable"] = round(printable / len(sample), 3)
        out["nul"] = round(sample.count(0) / len(sample), 3)
    try:
        text = sample.decode("utf-8-sig")
        out["utf8"] = True
    except UnicodeDecodeError as e:
        text = None
        out["utf8"] = n > SAMPLE and e.start > len(sample) - 4      # cut mid-character at the sample's end
    if text is not None and n <= SAMPLE:
        fmt = structured.sniff(text)
        if fmt:
            out["format"] = fmt
    notes = []
    if out["entropy"] > 7.5 and not out["signature"]:
        notes.append("near-random bytes: likely compressed or encrypted; decoding may need a key or a decompressor")
    if out.get("nul", 0) > 0.2 and out.get("printable", 0) > 0.3:
        notes.append("many zero bytes between printable ones: maybe UTF-16 text or fixed-width records")
    if not layers:
        notes.append("no text layer: nothing extracted it, so search can't reach it; see raw strings, raw hex and interp")
    if notes:
        out["notes"] = notes
    return out
