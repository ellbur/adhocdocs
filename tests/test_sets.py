"""Key sets: list condense, set, cover and uncover."""

import json
import shutil
from pathlib import Path

import pytest

from adhocdocs import cli, lists, sets
from adhocdocs.ingest import ingest_paths
from adhocdocs.matter import Matter

CORPUS = Path(__file__).parent.parent / "corpus"
WORDS = ("the pallet of bolts arrived at dock four on the afternoon of february twenty fourth and was "
         "signed for by the receiving clerk who noted no damage to the shrink wrap or the crate ").split()


@pytest.fixture(scope="module")
def matter(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sets")
    a, b = tmp / "a", tmp / "b"
    a.mkdir(), b.mkdir()
    for name in ("okafor-d/2026-03-12_proof-of-delivery.eml", "muller-z/2026-03-17_RE_proof-of-delivery.eml",
                 "muller-z/2026-03-09_RE_INV-2026-0347_past-due.eml", "kettering-t/dock4-camera-review-notes.txt"):
        shutil.copy(CORPUS / name, a)
    shutil.copy(CORPUS / "okafor-d/2026-03-09_RE_INV-2026-0347_past-due.eml", b)  # same text, other custodian
    shutil.copy(CORPUS / "kettering-t/dock4-camera-review-notes.txt", b / "copy-of-notes.txt")  # same original
    text = " ".join(f"{w}{i}" for i, w in enumerate(WORDS * 8))
    (a / "account.txt").write_text(text)
    (a / "account-v2.txt").write_text(text.replace("damage", "harm", 1))  # a near-duplicate
    (a / "unrelated.txt").write_text(" ".join(reversed(WORDS)) * 3)
    m = Matter.create(tmp / "matter")
    ingest_paths(m, [a], "a-a", "a")
    ingest_paths(m, [b], "b-b", "b")
    return Matter(m.root)


def by_name(m):
    from adhocdocs.analysis import _ready
    rows = _ready(m, "fields").rows()
    return {r["name"] + ("@" + r["custodian"] if r["custodian"] == "b-b" else ""): d for d, r in rows.items()
            if not r["parent"]}


def candidates(m, path, names=None):
    docs = by_name(m)
    header, items = lists.new("candidates", ["doc:" + d for n, d in sorted(docs.items(), key=lambda x: ("@" in x[0], x[0])) if names is None or n in names],
                              query="delivery")
    lists.write(path, header, items)
    return path


def shape(m, items):
    names = {d: n for n, d in by_name(m).items()}
    return {names[sets._doc(it["loc"])]: {names[sets._doc(c["loc"])]: c["fields"]["covered"]
                                          for c in it.get("children", [])} for it in items}


def test_condense_folds_mechanical_redundancy(matter, tmp_path):
    src = candidates(matter, tmp_path / "c.list.jsonl")
    header, items, summary = sets.condense(matter, [(str(src), *lists.read(src))], question="Delivered?")
    got = shape(matter, items)
    assert got["2026-03-17_RE_proof-of-delivery.eml"] == {
        "2026-03-12_proof-of-delivery.eml": "quoted-in-later-email",
        "2026-03-09_RE_INV-2026-0347_past-due.eml": "quoted-in-later-email",
        "2026-03-09_RE_INV-2026-0347_past-due.eml@b-b": "same-text"}
    notes = "dock4-camera-review-notes.txt"
    assert got[notes] == {"copy-of-notes.txt@b-b": "duplicate"}
    assert got["account-v2.txt"] == {"account.txt": "near-duplicate"} or got["account.txt"] == {"account-v2.txt": "near-duplicate"}
    assert got["unrelated.txt"] == {}
    assert summary["candidates"] == sum(1 + len(v) for v in got.values())
    assert header["question"] == "Delivered?" and header["sources"][0]["query"] == "delivery"
    # every covered document says what covers it directly
    for it in items:
        for c in it.get("children", []):
            assert c["fields"]["covered_by"].startswith("doc:")


def test_steps_can_be_turned_off(matter, tmp_path):
    src = candidates(matter, tmp_path / "c.list.jsonl")
    _, items, summary = sets.condense(matter, [(str(src), *lists.read(src))], near=0, quoted=0)
    assert set(summary["covered"]) == {"duplicate", "same-text"}


def test_judgments_survive_condensing_again(matter, tmp_path):
    src = candidates(matter, tmp_path / "c.list.jsonl")
    out = tmp_path / "set.list.jsonl"
    docs = by_name(matter)
    pod, notes, unrelated = (docs[n] for n in ("2026-03-12_proof-of-delivery.eml", "dock4-camera-review-notes.txt",
                                               "unrelated.txt"))
    h, items, _ = sets.condense(matter, [(str(src), *lists.read(src))])
    sets.uncover(items, [pod])
    sets.set_fields(items, pod, {"why": "the carrier's POD"})
    sets.cover(items, [unrelated], notes, note="says the same")
    with pytest.raises(ValueError, match="cover itself"):
        sets.cover(items, [notes], notes)
    lists.write(out, h, items)

    h2, items2, _ = sets.condense(matter, [(str(out), *lists.read(out))])
    got = shape(matter, items2)
    assert got["2026-03-12_proof-of-delivery.eml"] == {}
    assert got["dock4-camera-review-notes.txt"] == {"copy-of-notes.txt@b-b": "duplicate", "unrelated.txt": "restates"}
    item = next(it for it in items2 if sets._doc(it["loc"]) == pod)
    assert item["fields"] == {"why": "the carrier's POD", "kept": "quoted-in-later-email"}
    assert h2["sources"] == h["sources"]


def test_uncover_brings_what_it_covers(matter, tmp_path):
    src = candidates(matter, tmp_path / "c.list.jsonl")
    _, items, _ = sets.condense(matter, [(str(src), *lists.read(src))])
    sets.uncover(items, [by_name(matter)["2026-03-09_RE_INV-2026-0347_past-due.eml"]])
    got = shape(matter, items)
    assert got["2026-03-09_RE_INV-2026-0347_past-due.eml"] == {"2026-03-09_RE_INV-2026-0347_past-due.eml@b-b": "same-text"}
    assert got["2026-03-17_RE_proof-of-delivery.eml"] == {"2026-03-12_proof-of-delivery.eml": "quoted-in-later-email"}


def test_cli(matter, tmp_path, capsys):
    src = candidates(matter, tmp_path / "c.list.jsonl")
    out = tmp_path / "set.list.jsonl"
    args = ["--matter", str(matter.root)]
    cli.list_(["condense", str(src), "--out", str(out), "--question", "Delivered?", *args])
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["representatives"] == 4
    notes = by_name(matter)["dock4-camera-review-notes.txt"][7:19]
    cli.list_(["set", str(out), notes, "why=camera review", *args])
    unrelated = by_name(matter)["unrelated.txt"][7:19]
    cli.list_(["cover", str(out), unrelated, "--by", notes, "--note", "same", *args])
    cli.list_(["uncover", str(out), unrelated, *args])
    _, items = lists.read(out)
    it = next(it for it in items if notes in it["loc"])
    assert it["fields"]["why"] == "camera review"
    assert any(unrelated in x["loc"] and "fields" not in x for x in items)  # a restates uncover isn't marked kept
    with pytest.raises(SystemExit):
        cli.list_(["set", str(out), notes, "novalue", *args])
