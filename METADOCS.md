# Documents and Metadocuments

*Status: built. Grains and units are in
[`metadocs.py`](src/adhocdocs/metadocs.py), bundles in the annotation log
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
| `new` | what one message adds: its text without the part it quotes | the email extractor's `quoted` segments |

A unit is named from *any* document in it: `thread:3fa2…` means "the
thread 3fa2… is in", so a reference never needs the representative.
Tools report the canonical name, which uses a representative: the family's
top item, the thread's first message, the earliest copy or draft.

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

**Everywhere else.** A metadocument goes where a locator goes: `text
new:3fa2…` prints a message without its quotes, `view open thread:3fa2…`
opens the thread's first message, and `meta show REF --list FILE` makes a
list of its parts for `view list`. `meta of DOC` lists every unit and
bundle a document is in.

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

- Splitting a merged PDF into its letters automatically. A bundle per
  letter, made from page ranges, works now. Finding the boundaries (a new
  letterhead, page numbering restarting) is a detector still to write.
- `new` for documents other than emails, such as what a revision adds over
  the draft before it (`analyze redline` shows it; it isn't a unit yet).
- Units of units, such as a thread with its attachments' families.
- Viewer panes listing a metadocument's parts directly. Today `meta show
  --list` and `view list` do it in two steps.
