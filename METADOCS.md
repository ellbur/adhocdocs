# Documents and Metadocuments

*Status: built. Grains and units are in
[`metadocs.py`](src/adhocdocs/metadocs.py), splitting merged PDFs in
[`split.py`](src/adhocdocs/split.py), bundles in the annotation log
([`annotations.py`](src/adhocdocs/annotations.py)), and they reach search
(`--grain`), the graph (`--grain`), pages (metadocument references), tags
and notes (bundle targets), the `meta` tool, and the viewer's side panel.*

Where one document ends and the next begins is partly arbitrary. Is an
email with three attachments one document or four? A PDF that someone made
by merging six letters is one file and six documents. The reply above the
quoting line is a different thing from the whole email, which repeats
three earlier messages. Two custodians' copies of a memo are two files and
one memo. Most review platforms fix one answer at ingestion and give no
way past it: you can't tag part of a document, or tag a set of documents
as one thing.

## Documents are the atoms

A **document** is what ingestion decided was one, and that is not open to
interpretation afterwards ([LOCATORS.md](LOCATORS.md)): every locator names
a document or a place in one, and tags, notes, layers and Bates numbers
attach to documents. That fixed level is what lets everything else refer
to anything.

A **metadocument** is any other boundary drawn around content. There are
endlessly many, so they are not listed, stored or indexed. Each is worked
out when someone asks for it, from the documents and what is known about
how they relate.

**Why not index metadocuments?** An index exists for speed, not to say what
the data is. Keyword, semantic and near-duplicate indexes stay on
documents. Search results are then *re-read* at the scale the question
needs, so a new kind of metadocument costs a function, not a re-index.

## Two kinds

**Derived**, at a *grain*, by a rule:

