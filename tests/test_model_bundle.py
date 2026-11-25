"""The embedding model as a bundle (tools/corpus/model_bundle.py): exported
from fastembed's download as plain files with their hashes, checked,
installed where semantic.bundle_dir() finds it, loaded from there with no
download, and hashed the same as the download, so the embedding cache's keys
still match. A stand-in for fastembed: no model is downloaded here."""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from adhocdocs.index import semantic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "corpus"))
import model_bundle  # noqa: E402

WEIGHTS = b"not really onnx, but bytes to hash"


@pytest.fixture
def models(tmp_path, monkeypatch):
    """A models directory holding a download laid out as Hugging Face's cache
    lays it out (snapshot files are links to blobs), and a stand-in model."""
    d = tmp_path / "models"
    blobs = d / "models--Qdrant--bge-small-en-v1.5-onnx-Q" / "blobs"
    snap = d / "models--Qdrant--bge-small-en-v1.5-onnx-Q" / "snapshots" / "rev1"
    blobs.mkdir(parents=True)
    snap.mkdir(parents=True)
    for name, data in (("model_optimized.onnx", WEIGHTS), ("tokenizer.json", b"{}"), ("config.json", b'{"a": 1}')):
        blob = blobs / hashlib.sha256(data).hexdigest()
        blob.write_bytes(data)
        (snap / name).symlink_to(blob)
    monkeypatch.setenv("ADHOC_MODELS", str(d))
    monkeypatch.delenv("ADHOC_EMBED_MODEL_PATH", raising=False)
    fake = SimpleNamespace(model=SimpleNamespace(
        _model_dir=str(snap),
        model_description=SimpleNamespace(model_file="model_optimized.onnx",
                                          sources=SimpleNamespace(hf="Qdrant/bge-small-en-v1.5-onnx-Q"))))
    monkeypatch.setattr(semantic, "_model", fake)
    return d


def test_export_check_install_and_load(models, tmp_path, monkeypatch):
    want = "sha256:" + hashlib.sha256(WEIGHTS).hexdigest()
    assert semantic.model_hash() == want                  # the download, as before

    out = tmp_path / "bundle"
    info = model_bundle.export(out, None)
    assert info["model_hash"] == want and info["model"] == semantic.MODEL
    assert not (out / "model_optimized.onnx").is_symlink()   # the blob's bytes, not a link
    assert sorted(info["files"]) == ["config.json", "model_optimized.onnx", "tokenizer.json"]
    assert model_bundle.check(out) == []

    dest = model_bundle.install(out)
    assert dest == models / "bundles" / semantic.MODEL.replace("/", "--")
    assert semantic.bundle_dir() == dest

    # with a bundle installed, hashing it doesn't load the model, and gives the same hash
    def no_model():
        raise AssertionError("the model shouldn't load to hash a bundle")
    monkeypatch.setattr(semantic, "model", no_model)
    assert semantic.model_hash() == want


def test_the_model_loads_from_the_bundle(models, tmp_path, monkeypatch):
    out = tmp_path / "bundle"
    model_bundle.export(out, None)
    dest = model_bundle.install(out)
    seen = {}

    class TextEmbedding:
        def __init__(self, name, **kw):
            seen.update(kw, name=name)

    import fastembed
    monkeypatch.setattr(fastembed, "TextEmbedding", TextEmbedding)
    monkeypatch.setattr(semantic, "_model", None)
    semantic.model()
    assert seen["name"] == semantic.MODEL and seen["specific_model_path"] == str(dest)


def test_env_overrides_and_no_bundle(models, tmp_path, monkeypatch):
    assert semantic.bundle_dir() is None
    monkeypatch.setenv("ADHOC_EMBED_MODEL_PATH", str(tmp_path / "elsewhere"))
    assert semantic.bundle_dir() == tmp_path / "elsewhere"


def test_check_finds_changes(models, tmp_path):
    out = tmp_path / "bundle"
    model_bundle.export(out, None)
    (out / "tokenizer.json").write_text('{"changed": true}')
    assert any("tokenizer.json" in b for b in model_bundle.check(out))
    with pytest.raises(ValueError):
        model_bundle.install(out)

    info = json.loads((out / "model.json").read_text())
    info["model"] = "someone/else"
    (out / "model.json").write_text(json.dumps(info))
    assert any("someone/else" in b for b in model_bundle.check(out))
    assert model_bundle.check(tmp_path / "nothing") == [f"{tmp_path / 'nothing' / 'model.json'}: missing"]


def test_export_checks_the_matters_index(models, tmp_path, monkeypatch):
    class Ix:
        def manifest(self):
            return {"settings": {"model_hash": "sha256:other"}}
    import adhocdocs.index as index
    import adhocdocs.matter as matter
    monkeypatch.setattr(index, "open_index", lambda m, name: Ix())
    monkeypatch.setattr(matter, "Matter", lambda p: None)
    with pytest.raises(SystemExit, match="was made with sha256:other"):
        model_bundle.export(tmp_path / "bundle", str(tmp_path / "m"))
