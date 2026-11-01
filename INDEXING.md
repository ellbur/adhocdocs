# Indexing and Search

*Status: implemented: [`index/`](src/adhocdocs/index),
[`search.py`](src/adhocdocs/search.py),
[`query.py`](src/adhocdocs/query.py) (query language), CLIs `idx` and
`search` (`--help` has the usage). Schemas are in the index modules'
docstrings.*

## Goals

1. **Derived and disposable.** An index is computed from the store and can
   be deleted and rebuilt at any time. Losing one loses time, not data.
2. **Results are checked locators.** Every hit's quote is read from the
   layer, and a hit whose layer text disagrees with the index is dropped and
   counted (`dropped`, meaning run `idx verify`). An index can make a search
   miss things, never show text that isn't in the document.
3. **Honest about coverage.** Every search reports what it searched and what
   it couldn't: unextracted, empty, low-confidence OCR, and not yet indexed
   (`pending`). "No results" has to mean "no results in these documents".
4. **Exhaustive where it can be.** Keyword, field and grep searches return
   every match. Semantic and hybrid are ranked and say they aren't
   exhaustive.
5. **Plain.** SQLite plus flat files, queryable with `sqlite3`.
6. **Incremental.** Work is proportional to what was added.

## The indexes

- **`fields`**: one row per document from its object and metadata.
  Attachments inherit their parent's custodian. Tags are deliberately not
  here: search reads them live from the annotation log, so `tag:` is never
  stale.
- **`keyword`**: one FTS5 row per layer text, holding normalized tokens and
  each token's layer offsets. Normalization (in
  [`textnorm.py`](src/adhocdocs/textnorm.py)) folds case, compatibility
  forms and diacritics, and joins line-end hyphens. **No stemming**: in legal
  search `ship` must not quietly match `shipping`. FTS5 only proposes
  candidate texts; matches are confirmed and positioned by scanning the
  tokens, so phrases and proximity work across lines and pages and FTS5's
  tokenizer never decides a result.
- **`semantic`**: passages of about 200 words, never across a page,
  embedded by a local model (`BAAI/bge-small-en-v1.5`, MIT) on the CPU, and
  searched by brute force. It has to be local: the container's network
  allows only Claude Code's hosts, and sending text to an embedding service
  would breach Principle 2. Using one anyway would be a change to the
  allowlist, made by the user on the host.
- **`threads`**: each email's Message-ID, In-Reply-To, References and
  normalized subject. Threads are assembled from it at query time.
- **`neardup`**: a MinHash signature of each text's word 5-shingles, for
  near-duplicate and version detection.
- **`entities`**: detected entities in each text (people, organizations,
  defined terms, document numbers) for the entity layer's suggestions
  ([ENTITIES.md](ENTITIES.md)). It also writes a file per text and
  `candidates.json`, which the viewer reads without Python.
- **`dates`**: the dates written in each text, and each email's thread,
  for the date strip and date cards ([DATES.md](DATES.md)). Like
  `entities`, it writes files the viewer reads without Python.
- **`names`**: the person-name mentions in each text, with their evidence
  and any role beside them, for the cast of characters (`analyze people`).
- **`grep`** needs no index: a regex over the layer files, for strings an
  index tokenizes badly (account numbers, `PN 44-B/7`).

Text is indexed **once per text hash**, for every layer, not only the
primary. Duplicates and primary changes cost nothing; `--layers` chooses
which layers a search reads.

## Query language

The full syntax is in `search --help`. Choices worth knowing:

- `/s` and `/p` mean the same sentence and the same paragraph. Sentences
  end at `.`, `!` or `?` before a capital, except after common abbreviations
  (`Inc.`, `No.`, `v.`, `p.m.`…). Paragraphs end at blank lines, page breaks
  and the ends of paragraph and row segments. Text extracted from PDFs has
  no paragraph marks within a page beyond blank lines, so there `/p` is
  coarser.
- Proximity (`w/5`, `pre/5`) and precedence follow Westlaw and Lexis,
  because lawyers already know them: OR binds tightest, then proximity,
  then NOT, then AND.
- Boolean operators apply per document; proximity applies within a text.
- A plain-English query given to `--index hybrid` becomes an OR of its
  words for the keyword half, fused with semantic results by reciprocal
  rank.
- `--explain` prints the parsed query, so a search someone relies on can be
  read back.

## Keeping indexes up to date

Everything that changes what is in the matter is an event in one of two
append-only feeds: the provenance log on the host (`store`, one log per
installation of the matter, read merged: [SYNC.md](SYNC.md)) and the
workspace log (`workspace`, for derived layers). This is the one
requirement indexing puts on ingestion, **including removals**: `withdraw`
deletes index rows (not just hides them) unless another live document shares
the text. `idx changes` prints both feeds.

Each index keeps a cursor per log, advanced in the same transaction as its
rows, so an interrupted update resumes cleanly. Events synced from another
installation can arrive late and sort before ones already applied, so an
index skips an event that later ones have overtaken (an `add-document`
whose document has since been withdrawn, a `set-metadata` since replaced):
it ends up agreeing with the replayed state whatever order events arrived
in. Its `settings`
(normalization version, model and model hash, passage size) are its
identity. If the code's settings differ, the index refuses to answer and
asks for `idx rebuild`, so it never mixes two models or tokenizers.

Updates run at three points, all cheap when there is nothing to do:

1. **At session start:** `idx update --background` (the container's entry
   point will run it).
2. **Before every search:** `fields` and `keyword` catch up first. `semantic`
   answers from what it has, starts a background update, reports `pending`,
   and searches the pending texts by keyword so new documents aren't
   invisible.
3. **On request:** `idx update`.

Watching files (inotify) was left out: it is unreliable across `podman
machine` file sharing, and checking at query time covers the same cases.
`idx verify [--repair]` compares each index with the documents the feeds
say are live.

**Claude's own indexes** use the same protocol: `idx new <name> --updater
<cmd>` registers a directory under `.adhoc/indexes/` whose updater reads
change events on stdin. Nothing requires this; a throwaway index for one
question can just be a file.

## Performance notes

Embedding is the slow part: about 0.1 s per passage on a 12-core laptop
CPU. The 51-file test corpus (about 950 passages, mostly one 142-page PDF)
takes 2–3 minutes. Keyword indexing of the same corpus takes under a
second. Each command replays the feeds to know what is live, which is fine
at this size but will want a cache for large matters.

## Not built yet

- Nothing specific to search in the audit log: the log has each `search`
  command and its whole output, but not parsed out. A search saved with
  `--list` keeps its query, index settings and `as_of`, which is enough to
  rerun it.

## Decided

- Indexing runs in the container, not on the host.
- No stemming by default.
- Tags read live, not indexed.
- Every layer indexed, keyed by text hash.
- Keyword catches up before answering; semantic answers with `pending`.
- Search results as list files (`--list`); the results display is a preset
  of the list display.
- Withdrawals delete index rows.
- Any suitable CPU embedding model for now. Swapping it means rebuilding
  only `semantic`.
