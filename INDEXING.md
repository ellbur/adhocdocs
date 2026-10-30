# Indexing and Search

*Status: draft.*

The default indexes, the `search` and `idx` tools that use them, and how the
indexes keep up as documents and layers are added. As with every default
tool ([TOOLS.md](TOOLS.md)), these are a starting point. Claude can build
its own index for a task, and the update mechanism below is open to it.

## Goals

1. **Derived and disposable.** An index is never the record of anything. It
   is computed from the object store, the text layers and the metadata, and
   it can be deleted and rebuilt at any time. Losing an index loses time,
   not data.
2. **Results are locators, checked against the store.** Every hit is a
   locator ([LOCATORS.md](LOCATORS.md)) with a quote fingerprint taken from
   the layer, not from the index. An index only proposes candidates. A stale
   or damaged index can make a search miss things, but it can never make one
   show text that isn't in the document.
3. **Honest about coverage.** Every search says what it searched and what it
   couldn't: documents with no layer yet, layers with no text, documents
   added since the index was last updated. In legal work "no results" has to
   mean "no results in these documents", never "no results in what happened
   to be indexed".
4. **Exhaustive where it can be.** Keyword and field searches return every
   match. Semantic search is ranked and can't be exhaustive, and it says so.
5. **Plain.** Indexes are SQLite databases with a documented schema, plus
   flat files. Claude can query them with `sqlite3` or from a script, not
   only through `search` (Principles 3 and 4).
6. **Incremental.** Adding documents costs time in proportion to what was
   added. Nothing already indexed is redone unless the index's own settings
   change.

## The default indexes

| Index | Answers | Built from | Exhaustive |
|---|---|---|---|
| `fields` | "emails from Smith in March", "everything from custodian Jones", exact duplicates, families | document objects, extracted metadata | yes |
| `keyword` | words, phrases, Boolean and proximity queries | layer text | yes |
| `semantic` | "passages about the pallet never arriving", "more like this" | layer text, through a local embedding model | no |

Plus one search that needs no index:

- **`grep`** runs a regular expression over the layer text files directly,
  for exact strings an index might tokenize badly: account numbers, part
  numbers, odd punctuation. It is slower, but it is literally what is in the
  layer.

Later, *proposed*, using the same update mechanism: a near-duplicate index
(MinHash over shingles) for the near-duplicate and version tools, and an
email-threading index (Message-ID, In-Reply-To, References, normalized
subject). They are indexes in the same sense, so they belong here, but their
design goes with the analysis tools.

### The fields index

