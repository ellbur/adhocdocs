# Disputes: contrary claims and who holds them

*Status: implemented in [`dispute.py`](../src/adhocdocs/dispute.py) (its
docstring is the spec) as `analyze dispute` and `view dispute`. Built on
[TRACE.md](TRACE.md).*

A case turns on claims that can't both be true. Bellwether says "the
shipment was never received"; Harrowgate says the pallet was delivered to
Dock 4 on February 24. A hit list for either phrase is a list of
documents. What a reader wants is who says which, when they started, who
says both, who changed their story, and who kept to a story after someone
on their own side had told them otherwise. Kettering told Müller on March
10 about the unlabeled pallet in Q-3. On March 17 she still wrote "the
shipment was never received."

```
analyze dispute --side "never received" --query '"never received" OR "not received"' \
                --side "it arrived" --query '((delivered OR received OR arrived) AND "dock 4") OR (pallet AND q-3)' \
                [--exclude LOC]… [--stance LOC=STANCE]… [--list F]
analyze dispute --again F --stance LOC=reports      # the same dispute, judged further
view dispute F                                      # drawn as a time DAG
```

## Sides

Each `--side NAME` starts a claim. The `--seed`, `--text`, `--query`,
`--include` and `--exclude` after it are that side's. They work as in
`analyze trace`, because each side is traced. The sides are contrary: they
can't all be true. With two sides, denying one is the other's camp. With
one side, a dispute is a claim and its denials ("for" and "not …").

A query describes a side's claim, and a sentence makes the claim when the
query holds within that sentence alone. "I went through the Dock 4 log"
has "Dock 4" but not "delivered", so it isn't the claim that the pallet
arrived. The query language binds `OR` tighter than `AND`, so a side that
is either of two things needs parentheses: `((a OR b) AND c) OR (d AND e)`.

## Statements

Each sentence that makes a side's claim is cut at `;`, `:`, dashes and
"but". "The shipment was never received against our PO, but it may have
been sitting in Q-3" is two statements, one for each side. Each statement
has:

- **A speaker.** This is the email's sender, or the author of a quoted part
  ("On … Zoë Müller wrote:"), or a chat message's author. In a transcript
  it is the witness for an `A.` line and the examining lawyer for a `Q.`
  line. Otherwise it is the file's author, or the custodian whose files it
  came from.
- **A stance**, read from the words around it:

  | stance | | example |
  |---|---|---|
  | `asserts` | for | "the shipment was never received." |
  | `qualifies` | for, hedged | "As far as I knew, the shipment was never received." |
  | `denies` | against | "No pallet … was received at Dock 4" (against "it arrived") |
  | `doubts` | against, hedged | "it may not have been delivered" |
  | `rejects` | against: reported, then contradicted | "Bellwether's assertion that 'the shipment was never received' is contradicted by that receipt" |
  | `reports` | someone else's words | "customer says 'never received'", "Müller wrote on March 9 that …" |
  | `quotes` | a quoted part of a reply | |
  | `asks` | a question | "Did anyone open the Q-3 pallet?" |
  | `supposes` | a condition | "If payment is not received, …" |

  A report is attributed to its subject (a known person, a party such as
  "Bellwether", or a role such as "customer"). It counts toward what the
  speaker heard, not toward what they hold. "I told you …" and "Replied: …"
  are the speaker's own words.

- **A camp**: the side the statement supports, if its stance takes one.

Words decide stances, so some will be wrong. For example, a portal's
"Delivered 'Dock 4'" status line in a file Okafor only kept reads as Okafor
asserting it. `--stance LOC=STANCE` is Claude's or the user's judgment of
the statement at a locator, and `--again` keeps those judgments.

**Copies say nothing new.** The same email in two mailboxes, a letter and
its attachments, and the response forwarded on are all one statement, made
when the original was. A copy only counts as reaching the people it
reached.

## Tensions

- **`both`**: one person on both sides in one document. Kettering's March
  10 reply says there is no receipt for the PO, and that an unlabeled
  pallet from Tri-County went into Q-3. Müller's March 17 email concedes
  the quarantined pallet and still says the shipment was never received.
- **`switch`**: one side, then later the other. Delacroix-Hale's March 27
  litigation hold describes "the pallet held in quarantine bay Q-3 from
  2/24". His April 2 response says "Bellwether never received the goods."
- **`despite`**: someone *kept to* a side after a statement for another
  side reached them. That means they were on the side before, the
  statement reached them (they received it, sent it on, or had it in their
  mail), and they were on the side again afterwards. A statement from a
  colleague (the same email domain) comes first, because the other party
  saying otherwise is what a dispute is. A file in someone's folder doesn't
  count as reaching them, because it says nothing about when they had it.

Dates are the appearances' (TRACE.md). A record dated only by a date it
mentions doesn't place a switch or a "despite".

## The time DAG

`view dispute F` draws every side's appearances as one time DAG (TRACE.md,
"The time DAG"). Each event is coloured by the camps its speakers take, and
its line says who holds what ("Müller: both sides"). A red box is someone
on both sides in one document. A red edge is a switch, or a side kept after
a colleague's statement for another reached them ("still: never
received"). Click an event to see its passage. Its tooltip lists each
statement, with the words that decided its stance.

## What's next

- Claims found rather than given: passages that align as one fact
  (`edited`) but differ in a negation, a number, a date or a name.
- Reports as evidence: what "Müller wrote that …" says about Müller's
  position when her own words weren't collected.
- A stance model for paraphrase and sarcasm. The word rules are honest
  about their limits, and `--stance` is there for when they get it wrong.
