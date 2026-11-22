"""Received productions (docs/RECEIVED.md): load files in, Bates and control numbers out."""

import hashlib
from pathlib import Path
import json
import subprocess
import sys

import pytest
from PIL import Image

from adhocdocs import locators, received
from adhocdocs.matter import Matter
from adhocdocs.produce import bates_for, resolve_bates

SEP, Q = "\x14", "þ"
COLS = ["BEGBATES", "ENDBATES", "BEGATTACH", "ENDATTACH", "DOCID", "CUSTODIAN", "FILENAME", "DATESENT", "TIMESENT",
        "FROM", "TO", "SUBJECT", "NATIVEPATH", "TEXTPATH", "MD5HASH", "NOTES"]


def page(path, fmt="TIFF"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (170, 220), 255).save(path, fmt)


def dat_line(values):
    return SEP.join(Q + v + Q for v in values)


@pytest.fixture
def volume(tmp_path):
    """A volume as Windows tools make it: backslashes, upper-case names, a
    leading volume folder in the paths, a UTF-8 byte-order mark."""
    v = tmp_path / "ACME_VOL001"
    for n in (1, 2, 3, 4, 6):
        page(v / "images" / "0001" / f"ACME00000{n}.tif")
    page(v / "images" / "0001" / "ACME000005.png", "PNG")
    (v / "natives").mkdir()
    (v / "natives" / "ACME000006.csv").write_text("month,amount\nMarch,1200\n")
    (v / "natives" / "ACME000007.txt").write_text("A memo, produced natively.\n")
    (v / "text").mkdir()
    for n, t in ((1, "Re: the Dock 4 pallet\n\nIt never arrived."), (4, "Attachment: delivery log"),
                 (8, "Text only: the images went missing.")):
        (v / "text" / f"ACME00000{n}.txt").write_text(t, encoding="utf-8")
    md5 = hashlib.md5((v / "natives" / "ACME000006.csv").read_bytes()).hexdigest()
    rows = [COLS,
            ["ACME000001", "ACME000003", "ACME000001", "ACME000005", "CTL-1", "Smith, John", "Re pallet.msg",
             "03/04/2021", "10:32 AM", "John Smith <js@acme.com>", "Ann Lee <al@buyer.com>; Bo Ng <bo@buyer.com>",
             "Re: pallet", "", r"ACME_VOL001\TEXT\ACME000001.txt", "", "line one®line two"],
            ["ACME000004", "ACME000005", "ACME000001", "ACME000005", "CTL-2", "Smith, John", "log.pdf",
             "", "", "", "", "", "", r"ACME_VOL001\TEXT\ACME000004.txt", "", ""],
            ["ACME000006", "ACME000006", "", "", "CTL-3", "Jones, Ann", "totals.csv", "", "", "", "", "",
             r".\NATIVES\ACME000006.csv", "", md5, ""],
            ["ACME000007", "ACME000007", "", "", "CTL-4", "Jones, Ann", "memo.txt", "", "", "", "", "",
             r"NATIVES\ACME000007.txt", "", "0" * 32, ""],
            ["ACME000008", "ACME000008", "", "", "CTL-5", "Jones, Ann", "lost.pdf", "", "", "", "", "",
             "", r"TEXT\ACME000008.txt", "", ""]]
    (v / "DATA").mkdir()
    (v / "DATA" / "ACME_VOL001.dat").write_bytes(b"\xef\xbb\xbf" + "\r\n".join(dat_line(r) for r in rows).encode() + b"\r\n")
    opt = [("ACME000001", r"ACME_VOL001\IMAGES\0001\ACME000001.TIF", "Y", "3"),
           ("ACME000002", r"ACME_VOL001\IMAGES\0001\ACME000002.TIF", "", ""),
           ("ACME000003", r"ACME_VOL001\IMAGES\0001\ACME000003.TIF", "", ""),
           ("ACME000004", r"ACME_VOL001\IMAGES\0001\ACME000004.TIF", "Y", "2"),
           ("ACME000005", r"ACME_VOL001\IMAGES\0001\ACME000005.PNG", "", ""),
           ("ACME000006", r"ACME_VOL001\IMAGES\0001\ACME000006.TIF", "Y", "1"),     # the native's slip sheet
           ("ACME000008", r"ACME_VOL001\IMAGES\0001\ACME000008.TIF", "Y", "1")]     # not there
    (v / "DATA" / "ACME_VOL001.opt").write_text(
        "\r\n".join(f"{k},ACME_VOL001,{p},{y},,,{c}" for k, p, y, c in opt) + "\r\n")
    return v


