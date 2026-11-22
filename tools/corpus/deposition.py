"""Deposition transcript of Thomas Kettering (fictional), 25 numbered lines per page.

build_deposition(path) writes the PDF and returns an index of page:line
locations for phrases that the tests care about.
"""

from __future__ import annotations

import textwrap

import pymupdf

from common import FONT_DIR, FONTS, LETTER, save_pdf

WIDTH = 58  # characters per transcript line
LINES_PER_PAGE = 25


def caption_lines():
    c = [
        "        AMERICAN ARBITRATION ASSOCIATION",
        "         COMMERCIAL ARBITRATION TRIBUNAL",
        "",
        "HARROWGATE FASTENER SUPPLY CO.,  )",
        "                                 )",
        "             Claimant,           )",
        "                                 )  AAA Case No.",
        "     v.                          )  01-26-0004-1187",
        "                                 )",
        "BELLWETHER MARINE SYSTEMS, INC., )",
        "                                 )",
        "             Respondent.         )",
        "_________________________________)",
        "",
        "",
        "        DEPOSITION OF THOMAS KETTERING",
        "",
        "              Norfolk, Virginia",
        "          Tuesday, April 28, 2026",
        "                 9:34 a.m.",
        "",
        "",
        "",
        "Reported by: Linnea Fairbrother, RPR, CCR",
        "Harbor Court Reporting, Job No. 26-0428-HCR",
    ]
    assert len(c) == 25
    return c


def appearance_lines():
    a = [
        "APPEARANCES:",
        "",
        "  On behalf of the Claimant:",
        "     EVELYN STROUD, ESQ.",
        "     Pellham & Stroud LLP",
        "     41 East Gay Street, Suite 900",
        "     Columbus, Ohio 43215",
        "     estroud@pellhamstroud.example",
        "",
        "  On behalf of the Respondent:",
        "     MARCUS DELACROIX-HALE, ESQ.",
        "     General Counsel",
        "     Bellwether Marine Systems, Inc.",
        "     2200 Industrial Parkway",
        "     Norfolk, Virginia 23502",
        "",
        "  Also present:",
        "     Raymond Voss (Harrowgate Fastener Supply Co.)",
        "",
        "                    I N D E X",
        "  WITNESS                                  PAGE",
        "  THOMAS KETTERING",
        "    Examination by Ms. Stroud                 3",
        "    Examination by Mr. Delacroix-Hale         7",
        "",
    ]
    assert len(a) == 25
    return a


