# adhocdocs

Tools for working with evidentiary documents from Claude Code.

## Trying it

```sh
uv sync
uv run adhoc init /tmp/m && export ADHOC_MATTER=/tmp/m
for d in corpus/*/; do
  n=$(basename "$d"); [ "$n" = tools ] && continue
  uv run adhoc ingest --custodian "$n" --collection "$n" "$d"
done
uv run search '"never received" w/10 pallet'
uv run search 'who signed for the delivery' --index hybrid
uv run search grep 'PN 44-B/7'
uv run idx status
uv run page ref add memo.md nr "$(uv run search '"never received"' --by hit | sed -n 2p | jq -c .loc)"
uv run page export memo.md --format docx -o memo.docx   # memo.md quotes with :q{#nr}
(cd viewer && cargo build --release)   # the viewer: needs Rust 1.89, clang, WebKitGTK 4.1
uv run adhoc view && uv run view list nr.list.jsonl   # after search '"never received"' --list nr.list.jsonl
uv run adhoc ann-api &   # the annotation API; then e.g. ann define-tag hot, ann tag hot <doc>
uv run pytest            # ADHOC_TEST_SEMANTIC=1 to include semantic search
```

`corpus/` is a 51-file test matter: a fictional supply dispute plus real
public-domain federal documents. [corpus/SOURCES.md](corpus/SOURCES.md) lists
every file's source and the facts the tests check. OCR needs `tesseract`
with English data, either system-wide or in
`~/.cache/adhocdocs/models/tessdata/`. Every command has `--help` and prints
JSON.

## Design

- [DESIGN.md](DESIGN.md): principles
- [TOOLS.md](TOOLS.md): the tools, and which are built
- [LOCATORS.md](LOCATORS.md): naming documents and places in them
- [TEXTLAYERS.md](TEXTLAYERS.md): extracted text and its structure
- [INDEXING.md](INDEXING.md): indexes, search, keeping indexes up to date
- [LISTS.md](LISTS.md): document lists and the list display
- [ANNOTATIONS.md](ANNOTATIONS.md): tags and notes
- [PAGES.md](PAGES.md): literate pages that quote and cite documents
- [VIEWER.md](VIEWER.md): the document viewer
- [CONTAINER.md](CONTAINER.md): the container, sign-in, and Anthropic's terms

The design docs keep the reasons and decisions. Formats, schemas and usage
live with the code: module docstrings and `--help`.
