"""Received productions: a volume of load files, page images, natives and
text that another party produced, ingested one document per load-file
record, with its Bates numbers and control number as names (RECEIVED.md).

:func:`check` reads a volume and says what it would do and what's wrong;
:func:`ingest` does it. :func:`resolve_bates` and :func:`resolve_control`
find documents by the numbers they were produced under.
"""

import csv
import hashlib
import io
import os
import re
from datetime import datetime
from pathlib import Path

from .matter import Matter

# -- the load files' columns -----------------------------------------------------------------------

# Each field and the column names it goes by, compared without case, spaces or punctuation.
COLUMNS = {
    "begin": ["begbates", "begdoc", "begdocno", "begdocnumber", "begno", "prodbeg", "prodbegbates", "prodbegno",
              "beginbates", "beginningbates", "batesbegin", "batesbeg", "begbatesnumber", "startbates", "firstbates",
              "begprodbates", "batesnumber", "bates", "beginbatesnumber", "prodbegdoc"],
    "end": ["endbates", "enddoc", "enddocno", "enddocnumber", "endno", "prodend", "prodendbates", "prodendno",
            "endingbates", "batesend", "endbatesnumber", "lastbates", "endprodbates", "endbatesnumber", "prodenddoc"],
    "begattach": ["begattach", "begattachment", "attachbeg", "begfamily", "familybeg", "begattachbates",
                  "begbatesattach", "prodbegattach", "begattachno", "beginattach", "parentbates", "parentbegbates"],
    "endattach": ["endattach", "endattachment", "attachend", "endfamily", "familyend", "endattachbates",
                  "endbatesattach", "prodendattach", "endattachno"],
    "control": ["docid", "controlnumber", "controlno", "ctrlno", "control", "documentid", "controlid"],
    "custodian": ["custodian", "custodians", "allcustodians", "custodianname", "primarycustodian"],
    "native": ["nativepath", "nativelink", "native", "nativefile", "nativefilepath", "filepath", "nativelocation",
               "nativefilelink", "link"],
    "text": ["textpath", "textlink", "text", "extractedtext", "textfile", "textfilepath", "ocrpath", "fulltext",
             "textlocation", "fulltextpath", "extractedtextpath"],
    "name": ["filename", "originalfilename", "docname", "nativefilename", "filenameoriginal", "file"],
    "sent": ["datesent", "sentdate", "emaildatesent", "datetimesent", "sentdatetime", "sent", "emailsentdate"],
    "sent_time": ["timesent", "senttime", "emailtimesent"],
    "received": ["datereceived", "datercvd", "receiveddate", "emaildatereceived", "datetimereceived", "received"],
    "received_time": ["timereceived", "timercvd", "receivedtime"],
    "created": ["datecreated", "createddate", "createdate", "datecreate", "datetimecreated"],
    "created_time": ["timecreated", "createdtime"],
    "modified": ["datelastmod", "datemodified", "lastmodified", "modifieddate", "datelastmodified", "lastmoddate",
                 "datetimemodified", "datemod"],
    "modified_time": ["timelastmod", "timemodified", "lastmodtime"],
    "date": ["docdate", "date", "documentdate", "sortdate", "masterdate"],
    "from": ["from", "emailfrom", "sender", "emailsender"],
    "to": ["to", "emailto", "recipients", "recipient"],
    "cc": ["cc", "emailcc"],
    "bcc": ["bcc", "emailbcc"],
    "subject": ["subject", "emailsubject"],
    "title": ["title", "doctitle", "documenttitle"],
    "author": ["author", "docauthor", "createdby"],
    "md5": ["md5hash", "md5", "md5sum", "hashmd5", "md5value"],
    "sha1": ["sha1hash", "sha1", "hashsha1", "sha1value"],
}
SEP, QUOTE, NEWLINE = "\x14", "þ", "®"


def norm(col: str) -> str:
    return re.sub(r"[^a-z0-9]", "", col.lower())