# Testimony: (speaker, text). Speakers: Q, A, or a colloquy/parenthetical label.
TESTIMONY = [
    ("()", "THOMAS KETTERING, having been first duly sworn, was examined and testified as follows:"),
    ("hdr", "EXAMINATION"),
    ("hdr2", "BY MS. STROUD:"),
    ("Q", "Good morning, Mr. Kettering. Please state your name and your position for the record."),
    ("A", "Thomas Kettering. I'm the receiving supervisor at Bellwether Marine Systems in Norfolk."),
    ("Q", "How long have you held that position?"),
    ("A", "Six years in April. Before that I was a dock lead."),
    ("Q", "What does the receiving supervisor do?"),
    ("A", "I run the four docks. I check what comes in against purchase orders, I sign for freight, and I "
          "decide what goes into quarantine."),
    ("Q", "Who else is allowed to sign a carrier's delivery receipt?"),
    ("A", "Me, or whoever is posted as acting lead on the roster. They're supposed to print their full name."),
    ("MS. STROUD", "Let's mark this as Kettering Exhibit 1."),
    ("()", "(Kettering Exhibit 1 marked for identification: Dock receiving log, February 2026.)"),
    ("Q", "Do you recognize Exhibit 1?"),
    ("A", "Yes. It's our Dock 4 receiving log for February."),
    ("Q", "Please look at entry RCV-26-0224-07. What does it say?"),
    ("A", "February 24, 14:52, Tri-County, one pallet, unlabeled, no PO reference, no packing list. "
          "Quarantine, Q-3. Received by J. Ibáñez."),
    ("Q", "That's José Ibáñez?"),
    ("A", "Yes. He's a dock associate on Dock 4."),
    ("Q", "Where were you at 2:52 p.m. on February 24?"),
    ("A", "In Chesapeake, at a forklift-safety recertification. I left at about 12:30."),
    ("Q", "Who was acting lead that afternoon?"),
    ("A", "José was."),
    ("MS. STROUD", "Exhibit 2, please."),
    ("()", "(Kettering Exhibit 2 marked for identification: Receiving Discrepancy Report RDR-0224-02.)"),
    ("Q", "This is a discrepancy report, number RDR-0224-02. Whose handwriting is on it?"),
    ("A", "Mostly José's. The line at the bottom, \"seen TK 3/2,\" is mine."),
    ("Q", "Read the notes section for me, please."),
    ("A", "\"Driver in a hurry, would not wait for supervisor. Driver had me sign his handheld, I wrote TK "
          "because Tom is the supervisor of record.\""),
    ("Q", "So the initials TK on the carrier's receipt were written by Mr. Ibáñez?"),
    ("MR. DELACROIX-HALE", "Objection. Calls for speculation. You can answer."),
    ("A", "That's what his note says. I didn't write them. I wasn't there."),
    ("()", "(Kettering Exhibit 3 marked for identification: Tri-County delivery receipt, PRO 0047-2219-6.)"),
    ("Q", "Exhibit 3 is the Tri-County delivery receipt. It shows a delivery at Dock 4 at 2:47 p.m. on "
          "February 24. Do you have any reason to think that delivery didn't happen?"),
    ("A", "A pallet came off that truck. The log shows that. What I don't know is whose it was. "
          "There was no PO and no packing list on it."),
    ("Q", "The wrap said \"HFS,\" didn't it?"),
    ("A", "José's report says the wrap was printed HFS on two sides. I didn't see it until March 2."),
    ("Q", "You know HFS is Harrowgate Fastener Supply?"),
    ("A", "I know that now. Harrowgate's own truck brings most of their freight. I didn't connect a "
          "Tri-County pallet with no paperwork to them."),
    ("Q", "What did you do on March 2?"),
    ("A", "I emailed Tri-County dispatch and asked them to come get it."),
    ("()", "(Kettering Exhibit 4 marked for identification: Email, T. Kettering to TCFL dispatch, March 2, 2026.)"),
    ("Q", "Is Exhibit 4 that email?"),
    ("A", "Yes."),
    ("Q", "You didn't call Harrowgate?"),
    ("A", "No. I didn't know it was Harrowgate's. Our rule is no PO, no receipt. That's Section 6 of "
          "the supply agreement, as I understand it."),
    ("Q", "Did anyone in procurement tell you, before March 4, that a Harrowgate order was overdue?"),
    ("A", "No. Zoë asked me on March 10 to check. That was the first I heard of it."),
    ("Q", "And what did you tell her?"),
    ("A", "That there was no receipt for her PO, and about the pallet in Q-3. As far as I knew, the "
          "shipment was never received. Not against that PO."),
    ("Q", "But a pallet with Harrowgate's name on it sat in your quarantine cage from February 24 "
          "to March 4."),
    ("A", "Yes. Eight days."),
    ("MS. STROUD", "Mark this as Exhibit 5."),
    ("()", "(Kettering Exhibit 5 marked for identification: Invoice INV-2026/0347.)"),
    ("Q", "Have you seen invoice INV-2026/0347 before today?"),
    ("A", "Only when Marcus showed it to me. Receiving doesn't get invoices."),
    ("Q", "The invoice lists PN 44-B/7, 12,000 units. Did anyone open the Q-3 pallet to see if it "
          "held 44-B/7 bolts?"),
    ("A", "No. Quarantined freight isn't opened until it's identified."),
    ("Q", "Where is that pallet today?"),
    ("A", "My understanding is Tri-County still has it in Richmond."),
    ("MS. STROUD", "I'll pass the witness."),
    ("hdr", "EXAMINATION"),
    ("hdr2", "BY MR. DELACROIX-HALE:"),
    ("Q", "Mr. Kettering, did you sign the delivery receipt marked as Exhibit 3?"),
    ("A", "No."),
    ("Q", "Did you authorize anyone to sign your initials?"),
    ("A", "No. Acting lead signs in their own name."),
    ("Q", "When the pallet was returned to Tri-County on March 4, had anyone at Bellwether matched it to "
          "purchase order PO-BMS-26-0112?"),
    ("A", "No. The shipment was never received against that PO, in our system or on paper."),
    ("MR. DELACROIX-HALE", "Nothing further."),
    ("MS. STROUD", "Nothing further. We'll read and sign."),
    ("()", "(Whereupon, at 11:02 a.m., the deposition was concluded.)"),
]


