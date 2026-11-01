# Dates

*Status: built. Dates in text and a document's own date are in
[`dates.py`](src/adhocdocs/dates.py), found ahead of time by the `dates`
index ([`index/dates.py`](src/adhocdocs/index/dates.py)). The viewer's strip
and cards are in [`chrono.rs`](viewer/src/chrono.rs),
[`dates.js`](viewer/shell/dates.js) and [`shell.js`](viewer/shell/shell.js).
Dated events are entities ([ENTITIES.md](ENTITIES.md)).*

Most documents reflect a moment: when an email was sent, when a contract
was signed. They also mention other moments: the date in a quoted email's
header, "the March 9 call", "due 3/7". When you're reading one, you often
want to know where it fits: what came before it and what came after?

## What you see

- **The date strip**, under a document's header, says when the document is
  dated and where that date came from:
  - an email: *Sent*;
  - an attachment: *Attached to an email sent…*, with a link to the email;
  - anything else: *Created* or *Last modified*, from the file's metadata.
  It also shows the file's own created and modified dates when they say
  something the first date doesn't.
- **Dates in the text** have a dashed underline (in page, flow and text
  modes). `E` hides them with the entities, and the header counter counts
  them.
- **Hovering a date**, in the strip or the text, shows a **date card**.
  **Clicking** a date in the text selects it, so `view selection` reports
  the `date` and "what else happened then?" needs no more pointing.

## What the card shows

The hard question is what goes on the card. A timeline of every document
would bury the date. Choosing the important events automatically is too
hard to do well: a rule that ranks documents by importance would be wrong
in ways the user can't see or correct. So the card has two parts, and
neither part judges importance by itself.

**Context that needs no judgment.** It is always there, so the card is
useful on the first hover:

- for a date in the text, the document's own date and how far apart they
  are ("This document is dated Mar 9, 2026, 2 days later");
- for a date in a quoted email's header ("On Mar 9, 2026, Dana Okafor
  wrote:"), that email, found by day and sender. When the matter doesn't
  have it, the card says so, which is worth knowing in itself;
- the document's place in its email thread, with the messages before and
  after it, when the date is the document's own day;
- how many documents are dated within 3 days. Clicking the count opens
  them beside the pane, to step through with `]` and `[`.

**Events someone already judged worth noting.** The card shows the nearest
three on each side of the date, and the ones on the date itself. The
judgment was made somewhere else, by the user or by Claude, for its own
reasons:

| Source | Strength | Shown as |
|---|---|---|
| a chronology entry: a list item with an `event` ([LISTS.md](LISTS.md#the-timeline-display)) | strongest | the entry's own label |
| an event in the entity list with a `date` | | the event's name |
| a document in a key set | | the document |
| a document cited in a page | | the document |
| a document tagged with a tag named like "hot" or "key" | weakest | the document |

Being cited in a page, a chronology or a key set is a cheap, honest signal
of salience: someone thought the document mattered enough to rely on. When
one document comes from several sources, the strongest source names it, and
hovering the row lists them all.

Before any of this exists, the card says where events would come from and
offers a request to copy into Claude's prompt ("Write a chronology of the
key events…"), as the overview does. Discovery doesn't depend on Claude
having guessed what matters.

## Claude decides, when asked

As with entities, Claude can add the events that matter: dated entities of
kind `event` (`ann define-entity "Dock 4 rejection" --kind event --date
2026-03-09 --see …`), a dozen or so when it writes a chronology or when the
user asks. They come with the entity list's authorship and its rules (the
user's fields are the user's). An event's alias makes its mentions ("the
March 9 rejection") pointable too. A chronology list is the other way, and
often the natural one: its entries are events already.

For its own work, Claude has `analyze dates DOC` (a document's date, where
it came from, and its date mentions, resolved) and `analyze around DATE`
(documents, dated events and chronology entries near a date).

## Finding dates

Like the entity layer's suggestions, dates are found **ahead of time, by
rules**, when documents are indexed, so opening a document only reads a
small file. The rules, with examples, are in `dates.py`'s docstring:

- Written dates: "March 9, 2026", "9 Mar 2026", "2026-03-09", "3/9/2026".
- Months: "March 2026".
- Dates without a year: "March 9", and a bare "3/7" only after a word that
  takes a date ("due 3/7", "by 12/31"), so "1/2" is still a half. The year
  comes from the document's date.
- Relative dates: "yesterday", "last Friday", "next Tuesday", "last week",
  "next month", counted from the document's date. ("Today" is left out,
  because it only repeats the document's date.)
- Bare weekdays and days of the month: "on Friday", "by the 15th", only
  after a word that takes a date, and a weekday only when capitalized, so
  "on Fridays" and "the 2nd floor" aren't dates. "Since" looks back; "by",
  "until" and "due" look ahead; anything else takes the nearer one, and
  the card says it was read that way.
- Ranges: "March 2–6, 2026", "March 30 to April 2", "2–6 March 2026" are
  one mention with a first and last day, and the card shows both. A week
  ("last week") is shown as one too, Monday to Sunday.

Some rules are about mistakes the user can't see:

- **Ambiguous numeric dates.** A date like 3/4/2026 is read month first,
  and the card gives the other reading.
- **Placed dates are labeled.** The card says when a date was counted from
  the document's date, so a wrong anchor shows.
- **Metadata dates are named.** The strip names the source of a metadata
  date, because those dates are often wrong (a template's creation date, a
  file copied during collection). A date with its source can be weighed;
  a bare date gets trusted.

Dates without a year and relative dates are stored unplaced, because one
text can belong to documents with different dates. The viewer places them
using the document it is showing. Python and the viewer use the same rules,
and [`tests/date_cases.json`](tests/date_cases.json) keeps them in step.

## Decided

- The card shows only context that needs no judgment, plus events
  someone already marked. Nothing ranks documents by importance.
- Dated events are entities, not a new store.
- Tags count as salience by name ("hot", "key", "important", "critical",
  "significant"). A tag vocabulary has no polarity to read, and "not
  responsive" mustn't count.
- Copies of one email are one message in its thread.
- The dates and fields indexes rebuild themselves when their rules
  change, since nothing about them is a choice and they are cheap to make
  again. Other indexes still ask for `idx rebuild`.

## Not built yet

- File-system dates (the file's modified time when it was collected) aren't
  recorded at ingestion, so they can't be shown.
- A range's card is about its first day: what's dated near the start,
  not across the whole range.
- The day-first or month-first reading of ambiguous dates isn't learned
  from the rest of the corpus.
- Documents ingested before `date_source` existed show "Dated" with no
  source, until they are ingested again.
