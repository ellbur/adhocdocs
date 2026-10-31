"""Ingest manifests and the setup checks."""

from pathlib import Path

import pytest

from adhocdocs import setup
from adhocdocs.ingest import ingest_manifest, load_manifest
from adhocdocs.matter import Matter

CORPUS = Path(__file__).parent.parent / "corpus"


def test_corpus_manifest_covers_every_directory():
    colls = load_manifest(CORPUS / "manifest.toml")
    assert sorted(p for c in colls for p in c["paths"]) == sorted(d for d in CORPUS.iterdir() if d.is_dir())
    assert {c["name"]: c["custodian"] for c in colls}["public"] is None


def test_corpus_is_mostly_noise():
    """build_noise.py's irrelevant documents are all there, and outnumber each
    custodian's story documents more than ten to one."""
    from corpus_files import NOISE, story
    assert all(p.is_file() for p in NOISE)
    for d in ("delacroix-m", "kettering-t", "muller-z", "okafor-d", "voss-r"):
        noise = sum(1 for p in NOISE if p.parent.name == d)
        assert noise > 10 * len(story(d)), d


def test_manifest_ingest(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.txt").write_text("one")
    (tmp_path / "two.txt").write_text("two")
    man = tmp_path / "m.toml"
    man.write_text('[[collection]]\nname = "c1"\ncustodian = "smith-j"\npaths = ["a"]\n'
                   '[[collection]]\nname = "c2"\npaths = ["two.txt"]\n')
    m = Matter.create(tmp_path / "matter")
    docs = ingest_manifest(m, man)
    assert len(docs) == 2
    assert ingest_manifest(Matter(m.root), man) == docs  # again: the same documents, nothing new


@pytest.mark.parametrize("body, msg", [
    ("", "no [[collection]]"),
    ('[[collection]]\npaths = ["a"]\n', "needs a name"),
    ('[[collection]]\nname = "c"\n', "paths must be"),
    ('[[collection]]\nname = "c"\npaths = ["missing"]\n', "missing does not exist"),
    ('[[collection]]\nname = "c"\npaths = ["a"]\ncustodain = "x"\n', "unknown key(s) custodain"),
    ('[[collections]]\nname = "c"\n', "unknown top-level"),
    ("[[collection]\n", "m.toml"),
])
def test_manifest_errors(tmp_path, body, msg):
    (tmp_path / "a").mkdir()
    (tmp_path / "m.toml").write_text(body)
    with pytest.raises(ValueError, match=msg.replace("[", r"\[").replace("(", r"\(").replace(")", r"\)")):
        load_manifest(tmp_path / "m.toml")


def test_setup_reports_everything_missing_before_building(monkeypatch, capsys):
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    built = []
    monkeypatch.setattr(setup, "build_viewer", lambda: built.append("viewer"))
    with pytest.raises(setup.SetupError, match="podman, cargo, clang"):
        setup.setup()
    assert not built
    err = capsys.readouterr().err
    assert "MISSING podman" in err and "MISSING tesseract" in err and "To fix:" in err