def map_columns(columns: list[str], overrides: dict | None = None) -> dict:
    """field -> column. The first column a field's names match, in the
    order of its names; ``overrides`` (field -> column) first."""
    by_norm = {}
    for c in columns:
        by_norm.setdefault(norm(c), c)
    out, taken = {}, set()
    for field, col in (overrides or {}).items():
        if field not in COLUMNS:
            raise ValueError(f"no field {field!r}; the fields are {', '.join(COLUMNS)}")
        found = col if col in columns else by_norm.get(norm(col))
        if found is None:
            raise ValueError(f"--map {field}={col}: the .dat has no column {col!r}")
        out[field] = found
        taken.add(found)
    for field, names in COLUMNS.items():
        if field in out:
            continue
        for n in names:
            c = by_norm.get(n)
            if c is not None and c not in taken:
                out[field] = c
                taken.add(c)
                break
    return out


# -- reading load files --------------------------------------------------------------------------


def read_text(path) -> tuple[str, str]:
    """A load or text file's text and the encoding it was read as: its
    byte-order mark's, else UTF-8, else Windows-1252."""
    data = Path(path).read_bytes()
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(enc), enc.removesuffix("-sig")
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def _concordance(text: str, quote: str, sep: str) -> list[list[str]]:
    """Records of a delimited file whose values may hold the separator or
    newlines inside quotes."""
    rows, row, val, inq, i, n = [], [], [], False, 0, len(text)
    while i < n:
        ch = text[i]
        if quote and ch == quote:
            inq = not inq
        elif inq:
            val.append(ch)
        elif ch == sep:
            row.append("".join(val)); val = []
        elif ch in "\r\n":
            if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            row.append("".join(val)); val = []
            if any(x.strip() for x in row):
                rows.append(row)
            row = []
        else:
            val.append(ch)
        i += 1
    row.append("".join(val))
    if any(x.strip() for x in row):
        rows.append(row)
    return rows


def parse_dat(path) -> dict:
    """``{columns, rows: [dict], dialect, encoding}``."""
    text, enc = read_text(path)
    first = text.split("\n", 1)[0]
    if first.startswith((QUOTE, "^")) or SEP in first:
        quote = first[0] if first[0] in (QUOTE, "^", '"') else ""
        rest = first[1:] if quote else first
        sep = SEP
        if quote and quote in rest:
            after = rest[rest.index(quote) + 1:rest.index(quote) + 2]
            sep = after or SEP
        rows = _concordance(text, quote, sep)
        dialect = "concordance"
        rows = [[v.replace(NEWLINE, "\n") for v in r] for r in rows]
    else:
        try:
            d = csv.Sniffer().sniff(first, delimiters=",\t|;")
        except csv.Error:
            d = csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), d) if any(x.strip() for x in r)]
        dialect = "tab" if getattr(d, "delimiter", ",") == "\t" else "csv"
    if not rows:
        raise ValueError(f"{path}: no records")
    columns = [c.strip() for c in rows[0]]
    out = []
    for r in rows[1:]:
        r = r + [""] * (len(columns) - len(r))
        out.append({c: v.strip() for c, v in zip(columns, r)})
    return {"columns": columns, "rows": out, "dialect": dialect, "encoding": enc}


def parse_opt(path) -> list[dict]:
    """Opticon: per page, ``{key, path, first}``, in order."""
    text, _ = read_text(path)
    pages = []
    for line in text.splitlines():
        if not line.strip():
            continue
        f = [x.strip() for x in line.split(",")]
        f += [""] * (7 - len(f))
        pages.append({"key": f[0], "path": f[2], "first": f[3].upper() == "Y", "count": f[6]})
    return pages


# -- finding the files a load file names -----------------------------------------------------------


