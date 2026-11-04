# The Matter as a Graph

*Status: built. Links and facets are in the annotation log
([`annotations.py`](src/adhocdocs/annotations.py)), the graph is built and
queried by [`graph.py`](src/adhocdocs/graph.py) (`graph` and `view graph`),
and the viewer's entity cards, side panel and facet picker show it
([`viewer/shell/shell.js`](viewer/shell/shell.js)).*

The entity layer ([ENTITIES.md](ENTITIES.md)) answers "who is this?" one
name at a time. A case is rarely about one name. It is about who did what
to whom, on what evidence: Ames signed for the pallet, Ames works for
Bellwether, Voss signed the agreement for Harrowgate, and the delivery
receipt is the document all of that rests on. This layer records those
connections, so the documents read less like a list and more like a
network: from a name to the people and events it connects to, from a fact
to the documents it rests on, and from a document to the facts it
supports.

## Links

A **link** joins two entities with a relation, read from the first to the
second: *Rita Ames — signed for → Dock 4 delivery*. Like an entity, it
lives in the annotation log, with an author and history, and it carries:

| Field | |
|---|---|
| `from`, `to` | the two entities |
| `rel` | what `from` is or did to `to`: "works for", "signed", "sent", "paid", "party to" |
| `see` | the evidence: locators to the words that show it, checked like any target |
| `date` | when, if it happened at a time (`YYYY-MM-DD`, `YYYY-MM` or `YYYY`) |
| `summary` | a line on it, in the author's words (paraphrase: Principle 5) |

**Why entities and links, and not a general graph database.** The
entities are already where the user looks (the hover cards) and where
Claude records what it learns. Making the connections between them the
same kind of thing, in the same log, under the same rules, means they sync,
replay, purge and carry authorship with no new machinery, and the viewer
shows them where the names already are. Events and transactions are
entities of kind `event` or `contract`, so "which people are involved in
which transactions" is a question about links to those entities.