| Grain | A unit is | From |
|---|---|---|
| `family` | a document with its parent and attachments | the store's parent links |
| `thread` | an email conversation, with copies | threading headers |
| `copies` | one item, however many copies: duplicates, the same text in another format, the same message from several custodians | content hashes, text hashes, Message-IDs |
| `versions` | every draft, revision and copy of a text | near-duplicates (estimated: [GRAPH.md](GRAPH.md#how-documents-relate)) |
| `new` | what one document adds: a message's text without the part it quotes; a revision's changes over the version before it | the email extractor's `quoted` segments; the versions, compared word by word as `analyze redline` does |

A unit is named from *any* document in it: `thread:3fa2…` means "the
thread 3fa2… is in", so a reference never needs the representative.
Tools report the canonical name, which uses a representative: the family's
top item, the thread's first message, the earliest copy or draft.

**Grains joined.** Grains combine with `+`: `thread+family:D` is
everything reachable from D through its thread or its family, step after
step, until nothing new joins. An attachment brings its email, the email
its thread, and the thread's other emails their attachments. So a search
`--grain thread+family` gives one line per conversation, counting hits in
the attached documents too. `ann tag hot thread+family:D` tags the
conversation and everything attached to it, and `view meta
thread+family:D` lists each email in date order with its attachments under
it. Any of `thread`, `family`, `copies` and `versions` combine, in any
order (`thread+family+copies` adds every copy of each). The name is
written in that order, after the earliest document. `meta of DOC` and
`view meta DOC --of` include a document's thread with its attachments
when that is more than its thread or its family alone.

**Recorded**, as a **bundle**: documents, or ranges in them, that someone
chose to treat as one thing ("the Q3 board pack", the pages of a merged
PDF that were one letter, a thread as it stood when produced). A bundle has
a name, parts, a summary, an author, and where it was made from
(`ann bundle "Chase" thread:3fa2…` records `from: thread:…`). It lives in
the annotation log, so it syncs, replays and keeps its history like
everything else. `bundle:NAME` names it.

**Why bundles, and not tags on a "set" document?** A synthetic document
would be a document ingestion didn't make, which breaks the one fixed
level. A bundle sits beside the documents, like an entity, and points at
them.

## Where the scale changes something

**Search.** `search "past due" --grain new` reports one line per thing
someone wrote, not per file. Copies count once. A hit in quoted text goes
to the message that wrote those words: the quoting messages are listed in
`quoted_in`, and the hit points at the original. To find the writer, the
words around the hit (within the same quoted paragraph) are looked up in
earlier messages of the thread, in their own text. When the original isn't
in the matter, the hit goes to the earliest message that quotes it, marked
`quoted`. So an email quoted in 40 replies is one result, not 41.
`--grain thread`, `family`, `copies`, `versions` and `bundle` group
results the same way; each line lists its documents and their hits.

**Revisions.** For a document that quotes nothing but revises an earlier
one (a draft and its revision, found as `versions`), `new:D` is what the
revision changed: the passages inserted or reworded since the nearest
earlier version with different text. `meta show new:D` says which version
that is (`over`). Search at `--grain new` treats kept text the way it
treats quoted email: a hit in words the revision carried over counts for
the version that first had them, with the revision listed in `kept_in`. So
a phrase in all five drafts of a memo is one result, at the draft that
wrote it, and a phrase only the last draft has is a result for that
draft. A revision that only removed text adds nothing, and has no parts.

**Tags and notes.** Tags and notes could always target a range inside a
document. Now a target can also be a bundle: `ann tag hot bundle:"Board
pack"` is one application, on the bundle as a whole, and a note on a
bundle is about the set. For filters (`search --tag`, `ann find`, lists),
a document has the tags of every bundle it or part of it is in. A
metadocument used as a target (`ann tag responsive thread:3fa2…`) means
each of its parts, like `--family`. Use a bundle when the set is the
thing being judged, and the expansion when each document is.

**Citations.** A quotation is the same whatever it is quoted from: the text
has to contain the words, and nothing outside them matters. A citation
that isn't a quotation is different, because its scope tells the reader
what to look at. A page can cite a metadocument
(`page ref add page.md chase thread:3fa2…`, then `:cite{#chase}`): "the
thread “RE: past due” (4 messages, Mar. 9–17, 2026)". The reference keeps
the documents the metadocument held when it was cited. If a reply arrives
or the bundle is edited, `page check` reports it re-anchored, so the
author looks before `page refs update` accepts the new list. Export
provenance lists every document. A metadocument can't be quoted, since it
has no single text: quote a passage of one of its documents.

**The graph.** `graph … --grain thread` (and `view graph`) draws each
thread, family, item, text or bundle as one node. Edges inside a unit are
dropped, and edges from several of its documents merge into one, with how
many and each one's evidence. A diagram then shows "the thread" with the
facts that rest on it, rather than eleven emails.

**The viewer.** A document pane's side panel lists the bundles the document
is in: their tags (also shown as dashed ◫ chips), their notes, and links to
the other parts. A bundle's ranges in the document are marked. The user can
write a note on a bundle from there.

**Its parts in a pane.** `view meta REF` shows a metadocument's parts in a
list pane, with a linked preview, in the order that reads best at its
grain:

- a thread in reply order, each message indented by its depth, with its
  copies under it;
- a family from the top item down, attachments indented under their parent;
- copies and versions oldest first;
- what a message adds, passage by passage;
- a bundle's parts as recorded, each saying whether it is a whole document,
  pages or a passage.

Two more views start from a document. `view meta DOC --of` shows
everything it is in (its thread, family, copies, versions and bundles), a
group each, so "what is this part of?" is one pane. `view meta DOC
--splits` shows its recorded splits ([below](#splitting-a-merged-pdf)), a
group per split and a row per piece, so the user can step through the
pieces with the preview. `--splits NAME` shows one split. `--list FILE`
keeps any of these as a list file.

The user can open the same panes without asking. In a document pane:

- the link bar ends with *Everything it's part of…*;
- each bundle in the side panel has *list parts*;
- a split's pieces come under one heading, *Split "letters" · 7 pieces ·
  list them*;
- the ⋯ menu has *What it's part of* and, when there are any, *Its splits*.

The viewer asks the host tool for the pane (`adhoc meta-pane`, which reads
only, as the viewer's own tags and notes go through `adhoc
annotate-as-user`). The list opens beside the document, with that document
as its preview, so picking a row opens it where the user was reading.

**Everywhere else.** A metadocument goes where a locator goes: `text
new:3fa2…` prints a message without its quotes, and `view open thread:3fa2…`
opens the thread's first message. `meta show REF --list FILE` writes a list
of its parts, and `meta of DOC` lists every unit and bundle a document is
in.

## Splitting a merged PDF

A scan of a stack of papers, or a PDF someone made by merging files, is one
document to the store and several to a reader. Where the pieces are is a
judgment, not a fact: a letter with its enclosures is one thing or three, a
contract with its exhibits one or many, a fax cover sheet a piece of its
own or part of what it covers. Different questions want different cuts.
So a split is a metadocument: nothing about the document changes, and
someone who wants to keep a split records it as bundles of page ranges,
one per piece. A document can have several splits side by side ("letters",
"exhibits"), each named.

Finding the cuts by hand is slow, so `meta split DOC`
([`split.py`](src/adhocdocs/split.py)) proposes one. But the detector
advises; Claude decides, and the user's word beats both. Claude can
take any split as it comes, adjust it, or make its own (see *Claude's
judgment* below).

**What the detector weighs.** Each page after the first is weighed for
and against starting a new piece. Two kinds of evidence count. The first
is whether the page *announces* a start:

| For | Against |
|---|---|
| printed page numbers starting again at 1, or the page before saying it was the last ("2 of 2") | page numbers carrying on (2 → 3), or "1 of 3" before |
| an opening: letterhead, a date line, "Dear …", a memo's TO/FROM, a printed email's From/Sent/To/Subject, a caption, a form's title ("INVOICE", "PURCHASE ORDER") | a page numbered 2 after an unnumbered one: a letter's second page |
| a closing on the page before: "Sincerely", a signature, "cc:", "Enclosure" | a sentence running on from the page before |
| a running header or footer changing or stopping | the same running header or footer; "continued" |
| the same letterhead as an earlier first page; a page with no text before; scanned pages after native ones | |

The second is whether the page *looks like the pages before it*, which
often decides it when nothing announces a start: the middle of one
document followed by the middle of another, or a scan whose second letter
has no letterhead.

- **Words.** Each page's words are weighted by how rare they are in the
  document, so what every page says (the parties' names, in a batch about
  one dispute) counts for little. The two pages before a candidate are
  compared with the two after it. Sharing the rarer words and reference
  numbers ("PO-BMS-26-0112", "quarantine") is a sign against a cut; having
  almost nothing in common is a sign for one.
- **Layout.** The left margin, type size and line spacing, from the word
  positions. Laid out alike is a sign against; set differently is a sign
  for. The right margin isn't used, because ragged text and tables move it
  too much within a document. A page laid out like the one two back is a
  book's facing page, not a new document.
- **The page images.** Each page is drawn small (24 dpi) from the original,
  when the original is here. A limited copy doesn't fetch it for this.
  - A different page size (legal after letter) counts for a cut, and so
    does different paper: the background's colour or tone, which in a scan
    tells a tinted or greyed sheet from white. Turning to landscape counts
    a little, since tables do it within a document.
  - The top of the page is compared as a grid of ink, so a letterhead
    counts even when it's a logo with no text. It counts when the top
    looks like an earlier first page's and not like the pages in between.
    A running header is on every page, so it doesn't count, but a
    letterhead is only on the first.
  - A sentence that seems to run on onto a sheet of another size or paper
    counts for little.
  - `--pages` reports each page's size, paper colour and ink.
- **Both together.** When neither the words nor the layout look like the
  page before, that counts extra. A lowercase start after an unfinished
  line then counts for little, because that is also what two documents'
  middle pages look like.

The signs add up to a strength from 0 to 1, and every candidate comes with
its reasons and its similarity figures (`like_previous`: `text`, `around`,
`shared`, `layout`). Three levels give three splits, coarse to fine:
`sure`, `likely` (the default) and `possible`. The output says how many
pieces each level would make, and which candidates the chosen split
passes over. Each piece gets a label (its Re: line, its title, or its first
line) and a kind (letter, memo, email, form, filing, fax). Where its
printed numbering disagrees with its length ("its pages say 2, it has 3"),
the piece says so, which usually means a cut is off by a page.

**Claude's judgment.** A score is no substitute for reading, so Claude has
what it needs to look for itself and decide:

- `--pages 5-9` gives each page's edges: its printed number, header and
  footer, first and last lines, how like the page before it is, and the
  case for a cut there. Claude can read the cut points without reading the
  whole document, and `text DOC#page=7` reads a page in full.
- `--sheet FILE.png` draws pictures of the pages, small and numbered, in a
  grid (16 to a file), for what text can't show: letterhead art, paper,
  handwriting, stamps, a page upside down.
- `--cut 12`, `--join 7` adjust a level's split, and `--at 3,6,9` cuts
  exactly there.
- `--plan FILE` (or `-` for stdin) is a split of Claude's own, one JSON
  object a piece in order: `{"pages": "1-2,7", "label": "…", "kind": "…",
  "summary": "…"}`. A piece can be several ranges (a misfiled page, an
  enclosure scanned out of order). Pages in no piece (slip sheets, blank
  backs) are reported, and two pieces of one split can't share a page:
  another way of grouping is another split. Beside each piece, the output
  gives what the detector made of its first page, so a disagreement is
  visible.
- In code, `adhocdocs.split` has `pages`, `boundaries`, `similarities`,
  `pieces` and `from_plan`, for a judgment that needs a rule of its own
  ("each claim form starts with its barcode sheet").

Claude looks for itself where the evidence is mixed or thin, where the
user has said what they want ("the letters, each with its enclosures"),
and wherever a cut would change a review call.

**Keeping a split.**

- `--list FILE` writes the pieces as a list of page ranges: `view list
  FILE` steps through them with a preview, so the user can look at each
  cut.
- `view meta DOC --splits` shows the recorded splits the same way.
- `--record NAME` keeps the split as bundles named "DOC · NAME k/n ·
  label", each with `split: {of, name, piece, pieces}`. A piece can then be
  tagged, noted, cited or searched (`--grain bundle`) as one thing.
  `--replace` re-records a split, and Claude can't replace the user's.
  `meta splits DOC` lists a document's splits.
- `meta split --find` lists the documents that look like several: paged
  documents with a candidate at `--level` or stronger, most pieces first.

In the viewer, a document's side panel lists the pieces it is split into
("letters 3/7 · PURCHASE ORDER …"), with their pages. A click goes to the
piece's first page.

The detector reads text and word positions, so it works on native PDFs,
and on scans once they are OCRed, and small pictures of the pages for
their size, paper and letterhead art. Claude can look at bigger ones
(`--sheet`). In the corpus, the detector finds the seven documents of a
PDF made by merging them. It finds the seam between the middle pages of
two court opinions, where nothing announces a start. It leaves statutes,
opinions and the Federal Register whole at the `likely` level. In the
federal rules it cuts at the table of contents and the appendix, which is
a fair split too.

## Rules

- Bundles follow the annotation rules: Claude can't edit or delete a bundle
  the user made or edited, or delete one that has the user's tags or notes.
  Deleting a bundle removes its tags, notes and filings.
- Privilege calls can't be on a bundle, even by the user. A production
  withholds and redacts documents, so the call goes on the parts
  (`ann tag privileged bundle:B --parts`). Productions read a bundle's
  other tags as tags on each part.
- Two installations that make bundles of the same name get one bundle with
  both sets of parts, reported as a conflict.
- A purge hides a bundle's parts that are in purged documents, like an
  entity's evidence. When a replacement supersedes a document, the
  replacement joins the bundles the old document was in.

## Not built yet

- The detector seeing handwriting, stamps, or a change of typewriter or
  printer. It weighs page size, paper and letterhead art from the page
  images; for the rest Claude looks at the pictures (`--sheet`) and cuts
  by them.
- Choosing which version a revision is compared with. It is the nearest
  earlier one with different text; a revision of a revision of a branch
  may want another.