class Volume:
    """A volume's files, found from the paths load files give them: with
    Windows separators, a leading volume name or drive, and another case."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.index, self.by_name = {}, {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                self.index.setdefault(p.relative_to(self.root).as_posix().lower(), p)
                self.by_name.setdefault(p.name.lower(), []).append(p)

    def find(self, given: str) -> Path | None:
        if not given:
            return None
        s = given.strip().replace("\\", "/")
        s = re.sub(r"^[A-Za-z]:", "", s)
        parts = [x for x in s.split("/") if x not in ("", ".")]
        exact = self.root.joinpath(*parts) if parts else None
        if exact is not None and exact.is_file():
            return exact
        for k in range(len(parts)):            # a leading volume name, or more, that the files aren't under
            hit = self.index.get("/".join(parts[k:]).lower())
            if hit:
                return hit
        return None


def find_load_files(root: Path) -> tuple[Path | None, Path | None]:
    pick = lambda ext: sorted((p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ext),
                              key=lambda p: (len(p.parts), str(p)))
    dats, opts = pick(".dat") or pick(".csv"), pick(".opt")
    return (dats[0] if dats else None), (opts[0] if opts else None)


# -- values ----------------------------------------------------------------------------------------

BATES = re.compile(r"^(.*?)(\d+)$")


def bates_parts(s: str):
    m = BATES.match(s.strip())
    return (m.group(1), int(m.group(2)), len(m.group(2))) if m else None


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


_DATE_FORMATS = ["%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"]
_TIME_FORMATS = ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%I:%M:%S%p", "%I:%M%p"]


def parse_date(s: str, time: str = "", day_first: bool = False) -> str | None:
    """An ISO date (with the time, if there is one) from a load file's date
    and optional time column; None if it isn't a date."""
    s, time = (s or "").strip(), (time or "").strip()
    if not s:
        return None
    if not time:
        m = re.match(r"^(\S+)[ T](.+)$", s)
        if m and re.search(r"\d:\d", m.group(2)):
            s, time = m.group(1), m.group(2)
    time = re.sub(r"\s*(Z|[+-]\d\d:?\d\d|[A-Z]{3})$", "", time)       # a zone: dropped, as it can't be trusted
    slash = ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y"] if day_first else ["%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"]
    day = None
    for f in _DATE_FORMATS + slash:
        try:
            day = datetime.strptime(s, f)
            break
        except ValueError:
            continue
    if day is None:
        return None
    if time:
        for f in _TIME_FORMATS:
            try:
                t = datetime.strptime(time.upper().replace(".", ""), f)
                return day.replace(hour=t.hour, minute=t.minute, second=t.second).isoformat()
            except ValueError:
                continue
    return day.date().isoformat()


def people(s: str) -> list[str]:
    return [x.strip() for x in re.split(r";\s*|\n", s or "") if x.strip()]


# -- reading a volume ------------------------------------------------------------------------------


def _assemble(paths: list[Path]) -> bytes:
    """Page images (TIFF, JPEG, PNG or PDF) as one PDF, the same bytes every
    time for the same images."""
    import pymupdf
    out = pymupdf.open()
    for p in paths:
        src = pymupdf.open(p)
        out.insert_pdf(src if src.is_pdf else pymupdf.open("pdf", src.convert_to_pdf()))
    out.set_metadata({})
    return out.tobytes(garbage=3, deflate=True, no_new_id=True)