**Rules** (in the write path, like the entities'): each field remembers who
set it, and Claude can't change a field the user set or delete a link the
user made or edited. One link per (from, relation, to): a second is refused
(edit the first), and the same link made on two installations becomes one
on replay. Deleting an entity takes its links with it; Claude can't delete
an entity that has the user's links.

**Recorded and derived.** A link is someone's judgment that the documents
show a relation. Other connections can be computed from the text: an
entity is *mentioned* in a document when its aliases appear there, and two
entities are *co-mentioned* when they appear in the same documents. Those
are useful leads ("Voss and Ames appear together in 14 documents") but
they are not facts, so they are never stored as links. The graph marks
them `derived`, the diagram dashes them, and `graph path` explains a hop
over one as "a lead, not a relation".

**Facts and their documents.** "Which facts are based on which documents"
is the `see` of each link and entity, read both ways. `graph evidence DOC`
lists what rests on a document; a document pane's side panel shows the
same under *Evidence for*, so opening the delivery receipt says that two
links depend on it. `graph unsupported` lists links and entities with no
evidence: the facts the case still needs a document for, or that the user
should be told have none.

## Navigating

- **In the viewer**, the entity card shows an entity's *connections*: each
  link, the entity at its other end, and its evidence. Names inside a
  summary or recap that are themselves known entities are links too.
  Clicking one replaces the card with that entity's, with a way back, so a
  hover on "Ames" can go on to Bellwether, then to the agreement, then to
  Voss, without leaving the document. Clicking *evidence* opens the words
  that show the link. A link with no evidence is marked.
- **For Claude** and the code it writes, the `graph` tool and
  `adhocdocs.graph` treat the matter as nodes and edges:

  ```
  graph show REF            a node and everything joined to it, each edge with its evidence
  graph around REF --depth 2
  graph path A B            how two things connect, hop by hop, each hop with its evidence
  graph evidence REF        what rests on a document; what an entity or link rests on
  graph unsupported         links and entities with no evidence
  graph export --format json|dot|mermaid
  ```

  `--mentions` and `--cooccur` add the derived edges; `--facts` makes each
  link a node of its own, so documents point at the facts they support;
  `--facet` keeps one issue. From Python, `graph.load(m)` returns plain
  dicts (`g.nodes`, `g.edges`) with `around`, `paths` and `subgraph`, so a
  one-off analysis can walk it or hand it to a library.

## An entity's page

`graph page REF` writes a literate page ([PAGES.md](PAGES.md)) about one
entity: its summary (attributed), its connections, each with its evidence
quoted by reference, the dated ones in order, and the documents that name
it most (labeled as found by matching). Because the quotations are
references, the page is checked like any other, its quotes open their
documents, and it exports with their provenance. It is the dossier a
paralegal would assemble by hand, built from what has been recorded, so it
is only as complete as the links are: `graph unsupported` says what still
has no document.

## An entity's timeline

`view timeline --entity "Rita Ames"` lays an entity's links out by date,
in the timeline pane ([LISTS.md](LISTS.md#the-timeline-display)): each link
a chip labeled as a sentence ("Rita Ames signed for Dock 4 delivery"), at
its date, in a lane for the entity at its other end (`--by rel`: a lane
per relation). Clicking one previews its first piece of evidence, so
stepping through the chips walks the entity's story document by document.
`graph timeline REF` writes the same thing as a list file
([`graph.timeline_list`](src/adhocdocs/graph.py)), to keep, edit, or show
with `view timeline FILE`; a list in the workspace also puts its entries on
the date cards ([DATES.md](DATES.md)).

- **A link's date** is its own `date`, or else the date of an event at
  either end: "Ames signed for the Dock 4 delivery" happened when the
  delivery did. An entity with a date and evidence of its own (an event)
  is a chip too.
- **Undated links aren't guessed at.** That an email shows Voss works for
  Harrowgate doesn't mean he started that day, so by default undated links
  are listed after the timeline (the item's `undated` field). `--doc-dates`
  places them at their evidence's date anyway, drawn dashed as approximate
  and saying so in their tooltip.
- **Links with no evidence are left out:** a chip has to open something.
  They are reported (`no_evidence`), as `graph unsupported` reports them.
- More than one entity (`--entity A --entity B`) gives their links on one
  axis; `--facet` keeps one issue.

## Diagrams

`view graph` draws the graph as a diagram in a viewer pane, with a linked
document pane like a list's. It is static SVG (Principle 1: panes are thin),
so it needed nothing new in the viewer but letting plain SVG through the
pane sanitizer: shapes, text and markers, with no scripts, links, images,
`<use>`, `foreignObject` or animation. Claude can draw its own SVG in a
pane or page the same way.

The layout is layered, chosen for reading rather than for looks:

- **By kind** (the default): organizations, then people, then events,
  contracts and the rest, in columns, so "who is involved in which
  transactions" reads left to right. With `--facts`: entities, then the
  facts about them, then the documents they rest on.
- **By distance** (`view graph REF --depth 2`): the entity in the first
  column and each hop out in the next.

An edge that skips a column passes through a waypoint in each column it
crosses, so it runs between boxes rather than through them, and each column
is ordered to cross fewer edges. Boxes and relation labels are the pane's
items: a click previews the evidence in the linked pane, and `]`/`[` step
through them. Links without evidence are red and dashed; derived edges are
grey and dashed.

A force-directed layout was considered and not used: it moves on every
change, it tangles past a few dozen nodes, and it puts nothing in a
predictable place. A diagram the user can learn to read matters more here
than one that looks organic.

## Facets: one issue at a time

As annotations pile up, most of them are about something other than the
question at hand. A **facet** is a named issue ("delivery", "damages",
"notice"), defined like a tag (so a typo can't start a new one), and
entities, links, notes and tags are *filed* under facets. Filing is its own
event, like tagging, not a field of the thing filed:

- Claude can file the user's notes without changing them, and the user can
  file Claude's. Claude can't unfile what the user filed.
- A reply is filed with its thread, and a tag's applications with the tag.

The user picks an issue with `F` or the ◇ button in the toolbar. The viewer
then dims what isn't filed under it: known names in documents, tags and
notes in the side panel (with a count of what is hidden and a way to show
all), and connections on the entity cards. `view state` and `view
selection` report the facet, so Claude knows which issue the user is
working on, and `view facet NAME` switches it for them. Every read takes
`--facet` (`ann notes`, `ann entities`, `ann links`, `graph …`).

Facets were chosen over more tags because tags are about documents
(responsive, hot, privileged) and productions act on them, while an issue
cuts across documents, entities and notes alike, and filing something
under one shouldn't touch a production.

## How documents relate

Documents are joined to each other, not only to entities. Each index
update works out, for every document ([`analysis.doc_links`](src/adhocdocs/analysis.py)):

| Relation | From | |
|---|---|---|
| replies to / replied by | the threading headers | a fact of the documents |
| quotes / quoted in | a later message in the thread holding an earlier one's text | estimated |
| version | near-duplicate texts, or one mostly inside the other, outside a thread: drafts, revisions, re-sends | estimated, with its similarity and containment |
| same text | a copy in another format, or a custodian's copy | |

The near-duplicate index keeps them in `links.json`, which the viewer reads
directly: a document pane's link bar, which already listed the parent,
attachments and duplicates, now lists *Replies to*, *Replies*, *Quotes*,
*Quoted in* and *Versions*, so a thread or a run of drafts can be walked
from the document itself. The graph adds them with `--doc-links` (one step
out from the documents already in it; `--doc-links 2` for two), so `graph
path DOC1 DOC2 --doc-links 3` says how two documents connect, and `view
graph REF --doc-links` draws a document's family, thread and versions.
Replies are drawn solid; quotations and versions dashed, as estimates.

Candidates come from MinHash banding, finer than `analyze groups` (42
bands of 3 rows), because a revision that adds a page can share under half
its shingles with the original and still contain all of it. A short text
inside a much longer one (a paragraph lifted into a brief) can still be
missed; `analyze near DOC` compares one document against everything.

## People from the headers

Someone who appears only in From and To lines, or only in the text, isn't
an entity until someone adds them, so their name isn't marked and the
graph can't say who emailed whom. `ann people` lists the cast (`analyze
people`: addresses joined by display name, names in text joined by surname
and given name) that isn't in the entity list, each with its addresses,
counts, the roles found beside its name, and whether it looks like a
person, a group address (everyone@, a team), an organization, or a bulk
sender (it sends and is never written to: newsletters, notifications).
Only people are listed unless `--all`.

`ann add-people NAME…` (or `--top N`) adds them as entities, with their
display names, the forms the text uses ("T. Kettering", "Mr. Kettering")
and their addresses as aliases, and a summary that is only the counts and
roles, until someone writes a real one. `--known` gives entities already in
the list the addresses and forms they lack. That is what connects an
entity to its email: addresses as aliases mark the headers in the viewer,
and `graph --email` adds who emailed whom from the headers, one edge a
pair with the count each way and the emails as its evidence, and `sent`
and `received` edges to each email. Claude adds the people who matter to
the matter, not everyone in a To line, since a list that holds everyone
is as useless as none ([ENTITIES.md](ENTITIES.md)).

