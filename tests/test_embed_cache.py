"""The semantic index's embedding cache ($ADHOC_EMBED_CACHE), with a stand-in
for the model: what is cached comes back as it was stored, only passages it
lacks are embedded, and another model's vectors aren't used."""

import hashlib

import numpy as np

from adhocdocs.index import semantic


def fake_embed(calls):
    def embed(texts):
        calls.append(list(texts))
        rows = [np.frombuffer(hashlib.sha256(t.encode()).digest()[:16], dtype=np.uint8).astype(np.float32) + 1
                for t in texts]
        v = np.stack(rows)
        return (v / np.linalg.norm(v, axis=1, keepdims=True)).astype(np.float16)
    return embed


def test_cache_returns_what_it_stored_and_embeds_only_what_it_lacks(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(semantic, "_embed", fake_embed(calls))
    monkeypatch.setattr(semantic, "model_hash", lambda: "sha256:model-a")
    monkeypatch.setenv("ADHOC_EMBED_CACHE", str(tmp_path / "embed"))

    first = semantic.passage_vectors(["alpha", "beta"])
    assert calls == [["alpha", "beta"]] and first.dtype == np.float16
    again = semantic.passage_vectors(["beta", "gamma", "alpha"])
    assert calls[1:] == [["gamma"]]                      # only the new passage
    assert again[0].tobytes() == first[1].tobytes() and again[2].tobytes() == first[0].tobytes()

    monkeypatch.setattr(semantic, "model_hash", lambda: "sha256:model-b")
    semantic.passage_vectors(["alpha"])
    assert calls[2:] == [["alpha"]]                      # another model: embedded afresh


def test_no_cache_unless_set(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(semantic, "_embed", fake_embed(calls))
    monkeypatch.delenv("ADHOC_EMBED_CACHE", raising=False)
    semantic.passage_vectors(["alpha"])
    semantic.passage_vectors(["alpha"])
    assert len(calls) == 2
