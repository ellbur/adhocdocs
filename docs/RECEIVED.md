# Received productions

*Status: built ([`received.py`](../src/adhocdocs/received.py); `adhoc
ingest-production --help`, `adhoc overlay-production --help`), with
overlays and replacement volumes. See [Not built yet](#not-built-yet).*

Documents produced to us by another party (opposing counsel, a third party
answering a subpoena, a regulator) arrive as a **volume**: load files that
describe the documents, plus folders of page images, native files and
text. `produce` writes this format for our own productions
([TOOLS.md](TOOLS.md)); this is the other direction. What a received
document is called in the case is its Bates number, so that number must
work as a name everywhere a document ID does.

A typical volume:

```
ACME_VOL001/
  DATA/ACME_VOL001.dat      one line per document: Bates range, family, custodian, dates, paths
  DATA/ACME_VOL001.opt      one line per page image: Bates number, path, document break
  IMAGES/0001/ACME000001.tif
  NATIVES/0001/ACME000042.xlsx
  TEXT/0001/ACME000001.txt
```

## Ingesting

```
adhoc ingest-production ACME_VOL001 --party "Acme Corp." [--check]
```

It finds the load files in the volume (`--dat`, `--opt` to name them): a
`.dat` (or EDRM XML) and an `.opt` (or IPRO `.lfp`), works out which columns are which, and ingests one document per
`.dat` record. `--check` reads everything and reports the column mapping,
the counts and every problem, and ingests nothing: run it first. The
producing party (`--party`) is required, since who produced a document
matters to every later use of it. The volume's name (`--name`) defaults to
the folder's.

Re-running is safe: the same volume gives the same document IDs, so what's
in is skipped and an interrupted ingest finishes, as with `adhoc ingest`.

## Load files

- **`.dat`**: the Concordance format (fields quoted with `þ`, separated by
  `\x14`, newlines inside values written `®`), or a CSV or tab-separated
  file with a header row. Encoding: a byte-order mark if there is one
  (UTF-8, UTF-16), else UTF-8, else Windows-1252, which is what older tools
  write.
- **`.opt`** (Opticon): one line per page: `Bates,volume,image path,Y at a
  document's first page,,,page count`. A `.dat` record's images are the
  pages from its first Bates number to the next document break.
- **`.lfp`** (IPRO), used when there is no `.opt`: one `IM` line per page,
  `IM,Bates,flag,offset,@volume;folder;file;type`. The flag `D` starts a
  document and `C` an attached one; blank carries on. An `FT` line names a
  document's text file, used when the `.dat` gives none.
- **EDRM XML** (version 1.x), used when there is no `.dat`. It holds the
  records and the pages in one file:
  - each `<Document>`'s `Tag`s are its columns (`#BegDoc` is `BegDoc`, so
    the usual names map), and its `DocID` is the control number;
  - its `File`s give the native (with its hash, checked as an MD5), the
    text, and the page images, one file per page or one with a page count;
  - `<Relationship Type="Attachment">` makes the child an attachment of
    its parent.

  `--dat` names the XML file when the volume has several.