One row per document, from the document object and metadata extraction:
`doc`, `content`, `custodian`, `source`, `name`, `parent`, `part`, `kind`,
`date`, `from`, `to`, `cc`, `bcc`, `subject`, `pages`. These are the same
fields as the document columns in [LISTS.md](LISTS.md#columns). Email
addresses are stored normalized (lowercase, one row per address in a side
table) so `from:smith@acme.com` matches however the header wrote it.

Metadata extraction is still to be designed. This index takes whatever
fields it produces, and the table above is what search expects.

Tags are deliberately **not** in this index. They change constantly, from
the viewer as well as from Claude, so search reads them live from the
annotation log ([ANNOTATIONS.md](ANNOTATIONS.md)) at query time. A
`tag:hot` filter is therefore never stale.

### The keyword index

SQLite FTS5 over layer text.

**Normalization belongs to search, not to the layer.** Layers are literal
([TEXTLAYERS.md](TEXTLAYERS.md#goals)). The index searches a normalized
copy, and keeps a map from normalized offsets back to layer offsets, in the
same run format as a derived layer's offset map. The normalizations:

- case folding and Unicode compatibility folding (ligatures, full-width
  forms)
- diacritics removed
- curly quotes and dashes folded to their plain forms
- a hyphen at the end of a line joined to the next line, so `ship-\nment`
  matches `shipment` (the original hyphenated form still matches as well)
- line breaks and runs of whitespace treated as a single space, so a phrase
  matches across a line break

No stemming by default. In legal search, `ship` should not quietly match
`shipping`. Users who want it write `ship*`. A stemmed index can be added as
a second keyword index if it turns out to be wanted.

**Chunks.** Text is stored in chunks: one per page for paginated content,
and one per segment-bounded block (email header, body, quoted part,
paragraph group) otherwise, kept under a few thousand characters. Each chunk
records its range in the layer and the segment kinds it falls in, so a
search can be limited to `body` and leave out `quoted` text, or vice versa.
A phrase or proximity match across a chunk boundary is found by searching
neighbouring chunks together, so chunking never loses a match.

### The semantic index

Text is split into passages of a few hundred tokens with some overlap,
following segment boundaries where there are any (paragraphs, email bodies,
quoted parts, pages). Each passage has a char range in its layer and its
segment kinds. Each is embedded with an **embedding model that runs inside
the container**, on the CPU.

It has to be local. The container's network allows only Claude Code's own
hosts ([CONTAINER.md](CONTAINER.md#what-the-container-can-reach)), and
sending document text to an embedding service would breach Principle 2.
Anthropic doesn't offer an embeddings API, so a remote service would always
be a third party. If a user or firm wants to use one anyway, that is a
change to the network allowlist, which the user makes on the host. The
`semantic` index would then record which service made its vectors, and
Claude couldn't switch to it on its own.

Vectors are stored in a flat file (row *i* is passage *i*, as float16),
next to a SQLite table of passages. Search is brute force. That is fast
enough up to a few million passages, which covers most matters, and it
avoids an approximate index whose misses nobody can explain.

Embedding on a CPU is the slow part of indexing. A large matter can take
hours, not minutes. This is why the semantic index updates in the background
and reports what it hasn't reached yet (see
[Keeping indexes up to date](#keeping-indexes-up-to-date)).

## What gets indexed

- **Text is indexed once per text hash.** Index rows are keyed by the hash
  of the layer's text file, not by document or layer. Exact duplicate
  documents share content and therefore layers. Two layers with the same
  text, such as an OCR rerun that improved only the geometry, share a text
  hash. Either way the text is indexed once. At query time a hit is
  expanded to every document with that content, and the locator names a
  layer with that text, the primary one where possible.
- **Every layer, not only the primary one.** If the native text and the OCR
  of a PDF differ, both are indexed, and a search can ask for either.
  Searches use the primary layer by default (`--layers primary`), and can
  use all (`--layers all`) or one view (`--layers ocr`). Since all layers
  are indexed, changing a primary pointer needs no reindexing. The
  `markup` and `original` views are indexed in `keyword` only.
- **What can't be searched is counted.** Documents with no layer
  (unextracted), with only empty text (a photo), or whose layer is mostly
  low-confidence OCR are tracked in the index's coverage table, and every
  search reports them.

## Searching

```
search "<query>" [--index keyword|semantic|hybrid] [options]
search grep <regex> [options]
search similar <locator> [options]        semantic neighbours of a passage or document
```

Common options:

```
--within <list-file|->           only documents (or ranges) in a list, or locators on stdin
--where "<field query>"          field filters, same syntax as in the query
--tag hot --tag -privileged      live tag filters, - for absent
--layers primary|all|<view>
--in body|quoted|header|...      limit to segment kinds
--by doc|hit                     one item per document (default), or per hit
--limit <n>                      semantic only; keyword returns everything
--list <out.list.jsonl>          write a list file (see below)
--explain                        print the parsed query and the SQL it became
```

### Query syntax

A small, documented language, compiled to FTS5 and SQL:

| Form | Meaning |
|---|---|
| `pallet shipment` | both words (AND is the default) |
| `"never received"` | phrase |
| `ship*` | prefix |
| `pallet OR crate`, `NOT draft`, `( … )` | Boolean |
| `pallet w/5 received` | within 5 words, either order |
| `pallet pre/5 received` | within 5 words, in this order |
| `from:smith@acme.com`, `to:`, `cc:`, `custodian:smith-j`, `kind:email` | field filters |
| `date:2026-03`, `date:2026-03-01..2026-03-15` | date and date ranges |
| `tag:hot`, `-tag:privileged` | tags, read live |

The proximity operators follow the convention lawyers already know from
Westlaw and Lexis. Same-sentence and same-paragraph operators (`/s`, `/p`)
are *proposed*, using paragraph segments and a sentence splitter. The
example query in [LISTS.md](LISTS.md) (`custodian:smith-j date:2026-03
kind:email`) is valid in this language.

`--explain` matters because a search a lawyer relies on should be one they
can read back. It also lets Claude go straight to SQL when the language
can't express something.

### Hybrid

`--index hybrid` runs keyword and semantic searches and merges the rankings
by reciprocal rank fusion. It is the default for questions in plain English
that Claude turns into a search. It is ranked, so it reports itself as
non-exhaustive, like semantic search.

### Output

`search` prints JSON Lines: a header, then one line per item.

```json
{"type": "search", "v": 1, "query": "pallet w/5 received", "index": "keyword", "as_of": {"store": "ev_01J…", "workspace": "ev_01J…"}, "exhaustive": true, "matches": {"documents": 37, "hits": 52}, "coverage": {"documents": 41210, "searched": 40977, "unextracted": 12, "empty": 214, "low_confidence": 7, "pending": 0}}
{"loc": {"doc": "sha256:3f9a…", "layer": "sha256:8b21…", "select": {"char": [1200, 1231]}, "quote": {"exact": "the shipment was never received", "prefix": "stated that ", "suffix": ", and that"}}, "score": 11.4, "in": ["body"], "snippet": {"text": "…Smith stated that the shipment was never received, and that…", "highlight": [[19, 50]]}}
```

- `loc` is a full JSON locator. Its quote is read from the layer when the
  result is produced, so it can go straight into a page reference or a note.
- `snippet` is for display only. It is cut from the layer, but nothing
  should cite it.
- `as_of` names how far into each change feed the index had got (see
  below). With `coverage`, it says exactly what was searched.
- `pending` counts documents or layers known to exist but not yet in this
  index. It is zero for keyword and fields searches, which catch up before
  answering, and may be non-zero for semantic search.

**Every hit is resolved before it is returned:** the document must still be
in the matter, and the layer text at the offsets must match what the index
holds. A hit that fails is dropped and counted (`"dropped": n` in the
header), and it means the index needs `idx verify`.

### Search results are lists

`--list <file>` writes the results as a list file ([LISTS.md](LISTS.md)),
the header's `query` field holding the query, the index and `as_of`. With
`--by doc`, each item is a document, with its hits as `children` range
locators. With `--by hit`, each item is a range locator, grouped by
document. Score and snippet go in `fields`, so the `snippet` and `score`
columns work.

*Proposed:* the default results display is the list display with a
`snippet` column, rather than a separate kind of pane. The `results` pane
kind in [VIEWER.md](VIEWER.md#display-server-and-panes) would become a
preset of `view list`. That means search results get the preview pane,
multi-selection, tagging and paging for free.

Because lists are versioned with git, a saved search result keeps its query
and `as_of`. Running the query again later shows what is new.

## Keeping indexes up to date

### Where changes happen

Two places add things that indexes need to know about:

1. **The matter store, on the host.** Ingestion adds documents, layers and
   metadata. This happens outside the container, perhaps while no session is
   running, perhaps during one. The container sees the store read-only.
2. **The workspace, in the container.** Claude can make derived layers (a
   better OCR pass, a transcript view), which live in the workspace.

Both are append-only, and objects in them never change. That makes
incremental indexing simple: an object that has been indexed stays indexed,
and only new objects and pointer changes need work.

### The change feed

Each place keeps an append-only log, in the same style as the annotation
log: the provenance log on the host (already called for in
[LOCATORS.md](LOCATORS.md) and [TEXTLAYERS.md](TEXTLAYERS.md)), and a
workspace log that the layer tools append to. `idx changes` reads both and
prints one normalized feed:

```
idx changes [--since <cursor>]
```

```json
{"feed": "store", "id": "ev_01J…", "op": "add-document", "doc": "sha256:3f9a…", "content": "sha256:…"}
{"feed": "store", "id": "ev_01J…", "op": "add-layer", "layer": "sha256:8b21…", "content": "sha256:…", "text": "sha256:…", "view": "native"}
{"feed": "store", "id": "ev_01J…", "op": "set-metadata", "doc": "sha256:3f9a…", "metadata": "sha256:…"}
{"feed": "store", "id": "ev_01J…", "op": "set-primary", "content": "sha256:…", "layer": "sha256:8b21…"}
{"feed": "store", "id": "ev_01J…", "op": "supersede", "old": "sha256:…", "new": "sha256:…"}
{"feed": "store", "id": "ev_01J…", "op": "withdraw", "doc": "sha256:…", "reason": "clawback"}
{"feed": "workspace", "id": "ev_01J…", "op": "add-layer", "layer": "sha256:…", "content": "sha256:…", "text": "sha256:…", "view": "ocr"}
```

A cursor is a pair of event IDs, one per feed. This is the one requirement
indexing places on ingestion, which is still to be designed: **every change
to what is in the matter appears as an event in the provenance log**,
including the ones that remove things.

`idx changes` is also useful on its own. "What came in since yesterday?" is
a list made from `add-document` events.

### What each event does to an index

| Event | fields | keyword, semantic |
|---|---|---|
| `add-document` | add a row | nothing new to index if its content's layers are already indexed. Otherwise the document counts as unextracted until a layer arrives |
| `add-layer` | update coverage | index the text, unless its text hash is already indexed |
| `set-metadata` | update the row | nothing |
| `set-primary` | nothing | update the primary pointer table. No reindexing |
| `supersede` | redirect the old row to the new | nothing: the content is unchanged |
| `withdraw` | delete the row | delete the text's rows if no other remaining document shares the content |

**Withdrawals are removals, not hidden rows.** When a document is clawed
back, its text should not stay findable in a derived index inside the
container. The index deletes it, and the query-time check (above) drops it
even before the index has caught up.

### Each index keeps its own cursor

Every index is a directory with a manifest:

```json
{"type": "index", "v": 1, "name": "semantic", "kind": "semantic", "settings": {"model": "…", "model_hash": "sha256:…", "passage_tokens": 300, "overlap": 50}, "cursor": {"store": "ev_01J…", "workspace": "ev_01J…"}, "updater": "idx-semantic"}
```

- The cursor is advanced in the same transaction as the rows for the events
  it covers. A crash or an interrupted update leaves the index consistent,
  and the next update resumes where it stopped. Writes are idempotent
  (keyed by text hash), so redoing a batch is harmless.
- `settings` are the index's identity, like the fields of a layer object.
  If the tokenizer, the normalization or the embedding model changes, the
  settings no longer match the tool's, and the index refuses to answer and
  says it needs a rebuild. It never mixes vectors from two models or tokens
  from two tokenizers.

### When updates run

No single trigger is enough, so there are three, all cheap when there is
nothing to do:

1. **At session start.** The container's entry point runs
   `idx update --background`. This catches up on everything the host
   ingested while no session was running.
2. **Before every search.** `search` compares the end of each change feed
   with the index's cursor, which costs a file stat and reading one line.
   If the index is behind:
   - `fields` and `keyword` catch up first, then answer. Their cost is in
     proportion to the new text, so this is usually a short delay.
   - `semantic` answers from what it has, reports `pending`, and starts a
     background update if one isn't running. It also searches the pending
     text by keyword and says so, so a new document isn't invisible while
     it waits for embedding.
3. **On request.** `idx update` runs now, in the foreground. Claude uses it
   after making a derived layer, or when the user says new documents have
   arrived.

Watching the log for changes (inotify) was considered and left out. It
doesn't work reliably across `podman machine`'s file sharing on macOS and
Windows, and checking at query time covers the same cases.

Claude can't react to events as they happen, but it doesn't need to here:
the next search catches up, and `idx status` tells it, and the user, how
far behind anything is.

### Checking

Cursors can only be as good as the logs. `idx verify` compares the full set
of documents and text hashes in the store with what each index holds, and
reports or repairs any difference. It runs at session start after the
update when it is cheap enough (for `fields` and `keyword`, it is), and on
request for `semantic`. A `dropped` count in a search header is a reason to
run it.

## Managing indexes

```
idx status                     each index: settings, cursor, pending, coverage, size
idx update [<name>...] [--background]
idx verify [<name>...] [--repair]
idx rebuild <name>
idx changes [--since <cursor>]
idx new <name> --kind <kind> [--settings …]      e.g. a stemmed keyword index
```

Indexing runs in the container (*decided*; see [Decided](#decided)).
Indexes live in the workspace, under `.adhoc/indexes/<name>/`, and are left
out of git: they are large, and they can always be rebuilt. `idx status` is
the place to look when Claude needs to explain to the user why a search may
be incomplete.

## Claude's own indexes

Claude can build an index for a task, such as a table of every invoice
number and amount, or embeddings of only one custodian's email. To stay up to
date with the matter, it uses the same protocol as the default indexes:

- a directory under `.adhoc/indexes/` with a manifest
- an `updater`: a command that reads change events as JSON Lines on stdin,
  applies them, and records the new cursor
- optionally a `verify` command

`idx update` and `idx status` then treat it like any other index. Nothing
requires this. An index made for one question can just be a file Claude
throws away afterwards. The protocol is there for the ones worth keeping.

## Searches and the audit log

[TOOLS.md](TOOLS.md#audit-log--proposed) calls for a record of which
searches were run. `search` runs in the container, so a log it writes is
inside Claude's reach. For the record to defend work product, it would need
to be written on the host. That belongs with the audit log design. Until
then, a search saved as a list keeps its query, its index settings and its
`as_of`, which is enough to rerun it.

## Decided

- **Indexing runs in the container,** not on the host during ingestion.
  Keeping it off the host keeps the host-side code small, as Principle 2
  suggests, and lets Claude's own indexes use the same mechanism. A damaged
  index can only cause misses, which `idx verify` finds. If catching up at
  session start proves too slow for large matters, that is a performance
  problem to solve in the container, not a reason to move indexing.

## Decisions to make

- **Embedding model.** It must run on a CPU in the container, have a license
  that allows redistribution in the image the user builds, and handle long
  legal and business text. Candidates should be measured for speed and
  quality on representative documents before choosing.

## Decisions to confirm

- **No stemming by default** in the keyword index.
- **Tags read live** from the annotation log at query time, not indexed.
- **Every layer indexed,** keyed by text hash, so primary changes and
  duplicates cost nothing.
- **Keyword catches up before answering; semantic answers with `pending`**
  and searches the pending text by keyword.
- **Search results as list files,** with the results display as a preset of
  the list display.
- **Withdrawals delete index rows,** not just hide them.