@pytest.fixture
def m(tmp_path):
    return Matter.create(tmp_path / "m")


def meta(m, doc):
    m = Matter(m.root)
    return m.objects.get_object(m.state.metadata[doc])


def test_check_reports_without_ingesting(volume, m):
    r = received.check(volume)
    assert r["dialect"] == "concordance" and r["encoding"] == "utf-8"
    assert r["columns"]["begin"] == "BEGBATES" and r["columns"]["control"] == "DOCID" and r["columns"]["sent"] == "DATESENT"
    assert r["unmapped"] == ["NOTES"]
    assert (r["records"], r["as_images"], r["as_native"], r["as_text"], r["attachments"]) == (5, 2, 2, 1, 1)
    problems = {p.get("bates"): p["problem"] for p in r["problems"]}
    assert "MD5" not in json.dumps(problems)                     # hashes are checked when ingesting
    assert "ACME000008.TIF" in problems["ACME000008"]
    assert not Matter(m.root).state.documents


def test_ingest(volume, m):
    r = received.ingest(m, volume, "Acme Corp.")
    assert r["ingested"] == 5
    docs = r["docs"]
    m = Matter(m.root)
    # an email produced as images: three pages, dated as sent, the .dat's people
    email = meta(m, docs["ACME000001"])
    assert email["kind"] == "pdf" and email["name"] == "Re pallet.msg"
    assert (email["date"], email["date_source"]) == ("2021-03-04T10:32:00", "sent")
    assert email["to"] == ["Ann Lee <al@buyer.com>", "Bo Ng <bo@buyer.com>"]
    p = email["produced"]
    assert (p["party"], p["volume"], p["as"], p["control"]) == ("Acme Corp.", "ACME_VOL001", "images", "CTL-1")
    assert p["pages"] == ["ACME000001", "ACME000002", "ACME000003"] and p["fields"]["NOTES"] == "line one\nline two"
    import pymupdf
    assert pymupdf.open(m.objects.path(m.state.documents[docs["ACME000001"]])).page_count == 3
    # its text: the producer's, primary since the blank pages have none of their own
    primary = m.state.primary_layer(m.state.documents[docs["ACME000001"]])
    assert primary.view == "produced" and m.objects.get_text(primary.text).startswith("Re: the Dock 4 pallet")
    # the family: the attachment is the email's child (a TIFF and a PNG page)
    assert m.state.children[docs["ACME000001"]] == [docs["ACME000004"]]
    assert meta(m, docs["ACME000004"])["produced"]["pages"] == ["ACME000004", "ACME000005"]
    # natives are the originals, not their slip sheets
    assert m.objects.get_bytes(m.state.documents[docs["ACME000006"]]).startswith(b"month,amount")
    assert meta(m, docs["ACME000006"])["produced"]["as"] == "native" and meta(m, docs["ACME000006"])["produced"]["pages"] is None
    # identity: the volume and the begin number, and the custodian
    ident = m.objects.get_object(docs["ACME000006"])
    assert ident["source"] == {"collection": "ACME_VOL001", "path": "ACME000006"} and ident["custodian"] == "cust:jones-ann"
    assert meta(m, docs["ACME000008"])["produced"]["as"] == "text"
    assert any(p.get("bates") == "ACME000007" and "MD5" in p["problem"] for p in r["problems"])


def test_ingest_again_changes_nothing(volume, m):
    a = received.ingest(m, volume, "Acme Corp.")["docs"]
    n = len(list(Matter(m.root).logs["store"].read()))
    b = received.ingest(Matter(m.root), volume, "Acme Corp.")["docs"]
    assert a == b and len(list(Matter(m.root).logs["store"].read())) == n


