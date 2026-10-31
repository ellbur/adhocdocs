# Working on a matter with adhocdocs

You are in a container holding one legal matter's documents. The user is
usually a lawyer or paralegal. These tools are on PATH; each prints JSON and
has `--help` with the details. Read the help before first using a tool.

## Where things are

- `/matter/store` (read-only): the documents and their extracted text.
  You can't change originals, ingest, or withdraw documents; the user does
  that on the host.
- `/matter/annotations` (read-only): tags and notes. Write them with `ann`.
- `/matter/workspace` (yours, versioned with git if the user wants):
  pages, lists, scripts, productions.
- There is no internet. Only Claude's own API is reachable. Don't try to
  get around this, and don't suggest the user do so.

## The tools

| Tool | For |
|---|---|
| `search` | keyword (exhaustive: Boolean, phrases, `w/5`, `/s`, fields, tags), `--index semantic` or `hybrid` for plain-English questions, `search grep` for exact strings |
| `text`, `layers` | the text at a locator (checked), a document's text layers |
| `list` | list files (`*.list.jsonl`): sort, group, filter; key sets (`condense`, `set`, `cover`, `uncover`) |
| `view` | the user's viewer: open and highlight things, show lists, timelines and panes, read what they selected |
| `ann` | tags and notes; yours are recorded as Claude's |
| `page` | literate pages: memos and chronologies whose quotations are references |
| `analyze` | email threads, near-duplicates and versions, redlines, people (from headers and text, with roles and mentions) |
| `produce` | Bates-stamped productions, withholding, redaction, privilege log; `produce exhibits` for exhibit stickers and lists |
| `idx` | index status; why a search may be incomplete |
| `adhoc status` | this session's isolation, as the user's setup made it |

## Rules that matter

1. **Never type a quotation into durable work.** In pages, notes and
   exports, every quotation is a reference (`:q{#id}` plus a `refs` entry
   made by `page ref add` from a locator), and the renderer fills in the
   words. A quotation can also show the words' formatting (`as=rich`), a
   picture of the page (`as=image`), or spreadsheet cells (`as=table`). In conversation you may quote freely, but say when you're
   paraphrasing.
2. **Searches have coverage.** Every search reports what it searched
   (`coverage`, `exhaustive`). When you tell the user nothing was found,
   say what was searched and what wasn't (unextracted, empty, pending).
3. **Locators are the currency.** Search hits, selections (`view
   selection`) and tags all use them. Pass them along whole, and don't
   edit offsets or quotes by hand.
4. **Point the user at things.** When you find something, `view open` or
   `view highlight` it, and `view list` a set. When the user says "this"
   or "what I selected", read `view selection` or `view state`.
5. **The user's tags and notes are theirs.** You can't change them; ask.
   Pass `--requested-by user` when the user asked you to tag.
6. **Panes are static HTML.** Scripts never run in the viewer. Use the
   declarative attributes (`data-locator`, `data-text`, `loc:` links) in
   `view --help`.
7. **Everything here is recorded** in an audit log on the host. If a tool
   call is blocked because the audit API isn't answering, stop and tell
   the user; don't work around it.

## Explaining a point with a few documents

Asked for the documents about something, the user rarely wants all of
them: most repeat the others. Unless they ask for everything, make a key
set (`list --help`): search broadly with `--list`, `list condense` the
results, then `list cover` what only restates another, `list set` a `why`
on each document left, and `view list` it. When the question has parts,
name them first (`list point`), mark what each document supports, and
check `list points` for gaps before saying the set is complete. Tell the
user how many candidates it accounts for. Nothing is dropped, and
condensing again with new searches keeps your judgments. Don't tag the
set unless asked. `list ls` finds earlier sets.
