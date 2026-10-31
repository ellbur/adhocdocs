# The Entity Layer

*Status: built. The entity list is in the annotation log
([`annotations.py`](src/adhocdocs/annotations.py)), aliases are matched by
[`entities.py`](src/adhocdocs/entities.py) and
[`viewer/shell/entities.js`](viewer/shell/entities.js), suggestions are
detected by [`detect.py`](src/adhocdocs/detect.py) in the `entities`
index, and the viewer shows all of it.*

Reading a document, you meet people, companies, defined terms and invoice
numbers you half remember. You can ask Claude "who's Raymond Voss?", and
since Claude can read the selection, "who's this?" works too. The entity
layer removes one more step: names in a document are marked, hovering one
shows what it is, and clicking one selects it so you can ask Claude about
it. When Claude answers, it records the answer on the entity, so the next
hover reminds you.

## What you see

In a document pane (page, flow or text mode):

- **Known entities** are underlined. Hovering one shows a card: its name and
  kind, a line or two on what it is (the *summary*), what Claude last told
  you about it (the *recap*, dated), links to the words that define or show
  it (*see*), and who added it.
- **Suggestions** have a dotted underline. They are names the rules found
  that nobody has added yet. A suggestion's card says what it seems to be and
  why: a possible person (with the evidence: a title, a signature line, a
  role beside the name), an organization, a defined term with what it
  means, or a document number with what the text calls it. It also says how
  many documents have it.
- **Clicking** a mention selects it. `view selection` then reports the
  entity (or the suggestion) with the selection, so "who's this?" needs no
  more pointing.
- **`e`** adds the selection, or the clicked suggestion, to the entity list,
  with an optional description in your own words. On a known entity, `e`
  edits it.
- **`E`**, or the counter in the pane's header ("3 known · 12 suggested"),
  cycles between all, known only, and none.

## When entities are detected and defined

Detection and definition are separate problems with separate answers.

**Detection is done ahead of time, by rules, at indexing.** The worry with
detection is speed: anything that runs when you open a document gets in the
way. So it never runs then. The `entities` index runs the detector once per
text when documents are indexed (at the start of every session, in the
background, like the other indexes), and writes its results where the
viewer reads them directly. Opening a document only reads a small file and
matches the known entities' aliases, which takes milliseconds.

The detector is rules, not a model. Measured on the sample corpus it runs
at about half a million characters a second (whole matter: 1.5 seconds), so
it is effectively free; a model would cost seconds to minutes per thousand
documents for the same job, and would need shipping in the image. Rules
also say why they took something, which matters when the user is deciding
whether to trust a suggestion. What they find:

| Kind | Rule | Example |
|---|---|---|
| person | a name with evidence ([`names.py`](src/adhocdocs/names.py)): a title, an initial, a role, a signature, "By:" | "R. Ames, driver" |
| organization | capitalized words ending in a company form | "Harrowgate Fastener Supply Co." |
| defined term | `(“Seller”)` after a name, or `“Delivery” means …`, and every later use in the same document, with what it means | "Seller": Harrowgate Fastener Supply Co., an Ohio corporation |
| number | letters, a hyphen, and digits | "INV-2026-0347" (and "INV-2026/0347"), "PO-BMS-26-0112" |

Defined terms are the clearest win: a contract defines its own vocabulary,
so the definition comes free with the detection, and hovering "Buyer" on
page 9 tells you who the Buyer is. Terms are local to the document that
defines them.

Suggestions only make a name pointable and say what it seems to be. They
never become entities by themselves.

**Definition is done when there is something to say, by whoever knows it.**
An entity is defined (added to the list, with a summary) in three ways:

1. **The user asks about it.** "Who's this?" Claude answers in the
   conversation, then records the entity with a summary and a recap of the
   answer. This is the main path. The definition is written when someone
   wanted it, and it carries what Claude found.
2. **Claude identifies key players** in an analysis (a cast of characters,
   a chronology, a summary of the dispute). It adds the handful that matter,
   ten or so, with a summary each, and says so in a line. It doesn't add
   every suggestion: a list that holds everything is as useless as no list.
3. **The user marks one** with `e`, with or without a description. One
   without a description can be described later ("describe the entities I
   added").

**Discovery** comes from the suggestions themselves: the dotted underlines
and the header counter appear on the first document opened, and the card
says how to use them. No extra step is needed, such as a button that runs
detection.

## The entity list

Entities are kept in the annotation log, with tags and notes, so they have
authors, history and the same write path: the user's through the viewer,
Claude's through the annotation API.

| Field | |
|---|---|
| `name`, `kind` | person, organization, term, identifier, contract, event, place, thing, other |
| `aliases` | other ways it is written. The name is always one. Each one says who added it |
| `summary` | what it is, in a line or two |
| `recap` | what Claude last told the user about it, dated |
| `see` | locators to the words that define or show it, checked and fingerprinted like a tag's target |

**Rules** (enforced in the write path, like the other annotation rules):
each field remembers who set it last. Claude can't change a field the user
set, remove an alias the user added, or delete an entity with any of the
user's work in it. It can fill in what the user left blank: an entity the
user marked with only a name is Claude's to describe. Two entities of one
kind can't share a name, because a second "Raymond Voss" is almost always a
mistake. Give one a name that tells them apart.

**Summaries and recaps are Claude's paraphrase** (Principle 5): typed
quotations are warned about, as in notes. The document's own words go in
`see`, or in the definition a detected term carries, which the card shows
from the layer.

## Finding names in text

The same rules in Python (`ann entity --mentions`, `ann entities --doc`) and
in the viewer, held together by
[`tests/entity_match_cases.json`](tests/entity_match_cases.json):
case and diacritics are folded, whitespace matches any whitespace (names
wrap across lines), matches start and end at word boundaries, an alias with
a capital in it needs a capital in the text ("Brown" isn't "brown"), and
the leftmost longest match wins. A mention can name more than one entity
when they share an alias.

## For Claude

```
ann entities [--doc D --suggested]      the list, or a document's entity layer
ann entity <id|name> [--mentions]       one entity, where it is mentioned
ann suggestions [--kind person]         detected candidates nobody has added, most documents first
ann define-entity NAME --kind … --alias … --summary … --see LOC
ann edit-entity REF --recap …           remember what you told the user
view selection                          the selection, with "entity" when it is one
```

## Not built yet

- Joining suggestions across spellings ("T. Kettering", "Mr. Kettering",
  "Thomas Kettering") uses a simple key per spelling. `analyze people` joins
  them properly; Claude uses it when adding people.
- Spreadsheets in grid mode have no entity layer.
- Entities with no name-like form (an event, a meeting) are only reachable
  by their aliases. Mentions like "the March 9 call" need an alias that
  says so.
