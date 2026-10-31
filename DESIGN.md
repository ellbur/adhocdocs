# Design Principles

adhocdocs is a set of tools for working with evidentiary documents from
Claude Code. This document records the principles the tools are designed
around. When a design question comes up, it should be answerable from these.

## 1. The Claude Code prompt is the primary interface

Users accomplish things by telling Claude what they want. The tools exist to
give Claude the capabilities it needs, not to duplicate those capabilities in
a user interface.

- A GUI that shows a list of documents has no search box, filter controls,
  or sort menus. The user asks Claude to search, filter, or sort, and Claude
  does it.
- A GUI *may* provide ways for the user to **point at things**: selecting a
  document, highlighting a passage, marking a page range. These exist because
  pointing is often easier than describing ("this paragraph" vs. "the third
  paragraph on page 14 of the March 3 deposition").
- *Navigating* counts as viewing. For example, clicking a search-result
  snippet to open the document at that spot is fine; it is not an exception
  to this principle. Claude must still be able to see where the user went.
- Whatever the user points at must be **inspectable by Claude**. The current
  selection, highlight, or view is state that Claude can query, so "summarize
  what I've selected" just works.

Rule of thumb: GUI elements are for *identifying* and *viewing*; Claude is for
*doing*.

**Exception: tags and notes.** The user can create, edit and delete tags and
notes directly in the GUI. Rapid review needs one-key tagging, and a note is
the user's own words, so relaying it through Claude adds nothing. Tags and
notes stay fully programmable for Claude. See
[ANNOTATIONS.md](ANNOTATIONS.md).

This applies to displays Claude builds too. Claude acts when the user
prompts it, and it can't react to GUI events as they happen. So a display
Claude builds for a task stays **thin**: it shows things and collects simple
input (pointing, ticking boxes, picking options). Logic stays in the
container, and the prompt remains the main way to interact. See
[VIEWER.md](VIEWER.md#thin-panes).

## 2. Safety and data isolation do not depend on Claude

Principle 1 has one exception. Operations that control what data is reachable
— and therefore what data could leak, be mixed, or be modified — are performed
**outside Claude**, by the user, through mechanisms Claude cannot invoke.

- Connecting a set of documents to a session, disconnecting it, or granting
  write access is done by the user directly, not by asking Claude.
- No tool exposes an API that lets Claude widen its own access. The absence of
  that capability is the safeguard; we do not rely on Claude choosing not to
  use it.
- Because Claude Code can run arbitrary code (see Principle 3), isolation
  cannot be enforced merely by what the adhocdocs tools choose to expose. It
  is enforced at the environment level: Claude Code runs inside a container,
  and the only data it can reach is what has been made available to that
  container from outside. This boundary holds even if Claude bypasses the
  tools entirely.
- Claude may *observe* isolation state (e.g. "which matters are connected?")
  so it can explain it to the user, but it cannot change it.

Rule of thumb: if getting it wrong would expose or contaminate data, Claude is
not in the loop.

## 3. Everything else is programmable

Every capability other than safety and isolation is available to code, not
just to interactive use. If there is a search tool, Claude can write a script
that calls it in a loop, pipes its results into another tool, and so on.

This is the reason the tools are built for Claude Code: it lets Claude choose
the right workflow for the task at hand, on the fly, instead of the tools
enforcing a fixed workflow.

Implications:

- Each capability has a programmatic interface (CLI and/or library) with
  structured, machine-readable input and output.
- GUI state (Principle 1) is part of this: the current selection is readable
  from code, and where it makes sense, code can drive the view (e.g. open a
  document to a page to show the user a result).
- Nothing is available only through a GUI, except the isolation controls of
  Principle 2.

## 4. Composable tools, not a framework

As a corollary of Principle 3, we build small tools that Claude composes as it
sees fit, rather than a monolithic framework that owns the workflow.

- Each tool does one thing and can be used independently of the others.
- Tools communicate through plain, documented data (files, JSON, stable
  identifiers for documents and locations within them) rather than through
  shared in-process framework state.
- Tools do not assume they are called in a particular order or as part of a
  particular pipeline.
- When a new need arises, the default answer is a new tool or a new option on
  an existing one — not a new mode of a central system.

## 5. Quotations that matter are checkable by machine

Claude does not produce text and claim it is verbatim from a document. Where
accuracy matters (pages, exports, anything that may be relied on), quotations
must be checkable automatically. Accuracy comes from how the text gets there,
not from trusting Claude to copy carefully.

- In conversation, Claude may quote and paraphrase freely without claiming
  accuracy.
- In pages and other durable output, quotations are **references** to ranges
  of document text, not text typed by Claude.

The main strategy is **literate pages**, analogous to literate programming.
The page source embeds a reference to a range of text, and the renderer shows
the user the text at that range. The quote is verbatim by construction,
because Claude never typed it.

- Quoted text is rendered so it looks different from Claude's prose, and it
  can be clicked to open the viewer at that location.
- A reference records both its location and a fingerprint of the text it
  expects to find. If the underlying text changes (e.g. after re-OCR), the
  reference fails loudly instead of quietly showing different text.
- Elisions and alterations (ellipses, bracketed insertions, *[sic]*) are part
  of the reference structure and produced by the renderer, never hand-edited
  into the quote.
- When a page is exported to a format that must hold literal text (e.g. a
  brief as .docx), the export resolves each reference into text and keeps a
  record of where every quotation came from.

## Open questions

- **Verbatim relative to what?** *Mostly answered:* layers are literal
  ([TEXTLAYERS.md](TEXTLAYERS.md)), pages apply a small fixed set of display
  rules and mark every other change ([PAGES.md](PAGES.md)), and
  low-confidence OCR is marked so it can be compared with the page image.
- **Unbacked quotes in pages.** *Proposed:* a lint warning.
- **Seamless containers.** *Answered* in [CONTAINER.md](CONTAINER.md).
