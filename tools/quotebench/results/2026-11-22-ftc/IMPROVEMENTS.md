# What to improve, from the FTC run's transcripts

Four reviewers each read one cell's whole task turn: the commands, their
output, and the subagents' calls where they are interleaved. This list adds
what they found to what the [report](REPORT.md) already says. Claims were
checked against the code or the matter where that was quick, and say so.
Inference is marked.

Within each group the items are ranked by how much they cost the cells.

## Reading documents (every cell worked around these)

1. **`text` can't read part of a long document.** It has no page range, no
   section and no page count (checked: `--path`, `--json` and
   `--past-screen` are its only options). So no cell used it for reading:
   - **Cells 1 and 3** used `text --path` and then `sed`, `grep` and Read on
     the raw store files: cell 1 23 times, cell 3 throughout.
   - **Cell 2's readers** looped a homemade `q.py page` over 270 pages,
     restarting it per page, then dumped whole documents to scratch files to
     read in slices of about 28 KB.
   - **The cost:** the read log stayed empty, so `page ref add --from`'s
     default ("what you read last") had nothing to look in, and most of
     cell 2's $10.67 of Sonnet went on reading whole documents (inference).

   Wanted: `text <file> --pages 41-43`, a page count, an outline of headings
   with their pages, and a way to read a long document in order without
   writing a loop.
2. **File names aren't accepted, or shown, everywhere.**
   - `layers d09379bencoinitialdecisionpublic.pdf` fails with "matches 0
     objects" (cell 3), though `--in` takes file names.
   - Search hits in `--by hit` mode name the document only by its hash
     (checked). Cell 1 wrote `s.py` and `s2.py` just to put names on hits.
   - `search all` puts the name inside a nested `doc` object, so a cell
     looked for the locator under the wrong key and got "not a locator".

   Wanted: a file name wherever a document is asked for, and the file name
   in every hit.
3. **Printed page numbers.** Citations need the printed page; the store
   knows PDF pages.
   - **Briefs and the decision.** Each reader worked out its own offset
     (decision +6, complaint counsel's brief +11, its reply +14, Schein's
     +7). Schein's reply has no single offset, because a proposed order sits
     inside it.
   - **Transcripts.** Cell 1 only found the `page:line` labels (`"pl":
     "5229:1"`) after three failed attempts at grep and awk. They aren't
     documented. Cell 4's transcript reader counted footers from hard-coded
     starting pages instead, "right to within a page".
   - **The memos** cite PDF pages, or printed pages marked as estimated.

   Wanted: printed page labels detected per page, then used in hits, in
   `text` and in citations, with `page` and `pl` documented.
4. **Page furniture inside passages.** Running headers ("PUBLIC"), transcript
   footers ("For The Record, Inc. (301) 870-8025 … Trial - Public Record"),
   footnote numbers run into words ("policy.146"), and once a footnote's
   whole text all end up inside references.
   - **How much:** 21 of cell 4's 83 references that show words (checked).
   - **What's lost:** the dry run says when a page break falls inside, but
     the resolved row drops that flag, so the lead never saw it.
   - **What breaks:** the same furniture breaks matching of opening and
     closing words, as do line-end hyphens, curly quotes and capitals. Cell
     2's `find` failed on these repeatedly.

   Wanted: furniture marked in the text layer and left out of what's shown;
   the page-break flag kept in resolved rows; passages that stop before a
   footnote.

## The page tool

5. **PAGES.md tells Claude to write scripts.** This is the sentence: "rather
   than use it, Claude writes scripts to find where a paragraph ends and
   builds locators by hand; `--from` is for saying which passage". It was
   meant as the history behind `--from`, but it reads as advice. Cell 3
   followed it: it wrote `resolve_refs.py` and never tried `--from`.
   Rewrite it as the instruction it should be.
6. **Ranges from a script skip the ambiguity check.** Cell 3's script took
   the first match of each opening. 32 of 140 rows had several matches (up
   to 7), and the one it checked had the wrong one, the Steadfast answer
   for the PGMS one. The other 31 went in unexamined. The words are right,
   but the page cited may not be where it read them (inference). `--from`
   would have listed the candidates. Wanted: `page ref add` saying when a
   row's text also occurs elsewhere in the document, whatever made the row.
7. **Short quotations typed into a page aren't checked.** The lint warns
   only at 5 words or more (`UNBACKED_WORDS = 5`, checked). Cell 3's
   invented "everyone says no" got through. Cell 4's lead found about 25
   short phrases with its own regex. Wanted:
   - a lower threshold, with a way to mark a defined term;
   - a way to show a few words of an existing reference as a label, so a
     table label needn't be typed.