def test_bates_and_control_numbers_name_documents(volume, m):
    docs = received.ingest(m, volume, "Acme Corp.")["docs"]
    m = Matter(m.root)
    (hit,) = resolve_bates(m, "bates:ACME000002")
    assert hit["loc"] == {"doc": docs["ACME000001"], "select": {"page": 2}} and hit["party"] == "Acme Corp."
    hits = resolve_bates(m, "bates:ACME000002-ACME000004")
    assert [h["loc"] for h in hits] == [{"doc": docs["ACME000001"], "select": {"page": [2, 3]}},
                                        {"doc": docs["ACME000004"], "select": {"page": 1}}]
    assert [h["bates"] for h in hits] == [["ACME000002", "ACME000003"], ["ACME000004", "ACME000004"]]
    assert resolve_bates(m, "bates:ACME000006")[0]["loc"] == {"doc": docs["ACME000006"]}        # a native: whole
    with pytest.raises(locators.LocatorError, match="not in any production"):
        resolve_bates(m, "bates:ACME000099")
    assert received.resolve_control(m, "ctl:CTL-3")[0]["loc"] == {"doc": docs["ACME000006"]}
    with pytest.raises(locators.LocatorError):
        received.resolve_control(m, "ctl:CTL-99")
    # pages cite received documents by the numbers they were produced under
    assert bates_for(m, docs["ACME000001"], [2, 3]) == ["ACME000002", "ACME000003"]
    assert bates_for(m, docs["ACME000006"], None) == ["ACME000006", "ACME000006"]
    # and the tools take them
    r = subprocess.run([sys.executable, "-m", "adhocdocs.cli", "text", "--matter", str(m.root), "ctl:CTL-1"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["status"] == "exact"


def test_a_csv_dat_with_other_names(tmp_path, m):
    """Another tool's export: CSV, Windows-1252, its own column names, day-first dates."""
    v = tmp_path / "VOL2"
    page(v / "img" / "X-0001.tif")
    page(v / "img" / "X-0002.tif")
    (v / "export.dat").write_bytes(("First Page No,Last Page No,Custodian,Date Created,Author\r\n"
                                    "X-0001,X-0002,Ré Müller,03/04/2021,Ré\r\n").encode("cp1252"))
    (v / "export.opt").write_text("X-0001,VOL2,img\\X-0001.tif,Y,,,2\r\nX-0002,VOL2,img\\X-0002.tif,,,,\r\n")
    with pytest.raises(ValueError, match="begin Bates"):
        received.check(v)
    r = received.check(v, overrides={"begin": "First Page No", "end": "Last Page No"}, day_first=True)
    assert r["dialect"] == "csv" and r["encoding"] == "cp1252" and r["as_images"] == 1
    docs = received.ingest(m, v, "Third Party LLC", overrides={"begin": "First Page No", "end": "Last Page No"},
                           day_first=True)["docs"]
    md = meta(m, docs["X-0001"])
    assert (md["date"], md["date_source"], md["author"]) == ("2021-04-03", "created", "Ré")
    assert Matter(m.root).objects.get_object(docs["X-0001"])["custodian"] == "cust:r-m-ller"
    # prefixes with dashes
    assert resolve_bates(Matter(m.root), "bates:X-0001-X-0002")[0]["loc"]["select"] == {"page": [1, 2]}


def test_the_same_images_make_the_same_pdf(tmp_path):
    page(tmp_path / "a.tif")
    page(tmp_path / "b.png", "PNG")
    assert received._assemble([tmp_path / "a.tif", tmp_path / "b.png"]) == received._assemble([tmp_path / "a.tif", tmp_path / "b.png"])


@pytest.mark.parametrize("s,t,day_first,want", [
    ("03/04/2021", "", False, "2021-03-04"),
    ("03/04/2021", "", True, "2021-04-03"),
    ("20210304", "", False, "2021-03-04"),
    ("2021-03-04 14:05:09", "", False, "2021-03-04T14:05:09"),
    ("3/4/2021 2:05 PM", "", False, "2021-03-04T14:05:00"),
    ("03/04/2021", "10:32 AM", False, "2021-03-04T10:32:00"),
    ("03/04/2021", "10:32:00 EST", False, "2021-03-04T10:32:00"),
    ("unknown", "", False, None),
])
def test_dates(s, t, day_first, want):
    assert received.parse_date(s, t, day_first) == want


def test_the_cli(volume, m):
    run = lambda *a: subprocess.run([sys.executable, "-m", "adhocdocs.cli", "adhoc", "--matter", str(m.root),
                                     "ingest-production", str(volume), *a], capture_output=True, text=True)
    r = run("--check")
    assert r.returncode == 0 and json.loads(r.stdout)["records"] == 5
    r = run()
    assert r.returncode != 0 and "--party" in r.stderr
    r = run("--party", "Acme Corp.", "--map", "nonsense=X")
    assert r.returncode != 0 and "no field" in r.stderr
    r = run("--party", "Acme Corp.")
    assert r.returncode == 0 and json.loads(r.stdout)["ingested"] == 5


def test_our_own_production_received_elsewhere(tmp_path):
    """What `produce` writes, served to another matter, comes in as it went out."""
    from test_produce import CORPUS, make_list
    from adhocdocs import produce
    from adhocdocs.ingest import ingest_paths
    ours = Matter.create(tmp_path / "ours")
    ingest_paths(ours, [CORPUS / "voss-r" / "2026-03-06_INV-2026-0347_reminder.eml",
                        CORPUS / "voss-r" / "demand-letter-2026-03-25.pdf"], "voss-r", "v")
    ours = Matter(ours.root)
    lst = make_list(ours, tmp_path, ["2026-03-06_INV-2026-0347_reminder.eml", "demand-letter-2026-03-25.pdf"])
    out = produce.produce(ours, lst, "vol001", "HFS")
    sent = [json.loads(x) for x in (Path(out["path"]) / "production.jsonl").read_text().splitlines()][1:]
    theirs = Matter.create(tmp_path / "theirs")
    r = received.ingest(theirs, out["path"], "Hale & Finch LLP")
    assert r["ingested"] == len(sent) and not r["problems"]
    theirs = Matter(theirs.root)
    for s in sent:
        d = r["docs"][s["begin"]]
        p = meta(theirs, d)["produced"]
        assert (p["begin"], p["end"]) == (s["begin"], s["end"])
        assert len(p["pages"]) == s["pages"]                      # one PDF per document, named on each page
        if s["begin"] != s["begattach"]:
            assert d in theirs.state.children[r["docs"][s["begattach"]]]
    last = sent[-1]
    assert resolve_bates(theirs, "bates:" + last["end"])[0]["loc"]["select"] == {"page": last["pages"]}


# -- overlays and replacement volumes -------------------------------------------------------------


def test_an_overlay_corrects_and_adds_fields(volume, m, tmp_path):
    docs = received.ingest(m, volume, "Acme Corp.")["docs"]
    m = Matter(m.root)
    ov = tmp_path / "ACME_VOL001_OVERLAY.dat"
    # other column names, a column the volume lacked, a blank that changes nothing, and a family it can't change
    rows = [["BegDoc", "Email Subject", "Date Sent", "Confidentiality", "To", "BegAttach"],
            ["ACME000001", "Re: the Dock 4 pallet", "03/05/2021", "Highly Confidential", "", ""],
            ["ACME000006", "", "", "Confidential", "", "ACME000001"],
            ["ACME009999", "", "", "Confidential", "", ""]]
    ov.write_text("\r\n".join(dat_line(r) for r in rows), encoding="utf-8")
    r = received.overlay(m, ov, check=True)
    assert (r["matched_by"], r["changed"], r["applied"]) == ("begin", 2, False)
    assert meta(m, docs["ACME000001"])["subject"] == "Re: pallet"               # --check changed nothing
    problems = {p.get("bates"): p["problem"] for p in r["problems"]}
    assert "no received document" in problems["ACME009999"] and "family" in problems["ACME000006"]

    r = received.overlay(Matter(m.root), ov)
    assert r["changed"] == 2
    m = Matter(m.root)
    e = meta(m, docs["ACME000001"])
    assert e["subject"] == "Re: the Dock 4 pallet"
    # the new date, with the old time column still joined to it
    assert (e["date"], e["date_source"]) == ("2021-03-05T10:32:00", "sent")
    assert e["to"] == ["Ann Lee <al@buyer.com>", "Bo Ng <bo@buyer.com>"]         # blank: left alone
    p = e["produced"]
    assert p["fields"]["SUBJECT"] == "Re: the Dock 4 pallet" and p["fields"]["Confidentiality"] == "Highly Confidential"
    assert p["overlays"][0]["dat"] == ov.name and p["overlays"][0]["changed"]["DATESENT"] == ["03/04/2021", "03/05/2021"]
    n = meta(m, docs["ACME000006"])
    assert n["produced"]["fields"]["Confidentiality"] == "Confidential" and not n["produced"].get("begattach")
    # the overlay's fields are searchable like the volume's: the document is still found by its numbers
    assert resolve_bates(m, "bates:ACME000002")[0]["loc"]["doc"] == docs["ACME000001"]
    # again: nothing to change
    n_events = len(list(m.logs["store"].read()))
    assert received.overlay(m, ov)["changed"] == 0 and len(list(Matter(m.root).logs["store"].read())) == n_events


def test_an_overlay_leaves_what_the_extraction_found(tmp_path, m):
    """A native's own metadata wins over the load file's, in an overlay as on ingest."""
    v = tmp_path / "VOL"
    (v / "NATIVES").mkdir(parents=True)
    eml = "From: Real Sender <real@x.com>\nTo: a@b.com\nSubject: The real subject\nDate: Tue, 9 Mar 2021 10:00:00 +0000\n\nHi.\n"
    (v / "NATIVES" / "X1.eml").write_text(eml)
    (v / "load.csv").write_text("BEGBATES,ENDBATES,NATIVEPATH,SUBJECT,AUTHOR\nX1,X1,NATIVES\\X1.eml,Wrong subject,\n")
    docs = received.ingest(m, v, "X")["docs"]
    ov = tmp_path / "ov.csv"
    ov.write_text("BEGBATES,SUBJECT,AUTHOR\nX1,Also wrong,Pat Doe\n")
    r = received.overlay(Matter(m.root), ov)
    e = meta(m, docs["X1"])
    assert r["changed"] == 1 and e["subject"] == "The real subject" and e["author"] == "Pat Doe"
    assert e["produced"]["fields"]["SUBJECT"] == "Also wrong"


def test_an_overlay_by_control_number_and_volume(volume, m, tmp_path):
    docs = received.ingest(m, volume, "Acme Corp.")["docs"]
    ov = tmp_path / "ov.csv"
    ov.write_text("DOCID,Privilege\nCTL-3,Redacted\n")
    with pytest.raises(ValueError, match="--volume"):
        received.overlay(Matter(m.root), ov, volume="NOPE")
    r = received.overlay(Matter(m.root), ov, volume="ACME_VOL001")
    assert r["matched_by"] == "control" and r["documents"][0]["doc"] == docs["ACME000006"]


def test_a_replacement_volume_supersedes(volume, m, tmp_path):
    import shutil
    old = received.ingest(m, volume, "Acme Corp.")["docs"]
    # the same numbers produced again, with one document redacted (other bytes) and one left out
    new = tmp_path / "ACME_VOL001R"
    shutil.copytree(volume, new)
    (new / "natives" / "ACME000006.csv").write_text("month,amount\nMarch,[REDACTED]\n")
    dat = new / "DATA" / "ACME_VOL001.dat"
    lines = dat.read_bytes().split(b"\r\n")
    dat.write_bytes(b"\r\n".join(l for l in lines if b"ACME000008" not in l and b"0" * 32 not in l))

    m = Matter(m.root)
    r = received.check(new, m=m)                                     # without --replaces: reported, both kept
    assert any("--replaces ACME_VOL001" in p["problem"] for p in r["problems"])
    r = received.check(new, m=m, replaces=["ACME_VOL001"])
    assert {x["bates"] for x in r["would_supersede"]} == {"ACME000001", "ACME000004", "ACME000006"}
    assert r["not_replaced"] == ["ACME000007", "ACME000008"]
    with pytest.raises(ValueError, match="no received volume"):
        received.ingest(m, new, "Acme Corp.", replaces=["ACME_VOL009"], person="user:ann")

    r = received.ingest(Matter(m.root), new, "Acme Corp.", replaces=["ACME_VOL001"], person="user:ann")
    assert {x["bates"] for x in r["superseded"]} == {"ACME000001", "ACME000004", "ACME000006"}
    m = Matter(m.root)
    assert m.state.follow(old["ACME000006"]) == r["docs"]["ACME000006"]
    assert old["ACME000006"] in m.state.withdrawn and old["ACME000007"] not in m.state.withdrawn
    # the numbers name the replacement now
    (hit,) = resolve_bates(m, "bates:ACME000006")
    assert hit["loc"]["doc"] == r["docs"]["ACME000006"] and hit["production"] == "ACME_VOL001R"


def test_overlay_cli(volume, m, tmp_path):
    received.ingest(m, volume, "Acme Corp.")
    ov = tmp_path / "ov.csv"
    ov.write_text("BEGBATES,SUBJECT\nACME000001,Corrected\n")
    run = lambda *a: subprocess.run([sys.executable, "-m", "adhocdocs.cli", "adhoc", "--matter", str(m.root),
                                     "overlay-production", str(ov), *a], capture_output=True, text=True)
    r = run("--check")
    assert r.returncode == 0 and json.loads(r.stdout)["changed"] == 1 and not json.loads(r.stdout)["applied"]
    r = run()
    assert r.returncode == 0 and json.loads(r.stdout)["applied"]
    assert json.loads(run().stdout)["changed"] == 0


def test_a_replacement_carries_tags_and_notes_over_marked(volume, m, tmp_path):
    import shutil
    from adhocdocs import annotations as A
    old = received.ingest(m, volume, "Acme Corp.")["docs"]
    m = Matter(m.root)
    csv = old["ACME000006"]
    layer = m.state.primary_layer(m.state.documents[csv])
    text = m.objects.get_text(layer.text)
    rng = lambda w: A.normalize_target(m, locators.char_locator(csv, layer.id, text, text.index(w), text.index(w) + len(w)))
    for tag, by in (("hot", "user:ann"), ("check-privilege", "claude"), ("key", "user:ann")):
        A.write(m, by, {"op": "define-tag", "tag": tag})
    A.write(m, "user:ann", {"op": "tag", "tag": "hot", "target": {"doc": csv}})
    A.write(m, "claude", {"op": "tag", "tag": "check-privilege", "target": {"doc": csv}})
    A.write(m, "user:ann", {"op": "tag", "tag": "key", "target": rng("month")})
    A.write(m, "user:ann", {"op": "tag", "tag": "key", "target": rng("1200")})          # to be redacted
    (n1,) = A.write(m, "user:ann", {"op": "note", "target": {"doc": csv}, "body": "The March total."})
    A.write(m, "claude", {"op": "reply", "parent": n1["note"], "body": "It matches the invoice."})
    A.write(m, "user:ann", {"op": "note", "target": rng("1200"), "body": "Why 1200?"})

    new = tmp_path / "ACME_VOL001R"
    shutil.copytree(volume, new)
    (new / "natives" / "ACME000006.csv").write_text("month,amount\nMarch,[REDACTED]\n")
    dat = new / "DATA" / "ACME_VOL001.dat"
    dat.write_bytes(b"\r\n".join(l for l in dat.read_bytes().split(b"\r\n") if b"0" * 32 not in l))
    with pytest.raises(ValueError, match="--user"):
        received.ingest(Matter(m.root), new, "Acme Corp.", replaces=["ACME_VOL001"])
    r = received.ingest(Matter(m.root), new, "Acme Corp.", replaces=["ACME_VOL001"], person="user:bo")
    rep = r["docs"]["ACME000006"]
    assert [x["why"] for x in r["annotations_not_carried"]] == ["the tagged passage isn't in the replacement"]

    an = A.Annotations.load(Matter(m.root))
    tags = {(a["tag"], json.dumps(a["target"].get("select"))): a for a in an.applications() if a["target"]["doc"] == rep}
    hot, flag = tags[("hot", "null")], tags[("check-privilege", "null")]
    # a person's is carried as the person who carried it, keeping whose it was; Claude's stays Claude's
    assert (hot["by"], hot["carried"]["by"]) == ("user:bo", "user:ann") and hot["carried"]["from"] == {"doc": csv}
    assert flag["by"] == "claude" and "ACME_VOL001R" in flag["carried"]["reason"]
    key = next(a for (t, _), a in tags.items() if t == "key")
    assert key["target"]["quote"]["exact"] == "month" and key["carried"]
    notes = [n for n in an.notes.values() if n["target"]["doc"] == rep]
    by_body = {n["body"]: n for n in notes}
    assert by_body["It matches the invoice."]["parent"] == by_body["The March total."]["note"]     # still a thread
    assert by_body["Why 1200?"]["carried"]["unplaced"] and by_body["Why 1200?"]["target"] == {"doc": rep}
    # the old document keeps its own
    assert any(a["target"]["doc"] == csv and a["tag"] == "hot" and not a.get("carried") for a in an.applications())

    # confirming: Claude can confirm its own, not the user's; the user can confirm anything
    m = Matter(m.root)
    with pytest.raises(A.Refused, match="Claude can't"):
        A.write(m, "claude", {"op": "confirm-carried", "tag": "hot", "target": {"doc": rep}})
    A.write(m, "claude", {"op": "confirm-carried", "tag": "check-privilege", "target": {"doc": rep}})
    A.write(m, "user:bo", {"op": "confirm-carried", "tag": "hot", "target": {"doc": rep}})
    with pytest.raises(A.Refused, match="nothing carried"):
        A.write(m, "user:bo", {"op": "confirm-carried", "tag": "hot", "target": {"doc": rep}})
    A.write(m, "user:bo", {"op": "edit-note", "note": by_body["Why 1200?"]["note"], "body": "Redacted now."})
    an = A.Annotations.load(m)
    left = [a for a in an.applications() if a.get("carried")] + [n for n in an.notes.values() if n.get("carried")]
    assert sorted(x.get("tag") or x["body"] for x in left) == ["It matches the invoice.", "The March total.", "key"]
    assert next(a for a in an.applications() if a["tag"] == "hot" and a["target"] == {"doc": rep})["confirmed_by"] == "user:bo"


# -- IPRO .lfp and EDRM XML load files ------------------------------------------------------------

def test_an_ipro_lfp_instead_of_an_opt(volume, m):
    """The same volume with its page images in an IPRO .lfp: the same documents and pages."""
    (volume / "DATA" / "ACME_VOL001.opt").unlink()
    lines = ["IM,ACME000001,D,0,@ACME_VOL001;IMAGES\\0001;ACME000001.TIF;2",
             "IM,ACME000002,,0,@ACME_VOL001;IMAGES\\0001;ACME000002.TIF;2",
             "IM,ACME000003,,0,@ACME_VOL001;IMAGES\\0001;ACME000003.TIF;2",
             "IM,ACME000004,C,0,@ACME_VOL001;IMAGES\\0001;ACME000004.TIF;2",     # C: an attachment starts here
             "IM,ACME000005,,0,@ACME_VOL001;IMAGES\\0001;ACME000005.PNG;4",
             "IM,ACME000006,D,0,@ACME_VOL001;IMAGES\\0001;ACME000006.TIF;2",
             "IM,ACME000008,D,0,@ACME_VOL001;IMAGES\\0001;ACME000008.TIF;2",
             "FT,ACME000008,1,1,@ACME_VOL001;TEXT;ACME000008.txt"]
    (volume / "DATA" / "ACME_VOL001.lfp").write_text("\r\n".join(lines) + "\r\n")
    pages = received.parse_lfp(volume / "DATA" / "ACME_VOL001.lfp")
    assert [p["first"] for p in pages] == [True, False, False, True, False, True, True]
    assert pages[0]["path"] == "IMAGES/0001/ACME000001.TIF" and pages[-1]["text"] == "TEXT/ACME000008.txt"
    r = received.check(volume)
    assert r["opt"].endswith("ACME_VOL001.lfp")
    assert (r["records"], r["as_images"], r["as_native"], r["as_text"], r["attachments"]) == (5, 2, 2, 1, 1)
    docs = received.ingest(m, volume, "Acme Corp.")["docs"]
    assert meta(m, docs["ACME000001"])["produced"]["pages"] == ["ACME000001", "ACME000002", "ACME000003"]
    assert meta(m, docs["ACME000004"])["produced"]["pages"] == ["ACME000004", "ACME000005"]


EDRM = """<?xml version="1.0" encoding="UTF-8"?>
<Root DataInterchangeType="Update" Description="ACME production" MajorVersion="1" MinorVersion="2">
  <Batch>
    <Documents>
      <Document DocID="CTL-1" DocType="Message" MimeType="message/rfc822">
        <Tags>
          <Tag TagName="#BegDoc" TagValue="ACME000101" TagDataType="Text"/>
          <Tag TagName="#EndDoc" TagValue="ACME000102" TagDataType="Text"/>
          <Tag TagName="#Custodian" TagValue="Smith, John" TagDataType="Text"/>
          <Tag TagName="#Subject" TagValue="Re: the Dock 4 pallet" TagDataType="Text"/>
          <Tag TagName="#DateSent" TagValue="2021-03-04" TagDataType="Date"/>
        </Tags>
        <Files>
          <File FileType="Image">
            <ExternalFile FilePath="IMAGES\\0001" FileName="ACME000101.tif" FileSize="1" PageCount="1"/>
            <ExternalFile FilePath="IMAGES\\0001" FileName="ACME000102.tif" FileSize="1" PageCount="1"/>
          </File>
          <File FileType="Text"><ExternalFile FilePath="TEXT" FileName="ACME000101.txt"/></File>
        </Files>
      </Document>
      <Document DocID="CTL-2" DocType="File">
        <Tags>
          <Tag TagName="#BegDoc" TagValue="ACME000103"/>
          <Tag TagName="#EndDoc" TagValue="ACME000103"/>
          <Tag TagName="#Custodian" TagValue="Smith, John"/>
          <Tag TagName="#FileName" TagValue="totals.csv"/>
        </Tags>
        <Files>
          <File FileType="Native"><ExternalFile FilePath="NATIVES" FileName="ACME000103.csv" Hash="{md5}"/></File>
        </Files>
      </Document>
      <Document DocID="CTL-3" DocType="File">
        <Tags><Tag TagName="#BegDoc" TagValue="ACME000104"/><Tag TagName="#Custodian" TagValue="Jones, Ann"/></Tags>
        <Files><File FileType="Text"><ExternalFile FilePath="TEXT" FileName="ACME000104.txt"/></File></Files>
      </Document>
    </Documents>
    <Relationships>
      <Relationship Type="Attachment" ParentDocId="CTL-1" ChildDocId="CTL-2"/>
    </Relationships>
  </Batch>
</Root>
"""


def test_an_edrm_xml_volume(tmp_path, m):
    v = tmp_path / "ACME_VOL002"
    page(v / "IMAGES" / "0001" / "ACME000101.tif")
    page(v / "IMAGES" / "0001" / "ACME000102.tif")
    (v / "NATIVES").mkdir()
    (v / "NATIVES" / "ACME000103.csv").write_text("month,amount\nMarch,1200\n")
    (v / "TEXT").mkdir()
    (v / "TEXT" / "ACME000101.txt").write_text("Re: the Dock 4 pallet\n\nIt never arrived.")
    (v / "TEXT" / "ACME000104.txt").write_text("A note, text only.")
    md5 = hashlib.md5((v / "NATIVES" / "ACME000103.csv").read_bytes()).hexdigest()
    (v / "ACME_VOL002.xml").write_text(EDRM.replace("{md5}", md5))
    r = received.check(v)
    assert r["dialect"] == "edrm-xml" and r["columns"]["begin"] == "BegDoc" and r["columns"]["control"] == "DocID"
    assert (r["records"], r["as_images"], r["as_native"], r["as_text"], r["attachments"]) == (3, 1, 1, 1, 1)
    assert r["problems"] == []
    out = received.ingest(m, v, "Acme Corp.")
    docs = out["docs"]
    mm = Matter(m.root)
    email = meta(m, docs["ACME000101"])
    assert email["kind"] == "pdf" and email["date"].startswith("2021-03-04") and email["produced"]["control"] == "CTL-1"
    import pymupdf
    assert pymupdf.open(mm.objects.path(mm.state.documents[docs["ACME000101"]])).page_count == 2
    assert mm.state.children[docs["ACME000101"]] == [docs["ACME000103"]]
    assert mm.objects.get_bytes(mm.state.documents[docs["ACME000103"]]).startswith(b"month,amount")
    assert not any("MD5" in p["problem"] for p in out["problems"])               # the hash agreed
    assert meta(m, docs["ACME000104"])["produced"]["as"] == "text"
    assert received.resolve_name(mm, "ctl:CTL-2")[0]["loc"]["doc"] == docs["ACME000103"]