def render_lines():
    out = []
    for who, text in TESTIMONY:
        if who == "hdr":
            out.append(" " * ((WIDTH - len(text)) // 2) + text)
        elif who == "hdr2":
            out.append(text)
        elif who in ("Q", "A"):
            first = f"     {who}.   "
            ls = textwrap.wrap(text, WIDTH, initial_indent=first, subsequent_indent="")
            out.extend(ls)
        elif who == "()":
            out.extend(textwrap.wrap(text, WIDTH, initial_indent="          ", subsequent_indent="     "))
        else:
            out.extend(textwrap.wrap(f"{who}:  {text}", WIDTH, initial_indent="          ", subsequent_indent=""))
    return out


CERT = [
    "             REPORTER'S CERTIFICATE",
    "",
    "     I, Linnea Fairbrother, Registered Professional",
    "Reporter, certify that the witness was duly sworn by me,",
    "that the foregoing is a true record of the testimony",
    "given, and that I am neither counsel for, related to,",
    "nor employed by any party to this action.",
    "",
    "     Dated April 30, 2026.",
    "",
    "",
    "               /s/ Linnea Fairbrother",
    "               Linnea Fairbrother, RPR, CCR",
] + [""] * 12


def build_deposition(path):
    body = render_lines()
    pages = [caption_lines(), appearance_lines()]
    for i in range(0, len(body), LINES_PER_PAGE):
        chunk = body[i:i + LINES_PER_PAGE]
        pages.append(chunk + [""] * (LINES_PER_PAGE - len(chunk)))
    pages.append(CERT)

    mono = str(FONT_DIR / FONTS["mono"])
    doc = pymupdf.open()
    for pno, lines in enumerate(pages, 1):
        p = doc.new_page(width=LETTER.width, height=LETTER.height)
        p.insert_text((500, 60), f"Page {pno}", fontname="mono", fontfile=mono, fontsize=11)
        p.draw_rect(pymupdf.Rect(80, 80, 560, 712), width=0.8)
        p.draw_line((84, 80), (84, 712), width=0.4)
        y = 100
        for n, text in enumerate(lines, 1):
            p.insert_text((58, y), f"{n:>2}", fontname="mono", fontfile=mono, fontsize=11)
            if text:
                p.insert_text((92, y), text, fontname="mono", fontfile=mono, fontsize=11.5)
            y += 24.8
        p.insert_text((150, 745), "HARBOR COURT REPORTING  (757) 555-0110", fontname="mono", fontfile=mono, fontsize=9)
    save_pdf(doc, path, "Deposition of Thomas Kettering - April 28, 2026", "Harbor Court Reporting", "20260430120000")

    # Index: page:line of phrases (searched within single lines).
    def find(substr, nth=1, all_=False):
        hits = []
        for pno, lines in enumerate(pages, 1):
            for n, t in enumerate(lines, 1):
                if substr in t:
                    hits.append(f"{pno}:{n}")
        return hits if all_ else hits[nth - 1]

    # phrase spanning a line break: report start-end
    def find_span(phrase):
        flat = [(pno, n, t) for pno, lines in enumerate(pages, 1) for n, t in enumerate(lines, 1)]
        res = []
        for i in range(len(flat) - 1):
            a, b = flat[i][2], flat[i + 1][2]
            joined = a + " " + b.lstrip()
            k = joined.find(phrase)
            if k != -1 and k < len(a) and phrase not in a and phrase not in b:
                res.append(f"{flat[i][0]}:{flat[i][1]}-{flat[i + 1][0]}:{flat[i + 1][1]}")
        return res

    idx = {
        "pages": len(pages),
        "examination by Stroud begins": find("BY MS. STROUD:"),
        "examination by Delacroix-Hale begins": find("BY MR. DELACROIX-HALE:"),
        "exhibit 1": find("Exhibit 1 marked"),
        "exhibit 2": find("Exhibit 2 marked"),
        "exhibit 3": find("Exhibit 3 marked"),
        "exhibit 4": find("Exhibit 4 marked"),
        "exhibit 5": find("Exhibit 5 marked"),
        "'never received' on one line": find("never received", all_=True),
        "'never received' across a line break": find_span("never received"),
        "'José' (accented)": find("José", all_=True),
        "'Q-3'": find("Q-3", all_=True),
        "'44-B/7'": find("44-B/7", all_=True),
        "objection": find("Objection"),
        "'Eight days'": find("Eight days"),
    }
    return idx
