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
- Whatever the user points at must be **inspectable by Claude**. The current
  selection, highlight, or view is state that Claude can query, so "summarize
  what I've selected" just works.

Rule of thumb: GUI elements are for *identifying* and *viewing*; Claude is for
*doing*.

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

## Open questions

- **Seamless containers.** The container that enforces isolation
  (Principle 2) should be close to invisible to the user. How to achieve that
  while respecting Anthropic's rule against proxying Claude Code is not yet
  worked out.