def read_volume(root, dat=None, opt=None, overrides=None, day_first=False) -> dict:
    """Everything :func:`check` reports and :func:`ingest` needs: the load
    files read, columns mapped, and per record what it is and where its
    files are. Nothing is ingested."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"{root} is not a folder")
    found_dat, found_opt = find_load_files(root)
    dat = Path(dat) if dat else found_dat
    opt = Path(opt) if opt else found_opt
    if dat is None:
        raise ValueError(f"no .dat load file in {root} (name one with --dat)")
    vol = Volume(root)
    d = parse_dat(dat)
    cols = map_columns(d["columns"], overrides)
    if "begin" not in cols:
        raise ValueError(f"{dat.name}: no column looks like the begin Bates number (columns: "
                         f"{', '.join(d['columns'])}); name it with --map begin=COLUMN")
    problems = []
    pages = parse_opt(opt) if opt else []
    pos = {}
    for i, pg in enumerate(pages):
        pos.setdefault(pg["key"], i)
    breaks = any(pg["first"] for pg in pages)
    get = lambda row, f: row.get(cols[f], "") if f in cols else ""

    records, seen = [], {}
    for n, row in enumerate(d["rows"], 2):
        begin = get(row, "begin")
        if not begin:
            problems.append({"line": n, "problem": "no begin Bates number"})
            continue
        if begin in seen:
            problems.append({"bates": begin, "problem": f"begins two records (lines {seen[begin]} and {n}): the second is skipped"})
            continue
        seen[begin] = n
        end = get(row, "end") or begin
        r = {"begin": begin, "end": end, "line": n, "row": row,
             "begattach": get(row, "begattach"), "endattach": get(row, "endattach"),
             "control": get(row, "control") or begin}
        # its page images: from its first page to the next document break (or its end number)
        imgs, keys = [], []
        if begin in pos:
            i = pos[begin]
            while i < len(pages) and (i == pos[begin] or (not pages[i]["first"] if breaks else keys[-1] != end)):
                keys.append(pages[i]["key"])
                imgs.append(pages[i]["path"])
                i += 1
        r["keys"] = keys
        r["images"], missing = [], []
        for g in dict.fromkeys(imgs):                 # a multi-page image named on each of its pages: once
            p = vol.find(g)
            (r["images"].append(p) if p else missing.append(g))
        native, text = get(row, "native"), get(row, "text")
        r["native"] = vol.find(native) if native else None
        r["text"] = vol.find(text) if text else None
        if text and not r["text"]:
            missing.append(text)
        if not text:                                   # named for its number, if the .dat doesn't say
            r["text"] = next((vol.by_name[f"{x.lower()}.txt"][0] for x in (begin, r["control"]) if f"{x.lower()}.txt" in vol.by_name), None)
        if native and not r["native"]:
            missing.append(native)
        if missing:
            problems.append({"bates": begin, "problem": "files not in the volume: " + ", ".join(missing)})
        # the native if there is one; else the page images, all of them; else the text
        whole = r["images"] and len(r["images"]) == len(dict.fromkeys(imgs))
        r["as"] = "native" if r["native"] else "images" if whole else "text" if r["text"] else None
        if r["as"] is None:
            problems.append({"bates": begin, "problem": "no native, page images or text to ingest: skipped"})
        records.append(r)

    # families: an attachment's begattach is its parent's begin
    by_begin = {r["begin"]: r for r in records}
    for r in records:
        ba = r["begattach"]
        r["parent"] = None
        if ba and ba != r["begin"]:
            if ba in by_begin and by_begin[ba]["begattach"] in ("", ba):
                r["parent"] = ba
                by_begin[ba].setdefault("attachments", []).append(r["begin"])
            elif ba not in by_begin:
                problems.append({"bates": r["begin"], "problem": f"its family begins at {ba}, which isn't in this volume: "
                                                                 "ingested without a parent"})
    if pages:
        firsts = {pg["key"] for pg in pages if pg["first"]} if breaks else set()
        orphans = sorted(firsts - set(by_begin))
        if orphans:
            problems.append({"problem": f"{len(orphans)} documents in {opt.name} that no .dat record begins: "
                                        + ", ".join(orphans[:10]) + ("…" if len(orphans) > 10 else "")})
    return {"root": root, "dat": dat, "opt": opt, "dialect": d["dialect"], "encoding": d["encoding"],
            "columns": cols, "unmapped": [c for c in d["columns"] if c not in cols.values()],
            "records": records, "problems": problems, "day_first": day_first}


def summary(v: dict) -> dict:
    rs = v["records"]
    count = lambda a: sum(1 for r in rs if r["as"] == a)
    return {"volume": v["root"].name, "dat": str(v["dat"]), "opt": str(v["opt"]) if v["opt"] else None,
            "dialect": v["dialect"], "encoding": v["encoding"], "columns": v["columns"], "unmapped": v["unmapped"],
            "records": len(rs), "as_native": count("native"), "as_images": count("images"), "as_text": count("text"),
            "skipped": count(None), "attachments": sum(1 for r in rs if r["parent"]),
            "with_text": sum(1 for r in rs if r["text"]), "problems": v["problems"]}


def check(root, m: Matter | None = None, name: str | None = None, replaces: list[str] | None = None, **kw) -> dict:
    """What :func:`ingest` would do. With the matter: which documents
    already received it would supersede (``replaces``), or overlap."""
    v = read_volume(root, **kw)
    out = summary(v)
    if m is not None:
        out.update(_replacing(m, v, name or v["root"].name, replaces, None))
    return out


# -- ingesting -------------------------------------------------------------------------------------


def _derived(cols: dict, row: dict, day_first: bool) -> dict:
    """What a record says about the document, as metadata keys: the people,
    subject, title, author and the date with its source. Only keys with a
    value."""
    get = lambda f: row.get(cols[f], "") if f in cols else ""
    meta = {"from": people(get("from")), "to": people(get("to")), "cc": people(get("cc")), "bcc": people(get("bcc")),
            "subject": get("subject") or None, "title": get("title") or None, "author": get("author") or None}
    for src in ("sent", "received", "created", "modified", "date"):
        d = parse_date(get(src), get(src + "_time"), day_first)
        if d:
            meta["date"], meta["date_source"] = d, {"date": "metadata"}.get(src, src)
            break
    if get("name"):
        meta["name"] = os.path.basename(get("name").replace("\\", "/"))
    return {k: v for k, v in meta.items() if v not in (None, "", [])}


def _metadata(v: dict, r: dict, party: str, volume: str) -> dict:
    cols, row = v["columns"], r["row"]
    meta = _derived(cols, row, v["day_first"])
    meta.pop("name", None)                      # the name is ingest_file's to set (extra["name"])
    pages = None
    if r["as"] == "images":
        pages = r["keys"]
    meta["produced"] = {"party": party, "volume": volume, "begin": r["begin"], "end": r["end"],
                        "begattach": r["begattach"] or None, "endattach": r["endattach"] or None,
                        "control": r["control"], "pages": pages, "as": r["as"],
                        "custodian": row.get(cols["custodian"], "") if "custodian" in cols else None,
                        "fields": row, "columns": cols, "day_first": v["day_first"]}
    meta["produced"]["custodian"] = meta["produced"]["custodian"] or None
    return meta


def _hash_problem(v, r) -> str | None:
    cols, row = v["columns"], r["row"]
    for f, algo in (("md5", "md5"), ("sha1", "sha1")):
        want = row.get(cols[f], "").strip().lower() if f in cols else ""
        if want and r["native"]:
            h = hashlib.new(algo)
            with open(r["native"], "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != want:
                return f"the native's {algo.upper()} is {h.hexdigest()}, not {want} as the .dat says (ingested as received)"
    return None


def ingest(m: Matter, root, party: str, name: str | None = None, report=None, replaces: list[str] | None = None,
           person: str | None = None, **kw) -> dict:
    """Ingest a volume: one document per record, parents before their
    attachments. Returns :func:`summary` plus ``docs`` (Bates -> document)."""
    from .ingest import _ingest, ingest_file
    if not party or not party.strip():
        raise ValueError("--party: who produced this volume")
    if replaces and not person:
        raise ValueError("carrying the old documents' tags and notes over is a person's: say who (--user)")
    v = read_volume(root, **kw)
    volume = name or v["root"].name
    cols = v["columns"]
    docs = {}
    order = [r for r in v["records"] if not r["parent"]] + [r for r in v["records"] if r["parent"]]
    for r in order:
        if r["as"] is None:
            continue
        if r["parent"] and r["parent"] not in docs:
            v["problems"].append({"bates": r["begin"], "problem": f"its parent {r['parent']} wasn't ingested: ingested without one"})
        identity = {"source": {"collection": volume, "path": r["begin"]}}
        cust = r["row"].get(cols["custodian"], "") if "custodian" in cols else ""
        if cust:
            identity["custodian"] = "cust:" + slug(people(cust)[0] if people(cust) else cust)
        if r["parent"] and r["parent"] in docs:
            identity["parent"] = docs[r["parent"]]
        hp = _hash_problem(v, r)
        if hp:
            v["problems"].append({"bates": r["begin"], "problem": hp})
        extra = {"metadata": _metadata(v, r, party, volume),
                 "name": _derived(cols, r["row"], v["day_first"]).get("name")}
        if r["text"] and r["as"] != "text":
            extra["text"] = read_text(r["text"])[0]
        if r.get("attachments"):
            extra["children"] = False
        try:
            if r["as"] == "native":
                doc = ingest_file(m, r["native"], r["native"].name, identity, report, extra=extra)
            elif r["as"] == "images":
                data = _assemble(r["images"])
                if len(r["keys"]) != _page_count(data):
                    extra["metadata"]["produced"]["pages"] = _numbered(r["begin"], _page_count(data))
                doc = _ingest(m, m.objects.put_bytes(data), f"{r['begin']}.pdf", identity, report, data, extra=extra)
            else:
                doc = ingest_file(m, r["text"], r["text"].name, identity, report, extra=extra)
        except Exception as e:
            v["problems"].append({"bates": r["begin"], "problem": f"not ingested: {e}"})
            continue
        docs[r["begin"]] = doc
    _forget_cache()
    out = {**summary(v), "volume": volume, "party": party, "ingested": len(docs), "docs": docs}
    out.update(_replacing(m, v, volume, replaces, docs, person))
    _forget_cache()
    return out


def _replacing(m: Matter, v: dict, volume: str, replaces: list[str] | None, docs: dict | None,
               person: str | None = None) -> dict:
    """A replacement volume's documents supersede those in ``replaces`` that
    begin at the same Bates numbers, and the old ones' tags and notes are
    carried over to them, marked (annotations.carry_over, as ``person``).
    Without ``replaces``, numbers another volume already has are only
    reported. ``docs`` None: say what would be done (``--check``)."""
    begins = {r["begin"] for r in v["records"] if r["as"]}
    others: dict[str, list[dict]] = {}
    for p in produced(m):
        if p["volume"] != volume and p["begin"] in begins:
            others.setdefault(p["begin"], []).append(p)
    if not replaces:
        if others:
            vols = sorted({p["volume"] for ps in others.values() for p in ps})
            v["problems"].append({"problem": f"{len(others)} documents begin at Bates numbers already received in "
                                             f"{', '.join(vols)}: both are kept. If this volume replaces that one, "
                                             f"ingest it with --replaces {vols[0]}"})
        return {"problems": v["problems"]}
    known = {p["volume"] for p in produced(m)}
    for r in replaces:
        if r not in known and r != volume:
            raise ValueError(f"--replaces {r}: no received volume has that name (the volumes are "
                             f"{', '.join(sorted(known)) or 'none'})")
    plan = [(b, p) for b, ps in sorted(others.items()) for p in ps if p["volume"] in replaces]
    old, out = produced(m), []
    for b, p in plan:
        new = docs.get(b) if docs is not None else None
        if docs is not None and (new is None or new == p["doc"]):
            continue
        item = {"bates": b, "old": p["doc"], "old_volume": p["volume"]}
        if new:
            m.state.apply(m.logs["store"].append(op="supersede", old=p["doc"], new=new,
                                                 reason=f"replaced by {b} in received volume {volume}"))
            item["new"] = new
        out.append(item)
    left = sorted({p["begin"] for p in old if p["volume"] in replaces} - {i["bates"] for i in out})
    res = {"superseded" if docs is not None else "would_supersede": out, "not_replaced": left, "problems": v["problems"]}
    pairs = [(i["old"], i["new"]) for i in out if i.get("new")]
    if pairs:
        from . import annotations
        if not person:
            raise ValueError("carrying the old documents' tags and notes over is a person's: say who (--user)")
        c = annotations.carry_over(m, pairs, person, f"received volume {volume} replaced {', '.join(replaces)}")
        res["annotations_carried"], res["annotations_not_carried"] = c["carried"], c["not_carried"]
    return res


# -- overlays --------------------------------------------------------------------------------------

# Fields an overlay can't change: they made the document what it is (its
# files, its family, its identity), so a change is reported, not applied.
_FIXED = ("begin", "begattach", "endattach", "native", "text", "custodian")


def overlay(m: Matter, dat, volume: str | None = None, overrides=None, day_first: bool = False,
            check: bool = False) -> dict:
    """Apply an overlay: a later load file that corrects or adds fields to
    received documents already in, matched by begin Bates number (else
    control number). Each changed document gets new metadata: the record's
    fields merged (a blank value leaves the old one), what the load file
    filled recomputed from them, and the change kept in
    ``produced.overlays``. What the extraction found stays. ``check``:
    report, change nothing. Applying the same overlay again changes nothing."""
    from .matter import now
    dat = Path(dat)
    d = parse_dat(dat)
    cols = map_columns(d["columns"], overrides)
    key = "begin" if "begin" in cols else "control" if "control" in cols else None
    if key is None:
        raise ValueError(f"{dat.name}: no column looks like a begin Bates number or control number (columns: "
                         f"{', '.join(d['columns'])}); name one with --map begin=COLUMN or --map control=COLUMN")
    targets = [p for p in produced(m) if volume is None or p["volume"] == volume]
    if volume is not None and not targets:
        raise ValueError(f"--volume {volume}: no received volume has that name")
    by = {}
    for p in targets:
        by.setdefault(p["begin"] if key == "begin" else p.get("control"), []).append(p)
    problems, changed, unchanged = [], [], 0
    seen = set()
    for n, row in enumerate(d["rows"], 2):
        k = row.get(cols[key], "").strip()
        if not k:
            problems.append({"line": n, "problem": f"no {key} number"})
            continue
        if k in seen:
            problems.append({"bates": k, "problem": f"in the overlay twice (line {n} skipped)"})
            continue
        seen.add(k)
        hits = by.get(k, [])
        if not hits:
            problems.append({"bates": k, "problem": "no received document " + ("begins at" if key == "begin" else
                                                                                 "has control number") + " " + k})
            continue
        if len({p["volume"] for p in hits}) > 1:
            problems.append({"bates": k, "problem": "received in " + ", ".join(sorted({p["volume"] for p in hits}))
                                                    + ": name the volume the overlay is for with --volume"})
            continue
        for p in hits:
            r = _overlay_one(m, p, cols, row, day_first, dat.name, now())
            problems += r["problems"]
            if r["changed"]:
                changed.append({"bates": p["begin"], "doc": p["doc"], "volume": p["volume"], "changed": r["changed"]})
                if not check:
                    m.state.apply(m.logs["store"].append(op="set-metadata", doc=p["doc"],
                                                         metadata=m.objects.put_object(r["meta"])))
            else:
                unchanged += 1
    _forget_cache()
    return {"overlay": str(dat), "dialect": d["dialect"], "encoding": d["encoding"], "matched_by": key,
            "columns": cols, "unmapped": [c for c in d["columns"] if c not in cols.values()],
            "records": len(d["rows"]), "changed": len(changed), "unchanged": unchanged,
            "documents": changed, "problems": problems, "applied": not check}


def _overlay_one(m: Matter, p: dict, cols: dict, row: dict, day_first: bool, source: str, at: str) -> dict:
    meta = dict(m.objects.get_object(m.state.metadata[p["doc"]]))
    prod = dict(meta["produced"])
    old_fields = prod.get("fields") or {}
    old_cols = prod.get("columns") or map_columns(list(old_fields))
    old_df = prod.get("day_first", day_first)
    by_norm = {norm(c): c for c in old_fields}
    fields, new_cols, changes, problems = dict(old_fields), dict(old_cols), {}, []
    field_of = {c: f for f, c in cols.items()}
    for c, val in row.items():
        f = field_of.get(c)
        target = old_cols.get(f) if f in old_cols else by_norm.get(norm(c), c)
        if f and f not in new_cols:
            new_cols[f] = target
        if val == "" or fields.get(target, "") == val:
            continue                                # blank: no change
        if f in _FIXED:
            if f != "begin":
                problems.append({"bates": p["begin"], "problem": f"{c}: {fields.get(target, '')!r} -> {val!r} "
                                                                 "not applied: an overlay can't change a document's "
                                                                 "files, family or custodian"})
            continue
        changes[target] = [fields.get(target), val]
        fields[target] = val
    if not changes:
        return {"changed": None, "problems": problems}
    before, after = _derived(old_cols, old_fields, old_df), _derived(new_cols, fields, old_df)
    for k in ("from", "to", "cc", "bcc", "subject", "title", "author", "name"):
        if meta.get(k) in (None, "", []) or meta.get(k) == before.get(k):       # the load file's, not the extraction's
            if k in after:
                meta[k] = after[k]
            elif k != "name":
                meta.pop(k, None)
    ours = meta.get("date") and (meta.get("date"), meta.get("date_source")) != (before.get("date"), before.get("date_source"))
    if not ours:
        if "date" in after:
            meta["date"], meta["date_source"] = after["date"], after["date_source"]
        else:
            meta.pop("date", None), meta.pop("date_source", None)
    get = lambda f: fields.get(new_cols[f], "") if f in new_cols else ""
    if get("end"):
        prod["end"] = get("end")
    if get("control"):
        prod["control"] = get("control")
    prod["fields"], prod["columns"] = fields, new_cols
    prod["overlays"] = list(prod.get("overlays") or []) + [{"dat": source, "at": at, "changed": changes}]
    meta["produced"] = prod
    return {"changed": changes, "meta": meta, "problems": problems}


def _page_count(pdf: bytes) -> int:
    import pymupdf
    return pymupdf.open("pdf", pdf).page_count


def _numbered(begin: str, n: int) -> list[str]:
    prefix, first, digits = bates_parts(begin)
    return [f"{prefix}{first + i:0{digits}d}" for i in range(n)]


# -- names: Bates and control numbers ------------------------------------------------------------

_cache: dict = {}


def _forget_cache():
    _cache.clear()


def produced(m: Matter) -> list[dict]:
    """Every live received document's ``produced`` record, with its ``doc``."""
    state = m.state
    key = (str(m.root), hash(tuple(sorted(state.metadata.items()))), len(state.withdrawn))
    if _cache.get("key") == key:
        return _cache["records"]
    out = []
    for d in state.live_documents():
        mid = state.metadata.get(d)
        if not mid:
            continue
        p = m.objects.get_object(mid).get("produced")
        if p:
            out.append({**p, "doc": d})
    _cache.update(key=key, records=out)
    return out


