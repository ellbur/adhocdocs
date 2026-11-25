#!/bin/sh
# Make the FTC docket (Benco/Schein/Patterson, Docket No. 9379) a matter at
# matter/ in this checkout, ready to search: pull the files, their OCR, their
# passages' embeddings and the embedding model from the public bucket, ingest
# them, index them.
# A session's first turn runs it.
#
#   sh tools/corpus/ftc_matter.sh            # or: MATTER=~/matters/ftc sh tools/corpus/ftc_matter.sh
#
# The OCR cache stands in for tesseract, which cloud sessions lack, so the
# scanned exhibits (about 1,300 pages) have their text there too. The
# embedding cache makes the semantic index a lookup instead of an hour of
# CPU; without it (pulled with --no-embed-cache, or the model changed) the
# semantic index builds in the background, and searches say how much of the
# matter it covers until it's done. Both need the model: from the bucket
# (model/, installed by pull_corpus.py), so no Hugging Face access is
# needed, or else downloaded from huggingface.co. With neither, the keyword
# index is built and the semantic one isn't. Run again, it finishes what an
# interrupted run left: files already pulled or ingested are skipped.
set -eu
cd "$(dirname "$0")/../.."
MATTER=${MATTER:-matter}

uv run python tools/corpus/pull_corpus.py
export ADHOC_OCR_CACHE="$PWD/corpus-ftc/ocr-cache"
if [ -f corpus-ftc/embed-cache/embeddings.sqlite ]; then
  export ADHOC_EMBED_CACHE="$PWD/corpus-ftc/embed-cache"
fi

[ -d "$MATTER" ] || uv run adhoc init "$MATTER"
uv run adhoc --matter "$MATTER" ingest --manifest tools/corpus/benco-9379.toml >/dev/null
uv run idx --matter "$MATTER" update fields keyword >/dev/null
if [ -n "${ADHOC_EMBED_CACHE:-}" ]; then
  if uv run idx --matter "$MATTER" update semantic >/dev/null; then
    echo "matter ready: $MATTER (keyword and semantic indexes built)"
  else
    echo "matter ready: $MATTER (keyword index built; no semantic index: the embedding model couldn't be loaded," \
         "and the bucket has no model/ for pull_corpus.py to install, or this machine can't reach huggingface.co)"
  fi
else
  uv run idx --matter "$MATTER" update --background semantic >/dev/null
  echo "matter ready: $MATTER (keyword index built; the semantic index is building in the background: idx status)"
fi