- **Paths** are relative to the volume, usually with Windows backslashes,
  sometimes with a leading `\` or `.\`, and often in a different case from
  the files on disk (they were made on Windows). They're matched ignoring
  case when the exact path isn't there.

**Columns** are recognized by their usual names, ignoring case, spaces and
underscores (`BEGBATES`, `BegDoc`, `ProdBeg`, `Begin Bates`, …). Each is
reported, with the columns no rule took. `--map field=Column` overrides one
(`--map begin="Prod Beg No"`). The fields:

| Field | Usual columns | Used for |
|---|---|---|
| `begin`, `end` | BEGBATES, ENDBATES, BegDoc, EndDoc, ProdBeg, ProdEnd | the document's Bates range (`begin` is required) |
| `begattach`, `endattach` | BEGATTACH, ENDATTACH, BegAttach, ParentBates | families |
| `control` | DOCID, ControlNumber, ControlNo | `ctl:` (else the begin number) |
| `custodian` | CUSTODIAN, Custodians, AllCustodians | identity (as `cust:<slug>`) and metadata |
| `native`, `text` | NATIVEPATH, NativeLink, FilePath; TEXTPATH, TextLink, ExtractedText | the files |
| `name` | FILENAME, FileName, DocTitle | metadata |
| `sent`, `received`, `created`, `modified`, `date` | DateSent, DateRcvd, DateCreated, DateLastMod, DocDate | the document's date (and its source) |
| `from`, `to`, `cc`, `bcc`, `subject`, `title`, `author` | the same names, EmailFrom, EmailSubject… | metadata |
| `md5`, `sha1` | MD5HASH, MD5, SHA1HASH | checked against the native |

Dates are read as month first (`03/04/2021` is 4 March 2021) unless
`--day-first`, and `YYYYMMDD` and ISO dates either way. A separate time
column (`TimeSent`) joins its date.

## A record becomes a document

- **The original** is the native file if the record names one that's
  there: that is what was produced natively, and a slip sheet's single page
  image ("produced in native format") isn't the document. Else the page
  images, joined into one PDF in Bates order (TIFF, JPEG, PNG or PDF pages;
  made the same way every time, so the same images give the same bytes).
  Else the text file. A record with none of these is reported and skipped.
- **Identity**: the collection is the volume, the path is the begin Bates
  number, plus the custodian if the `.dat` names one, and the parent for an
  attachment. So the document ID is stable across re-ingests and two
  volumes that produce the same bytes under different numbers stay two
  documents, each with its own Bates numbers (near-duplicate detection
  finds them).
- **Text**: the extraction runs as for any file (a PDF of images is OCR'd).
  The producer's text file becomes a layer of its own, view `produced`. It
  is primary only when our extraction found no text, so what Claude quotes
  is our reading of the original wherever there is one, and searches reach
  the producer's text with `--layers all`.
- **Metadata**: the extraction's, with the `.dat`'s filling what it lacks:
  the file name, from, to, cc, bcc (split on `;`), subject, title, author,
  and the date, whose source is `sent` when the record has a sent date
  (so an email produced as images is dated as an email). The whole record
  is kept too:

  ```json
  "produced": {"party": "Acme Corp.", "volume": "ACME_VOL001", "begin": "ACME000001", "end": "ACME000004",
               "begattach": "ACME000001", "endattach": "ACME000009", "control": "ACME000001",
               "pages": ["ACME000001", "ACME000002", "ACME000003", "ACME000004"], "as": "images",
               "fields": {"BEGBATES": "ACME000001", "…": "…"}}
  ```

  `pages` names each page of the original by its Bates number, in order
  (null for a native, which has one number for the whole file). `as` is
  `native`, `images` or `text`.
- **Families**: a record whose `begattach` is another record's `begin`
  (and isn't its own) is that record's attachment, ingested as its child.
  Parents go first. When the production lists a parent's attachments, the
  parent's native isn't opened for attachments of its own (an email's
  attachments would otherwise come in twice).
- **Hashes**: a native whose MD5 or SHA-1 differs from the `.dat`'s is
  reported (it is still ingested: the file is what was received).

## Bates and control numbers as names

`bates:` locators ([LOCATORS.md](LOCATORS.md)) resolve through received
productions as well as ours: `bates:ACME000003` is page 3 of the document
that begins at ACME000001, and a range resolves to every document it
touches. A native resolves whole. Prefixes may hold any characters
(`ACME_`, `ACME-`, `ACME.`): the number is the digits at the end.

`ctl:<number>` names a document by its control number, or its begin
Bates number when the `.dat` has no control number. `view open`, `text`
and page references take both.

Since these live in the documents' metadata, they sync with the store
like everything else ([SYNC.md](SYNC.md)).

## Problems

Reported by `--check` and by the ingest, which goes on past them:

- a `.dat` record with no begin number, or one already used in the volume;
- files the `.dat` or `.opt` names that aren't in the volume;
- `.opt` documents no `.dat` record begins, and records the `.opt` has no
  pages for (fine when there's a native or text);
- page images that can't be read, and (when ingesting: it reads every
  native) natives whose hashes disagree with the `.dat`'s;
- attachments whose parent isn't in the volume (ingested without a
  parent, and said so).

## Overlays

```
adhoc overlay-production ACME_VOL001_OVERLAY.dat [--volume ACME_VOL001] [--check]
```

An **overlay** is a later load file that corrects or adds fields on
documents already received: a fixed date, a confidentiality designation
added after the fact. It's read like a volume's `.dat` (the same dialects,
encodings, column names and `--map`), with no images or files. Each record
is matched to a received document by its begin Bates number, or by its
control number if it has no begin column. `--volume` is needed only when
two volumes have the same numbers. `--check` reports what would change and
changes nothing; run it first.

- **Merged, not replaced:** each non-blank value replaces the old one, a
  column the volume didn't have is added, and a blank changes nothing.
  Columns are matched to the volume's by field (`Date Sent` overlays
  `DATESENT`), else by name.
- **The metadata follows**, but only what the load file supplied in the
  first place: subject, people, title, author, name and the date (with its
  source) are worked out again from the merged record. What the extraction
  found in the original stays, as on ingest: an email native's own subject
  beats any load file's.
- **Not changed:** the document's files, family and custodian
  (`begattach`, `endattach`, `native`, `text`, `custodian`). They made the
  document what it is (the custodian is part of its identity), so a change
  to them is reported, not applied. A corrected family or file is a
  replacement volume.
- **Kept:** each change, under `produced.overlays` (`{"dat", "at",
  "changed": {column: [old, new]}}`), with `produced.fields` the merged
  record. Applying the same overlay again changes nothing.

The new metadata is a `set-metadata` event like an ingest's, so it reaches
the indexes and syncs ([SYNC.md](SYNC.md)).

## Replacement volumes

```
adhoc ingest-production ACME_VOL001R --party "Acme Corp." --replaces ACME_VOL001 [--check]
```

The same Bates numbers produced again (after a clawback, say, with
redactions added). With `--replaces`, each document in the new volume
supersedes the one in the named volume that begins at the same number: the
old is withdrawn as superseded, references to it follow to the new
([LOCATORS.md](LOCATORS.md)), and `bates:` names the new. The result lists
what was superseded and, under `not_replaced`, the old volume's documents
the new one doesn't have, which stay; withdrawing those is the user's call
(`adhoc withdraw`). `--check` with `--replaces` lists what would be
superseded.

Without `--replaces`, numbers another volume already has are reported, and
both documents are kept.

**Tags and notes come along, marked.** Each superseded document's tags and
notes are copied to its replacement, marked *carried over*: they were
judgments about the old version, and the replacement may differ (a
redaction, a corrected page), so they might not hold for it. The viewer
shows them apart (a `↪` chip, a flagged entry beside the document) until
someone confirms them, which removes the mark. Editing a carried note
confirms it too. `ann carried` lists the ones still marked, and `ann
confirm-carried` confirms one ([ANNOTATIONS.md](ANNOTATIONS.md)).

- A tag or note on a passage moves to where the passage's quote is found in
  the replacement, as references do. A tagged passage the replacement
  doesn't have (redacted, say) isn't carried; the result lists it under
  `annotations_not_carried`. A note on one is carried to the whole
  document, marked that its passage is gone.
- Whose they are: a person's are carried as the person running the ingest
  (`--user`, default `$USER`), since only an installation's own people
  write from it, and the mark keeps whose it was. Claude's stay Claude's, so
  a `check-privilege` flag stays a flag. The old document keeps its own.
- Replies stay under the notes they answer.

## Not built yet

- Other load-file formats: Summation `.dii`, Relativity `.rdo` exports,
  EDRM XML 2.0's packaging. Their records can be converted to a CSV `.dat`
  with the paths, for now.
