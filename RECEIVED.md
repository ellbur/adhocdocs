# Received productions

*Status: built ([`received.py`](src/adhocdocs/received.py); `adhoc
ingest-production --help`). See [Not built yet](#not-built-yet).*

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

It finds the `.dat` and `.opt` in the volume (`--dat`, `--opt` to name
them), works out which columns are which, and ingests one document per
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

## Not built yet

- IPRO `.lfp` image load files, EDRM XML, and other formats than these.
- **Overlays**: a later `.dat` that corrects or adds fields to documents
  already in. For now the first ingest's fields stay.
- Replacement volumes (the same Bates numbers produced again, say after a
  clawback). They come in as new documents beside the old, and withdrawing
  the old is the user's call (`adhoc withdraw`).
