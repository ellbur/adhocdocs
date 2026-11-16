# Guide

The commands below run from a clone of the repository, on the sample matter
made in the [README](../README.md)'s setup.

## Working with Claude

Talk to Claude in plain language. It knows the tools (a guide is built into
the container) and will open things in the viewer as it goes. Some prompts
to try on the sample matter:

**Finding things**

- *"What is this dispute about? Give me a short summary with the key documents."*
- *"Find every document that mentions invoice INV-2026-0347 and list them in date order."*
- *"Who signed for the delivery, and what evidence is there that the goods arrived?"*
- *"Search for 'never received' within ten words of 'pallet'. Did you search everything?"*
- *"What did Kettering say in the deposition about the proof of delivery? Show me the page and line."*
- *"Highlight everything in the supply agreement that bears on delivery and risk of loss."* Then step through the passages with the Prev/Next buttons above the document (or `]` and `[`); each one's label says why Claude picked it.

**Organizing**

- *"Make tags hot, privileged and responsive, with hotkeys h, p and r."*
- *"Find the few documents that establish the proof of delivery and save them as a set. Say what each one adds, and show me which others just repeat them."*
- *"What does the set need to show: shipped, delivered, signed for, disputed? Is any of those without a document?"*
- Later: *"Show me the proof-of-delivery set again,"* or *"Tag its key documents as hot."*
- *"Reconstruct the email threads about the past-due invoice. Are any messages missing?"*
- *"Which documents are near-duplicates or versions of each other? Show me a redline of the two memos."*
- *"Who are the people in this case, and how do they relate to each other?"*
- *"Who is José Ibáñez? Show me every document that mentions him."*
- *"Trace the pallet in Q-3: who knew about it, when, and how did each of them hear? Where does it turn up with no record of how it got there?"* ([TRACE.md](TRACE.md))
- *"Bellwether says the shipment was never received; Harrowgate says it arrived. Who says which, and when? Who said both, who changed their story, and who kept to it after a colleague said otherwise?"* ([DISPUTES.md](DISPUTES.md))
- Hover over an underlined name in the viewer to see who or what it is. Click one and ask *"Who's this?"*: Claude answers and records it, so the next hover reminds you.
- *"Add the key people and companies in this dispute to the entities, with a line on each."*
- *"Record who works for whom, who signed what, and who handled the delivery, as links with their evidence."* Then a hover card lists each entity's connections; click one to go on to the next entity.
- *"Draw who is involved in which transactions."* Or: *"Draw Ames's connections, two steps out, with the documents they rest on."*
- *"How is Voss connected to Kettering Freight? Show me the documents for each step."*
- *"Which of the facts we've recorded have no document behind them?"*
- *"Make me a page on Ibáñez: what connects him to the delivery, with the documents quoted."*
- *"Show me Ames's part in this on a timeline."* Each of Ames's links sits at its date; click one to see the document it rests on.
- *"Who in the emails isn't in the entities yet? Add the ones who matter, and draw who corresponds with Kettering."*
- *"Show me this memo's earlier drafts, and the thread this email belongs to."* A document's link bar lists its replies, quotations and versions too.
- *"Search the conversations about the proof of delivery, attachments included, one result per conversation,"* or *"Tag the whole conversation hot, attachments and all."*
- *"What is this document part of?"* A pane lists its thread, family, copies, versions and bundles, each with its parts. Or: *"Show me the whole thread, with the preview."*
- *"Which PDFs are really several documents scanned together? Split scan-batch.pdf into its letters, and show me where you'd cut and why."* Then *"Keep pages 7 and 8 together, put the misfiled page 14 with the first letter, and save that as the letters split."*
- *"Set up issues for delivery, damages and notice, and file the entities and notes under them."* Then press `F` in the viewer to see one issue at a time.
- Hover over a date, in a document or its date strip, to see where it falls: the thread, documents near it, and the nearest events before and after. Click one and ask *"What else happened then?"*

**Writing**

- *"Write a chronology of the dispute as a page, quoting the documents for each entry."* Its entries then appear on the date cards.
- *"Add the turning points of the dispute to the entities as dated events."*
- *"Show me a timeline of the emails, one lane per thread."* Or: *"…of the chronology entries."*
- *"Draft a memo on whether the goods were delivered, with quotations, and export it to Word."*
- *"In the memo, show the signature line of the delivery receipt as a picture, and the aging report rows as a table."*
- *"What is this .dat file? If you can decode it, show me the records as a table, and chart the amounts by month."*

