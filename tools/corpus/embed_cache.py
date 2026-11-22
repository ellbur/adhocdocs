"""Fill an embedding cache ($ADHOC_EMBED_CACHE) from a matter's finished
semantic index, so indexing the same documents in another matter needn't
run the model. See tools/corpus/ftc_matter.sh.

    uv run python tools/corpus/embed_cache.py --matter ~/matters/ftc --cache corpus-ftc/embed-cache [--check]

Each passage's vector is copied as the index stored it, under the key the
index would look it up by (the model's weights and the passage's text), so
it's what embedding that passage here gave. ``--check`` then builds the
semantic index again in a scratch copy of the matter with the model
disabled, and compares its vectors with the original's.
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

from adhocdocs.index import open_index, semantic
from adhocdocs.matter import Matter


def fill(m: Matter, cache: Path) -> int:
    ix = open_index(m, "semantic")
    if ix.manifest().get("settings", {}).get("model_hash") != semantic.model_hash():
        sys.exit("the matter's semantic index was made with another model than this one")
    if ix.behind():
        sys.exit("the matter's semantic index isn't finished: run `idx update semantic` first")
    vecs = ix.vectors()
    rows = ix.db.execute('SELECT id, text_hash, start, "end" FROM passages ORDER BY id').fetchall()
    os.environ["ADHOC_EMBED_CACHE"] = str(cache)
    db = semantic._embed_cache()
    texts = {}
    n = 0
    batch = []
    for pid, text_hash, s, e in rows:
        if text_hash not in texts:
            texts = {text_hash: m.objects.get_text(text_hash)}
        batch.append((semantic.embed_cache_key(texts[text_hash][s:e]), vecs[pid].tobytes()))
        if len(batch) >= 2000:
            db.executemany("INSERT OR IGNORE INTO vectors VALUES(?, ?)", batch)
            n += len(batch)
            batch = []
    db.executemany("INSERT OR IGNORE INTO vectors VALUES(?, ?)", batch)
    n += len(batch)
    db.commit()
    db.execute("VACUUM")
    db.close()
    return n


def check(m: Matter, cache: Path) -> dict:
    """Rebuild the semantic index in a copy of the matter from the cache
    alone, and compare."""
    os.environ["ADHOC_EMBED_CACHE"] = str(cache)

    def no_model(texts):
        raise RuntimeError("the model ran: the embedding cache missed")
    semantic._embed = no_model
    def link_objects(src, dst):
        # stored objects never change: link them rather than copy (the
        # copy is next to the matter, on the same filesystem)
        if "objects" in Path(src).parts:
            os.link(src, dst)
        else:
            shutil.copy2(src, dst)
    with tempfile.TemporaryDirectory(dir=Path(m.root).parent) as d:
        copy = Path(d) / "m"
        shutil.copytree(m.root, copy, symlinks=True, ignore=shutil.ignore_patterns("semantic"),
                        copy_function=link_objects)
        mc = Matter(copy)
        open_index(mc, "semantic").update()
        a, b = open_index(m, "semantic").vectors(), open_index(mc, "semantic").vectors()
        same = a.shape == b.shape and np.array_equal(a, b)
    return {"passages": int(a.shape[0]), "identical": bool(same)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matter", required=True)
    ap.add_argument("--cache", required=True, help="the embedding cache folder (ADHOC_EMBED_CACHE)")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    m, cache = Matter(a.matter), Path(a.cache).resolve()
    n = fill(m, cache)
    print(f"{n} passages into {cache / 'embeddings.sqlite'}", file=sys.stderr, flush=True)
    if a.check:
        r = check(m, cache)
        print(f"check: rebuilt {r['passages']} passages from the cache alone; identical: {r['identical']}", file=sys.stderr)
        sys.exit(0 if r["identical"] else 1)


if __name__ == "__main__":
    main()