8. **The lead can't see a reader's passages.** A resolved row has only the
   first and last few words. There's no command to print the passages by
   id, and the instructions say not to read back. Cell 4 wrote
   `showrefs.py` (57 KB of output for 152 passages), after reading all 591
   notes (about 100 KB). Wanted: `page ref show <resolved files> <ids>`.
9. **Adding only the references a page uses takes a script.** "Add its
   `parts/<name>.resolved.jsonl` with `--batch`", done literally, adds 591
   references, 437 of them unused. Cell 4 pulled the ids out of the page with
   a regex and gathered their rows by hand. Wanted: `--batch` over several
   resolved files that adds only the ids the page cites.
10. **Readers make far more than is used.** Cell 4 asked for 25–60 per
    reader; they made 67–134, and 26% were used. Cell 2 asked for 40–80;
    they made 94–131, and 20% were used. Handbacks ran 14–38 lines against
    "about 15". The instructions should put the cap in rows and lines, and
    the reader should say how many it dropped.
11. **`page check` line numbers are off** by the number of front-matter
    lines (4 in cell 3, checked in the code: it counts after stripping the
    front matter). Cell 3 printed two blank lines before finding the right
    ones.
12. **Slow and unclear tail:**
    - `page check` takes 24–32 s, and `--show` about 48 s.
    - `--show` doesn't print the quotation's text, so cell 3's filter for
      page-number artifacts found nothing and gave false comfort.
    - `page view` took 47–69 s to fail with "the viewer isn't running". It
      should fail at once.
13. **`--batch` errors.**
    - "row needs hash prefix too short: sha256:d6a0138" is garbled and
      doesn't say the minimum (8 hex digits).
    - Every error repeats the row's note.
    - reader.md asks for a hash prefix, though `--in <file name>` works.
    - One preview's "under" heading was a transcript Q/A line.

## Search

14. **Unknown operators are read as words, silently.** `"Patterson" NEAR/5
    "dismissed"` was parsed as `(patterson AND "near 5" AND dismissed)`
    (checked) and gave 688 hits. Wanted: an error naming `w/5`.
15. **Tables of contents swamp phrase hits.** The first of 165 hits for
    `"buying group" w/10 Schein` in one document were its table of contents.
    After that, cells 2 and 3 searched with grep instead. Wanted: a way to
    leave out contents and index pages, or rank them last.
16. **Big listings.** `search all` is 18.7 KB for 91 documents. Search
    results and Read windows were over the size limits several times and had
    to be re-run. Wanted: a compact listing (file name, pages, date).

## Quotations without the page tool

17. **Nothing checks typed quotations.** Cell 1 made no check after writing.
    Its reviewer found about a dozen quotations in the memo that appear in no
    tool output in the transcript, including the misquoted Sullivan
    sentence. The likely source is memory of this public case, or a saved
    32 KB output it saw only a 2 KB preview of (inference). Most of those
    quotations are still verbatim, which is why nobody would notice. Wanted:
    freecheck as a session tool (`quote check memo.md`) that the no-tool
    instructions require before a memo is done. It is also worth having with
    the page tool, for typed text.
18. **A cell's own rendering added errors.** Cell 2 built its memo from its
    rows with its own `render.py`, which collapses whitespace. Every
    line-end hyphen in a quotation therefore came out as "word- word" ("no-
    GPO", "co- owner", "pre- existing", "long- running", "four- month").
    This is the page tool's display rule, rebuilt without the hyphen
    handling. It argues for a plain-Markdown export from rows, usable
    without pages.

## The corpus and the bench

19. **Transcript dates.** Volumes 18–20 are dated 2020-08-04, the posting
    date, not the trial days in February 2019. Cell 4's transcript reader
    caught it.
20. **The model in the bucket.** The code is done; uploading the bundle is
    still to do (QUOTEBENCH.md has the commands).
21. **Setup for driven runs:**
    - Start each cell on its own branch.
    - Have the user approve pushes up front.
    - Give each reader its own scratch folder. Cell 1's reviewer even lost a
      file to another reviewer in a shared scratchpad.
    - Make the instructions' tool list match what is on PATH; the
      `reader` agent stayed in the no-tool cells.

## What worked, and should stay

- **References were exact every time.** `--batch` start/end rows (142 in
  cell 3), the readers' dry-run loops (591 rows in cell 4) and `--from`
  (cell 4's lead) all came out exact.
- **Confirm previews settled ties in one edit.** Their first and last
  words, the words around them and the heading let a reader pick in one
  edit.
- **Coverage was reported honestly.** Search coverage headers (`exhaustive`,
  `coverage`) made every memo say what it hadn't searched.
- **`cite-style: filename`** gave the file-name citations the task asked for.
- **A shared brief helped readers line up.** Evidence keys like
  `[BP-FEB2013]` let different readers' notes line up by exhibit.
