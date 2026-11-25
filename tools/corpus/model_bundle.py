"""The semantic index's model as a plain folder, for the test corpus's bucket,
so a session that can't reach Hugging Face still has semantic search: the
embedding cache's keys are the weights' hash, and queries are embedded with
the model. See docs/QUOTEBENCH.md.

    # on a machine that can reach huggingface.co: download the model, copy its files out
    uv run python tools/corpus/model_bundle.py export --out corpus-ftc/model [--matter matter]
    gcloud storage rsync -r corpus-ftc/model gs://ellbur-adhocdocs-corpora/ftc/model

    # anywhere: check a bundle, and install it where the tools look (pull_corpus.py does this)
    uv run python tools/corpus/model_bundle.py check corpus-ftc/model
    uv run python tools/corpus/model_bundle.py install corpus-ftc/model

The bundle is the files fastembed downloads for ``$ADHOC_EMBED_MODEL``
(``BAAI/bge-small-en-v1.5``, MIT; about 67 MB), copied as they are, and
``model.json``: the model, where it came from, its weights' hash, and each
file's SHA-256. ``export --matter`` also checks that the hash is the one the
matter's semantic index (and so the embedding cache made from it) was built
with. ``install`` copies it to ``<models dir>/bundles/<model>/``, where
``semantic.bundle_dir()`` finds it, so the model loads with no download and
no environment variable.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from adhocdocs.index import semantic

MANIFEST = semantic.BUNDLE_MANIFEST


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def export(out: Path, matter: str | None) -> dict:
    m = semantic.model()                     # downloads it, if it isn't here yet
    inner = getattr(m, "model", m)
    src = Path(getattr(inner, "_model_dir"))
    desc = getattr(inner, "model_description", None)
    model_file = getattr(desc, "model_file", None) or "model_optimized.onnx"
    files = sorted(p for p in src.rglob("*") if p.is_file() and not p.name.startswith("."))
    if not any(p.name == model_file for p in files):
        sys.exit(f"{src}: no {model_file}")
    out.mkdir(parents=True, exist_ok=True)
    listed = {}
    for p in files:
        rel = p.relative_to(src)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p.resolve(), dest)     # the Hugging Face cache's files are links to its blobs
        listed[str(rel)] = sha256(dest)
    weights = "sha256:" + listed[model_file]
    if weights != semantic.model_hash():
        sys.exit(f"the copied weights hash {weights}, the loaded model {semantic.model_hash()}")
    if matter:
        from adhocdocs.index import open_index
        from adhocdocs.matter import Matter
        made = open_index(Matter(Path(matter)), "semantic").manifest().get("settings", {}).get("model_hash")
        if made != weights:
            sys.exit(f"{matter}'s semantic index was made with {made}, not these weights ({weights})")
    info = {"model": semantic.MODEL, "source": getattr(getattr(desc, "sources", None), "hf", None),
            "model_file": model_file, "model_hash": weights, "files": listed}
    (out / MANIFEST).write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8", newline="\n")
    return info


def check(d: Path) -> list[str]:
    """Problems with a bundle: no manifest, another model, a file missing or not what was exported."""
    m = d / MANIFEST
    if not m.exists():
        return [f"{m}: missing"]
    info = json.loads(m.read_text(encoding="utf-8"))
    bad = []
    if info.get("model") != semantic.MODEL:
        bad.append(f"{d}: a bundle of {info.get('model')}, not {semantic.MODEL}")
    for rel, want in info.get("files", {}).items():
        p = d / rel
        if not p.exists():
            bad.append(f"{rel}: missing")
        elif sha256(p) != want:
            bad.append(f"{rel}: SHA-256 differs from when it was exported")
    if "sha256:" + info.get("files", {}).get(info.get("model_file", ""), "") != info.get("model_hash"):
        bad.append(f"{m}: model_hash isn't the weights' SHA-256")
    return bad


def install(d: Path) -> Path:
    """Copy a checked bundle to where semantic.bundle_dir() looks."""
    bad = check(d)
    if bad:
        raise ValueError("; ".join(bad))
    dest = semantic.models_dir() / "bundles" / semantic.MODEL.replace("/", "--")
    if dest.resolve() == d.resolve():
        return dest
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(d, tmp)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="copy the model's files and write model.json")
    e.add_argument("--out", required=True)
    e.add_argument("--matter", help="also check the weights are the ones this matter's semantic index used")
    c = sub.add_parser("check", help="check a bundle's files against its model.json")
    c.add_argument("dir")
    i = sub.add_parser("install", help="check a bundle and install it in the models directory")
    i.add_argument("dir")
    a = ap.parse_args()
    if a.cmd == "export":
        info = export(Path(a.out), a.matter)
        print(json.dumps({"bundle": a.out, "model": info["model"], "model_hash": info["model_hash"],
                          "files": len(info["files"])}))
    elif a.cmd == "check":
        bad = check(Path(a.dir))
        for b in bad:
            print(b, file=sys.stderr)
        if bad:
            sys.exit(1)
        print(json.dumps({"bundle": a.dir, "ok": True}))
    else:
        try:
            print(json.dumps({"installed": str(install(Path(a.dir)))}))
        except ValueError as err:
            sys.exit(str(err))


if __name__ == "__main__":
    main()