Claude's quotations in pages are references to the documents, not retyped
text. So an export can show where every quote came from, and a quote that
no longer matches its document is flagged.

**Producing**

- *"Flag anything in Delacroix's files that might be privileged, with your reasons, for me to review."*
- *"Produce everything tagged responsive with Bates prefix SMITH, withhold what I tagged privileged, and give me the privilege log."*
- *"Make exhibits for the Kettering deposition from the hot documents, oldest first, and give me the exhibit list in Word."*

**With the viewer.** Select text, a region of a page, or cells in a
spreadsheet, then ask about it:

- *"What is this?"* or *"Find other documents that say the same thing as what I selected."*
- *"Show me the list of hot documents."* Then step through them in the viewer with `]` and `[`.
- *"Make me a review form for these documents with a responsive/not responsive choice."* Then tick your answers in the viewer and ask Claude to record them.

In the viewer, press `?` for its keys. `t` opens the tag palette, tag
hotkeys tag the current selection or document, and `N` adds a note. `W`
moves the focused pane into a window of its own, for example to keep a list
on one screen and its documents on another. Your
tags and notes are recorded as yours, and Claude's as Claude's. Claude
can't change yours.

Everything Claude writes goes to `<matter>/workspace`: pages, lists,
exports and productions. Open them from there on your own machine.

## Using the tools directly

Every tool also works outside a session, on the host, with `--matter` or
`ADHOC_MATTER` naming the matter. Each prints JSON and has `--help`. These
commands exercise them on the sample matter:

```sh
export ADHOC_MATTER=~/matters/demo
uv run idx update                       # build the indexes (semantic search takes a few minutes)
uv run idx status
uv run search '"never received" w/10 pallet'
uv run search 'who signed for the delivery' --index hybrid
uv run search grep 'PN 44-B/7'
uv run search '"never received"' --list nr.list.jsonl
uv run list cat nr.list.jsonl
uv run search '"proof of delivery" OR POD' --list pod.list.jsonl
uv run list condense nr.list.jsonl pod.list.jsonl --out pod.set.list.jsonl   # a key set: repeats folded away
uv run list point pod.set.list.jsonl delivered "The pallet reached Dock 4" && uv run list points pod.set.list.jsonl
uv run list ls                          # the workspace's lists and sets
uv run text "$(uv run search '"never received"' --by hit | sed -n 2p | jq -c .loc)"
uv run analyze threads
uv run analyze groups                   # near-duplicates and versions
uv run analyze people                   # from email headers and names in the documents
uv run analyze people --person ibanez --list jose.list.jsonl   # every document that names him
uv run analyze trace --query 'pallet AND (q-3 OR quarantine)' --list q3.list.jsonl   # who knew, when, and how
uv run view trace q3.list.jsonl                 # the trace as a time DAG: what came from what, in strands
uv run graph path "Raymond Voss" "Rita Ames"   # how two entities connect, with the evidence for each step
uv run graph export --format mermaid           # the entities and their links, for a diagram
uv run search '"past due"' --grain new          # one result per thing written, not per email that quotes it
uv run meta of "$(uv run search '"past due"' | sed -n 2p | jq -r .loc)"   # its thread, family, copies, versions, bundles
uv run page ref add memo.md nr "$(uv run search '"never received"' --by hit | sed -n 2p | jq -c .loc)"
uv run page export memo.md --format docx -o memo.docx      # memo.md quotes with :q{#nr}
uv run page docs memo.md --list memo.list.jsonl            # the documents memo.md cites
uv run adhoc view && uv run view list nr.list.jsonl        # the viewer, outside a session
uv run adhoc ann-api &                                     # the annotation API, then:
uv run ann define-tag hot && uv run ann tag hot <doc>
uv run adhoc status                                        # inside a session: mounts, network, services
```

Tests:

```sh
uv run pytest                           # ADHOC_TEST_SEMANTIC=1 to include semantic search
(cd viewer && cargo test)
```

[tools/corpus/SOURCES.md](../tools/corpus/SOURCES.md) lists every sample
file's source and the facts the tests check. If `tesseract` has no English
data system-wide, put `eng.traineddata` in
`~/.cache/adhocdocs/models/tessdata/`.

