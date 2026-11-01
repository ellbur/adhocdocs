# adhocdocs

Tools for working with evidentiary documents from Claude Code. 

The idea is that we don't need a legal tech platform: Claude is already good at doing everything lawyers do. We just need to give Claude a way to display its results and isolate it so it's safe. Then you just interact with Claude like you would normally.

These tools differ from other legal AI software in that they give Claude maximum freedom to customize its workflow, including by writing custom code for the analysis at hand.

You work with Claude in a sealed session that
sees only one matter. You "ingest" outside documents into the matter, and Claude can do whatever it wants with them within the confines of its container.


Claude searches, reads, tags, drafts and builds
productions. A document viewer on your desktop shows you what Claude points
at, and sends back what you select.

It runs on Linux for now. Windows is in progress: see [WINDOWS.md](WINDOWS.md).

## Setting up

You need [uv](https://docs.astral.sh/uv/), which brings its own Python.
Then, from a clone of this repository:

```sh
uv run adhoc setup
```

This checks for everything else first and, if anything is missing, says
what to install:

- rootless [Podman](https://podman.io/), for the session container
- Rust via [rustup](https://rustup.rs/) (the viewer pins its version), clang,
  a C toolchain and WebKitGTK 4.1, to build the viewer
- `tesseract` with English data, to OCR scanned pages when you add documents

Then it builds the viewer and the session image. The first run takes a few
minutes; later runs reuse what's built and finish quickly, so run it again
after pulling changes. `uv run adhoc setup --check` only checks.

The image is built on your machine. It installs Claude Code with
Anthropic's official installer. To pick up a newer Claude Code, run
`uv run adhoc setup --no-cache`.

The commands below run from the clone. A matter can live anywhere.

## Making a matter

A matter is a directory that holds one case's documents, your tags and
notes, and Claude's work. Create it and add documents on your own machine,
outside any session:

```sh
uv run adhoc init ~/matters/smith-v-jones
uv run adhoc --matter ~/matters/smith-v-jones ingest \
    --custodian smith-j --collection smith-laptop-2026-03 /path/to/smith-files
```

Adding documents works for these formats:
- PDF, including scans, which are OCRed
- images
- email (`.eml`, `.mbox`), with attachments
- `.docx` and `.xlsx`
- CSV, HTML and text
- zip archives

Originals are kept exactly as given, and identical files are stored once.
Add more documents at any time; the session catches its indexes up when it
starts. Each `ingest` names the collection the files came from. The
custodian is optional.

To try it out, make a matter from the sample documents in `corpus/`. It
holds a fictional supply dispute, buried the way real ones are in several
hundred irrelevant documents (marketing email, calendar invitations, office
chatter, drafts of unclear origin), plus some real public-domain court and
government documents:

```sh
uv run adhoc init ~/matters/demo
uv run adhoc --matter ~/matters/demo ingest --manifest corpus/manifest.toml
```

[corpus/manifest.toml](corpus/manifest.toml) lists the collections and
their custodians. Write one like it to add several collections of your own
in one command. Paths in it are relative to the manifest. Every entry is
checked before anything is added.

## Starting a session

```sh
uv run adhoc open ~/matters/demo
```

This starts three things:

- **Claude Code**, in a container that holds only this matter. Its
  documents are read-only; its workspace (`<matter>/workspace`) is
  writable. It has no internet access: only Claude's own sign-in and API
  hosts are reachable.
- **The viewer**, a window on your desktop showing this matter's documents.
- **An audit log** on your machine, outside the container, recording every
  prompt, every command Claude runs, and every network connection.

When you leave Claude Code (`/exit` or Ctrl+D), the container and viewer
close. To pick up an earlier conversation, run `uv run adhoc open
~/matters/demo -- claude --resume`.

Indexes build in the background when a session starts. Keyword search is
ready within seconds. Semantic search over a new matter takes a few
minutes; until then, Claude's searches say what they couldn't yet cover.

### Signing in

The first time you open a matter, Claude Code asks you to sign in:

1. Choose to sign in with your Claude account (or an Anthropic Console
   account for API billing).
2. Claude Code shows a link. It can't open a browser from inside the
   container, so open the link yourself, on this computer.
3. Sign in, copy the code the page gives you, and paste it back into the
   terminal.

Each matter keeps its own sign-in in `<matter>/claude-home`, so you sign in
once per matter, not every session. adhocdocs never reads or copies your
credentials. If you use an API key instead, set `ANTHROPIC_API_KEY` before
`adhoc open`; it is passed through and not saved.

**Before you sign in, consider which account to use.** Free, Pro and Max
accounts are under Anthropic's Consumer Terms, which allow chats to be used
for training unless you opt out in your privacy settings. Team, Enterprise
and API accounts are under the Commercial Terms, which don't. Which to use
for client documents is your (or your firm's) decision.

### Working with Claude

Talk to Claude in plain language. It knows the tools (a guide is built into
the container) and will open things in the viewer as it goes. Some prompts
to try on the sample matter:

**Finding things**

- *"What is this dispute about? Give me a short summary with the key documents."*
- *"Find every document that mentions invoice INV-2026-0347 and list them in date order."*
- *"Who signed for the delivery, and what evidence is there that the goods arrived?"*
- *"Search for 'never received' within ten words of 'pallet'. Did you search everything?"*
- *"What did Kettering say in the deposition about the proof of delivery? Show me the page and line."*

**Organizing**

- *"Make tags hot, privileged and responsive, with hotkeys h, p and r."*
- *"Find the few documents that establish the proof of delivery and save them as a set. Say what each one adds, and show me which others just repeat them."*
- *"What does the set need to show: shipped, delivered, signed for, disputed? Is any of those without a document?"*
- Later: *"Show me the proof-of-delivery set again,"* or *"Tag its key documents as hot."*
- *"Reconstruct the email threads about the past-due invoice. Are any messages missing?"*
- *"Which documents are near-duplicates or versions of each other? Show me a redline of the two memos."*
- *"Who are the people in this case, and how do they relate to each other?"*
- *"Who is José Ibáñez? Show me every document that mentions him."*
- Hover over an underlined name in the viewer to see who or what it is. Click one and ask *"Who's this?"*: Claude answers and records it, so the next hover reminds you.
- *"Add the key people and companies in this dispute to the entities, with a line on each."*

**Writing**

- *"Write a chronology of the dispute as a page, quoting the documents for each entry."*
- *"Show me a timeline of the emails, one lane per thread."* Or: *"…of the chronology entries."*
- *"Draft a memo on whether the goods were delivered, with quotations, and export it to Word."*
- *"In the memo, show the signature line of the delivery receipt as a picture, and the aging report rows as a table."*

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

### Afterwards

```sh
uv run adhoc --matter ~/matters/demo audit sessions   # what each session did
uv run adhoc --matter ~/matters/demo audit show --kind prompt --kind tool
uv run adhoc --matter ~/matters/demo audit verify     # the log is intact
```

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

[tools/corpus/SOURCES.md](tools/corpus/SOURCES.md) lists every sample
file's source and the facts the tests check. If `tesseract` has no English
data system-wide, put `eng.traineddata` in
`~/.cache/adhocdocs/models/tessdata/`.

## Design

- [DESIGN.md](DESIGN.md): principles
- [TOOLS.md](TOOLS.md): the tools, and which are built
- [LOCATORS.md](LOCATORS.md): naming documents and places in them
- [TEXTLAYERS.md](TEXTLAYERS.md): extracted text and its structure
- [INDEXING.md](INDEXING.md): indexes, search, keeping indexes up to date
- [LISTS.md](LISTS.md): document lists and the list display
- [ANNOTATIONS.md](ANNOTATIONS.md): tags and notes
- [ENTITIES.md](ENTITIES.md): the entity layer: hover a name to see who or what it is
- [PAGES.md](PAGES.md): literate pages that quote and cite documents
- [VIEWER.md](VIEWER.md): the document viewer
- [CONTAINER.md](CONTAINER.md): the container, sign-in, the audit log, and Anthropic's terms
- [WINDOWS.md](WINDOWS.md): running on Windows (and macOS): the bridge into Podman's VM

The design docs keep the reasons and decisions. Formats, schemas and usage
live with the code: module docstrings and `--help`.
