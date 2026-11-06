# Tracing a fact

*Status: implemented in [`trace.py`](../src/adhocdocs/trace.py) (its docstring
is the spec) as `analyze trace`.*

In litigation, *when did they know* often decides the case: when the
buyer learned the pallet was sitting in its own quarantine bay, or when
the seller learned the buyer had it. Answering that by hand means reading
every thread, every draft and every set of notes, and keeping a chart.
A trace does the legwork. It follows one fact through the matter: where it
appears, how it got from each place to the next, who had it when, and
where it turns up with nothing in the matter to explain how.

```
analyze trace DOC#char=… [--query Q] [--include LOC]… [--exclude LOC]… [--list F]
analyze trace --text "…" …
analyze trace --again F --exclude LOC      # the same trace, judged further
view trace F                               # drawn as a time DAG
```

## Describing the fact

A fact travels in other words more often than in the same ones. Kettering
writes that José "put it in Q-3"; Müller's notes say "unlabeled pallet in
Q-3"; the litigation hold says "the pallet held in quarantine bay Q-3"; the
seller's sales rep reads "they admit the HFS pallet sat in their Q-3".
So a trace takes the fact in up to three ways, and they combine:

- **A seed passage** (a locator, or `--text`). Its words are aligned with
  every text that has most of its rarest words, and the best windows are
  kept when enough of its words are there in order: `verbatim`, or
  `edited` (a revision, a reworded quote) with a score.
- **A query** (`--query`, search's query language) that describes the
  fact: `pallet AND (q-3 OR quarantine)`. Hits near each other make a
  passage, widened to its sentence (a row, in a spreadsheet), and each is
  an appearance that `mentions` it. A query finds what's reworded; it also
  finds what isn't the fact (a contract clause that lets the buyer
  quarantine pallets), and that is what `--exclude` is for.
- **Judgment.** `--include LOC` adds a passage that says the same thing
  in other words (`reworded`). With the embedding model installed, the
  trace offers semantically similar passages as `candidates` to judge;
  they never join on their own.

Claude does the describing and the judging: picks the seed, writes the
query, reads the appearances, and excludes what doesn't belong.
`--list F` keeps the trace as a list file, its header holding the seed,
query, inclusions and exclusions, so `--again F` makes it again with more
judgments added.

## Routes

Each appearance gets a date: when the message was sent or posted, when
the email carrying an attachment was sent, else the file's own date. A
file with no date (a receiving log, notes) is dated by the latest full date
its passage writes, and says so (`not before a date it mentions`): a lower
bound, not a time. Then a route, the first of these that holds:

| Route | What it means |
|---|---|
| `quoted` | in a quoted part of a reply or forward, from the message it quotes (or **from a message that isn't in the matter**: a gap) |
| `copy` | the same message or file as an earlier appearance (another custodian's copy) |
| `attached` | an attachment: the same file, or a version of one, seen earlier; or its sender already had it |
| `draft` | kept from an earlier version of the document |
| `knew` | whoever wrote, sent or held it had already had it, from an earlier appearance (a firmly dated one if there is one) |
| `earliest` | the first appearance |
| `unexplained` | none of these: someone had it, and nothing in the matter shows how. **A gap** |
| `unplaced` | dated only by what it mentions, or not at all, and no route fits: no gap claimed |

**Gaps** are the leads: a phone call, a meeting, a document never
collected. Tracing "the unclaimed pallet picked up on March 4" through the
test corpus finds the seller's revised collection memo of 3/18 as its
first firm record on that side, with nothing showing how its author knew.
The memo says the carrier told the sales rep by phone on 3/17, and the
sales rep's call notes have it, but they're undated, so the trace can't
place them. That's the trail a lawyer would follow: whether the notes
were written that day, and who else heard.

## Who knew when

Each appearance names the people it reached: `wrote` (its author), `sent`
(passed it on: quoted, forwarded or attached it), `received` (the
recipients, a chat's other members) and `held` (it's in their files).
People are matched across email addresses, display names, chat authors and
custodians (`kettering-t` is Thomas Kettering when one known person
fits).

`people` gives each person's first exposure: the first firmly dated one,
with the appearance, role and route. An earlier record dated only by what
it mentions is given beside it (`maybe_earlier`). An exhibit list naming
a 3/2 email isn't evidence that its holder knew on 3/2.

## The time DAG

`view trace F` draws a trace as a **time DAG**, not a timeline. A timeline
puts every event on one line, as if each followed from the one before.
Most don't: Kettering's note to the carrier and Voss's collection memo
happened weeks apart in different companies, and neither had anything to
do with the other. A trace already knows what came from what, so the
diagram draws only that:

- **An event** is a document with its copies and its other passages: one
  box, with its date (`≥` when only a lower bound) and who sent it to whom.
- **An edge** is a route: `quoted`, `attached`, `kept from a draft`, or
  `via Kettering` (whoever wrote or held the later one had it from the
  earlier). A fan-out is labeled once.
- **Columns follow the edges, not the calendar.** An event sits one step
  after the latest event it came from, so two events in the same column
  aren't claimed to be at the same time, and an event further right isn't
  claimed to be later than one it has no path from.
- **Strands** are the groups of events joined by edges: the subplots.
  Each is drawn apart, under the names of the people in it, with no order
  between strands. A record that joins nothing (an exhibit list, undated
  notes) is a strand of its own, which is itself worth seeing.
- **A gap** is a dashed `?` leading into the event nothing explains.

It is a pane like `view graph`'s: a click on an event previews its
passage in the linked document pane, and `]`/`[` step through the events,
strand by strand. The diagram is made again from the list's header, so it
follows the latest judgments (`analyze trace --again F --exclude …`).

## Limits

- Routes are worked out from what the matter has: threads, copies,
  attachments, versions and people. A route says the record is
  consistent with it, not that it happened that way: Müller may have heard
  about Q-3 in the hallway before Kettering's email.
- Paraphrases are found only by the query or by judgment, or by the
  embedding model when it's installed.
- A person's possession of a loose file is dated by the file, which may be
  long after they first had it.

For contrary claims, who holds which and who changed their story, see
[DISPUTES.md](DISPUTES.md): a dispute is traces, one per side.