def resolve_bates(m: Matter, prefix: str, lo: int, hi: int) -> list[dict]:
    """Received documents a Bates range touches: ``{loc, bates, production,
    party}``, a page selection where the document's pages have numbers."""
    out = []
    for p in produced(m):
        b, e = bates_parts(p["begin"]), bates_parts(p["end"] or p["begin"])
        if not b or not e or b[0] != prefix or hi < b[1] or lo > e[1]:
            continue
        loc = {"doc": p["doc"]}
        first, last = max(lo, b[1]), min(hi, e[1])
        if p.get("pages"):                      # the pages those numbers are (a native has one number, no pages)
            nums = [bates_parts(k) for k in p["pages"]]
            idx = [i + 1 for i, x in enumerate(nums) if x and x[0] == prefix and first <= x[1] <= last]
            if idx:
                loc["select"] = {"page": idx[0] if idx[0] == idx[-1] else [idx[0], idx[-1]]}
        out.append({"loc": loc, "bates": [f"{prefix}{first:0{b[2]}d}", f"{prefix}{last:0{b[2]}d}"],
                    "production": p["volume"], "party": p["party"]})
    return out


def resolve_control(m: Matter, s: str) -> list[dict]:
    """``ctl:NUMBER``: the received documents with that control number (or
    begin Bates number, where the load file had no control numbers)."""
    from .locators import LocatorError
    n = s.removeprefix("ctl:").strip()
    hits = [p for p in produced(m) if p.get("control") == n] or [p for p in produced(m) if p["begin"] == n]
    if not hits:
        raise LocatorError(f"{s}: no received document has that control number")
    return [{"loc": {"doc": p["doc"]}, "control": n, "production": p["volume"], "party": p["party"]} for p in hits]


NAMES = ("bates:", "exh:", "ctl:")


def resolve_name(m: Matter, s: str) -> list[dict]:
    """A document named by a number it was given, not its ID: ``bates:``
    (ours or received), ``exh:`` (exhibit sets) or ``ctl:`` (received
    control numbers). Each hit has its ``loc``."""
    if s.startswith("bates:"):
        from .produce import resolve_bates
        return resolve_bates(m, s)
    if s.startswith("exh:"):
        from .exhibits import resolve_exhibit
        return resolve_exhibit(m, s)
    if s.startswith("ctl:"):
        return resolve_control(m, s)
    raise ValueError(f"not a number name: {s}")
