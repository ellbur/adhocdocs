"""Generate the synthetic documents of the adhocdocs test corpus.

Story: Harrowgate Fastener Supply Co. (seller, Columbus OH) says it delivered one
pallet under PO-BMS-26-0112 / invoice INV-2026/0347 to Bellwether Marine
Systems, Inc. (buyer, Norfolk VA) on 2026-02-24. Bellwether says the shipment
was never received. An unlabeled pallet sat in Bellwether's quarantine bay Q-3
and was handed back to the carrier on 2026-03-04. All people, companies,
addresses and domains (.example) are fictional.

Run:  uv run --project /home/owen/adhocdocs python tools/corpus/build_synthetic.py
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from email.utils import format_datetime

import docx
import numpy as np
import pymupdf
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from common import (LETTER, FONT_DIR, FONTS, PdfWriter, email_msg, font, out, quote,
                    save_docx_reproducibly, save_pdf, scan_pdf, write_eml)
from deposition import build_deposition

EST = dt.timezone(dt.timedelta(hours=-5))
EDT = dt.timezone(dt.timedelta(hours=-4))


def when(y, mo, d, h=9, mi=0, s=0):
    tz = EDT if (mo, d) >= (3, 8) else EST
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=tz)


def rfcdate(*a):
    return format_datetime(when(*a))


# People ---------------------------------------------------------------
DANA = "Dana Okafor <dana.okafor@harrowgatefastener.example>"
VOSS = "Raymond Voss <rvoss@harrowgatefastener.example>"
AR = "Harrowgate Accounts Receivable <ar@harrowgatefastener.example>"
PORTAL = "Harrowgate Order Portal <noreply@portal.harrowgatefastener.example>"
ZOE = "Zoë Müller <zoe.muller@bellwethermarine.example>"
TOM = "Thomas Kettering <tkettering@bellwethermarine.example>"
MARCUS = "Marcus Delacroix-Hale <mdelacroix-hale@bellwethermarine.example>"
STROUD = "Evelyn Stroud <estroud@pellhamstroud.example>"
TCFL = "Tri-County Freight Lines Dispatch <dispatch@tricountyfreight.example>"
AP = "Bellwether Accounts Payable <ap@bellwethermarine.example>"

HFS = "Harrowgate Fastener Supply Co."
HFS_ADDR = ["1180 Marcourt Road", "Columbus, OH 43219"]
BMS = "Bellwether Marine Systems, Inc."
BMS_ADDR = ["2200 Industrial Parkway", "Norfolk, VA 23502"]

LINES = [  # PN, description, qty, unit price
    ("PN 44-B/7", "Hex flange bolt, M10×1.25×40, 316 stainless", 12000, 2.85),
    ("PN 44-C/2", "Split lock washer, M10, 316 stainless", 20000, 0.19),
    ("PN 17-K.3/A", "Nylon-insert lock nut, M10, 316 stainless", 20000, 0.41),
    ("PN 90-T/12", "Thread sealant, marine grade, 50 ml tube", 120, 11.40),
]
SUBTOTAL = sum(q * p for _, _, q, p in LINES)  # 47,568.00
FREIGHT = 1044.50
TOTAL = SUBTOTAL + FREIGHT  # 48,612.50
assert abs(TOTAL - 48612.50) < 0.001

files_written: list[str] = []


def note(p):
    files_written.append(str(p.relative_to(p.parents[1])))
    return p


def money(x):
    return f"${x:,.2f}"


# ===================================================================== PDFs


def invoice_pdf() -> bytes:
    w = PdfWriter("Invoice INV-2026/0347", HFS, "20260220101500")
    w.line(HFS, "sans-bold", 16)
    for a in HFS_ADDR + ["Tel. (614) 555-0142  ·  ar@harrowgatefastener.example"]:
        w.line(a, "sans", 9, lead=1.25)
    w.space(14)
    w.line("INVOICE", "sans-bold", 20, align="right")
    w.line("Invoice No.: INV-2026/0347", "sans", 10, align="right")
    w.line("Invoice Date: February 20, 2026", "sans", 10, align="right")
    w.line("Terms: Net 15 (due March 7, 2026)", "sans", 10, align="right")
    w.space(10)
    w.line("Bill To / Ship To:", "sans-bold", 10)
    for a in [BMS, "Attn: Zoë Müller, Procurement"] + BMS_ADDR + ["Receiving: Dock 4"]:
        w.line(a, "sans", 10, lead=1.25)
    w.space(10)
    w.table([["Customer PO", "Ship Date", "Carrier", "BOL", "PRO No."],
             ["PO-BMS-26-0112", "02/19/2026", "Tri-County Freight Lines", "TCF-889104", "0047-2219-6"]],
            [95, 70, 140, 80, 80])
    w.space(12)
    rows = [["Part No.", "Description", ("Qty"), "Unit", "Amount"]]
    for pn, d, q, p in LINES:
        rows.append([pn, d, f"{q:,}", f"{p:.2f}", f"{q * p:,.2f}"])
    w.table(rows, [70, 250, (60,), (50,), (70,)])
    w.space(6)
    w.table([["", ""], ["Subtotal", money(SUBTOTAL)], ["Freight (FOB Destination, prepaid & added)", money(FREIGHT)],
             ["TOTAL DUE", money(TOTAL)]], [380, (120,)], bold_header=False)
    w.space(20)
    w.para("Shipment: 1 pallet, 48″ × 40″ × 52″, 1,410 lb, shrink-wrapped, HFS label on two sides. "
           "Please reference INV-2026/0347 on remittance. Questions about this invoice: Raymond Voss, Controller.",
           "sans", 9)
    w.para("Late payments accrue interest at 1.5% per month under §4.3 of the Master Supply Agreement dated "
           "November 3, 2025.", "sans", 9)
    path = out("voss-r", "INV-2026-0347.pdf")
    w.save(path)
    data = path.read_bytes()
    note(path)
    p2 = out("muller-z", "INV-2026-0347.pdf")  # byte-identical duplicate
    p2.write_bytes(data)
    note(p2)
    return data


def po_pdf() -> bytes:
    w = PdfWriter("Purchase Order PO-BMS-26-0112", BMS, "20260209083000",
                  footer=lambda n: f"PO-BMS-26-0112  —  page {n}")
    w.line(BMS, "sans-bold", 16)
    for a in BMS_ADDR:
        w.line(a, "sans", 9, lead=1.25)
    w.space(10)
    w.line("PURCHASE ORDER  PO-BMS-26-0112", "sans-bold", 14)
    w.line("Date: 9 February 2026     Buyer: Zoë Müller     Required by: 27 February 2026", "sans", 10)
    w.line("Vendor: Harrowgate Fastener Supply Co. (vendor no. V-01187)", "sans", 10)
    w.line("Terms: per Master Supply Agreement dated November 3, 2025; FOB Destination; Net 15", "sans", 10)
    w.space(10)
    rows = [["Line", "Part No.", "Description", "Qty", "Unit", "Ext."]]
    for i, (pn, d, q, p) in enumerate(LINES, 1):
        rows.append([str(i), pn, d, f"{q:,}", f"{p:.2f}", f"{q * p:,.2f}"])
    rows.append(["", "", "Estimated freight", "", "", f"{FREIGHT:,.2f}"])
    rows.append(["", "", "PO total", "", "", f"{TOTAL:,.2f}"])
    w.table(rows, [30, 70, 230, (55,), (45,), (70,)])
    w.space(16)
    w.line("Delivery instructions", "sans-bold", 11)
    w.para("Deliver to Dock 4, Monday–Friday 07:00–15:30. Every pallet must carry the PO number "
           "PO-BMS-26-0112 on a packing list in a sleeve on the outside of the wrap. Pallets without a PO "
           "reference are quarantined in bay Q-3 and may be refused. The receiving supervisor or a "
           "designated dock lead must sign the carrier’s delivery receipt and print their name.", "sans", 10)
    w.para("Material certification (mill test report, EN 10204 3.1) for PN 44-B/7 and PN 17-K.3/A must "
           "accompany the shipment.", "sans", 10)
    w.new_page()
    w.line("Standard Purchase Terms (summary)", "sans-bold", 12)
    terms = [
        "1. Acceptance. This order is governed by the Master Supply Agreement between Bellwether Marine "
        "Systems, Inc. and Harrowgate Fastener Supply Co. dated November 3, 2025 (the “MSA”). If this "
        "order conflicts with the MSA, the MSA controls.",
        "2. Delivery and risk of loss. Delivery is FOB Destination. Title and risk of loss pass to Buyer "
        "only upon delivery to and acceptance at the Buyer receiving dock named above.",
        "3. Inspection. Buyer may inspect goods within five (5) business days of receipt and may reject "
        "non-conforming goods by written notice.",
        "4. Invoicing. Invoices must reference this PO number. Invoices without a PO number will be "
        "returned unpaid.",
        "5. Changes. No change to quantity, price or delivery date is binding unless confirmed in writing "
        "by Bellwether Procurement.",
    ]
    for t in terms:
        w.para(t, "sans", 10)
    path = note(out("muller-z", "PO-BMS-26-0112.pdf"))
    w.save(path)
    return path.read_bytes()


MEMO_HYPH = {  # allowed break points, as a dictionary hyphenator would give them
    w.replace("-", ""): w for w in """
    ship-ment ac-knowl-edged doc-u-men-ta-tion re-ceiv-able Bell-weth-er de-liv-ery out-stand-ing
    rec-om-men-da-tion col-lec-tion pro-cure-ment sig-na-ture Har-row-gate re-ceiv-ing ac-cep-tance
    agree-ment Agree-ment in-for-ma-tion im-me-di-ate-ly cus-tom-er in-voice car-ri-er Des-ti-na-tion
    re-ceipt pal-let con-firmed po-si-tion oth-er-wise there-fore mis-placed in-ter-est
    ap-prox-i-mate-ly dis-patch-er re-mem-bers time-stamp men-tioned Feb-ru-ary de-liv-ered
    ini-tials Sup-ply Mas-ter hap-pened ques-tion Coun-ty Freight Sys-tems Ma-rine man-ag-er
    pay-ment dis-putes bal-ance ac-counts cur-rent coun-sel su-per-vi-sor be-longs Pell-ham
    re-ceived ad-dress con-trol-ler re-ship lo-ca-tion orig-i-nal re-fer re-ferred un-claimed
    wash-ers sealant hex flange
    """.split()
}


def memo_pdf(revised: bool):
    date = "March 18, 2026" if revised else "March 16, 2026"
    w = PdfWriter("Memo: Bellwether Marine collection", "Raymond Voss",
                  "20260318160000" if revised else "20260316113000", margin=112,
                  footer=lambda n: f"Harrowgate Fastener Supply Co. — Internal — Page {n}")
    w.line("MEMORANDUM" + (" (REVISED)" if revised else ""), "serif-bold", 14)
    w.space(6)
    for k, v in [("TO:", "File; cc: Dana Okafor, Sales"), ("FROM:", "Raymond Voss, Controller"),
                 ("DATE:", date), ("RE:", "Bellwether Marine Systems — INV-2026/0347 (PO-BMS-26-0112)")]:
        w.line(f"{k:<7}{v}", "serif", 11)
    w.rule()
    S = 11.5
    P = lambda t: w.para(t, "serif", S, hyph=MEMO_HYPH, lead=1.4, after=8)
    P("Summary. Bellwether Marine Systems, Inc. has not paid invoice INV-2026/0347 for "
      f"{money(TOTAL)}, which was due on March 7, 2026. Bellwether’s procurement manager, "
      "Zoë Müller, wrote on March 9 that “the shipment was never received.” Our "
      "documentation says otherwise: the carrier’s proof of delivery shows the pallet was "
      "delivered to Dock 4 on February 24, 2026 at 2:47 p.m. and signed for with the initials “TK.”")
    P("Background. The order was acknowledged by Dana Okafor on February 9 and shipped on "
      "February 19 by Tri-County Freight Lines under bill of lading TCF-889104 (PRO 0047-2219-6). "
      "The pallet held PN 44-B/7 hex flange bolts, PN 44-C/2 washers, PN 17-K.3/A lock nuts and "
      "PN 90-T/12 sealant. Under the Master Supply Agreement the terms are FOB Destination, so risk "
      "of loss stays with us until delivery; the question is therefore whether delivery happened, "
      "not whether the goods were later misplaced.")
    if revised:
        P("Carrier position. On March 17 the Tri-County dispatcher told Dana that the driver, D. Pruitt, "
          "remembers leaving the pallet with “a guy in a hi-vis vest” at Dock 4 and that the "
          "carrier’s scanner timestamp matches the proof of delivery. The dispatcher also mentioned, "
          "almost in passing, that a pallet was picked up from Bellwether on March 4 as “unclaimed "
          "freight.” If that is our pallet, Bellwether had it for eight days.")
    P("Customer history. Bellwether has been a customer since 2019 with no prior payment "
      "disputes. Its accounts receivable balance with us is otherwise current.")
    if revised:
        P(f"Amount. With interest at 1.5% per month under §4.3 of the agreement, the balance "
          f"through March 31 is approximately {money(TOTAL * (1 + 0.015 * 24 / 31))}.")
    else:
        P(f"Amount. The outstanding balance is {money(TOTAL)}, excluding interest.")
    if revised:
        P("Recommendation. (1) Place Bellwether on credit hold effective March 19; (2) ask Tri-County "
          "to confirm in writing what it picked up on March 4 and where that pallet is now; (3) refer "
          "the matter to Pellham & Stroud LLP for a demand letter if payment is not received by "
          "March 24. We should not reship until the location of the original pallet is confirmed.")
    else:
        P("Recommendation. Place Bellwether on credit hold and refer the matter to outside counsel "
          "for a demand letter. In the meantime Dana should ask Bellwether’s receiving supervisor "
          "to check whether the signature on the delivery receipt belongs to anyone on his team.")
    P("Raymond Voss")
    name = "memo-collection-bellwether-2026-03-18-rev.pdf" if revised else "memo-collection-bellwether-2026-03-16.pdf"
    path = note(out("voss-r", name))
    w.save(path)
    return w.hyphenations


def demand_letter_pdf() -> bytes:
    w = PdfWriter("Demand for payment - INV-2026/0347", "Evelyn Stroud", "20260325150000", margin=80,
                  footer=lambda n: f"Page {n} of 2")
    w.line("PELLHAM & STROUD LLP", "serif-bold", 15, align="center")
    w.line("41 East Gay Street, Suite 900  ·  Columbus, Ohio 43215  ·  (614) 555-0190", "serif", 9, align="center")
    w.rule()
    w.space(6)
    w.line("March 25, 2026", "serif", 11)
    w.space(6)
    w.line("VIA EMAIL AND CERTIFIED MAIL", "serif-bold", 10)
    w.space(4)
    for a in ["Marcus Delacroix-Hale, General Counsel", BMS] + BMS_ADDR:
        w.line(a, "serif", 11, lead=1.25)
    w.space(8)
    w.line("Re:  Invoice INV-2026/0347; Purchase Order PO-BMS-26-0112", "serif-bold", 11)
    w.space(6)
    P = lambda t: w.para(t, "serif", 11, lead=1.4, after=9)
    P("Dear Mr. Delacroix-Hale:")
    P("This firm represents Harrowgate Fastener Supply Co. (“Harrowgate”) in connection with the "
      f"unpaid balance of {money(TOTAL)} on invoice INV-2026/0347, issued February 20, 2026 for goods "
      "ordered by Bellwether Marine Systems, Inc. (“Bellwether”) under purchase order "
      "PO-BMS-26-0112 and the parties’ Master Supply Agreement dated November 3, 2025 (the “Agreement”).")
    P("The goods — including 12,000 units of PN 44-B/7 and 20,000 units of PN 17-K.3/A — were tendered "
      "by Tri-County Freight Lines and delivered to Bellwether’s Dock 4 on February 24, 2026, as shown "
      "by the signed delivery receipt for bill of lading TCF-889104. Bellwether’s assertion that "
      "“the shipment was never received” is contradicted by that receipt and by the carrier’s "
      "electronic delivery scan.")
    P("Section 7.2 of the Agreement required Bellwether to give written notice of any non-receipt or "
      "shortage within ten (10) days after the invoice date, i.e., by March 2, 2026. Harrowgate received "
      "no such notice. Bellwether’s first communication disputing delivery was sent on March 9, 2026.")
    P("We understand, moreover, that on or about March 4, 2026 Bellwether released a Harrowgate pallet to "
      "the carrier as “unclaimed.” Goods in Bellwether’s possession for eight days were plainly "
      "received; Bellwether cannot avoid payment by handing them back to a third party without notice "
      "to Harrowgate.")
    P(f"Demand is hereby made for payment of {money(TOTAL)}, plus interest at 1.5% per month under "
      "Section 4.3 of the Agreement, within ten (10) days of the date of this letter. If payment is not "
      "received, Harrowgate intends to commence arbitration under Section 12 of the Agreement and the "
      "Commercial Arbitration Rules of the American Arbitration Association, and to seek its fees and costs.")
    P("Please also take this letter as notice to preserve all documents and electronically stored "
      "information concerning the shipment, including receiving logs, dock camera footage, and "
      "communications with Tri-County Freight Lines.")
    P("Very truly yours,")
    w.space(18)
    w.line("Evelyn Stroud", "serif-italic", 13)
    w.line("Evelyn Stroud", "serif", 11)
    w.space(8)
    w.line("cc: Raymond Voss, Harrowgate Fastener Supply Co.", "serif", 9)
    path = note(out("voss-r", "demand-letter-2026-03-25.pdf"))
    w.save(path)
    return path.read_bytes()


def deposition_notice_pdf():
    w = PdfWriter("Notice of Deposition - Thomas Kettering", "Evelyn Stroud", "20260414110000", margin=80,
                  footer=lambda n: f"Notice of Deposition — {n}")
    w.line("AMERICAN ARBITRATION ASSOCIATION", "serif-bold", 12, align="center")
    w.line("Commercial Arbitration Tribunal", "serif", 11, align="center")
    w.space(10)
    w.line("HARROWGATE FASTENER SUPPLY CO., Claimant,", "serif", 11)
    w.line("v.", "serif", 11)
    w.line("BELLWETHER MARINE SYSTEMS, INC., Respondent.", "serif", 11)
    w.line("AAA Case No. 01-26-0004-1187", "serif-bold", 11)
    w.space(10)
    w.line("CLAIMANT’S NOTICE OF DEPOSITION OF THOMAS KETTERING", "serif-bold", 12, align="center")
    w.space(8)
    P = lambda t: w.para(t, "serif", 11, lead=1.6, after=10)
    P("PLEASE TAKE NOTICE that, pursuant to the Arbitrator’s Procedural Order No. 1 dated April 9, 2026, "
      "Claimant Harrowgate Fastener Supply Co. will take the deposition upon oral examination of Thomas "
      "Kettering, Receiving Supervisor of Respondent Bellwether Marine Systems, Inc., on April 28, 2026, "
      "beginning at 9:30 a.m. Eastern time, at the offices of Harbor Court Reporting, 150 West Main "
      "Street, Suite 1400, Norfolk, Virginia 23510, or at such other time and place as counsel agree.")
    P("The deposition will be recorded stenographically by a certified court reporter and may be "
      "video-recorded. It will continue from day to day until completed.")
    P("The witness is requested to bring: (a) the Dock 4 receiving logs for February and March 2026; "
      "(b) any receiving discrepancy reports for February 24, 2026; (c) the shift roster for the week of "
      "February 23, 2026; and (d) all communications with Tri-County Freight Lines concerning the pallet "
      "held in bay Q-3.")
    w.new_page()
    P("Dated: April 14, 2026")
    w.line("PELLHAM & STROUD LLP", "serif-bold", 11)
    w.line("By: /s/ Evelyn Stroud", "serif", 11)
    w.line("Counsel for Claimant", "serif", 11)
    w.space(20)
    w.line("CERTIFICATE OF SERVICE", "serif-bold", 11, align="center")
    P("I certify that on April 14, 2026 a copy of this notice was served by email on Marcus "
      "Delacroix-Hale, counsel for Respondent, at mdelacroix-hale@bellwethermarine.example.")
    w.line("/s/ Evelyn Stroud", "serif", 11)
    path = note(out("delacroix-m", "notice-of-deposition-kettering-2026-04-14.pdf"))
    w.save(path)


# ===================================================================== scans


def _scan_src_page(doc):
    return doc.new_page(width=LETTER.width, height=LETTER.height)


def T(page, x, y, text, fname="sans", size=11):
    page.insert_text((x, y), text, fontname=fname.replace("-", ""),
                     fontfile=str(FONT_DIR / FONTS[fname]), fontsize=size)


def pod_scan() -> bytes:
    """Carrier delivery receipt with a scrawled signature. Image-only."""
    d = pymupdf.open()
    p = _scan_src_page(d)
    T(p, 60, 70, "TRI-COUNTY FREIGHT LINES", "sans-bold", 18)
    T(p, 60, 88, "Richmond Terminal · 4900 Commerce Road, Richmond, VA 23234 · (804) 555-0177", "sans", 9)
    T(p, 360, 120, "DELIVERY RECEIPT", "sans-bold", 16)
    T(p, 360, 140, "PRO No. 0047-2219-6", "sans-bold", 12)
    y = 175
    fields = [("Shipper", "Harrowgate Fastener Supply Co., Columbus OH"),
              ("Consignee", "Bellwether Marine Systems, Inc."),
              ("Deliver to", "2200 Industrial Pkwy, Norfolk VA 23502 - DOCK 4"),
              ("BOL No.", "TCF-889104"), ("Shipper Ref", "PO-BMS-26-0112 / INV-2026/0347"),
              ("Pieces", "1 PLT"), ("Weight", "1,410 LB"), ("Terms", "PREPAID"),
              ("Driver", "D. PRUITT  #4471"), ("Pickup date", "02/19/2026")]
    for k, v in fields:
        p.draw_rect(pymupdf.Rect(60, y - 14, 552, y + 8), width=0.7)
        T(p, 66, y, k.upper(), "sans-bold", 9)
        T(p, 170, y, v, "mono", 11)
        y += 22
    y += 14
    T(p, 60, y, "Received the above described property in apparent good order, except as noted.", "sans", 10)
    y += 40
    T(p, 60, y, "Delivery date:", "sans-bold", 10)
    T(p, 150, y, "2/24/26", "serif-italic", 16)
    T(p, 300, y, "Time:", "sans-bold", 10)
    T(p, 340, y, "2:47 pm", "serif-italic", 16)
    y += 40
    T(p, 60, y, "Consignee signature:", "sans-bold", 10)
    # scrawl
    x0 = 190
    p.draw_bezier((x0, y), (x0 + 10, y - 25), (x0 + 22, y + 10), (x0 + 30, y - 12), width=1.4)
    p.draw_bezier((x0 + 30, y - 12), (x0 + 45, y - 30), (x0 + 50, y + 8), (x0 + 70, y - 4), width=1.4)
    p.draw_bezier((x0 + 70, y - 4), (x0 + 90, y - 14), (x0 + 110, y + 2), (x0 + 150, y - 6), width=1.2)
    p.draw_line((180, y + 4), (420, y + 4), width=0.6)
    y += 34
    T(p, 60, y, "Print name:", "sans-bold", 10)
    T(p, 150, y, "TK", "serif-italic", 18)
    p.draw_line((140, y + 4), (420, y + 4), width=0.6)
    y += 40
    T(p, 60, y, "Exceptions:", "sans-bold", 10)
    T(p, 150, y, "none", "serif-italic", 14)
    y += 60
    T(p, 60, y, "Delivery scan 02/24/2026 14:47:12 EST  HH-4471  GPS 36.8619,-76.2134", "mono", 9)
    T(p, 60, 740, "CONSIGNEE COPY", "sans-bold", 9)
    path = note(out("okafor-d", "pod-TCF-889104.pdf"))
    scan_pdf(d, path, seed=11, title="", date="20260312093000")
    return path.read_bytes()


def discrepancy_report_scan():
    """Bellwether receiving discrepancy form, two pages, filled in by hand."""
    d = pymupdf.open()
    p = _scan_src_page(d)
    T(p, 60, 70, "BELLWETHER MARINE SYSTEMS, INC.", "sans-bold", 15)
    T(p, 60, 90, "Receiving Discrepancy Report  (form RCV-07, rev. 3)", "sans", 11)
    T(p, 420, 70, "No. RDR-0224-02", "sans-bold", 11)
    y = 130
    rows = [("Date / time", "2/24/26   2:52 pm"), ("Dock", "4"), ("Carrier", "Tri-County (TCFL) - truck 4471"),
            ("PO number", "NONE ON PALLET"), ("Vendor", "? - wrap says HFS"),
            ("Pieces", "1 pallet, approx 1400 lb"), ("Discrepancy", "No packing list. No PO ref."),
            ("Action", "Quarantine bay Q-3"), ("Received by", "J. Ibáñez")]
    for k, v in rows:
        p.draw_rect(pymupdf.Rect(60, y - 16, 552, y + 10), width=0.7)
        T(p, 66, y, k, "sans-bold", 10)
        T(p, 190, y, v, "serif-italic", 15)
        y += 26
    y += 20
    T(p, 60, y, "Notes:", "sans-bold", 10)
    notes = ["Driver in a hurry, would not wait for supervisor.",
             "Driver had me sign his handheld, I wrote TK because",
             "Tom is the supervisor of record. Tom offsite (training).",
             "Pallet wrap printed HFS on two sides. Did not open.",
             "Left copy of this report in Tom's tray."]
    for n in notes:
        y += 24
        T(p, 80, y, n, "serif-italic", 14)
        p.draw_line((76, y + 5), (552, y + 5), width=0.3)
    y += 50
    T(p, 60, y, "Supervisor review:", "sans-bold", 10)
    T(p, 190, y, "seen TK 3/2 - no PO, call carrier", "serif-italic", 14)
    p2 = _scan_src_page(d)
    T(p2, 60, 70, "RDR-0224-02  (continued)", "sans-bold", 12)
    T(p2, 60, 110, "Follow-up log", "sans-bold", 11)
    fl = ["3/2  T.K. emailed TCFL dispatch re unclaimed pallet Q-3",
          "3/4  TCFL picked up pallet, driver R. Ames, 10:20 am",
          "3/4  Q-3 cleared. Report closed.",
          "3/10 Re-opened per Z. Muller (procurement) - possible",
          "     HFS PO-BMS-26-0112 ?? sent to TK"]
    y = 140
    for n in fl:
        T(p2, 70, y, n, "serif-italic", 14)
        y += 26
    path = note(out("kettering-t", "receiving-discrepancy-report-RDR-0224-02.pdf"))
    scan_pdf(d, path, seed=22, title="", date="20260310161500", skew=1.1, noise=22)


def carrier_letter_scan():
    d = pymupdf.open()
    p = _scan_src_page(d)
    T(p, 72, 72, "TRI-COUNTY FREIGHT LINES, INC.", "serif-bold", 16)
    T(p, 72, 90, "Claims Department · 4900 Commerce Road · Richmond, Virginia 23234", "serif", 10)
    lines = [
        "April 10, 2026", "",
        "Mr. Raymond Voss", "Harrowgate Fastener Supply Co.", "1180 Marcourt Road", "Columbus, OH 43219", "",
        "Re: Claim No. TC-26-01953; PRO 0047-2219-6; BOL TCF-889104", "",
        "Dear Mr. Voss:", "",
        "We have completed our review of your claim dated March 20, 2026 for",
        "non-delivery of one pallet consigned to Bellwether Marine Systems, Inc.",
        "", "Our records show the shipment was delivered to the consignee at",
        "Dock 4 on February 24, 2026 at 14:47. The delivery receipt was signed",
        "and no exceptions were noted. Your claim for non-delivery is therefore",
        "declined.", "",
        "On March 4, 2026 our driver collected one unlabeled pallet from the",
        "consignee at the consignee's request. That pallet is being held at our",
        "Richmond terminal as refused freight under storage number RS-2026-0412.",
        "Storage accrues at $35.00 per day beginning March 11, 2026. Please",
        "advise disposition. We will release the pallet to the shipper or to",
        "the consignee upon written instruction and payment of charges.", "",
        "Sincerely,", "", "", "Gary Lindqvist", "Claims Manager",
    ]
    y = 140
    for ln in lines:
        if ln:
            T(p, 72, y, ln, "serif", 12)
        y += 17
    p.draw_bezier((72, 612), (90, 590), (110, 625), (150, 600), width=1.3)
    path = note(out("voss-r", "tcfl-claim-letter-2026-04-10.pdf"))
    scan_pdf(d, path, seed=33, title="", date="20260413090000", skew=-0.8, noise=15, quality=55)


# ===================================================================== DOCX


def _doc():
    d = docx.Document()
    st = d.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(11)
    return d


def msa_docx():
    d = _doc()
    d.core_properties.author = "Marcus Delacroix-Hale"
    d.core_properties.title = "Master Supply Agreement"
    h = d.add_heading("MASTER SUPPLY AGREEMENT", 0)
    d.add_paragraph(
        "This Master Supply Agreement (the “Agreement”) is made as of November 3, 2025 between "
        "Harrowgate Fastener Supply Co., an Ohio corporation (“Seller”), and Bellwether Marine "
        "Systems, Inc., a Virginia corporation (“Buyer”).")
    secs = [
        ("1. Scope", "Seller will sell and Buyer will purchase the fasteners and consumables listed in "
         "Schedule A, in the quantities stated in Buyer’s written purchase orders. Each purchase order "
         "is governed by this Agreement."),
        ("2. Orders", "Seller will acknowledge each purchase order in writing within two (2) business days. "
         "An acknowledged purchase order is binding on both parties."),
        ("3. Prices", "Prices are as set out in Schedule A and are fixed through December 31, 2026. Freight "
         "is prepaid by Seller and added to the invoice at cost."),
        ("4. Invoicing and Payment", "4.1 Seller will invoice upon shipment. 4.2 Payment is due fifteen (15) "
         "days after the invoice date (“Net 15”). 4.3 Late payments bear interest at one and one-half "
         "percent (1.5%) per month or the highest lawful rate, if less."),
        ("5. Delivery; Risk of Loss", "All deliveries are FOB Destination, Buyer’s receiving dock. Title "
         "and risk of loss pass to Buyer upon delivery. “Delivery” means physical tender at Buyer’s "
         "receiving dock during receiving hours, evidenced by a delivery receipt signed by Buyer’s "
         "receiving personnel."),
        ("6. Packaging and Labeling", "Each pallet must bear the Buyer purchase order number and a packing "
         "list. Buyer may quarantine or refuse any pallet that does not."),
        ("7. Inspection; Notice of Claims", "7.1 Buyer may inspect goods within five (5) business days after "
         "delivery and reject non-conforming goods by written notice. 7.2 Any claim of non-receipt or "
         "shortage must be made in writing within ten (10) days after the invoice date, or it is waived."),
        ("8. Warranty", "Seller warrants that the goods conform to Schedule A and to the material "
         "specifications stated there for twelve (12) months after delivery."),
        ("9. Limitation of Liability", "Neither party is liable for consequential or punitive damages. "
         "Seller’s aggregate liability under any purchase order is limited to the price of that order."),
        ("10. Term", "This Agreement runs until December 31, 2026 and renews for one-year terms unless "
         "either party gives sixty (60) days’ written notice."),
        ("11. Governing Law", "This Agreement is governed by the laws of the State of Ohio, including "
         "Article 2 of the Uniform Commercial Code as adopted in Ohio."),
        ("12. Dispute Resolution", "Any dispute arising out of this Agreement will be resolved by binding "
         "arbitration administered by the American Arbitration Association under its Commercial Arbitration "
         "Rules, before a single arbitrator, seated in Columbus, Ohio. Judgment on the award may be entered "
         "in any court having jurisdiction. The prevailing party may recover reasonable attorneys’ fees."),
    ]
    for title, body in secs:
        d.add_heading(title, level=2)
        d.add_paragraph(body)
    d.add_page_break()
    d.add_heading("Schedule A — Products and Prices", level=1)
    t = d.add_table(rows=1, cols=5)
    t.style = "Table Grid"
    for c, txt in zip(t.rows[0].cells, ["Part No.", "Description", "Material", "Unit", "Price (USD)"]):
        c.text = txt
    sched = [("PN 44-B/7", "Hex flange bolt M10×1.25×40", "316 SS, EN 10204 3.1 cert", "each", "2.85"),
             ("PN 44-B/9", "Hex flange bolt M10×1.25×60", "316 SS, EN 10204 3.1 cert", "each", "3.40"),
             ("PN 44-C/2", "Split lock washer M10", "316 SS", "each", "0.19"),
             ("PN 17-K.3/A", "Nylon-insert lock nut M10", "316 SS", "each", "0.41"),
             ("PN 17-K.3/B", "Nylon-insert lock nut M12", "316 SS", "each", "0.58"),
             ("PN 90-T/12", "Thread sealant, marine grade, 50 ml", "—", "tube", "11.40")]
    for row in sched:
        cells = t.add_row().cells
        for c, txt in zip(cells, row):
            c.text = txt
    d.add_paragraph()
    d.add_paragraph("IN WITNESS WHEREOF, the parties have signed this Agreement as of the date first written above.")
    sig = d.add_table(rows=4, cols=2)
    for r, (a, b) in enumerate([("HARROWGATE FASTENER SUPPLY CO.", "BELLWETHER MARINE SYSTEMS, INC."),
                                ("By: /s/ Raymond Voss", "By: /s/ Marcus Delacroix-Hale"),
                                ("Raymond Voss, Controller", "Marcus Delacroix-Hale, General Counsel"),
                                ("Date: November 3, 2025", "Date: November 3, 2025")]):
        sig.cell(r, 0).text, sig.cell(r, 1).text = a, b
    sec = d.sections[0]
    sec.footer.paragraphs[0].text = "MSA — Harrowgate / Bellwether — Confidential"
    path = note(out("muller-z", "master-supply-agreement-2025-11-03.docx"))
    save_docx_reproducibly(d, path, dt.datetime(2025, 11, 3, 14, 0, 0))


def response_letter_docx() -> bytes:
    d = _doc()
    d.core_properties.author = "Marcus Delacroix-Hale"
    d.core_properties.title = "Response to demand - INV-2026/0347"
    p = d.add_paragraph("BELLWETHER MARINE SYSTEMS, INC.")
    p.runs[0].bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_paragraph("Office of the General Counsel · 2200 Industrial Parkway · Norfolk, VA 23502").alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_paragraph("April 2, 2026")
    d.add_paragraph("Evelyn Stroud, Esq.\nPellham & Stroud LLP\n41 East Gay Street, Suite 900\nColumbus, Ohio 43215")
    d.add_paragraph("Re:\tHarrowgate Fastener Supply Co. — INV-2026/0347; PO-BMS-26-0112").runs[0].bold = True
    for t in [
        "Dear Ms. Stroud:",
        "I write in response to your letter of March 25, 2026. Bellwether disputes the invoice in full.",
        "First, Bellwether never received the goods described in INV-2026/0347. No pallet bearing purchase "
        "order PO-BMS-26-0112 was received at Dock 4 on February 24, 2026 or on any other date. The delivery "
        "receipt you rely on bears only the initials “TK” and an illegible scrawl. Our receiving "
        "supervisor, Thomas Kettering, did not sign it; he was attending an offsite forklift-safety "
        "recertification that afternoon.",
        "Second, under Section 5 of the Agreement, delivery is FOB Destination and “Delivery” means "
        "tender evidenced by a receipt signed by Bellwether’s receiving personnel. Section 6 requires every "
        "pallet to bear the PO number and a packing list, and permits Bellwether to quarantine or refuse any "
        "pallet that does not. The pallet your letter refers to carried neither. It was quarantined in bay Q-3 "
        "in accordance with Section 6 and returned to Tri-County Freight Lines on March 4, 2026 as unidentified "
        "freight. Risk of loss never passed.",
        "Third, Section 7.2’s ten-day notice provision addresses claims of non-receipt of goods that were "
        "delivered. It cannot convert a non-delivery into a delivery. In any event Ms. Müller notified "
        "Ms. Okafor by telephone on March 2, 2026 that the order had not arrived.",
        "Bellwether remains willing to accept a conforming delivery of the goods ordered under PO-BMS-26-0112 "
        "and to pay for them on the Agreement’s terms. If the pallet now held by Tri-County is in fact "
        "Harrowgate’s, the parties should arrange for it to be inspected and, if conforming, redelivered "
        "with proper documentation.",
        "Nothing in this letter waives any of Bellwether’s rights or defenses, all of which are reserved.",
        "Sincerely,",
        "Marcus Delacroix-Hale\nGeneral Counsel",
        "cc: Zoë Müller, Procurement",
    ]:
        d.add_paragraph(t)
    path = note(out("muller-z", "response-to-demand-2026-04-02.docx"))
    save_docx_reproducibly(d, path, dt.datetime(2026, 4, 2, 16, 0, 0))
    return path.read_bytes()


def legal_memo_docx():
    d = _doc()
    d.core_properties.author = "Marcus Delacroix-Hale"
    d.core_properties.title = "Privileged memo - Harrowgate shipment dispute"
    hdr = d.sections[0].header.paragraphs[0]
    hdr.text = "PRIVILEGED & CONFIDENTIAL — ATTORNEY WORK PRODUCT"
    d.add_heading("Memorandum", level=1)
    for k, v in [("To", "File; Zoë Müller (Procurement)"), ("From", "Marcus Delacroix-Hale, General Counsel"),
                 ("Date", "March 27, 2026"), ("Re", "Harrowgate demand — INV-2026/0347 / PO-BMS-26-0112")]:
        p = d.add_paragraph()
        p.add_run(f"{k}: ").bold = True
        p.add_run(v)
    d.add_heading("Question", level=2)
    d.add_paragraph("Whether Bellwether owes Harrowgate $48,612.50 for a pallet that Harrowgate says was delivered "
                    "on February 24, 2026 and that procurement says was never received.")
    d.add_heading("Short answer", level=2)
    d.add_paragraph("Probably not, but the Q-3 pallet is a real problem. If the unlabeled pallet that sat in Q-3 "
                    "from February 24 to March 4 was Harrowgate’s, an arbitrator could find that tender occurred "
                    "and that our own labeling rule, not non-delivery, is why we did not process it.")
    d.add_heading("Facts", level=2)
    for t in [
        "Receiving log entry RCV-26-0224-07 records an unlabeled pallet at Dock 4 at 14:52 on February 24 with "
        "“HFS” printed on the wrap. José Ibáñez, a dock associate, signed the carrier’s handheld and "
        "wrote “TK” because Tom Kettering is the supervisor of record. Tom was offsite.",
        "Tom reviewed the discrepancy report on March 2, did not connect it to PO-BMS-26-0112, and asked "
        "Tri-County to collect the pallet. Tri-County collected it on March 4.",
        "Zoë says she raised non-arrival with Dana Okafor by phone on March 2. Harrowgate’s call notes "
        "(which we have not seen) may not agree. Her first written notice is the March 9 email.",
    ]:
        d.add_paragraph(t, style="List Bullet")
    d.add_heading("Analysis", level=2)
    d.add_paragraph("Under §5 of the MSA “Delivery” requires tender at the dock evidenced by a receipt "
                    "signed by our receiving personnel. José is receiving personnel. The “TK” initials are a "
                    "problem only for the witness, not for the contract. Our better argument is §6: the pallet "
                    "had no PO and no packing list, and §6 expressly allows quarantine or refusal. A refusal "
                    "under §6 means the goods were never accepted and risk of loss stayed with Harrowgate "
                    "(UCC § 2-510 by analogy; Ohio R.C. 1302.53).")
    d.add_paragraph("The §7.2 ten-day notice argument is weaker for Harrowgate than its letter suggests, "
                    "because the clause speaks of non-receipt of delivered goods. But we should not rely on the "
                    "March 2 phone call unless there is a record of it.")
    d.add_heading("Recommendations", level=2)
    for t in ["Issue a litigation hold to Procurement, Receiving and AP today.",
              "Preserve Dock 4 camera footage for February 24 (retention is 45 days — it will be overwritten on "
              "April 10).",
              "Ask Tri-County to photograph the pallet held under storage no. RS-2026-0412 without opening it.",
              "Prepare Tom Kettering for a deposition; he should be candid that the wrap said HFS."]:
        d.add_paragraph(t, style="List Number")
    path = note(out("delacroix-m", "privileged-memo-harrowgate-2026-03-27.docx"))
    save_docx_reproducibly(d, path, dt.datetime(2026, 3, 27, 18, 0, 0))


# ===================================================================== CSV


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerow(header)
        w.writerows(rows)
    note(path)


VENDORS = [("Coastline Hydraulics", "PO-BMS-26-0{:03d}"), ("Norfolk Rope & Rigging", "PO-BMS-26-0{:03d}"),
           ("Atlantic Gasket Works", "PO-BMS-26-0{:03d}"), ("Tidewater Paint Supply", "PO-BMS-26-0{:03d}"),
           ("Harrowgate Fastener Supply", "PO-BMS-26-0{:03d}"), ("Brightwater Electrical", "PO-BMS-26-0{:03d}")]
CARRIERS = ["Tri-County Freight Lines", "Old Point Express", "UPS Freight", "Estes", "Vendor truck"]


def receiving_logs():
    rng = np.random.default_rng(7)
    header = ["log_id", "date", "time", "dock", "carrier", "pro_or_bol", "po_number", "vendor", "pallets",
              "description", "status", "location", "received_by", "notes"]
    staff = ["T. Kettering", "J. Ibáñez", "K. Osei", "M. Brandt"]

    def gen(month, days, fixed):
        rows = []
        for day in days:
            n = int(rng.integers(1, 4))
            for k in range(n):
                v, pofmt = VENDORS[int(rng.choice([0, 1, 2, 3, 5]))]  # never Harrowgate
                hh, mm = int(rng.integers(7, 15)), int(rng.integers(0, 60))
                rows.append([None, f"2026-{month:02d}-{day:02d}", f"{hh:02d}:{mm:02d}", str(int(rng.choice([1, 2, 4]))),
                             CARRIERS[int(rng.integers(0, len(CARRIERS)))], f"{int(rng.integers(1000000, 9999999))}",
                             pofmt.format(int(rng.integers(40, 160))), v, str(int(rng.integers(1, 4))),
                             "per packing list", "RECEIVED", f"R-{int(rng.integers(1, 30)):02d}",
                             staff[int(rng.integers(0, len(staff)))], ""])
        rows += fixed
        rows.sort(key=lambda r: (r[1], r[2]))
        per_day = {}
        for r in rows:
            per_day[r[1]] = per_day.get(r[1], 0) + 1
            r[0] = r[0] or f"RCV-26-{r[1][5:7]}{r[1][8:10]}-{per_day[r[1]]:02d}"
        return rows

    feb_fixed = [
        ["RCV-26-0224-07", "2026-02-24", "14:52", "4", "Tri-County Freight Lines", "PRO 0047-2219-6 (from driver handheld)",
         "", "UNKNOWN (wrap printed “HFS”)", "1", "UNLABELED PALLET - NO PO REF - no packing list",
         "QUARANTINE", "Q-3", "J. Ibáñez", "signed carrier handheld as TK (supervisor of record); see RDR-0224-02"],
        ["RCV-26-0212-05", "2026-02-12", "10:05", "4", "Vendor truck", "HFS-DT-0212", "PO-BMS-26-0098",
         "Harrowgate Fastener Supply", "1", "PN 44-B/9 x 4,000; PN 17-K.3/B x 6,000", "RECEIVED", "R-11",
         "T. Kettering", ""],
    ]
    feb = gen(2, [2, 3, 5, 6, 9, 10, 11, 12, 13, 17, 18, 19, 20, 23, 24, 25, 26, 27], feb_fixed)
    write_csv(out("kettering-t", "dock-receiving-log-2026-02.csv"), header, feb)
    mar_fixed = [
        ["RCV-26-0304-01", "2026-03-04", "10:20", "4", "Tri-County Freight Lines", "PRO 0047-2219-6",
         "", "UNKNOWN (wrap printed “HFS”)", "-1", "RETURNED TO CARRIER - unclaimed pallet from Q-3",
         "RELEASED", "Q-3", "T. Kettering", "driver R. Ames; per TK email to TCFL dispatch 3/2"],
        ["RCV-26-0310-04", "2026-03-10", "15:40", "4", "", "", "PO-BMS-26-0112", "Harrowgate Fastener Supply",
         "0", "LOG REVIEW - no receipt found for PO-BMS-26-0112", "NOT RECEIVED", "", "T. Kettering",
         "requested by Z. Müller; see RCV-26-0224-07 / RDR-0224-02"],
    ]
    mar = gen(3, [2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 23, 24, 25, 26, 27, 30, 31], mar_fixed)
    write_csv(out("kettering-t", "dock-receiving-log-2026-03.csv"), header, mar)


def harrowgate_csvs():
    header = ["ship_date", "bol", "pro_no", "customer", "customer_po", "invoice", "pallets", "weight_lb",
              "carrier", "status", "delivered", "pod_signed_by"]
    rng = np.random.default_rng(3)
    custs = [("Bellwether Marine Systems", "PO-BMS-26-0{:03d}"), ("Lakeshore Dock & Marine", "LDM-{:05d}"),
             ("Ridgeway Tractor Co.", "RT/26/{:04d}"), ("Pinecrest Fabrication", "PF-{:04d}"),
             ("Summit Rail Components", "SRC#{:04d}")]
    rows = []
    inv = 300
    for day in [2, 3, 4, 6, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 23, 24, 25, 26, 27]:
        for _ in range(int(rng.integers(1, 4))):
            c, fmt = custs[int(rng.integers(1, len(custs)))]
            inv += 1
            if inv == 347:
                inv += 1
            dd = min(day + int(rng.integers(1, 5)), 27)
            rows.append([f"2026-02-{day:02d}", f"TCF-{int(rng.integers(880000, 889999))}",
                         f"00{int(rng.integers(40, 49))}-{int(rng.integers(1000, 9999))}-{int(rng.integers(0, 9))}",
                         c, fmt.format(int(rng.integers(100, 9999))), f"INV-2026/0{inv}",
                         str(int(rng.integers(1, 5))), str(int(rng.integers(300, 2400))), "Tri-County Freight Lines",
                         "DELIVERED", f"2026-02-{dd:02d}" if dd > day else f"2026-03-02",
                         ["R. HALVORSEN", "P. LIM", "J. DOE", "M.K."][int(rng.integers(0, 4))]])
    rows.append(["2026-02-19", "TCF-889104", "0047-2219-6", "Bellwether Marine Systems", "PO-BMS-26-0112",
                 "INV-2026/0347", "1", "1410", "Tri-County Freight Lines", "DELIVERED", "2026-02-24", "TK"])
    rows.sort(key=lambda r: (r[0], r[5]))
    write_csv(out("voss-r", "outbound-shipping-log-2026-02.csv"), header, rows)

    header = ["customer", "invoice", "invoice_date", "due_date", "amount", "current", "1-30", "31-60", "61-90",
              "status", "note"]
    ar = [
        ["Bellwether Marine Systems", "INV-2026/0347", "2026-02-20", "2026-03-07", "48612.50", "0.00", "48612.50",
         "0.00", "0.00", "DISPUTED", "customer says “never received” - see memo 3/18; CREDIT HOLD 3/19"],
        ["Bellwether Marine Systems", "INV-2026/0388", "2026-03-04", "2026-03-19", "6120.00", "0.00", "6120.00",
         "0.00", "0.00", "OPEN", "PO-BMS-26-0131"],
        ["Lakeshore Dock & Marine", "INV-2026/0352", "2026-02-23", "2026-03-25", "12880.40", "0.00", "12880.40",
         "0.00", "0.00", "OPEN", ""],
        ["Ridgeway Tractor Co.", "INV-2026/0290", "2026-01-28", "2026-02-27", "3311.75", "0.00", "3311.75", "0.00",
         "0.00", "OPEN", "promised 4/03"],
        ["Pinecrest Fabrication", "INV-2026/0215", "2026-01-12", "2026-01-27", "947.20", "0.00", "0.00", "0.00",
         "947.20", "COLLECTIONS", ""],
        ["Summit Rail Components", "INV-2026/0401", "2026-03-11", "2026-04-10", "22510.00", "22510.00", "0.00",
         "0.00", "0.00", "CURRENT", ""],
        ["Summit Rail Components", "INV-2026/0377", "2026-03-02", "2026-04-01", "18004.60", "18004.60", "0.00",
         "0.00", "0.00", "CURRENT", ""],
    ]
    write_csv(out("voss-r", "ar-aging-2026-03-31.csv"), header, ar)


def exhibit_list_csv(dep_index):
    header = ["exhibit", "description", "date", "file", "first_referenced"]
    rows = [
        ["Kettering 1", "Dock receiving log, February 2026", "2026-02-28", "dock-receiving-log-2026-02.csv",
         dep_index["exhibit 1"]],
        ["Kettering 2", "Receiving Discrepancy Report RDR-0224-02", "2026-02-24",
         "receiving-discrepancy-report-RDR-0224-02.pdf", dep_index["exhibit 2"]],
        ["Kettering 3", "Tri-County delivery receipt, PRO 0047-2219-6", "2026-02-24", "pod-TCF-889104.pdf",
         dep_index["exhibit 3"]],
        ["Kettering 4", "Email T. Kettering to TCFL dispatch re unclaimed pallet", "2026-03-02",
         "2026-03-02_unclaimed-pallet-Q3.eml", dep_index["exhibit 4"]],
        ["Kettering 5", "Invoice INV-2026/0347", "2026-02-20", "INV-2026-0347.pdf", dep_index["exhibit 5"]],
    ]
    write_csv(out("delacroix-m", "deposition-exhibits-kettering.csv"), header, rows)


# ===================================================================== TXT / HTML


def write_text(path, text):
    path.write_text(text.lstrip("\n"), encoding="utf-8")
    note(path)


def texts():
    write_text(out("okafor-d", "call-notes-bellwether-2026-03.txt"), """
CALL NOTES - BELLWETHER MARINE (acct 1187)          D. Okafor

2026-03-02 (Mon) 11:40
  Zoe Muller called re PO-BMS-26-0112. Asked if the order had shipped yet,
  production line needs the 44-B/7 bolts by 3/9. Told her it shipped 2/19,
  TCFL, BOL TCF-889104, should have delivered last week. She said she'd
  check with receiving. Did NOT say it was missing - just asking for ETA.
  (Note: the 10-day notice window under the MSA closed today.)

2026-03-09 (Mon) 10:14
  Sent past-due follow up on INV-2026/0347. Zoe replied within the hour:
  "the shipment was never received." Asked for POD. Requested from TCFL.

2026-03-12 (Thu) 09:30
  Got POD from TCFL. Signed "TK", 2/24 2:47pm, Dock 4. Sent to Zoe.

2026-03-13 (Fri) 15:05
  Zoe: TK would be Tom Kettering (receiving supvr) but he says he didn't
  sign and he was offsite that afternoon. She is "escalating internally."
  I suggested they check for the pallet in their yard - 1 PLT, 1,410 lb,
  shrink wrap w/ HFS logo two sides.

2026-03-17 (Tue) 14:20
  TCFL dispatch (Gary L.): driver Pruitt remembers the drop. Also said
  TCFL picked up an "unclaimed" pallet at Bellwether on 3/4 !!  Told Ray.
""")
    write_text(out("voss-r", "credit-hold-notes.txt"), """
Credit hold log -- Harrowgate Fastener Supply Co. -- Finance

2026-03-19  BELLWETHER MARINE SYSTEMS (cust 1187)
            Hold reason: INV-2026/0347 disputed, $48,612.50, 12 days past due.
            Approved: R. Voss.  Open orders affected: PO-BMS-26-0131 (shipped
            3/4, INV-2026/0388), PO-BMS-26-0140 (not yet released).
            Do not release PO-BMS-26-0140 without Controller sign-off.

2026-03-24  BELLWETHER -- no payment. Referred to Pellham & Stroud (E. Stroud).

2026-04-13  BELLWETHER -- TCFL claim TC-26-01953 declined 4/10. Pallet at
            TCFL Richmond, storage RS-2026-0412, $35/day from 3/11. Asked
            counsel whether we should take the pallet back without prejudice.

2026-04-20  RIDGEWAY TRACTOR -- hold lifted, paid INV-2026/0290 in full.
""")
    write_text(out("muller-z", "dispute-timeline-notes.txt"), """
Harrowgate / PO-BMS-26-0112 — my timeline (Zoë)

09 Feb  PO-BMS-26-0112 sent to Dana. Acknowledged same day. Need date 27 Feb.
19 Feb  Portal email says “shipped” — TCF-889104.
24 Feb  (per Harrowgate) delivered Dock 4, 2:47 pm. Nothing in our system.
02 Mar  Called Dana to chase ETA — told her it hadn’t shown up. (She may
        remember this as just an ETA question. I’m sure I said it wasn’t here.)
06 Mar  AR reminder from Harrowgate for INV-2026/0347.
09 Mar  Dana “past due” email. Replied: the shipment was never received.
10 Mar  Tom checked logs: no receipt for our PO. BUT: unlabeled pallet in Q-3
        on 24 Feb, wrap said “HFS”, given back to Tri-County on 4 Mar. Ugh.
12 Mar  Dana sent POD signed “TK”. Tom says not his. José signed it?
17 Mar  Told Dana TK isn’t a valid signature under the MSA (needs printed name).
26 Mar  Demand letter from Pellham & Stroud → forwarded to Marcus.
02 Apr  Marcus sent response. Line is down 44-B/7 since 9 Mar; bought 3,000
        from a distributor at $4.10 to bridge.
""")
    write_text(out("kettering-t", "dock4-camera-review-notes.txt"), """
DOCK 4 CAMERA REVIEW -- 2026-02-24 -- reviewed by T. Kettering 2026-03-11

Camera D4-EXT (exterior, facing door 4)
  14:41:06  Tri-County tractor #4471 backs to door 4.
  14:43:30  Driver opens trailer, pallet jack out. One pallet, white wrap.
  14:45:58  Pallet staged inside door, left side (in front of Q-3 cage).
  14:46:40  Driver and Jose Ibanez at podium. Jose signs handheld.
  14:48:15  Truck departs.

Camera D4-INT (interior)
  14:52:10  Jose moves pallet into Q-3 cage, attaches orange QUARANTINE tag.
  Label on wrap visible but not readable at this resolution. Logo looks like
  the Harrowgate "HFS" mark (compare PO-BMS-26-0098 delivery 2/12).

I was at the forklift recert in Chesapeake from 12:30 to 17:00 (sign-in sheet
from Tidewater Safety Training on file).

Footage exported to USB, given to M. Delacroix-Hale 3/27. Retention on the
NVR is 45 days so the original will be gone after 4/10.
""")


def htmls():
    html = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Order PO-BMS-26-0112 — Harrowgate Order Portal</title>
<style>body{font-family:Arial,sans-serif;margin:24px;color:#222}table{border-collapse:collapse}
td,th{border:1px solid #bbb;padding:4px 8px}th{background:#eee}.status{color:#0a6b2c;font-weight:bold}
nav a{margin-right:12px}</style></head>
<body>
<nav><a href="/orders">Orders</a><a href="/invoices">Invoices</a><a href="/account">Account</a><a href="/logout">Sign out</a></nav>
<h1>Order PO-BMS-26-0112</h1>
<p>Customer: <b>Bellwether Marine Systems, Inc.</b> (account 1187) &middot; Buyer: Zo&euml; M&uuml;ller</p>
<p>Status: <span class="status">DELIVERED</span> &mdash; 02/24/2026 2:47 PM, Norfolk VA (signed: TK)</p>
<h2>Shipment</h2>
<table>
<tr><th>Ship date</th><th>Carrier</th><th>BOL</th><th>PRO</th><th>Pallets</th><th>Weight</th></tr>
<tr><td>02/19/2026</td><td>Tri-County Freight Lines</td><td>TCF-889104</td><td>0047-2219-6</td><td>1</td><td>1,410 lb</td></tr>
</table>
<h2>Lines</h2>
<table>
<tr><th>Line</th><th>Part</th><th>Description</th><th>Ordered</th><th>Shipped</th></tr>
""" + "".join(f"<tr><td>{i}</td><td>{pn}</td><td>{d}</td><td>{q:,}</td><td>{q:,}</td></tr>\n"
                for i, (pn, d, q, p) in enumerate(LINES, 1)) + """</table>
<h2>Tracking history</h2>
<ul>
<li>02/19/2026 16:05 &mdash; Picked up, Columbus OH</li>
<li>02/20/2026 03:12 &mdash; Departed terminal, Columbus OH</li>
<li>02/23/2026 07:44 &mdash; Arrived at terminal, Richmond VA</li>
<li>02/24/2026 11:02 &mdash; Out for delivery</li>
<li>02/24/2026 14:47 &mdash; Delivered &ldquo;Dock 4&rdquo; &mdash; signed TK</li>
</ul>
<p>Invoice: <a href="/invoices/INV-2026/0347">INV-2026/0347</a> &mdash; $48,612.50 &mdash; <b>past due</b></p>
<footer><small>Saved from portal.harrowgatefastener.example on 2026-03-09 by D. Okafor</small></footer>
</body></html>
"""
    write_text(out("okafor-d", "portal-order-PO-BMS-26-0112.html"), html)

    roster = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Receiving shift roster — week of 23 Feb 2026</title></head>
<body>
<h1>Bellwether Marine Systems &mdash; Receiving</h1>
<h2>Shift roster, week of February 23, 2026</h2>
<table border="1" cellpadding="4">
<thead><tr><th>Name</th><th>Role</th><th>Mon 2/23</th><th>Tue 2/24</th><th>Wed 2/25</th><th>Thu 2/26</th><th>Fri 2/27</th></tr></thead>
<tbody>
<tr><td>Thomas Kettering</td><td>Receiving Supervisor</td><td>06:30&ndash;15:00</td><td>06:30&ndash;12:30 <em>(PM: forklift recert, offsite)</em></td><td>06:30&ndash;15:00</td><td>06:30&ndash;15:00</td><td>06:30&ndash;15:00</td></tr>
<tr><td>Jos&eacute; Ib&aacute;&ntilde;ez</td><td>Dock Associate (Dock 4)</td><td>07:00&ndash;15:30</td><td>07:00&ndash;15:30 <strong>acting lead PM</strong></td><td>07:00&ndash;15:30</td><td>OFF</td><td>07:00&ndash;15:30</td></tr>
<tr><td>Kwame Osei</td><td>Dock Associate (Docks 1&ndash;2)</td><td>07:00&ndash;15:30</td><td>07:00&ndash;15:30</td><td>OFF</td><td>07:00&ndash;15:30</td><td>07:00&ndash;15:30</td></tr>
<tr><td>Mareike Brandt</td><td>Inventory Control</td><td>08:00&ndash;16:30</td><td>08:00&ndash;16:30</td><td>08:00&ndash;16:30</td><td>08:00&ndash;16:30</td><td>OFF</td></tr>
</tbody></table>
<p>Notes: Only the supervisor or the posted acting lead may sign carrier delivery receipts. Acting lead must print full name.</p>
<p><small>Exported from WFM scheduler 2026-03-11 08:14</small></p>
</body></html>
"""
    write_text(out("kettering-t", "receiving-shift-roster-2026-02-23.html"), roster)


# ===================================================================== EMAIL


def eml(custodian, filename, headers, text=None, html=None, attachments=(), extra_top=None):
    m = email_msg(headers, text, html, attachments, boundary_seed=filename.split("_")[0].replace("-", ""))
    path = note(out(custodian, filename))
    data = m.as_bytes(policy=m.policy)
    if extra_top:  # e.g. Received / X-Folder lines a mail store would add
        data = "".join(f"{k}: {v}\r\n" for k, v in extra_top).encode() + data
    path.write_bytes(data)
    return data


def emails(inv_pdf, po_pdf_b, pod_pdf, demand_pdf, response_docx):
    eml("muller-z", "2026-02-09_PO-BMS-26-0112.eml", {
        "From": ZOE, "To": DANA, "Date": rfcdate(2026, 2, 9, 8, 41),
        "Subject": "PO-BMS-26-0112 — fasteners for hull line 2",
        "Message-ID": "<20260209084100.11a2@bellwethermarine.example>"},
        text="""Hi Dana,

Attached is PO-BMS-26-0112 for the next batch of 316 stainless hardware:

  PN 44-B/7     12,000
  PN 44-C/2     20,000
  PN 17-K.3/A   20,000
  PN 90-T/12       120

We need it on the dock by 27 Feb — hull line 2 starts the new deck fittings
the following week. Please make sure the PO number is on the packing list; our
receiving team has been strict about that since January.

Thanks,
Zoë

--
Zoë Müller | Procurement Manager
Bellwether Marine Systems, Inc. | 2200 Industrial Parkway, Norfolk VA
""", attachments=[("PO-BMS-26-0112.pdf", po_pdf_b, "application", "pdf")])

    ack_text = """Zoë,

Thanks — PO-BMS-26-0112 is acknowledged. All four lines are in stock in
Columbus. We’ll ship on or about 2/19 via Tri-County, which puts it on your
Dock 4 around 2/24. I’ve flagged the “PO on packing list” requirement with
the warehouse.

Best,
Dana Okafor
Account Manager, Harrowgate Fastener Supply Co.
(614) 555-0142 x221
"""
    eml("okafor-d", "2026-02-09_RE_PO-BMS-26-0112.eml", {
        "From": DANA, "To": ZOE, "Date": rfcdate(2026, 2, 9, 11, 2),
        "Subject": "RE: PO-BMS-26-0112 — fasteners for hull line 2",
        "Message-ID": "<20260209110200.77c1@harrowgatefastener.example>",
        "In-Reply-To": "<20260209084100.11a2@bellwethermarine.example>",
        "References": "<20260209084100.11a2@bellwethermarine.example>"}, text=ack_text)

    # HTML-only: portal shipment notification
    eml("okafor-d", "2026-02-19_shipped-PO-BMS-26-0112.eml", {
        "From": PORTAL, "To": ZOE, "Cc": DANA, "Date": rfcdate(2026, 2, 19, 16, 7),
        "Subject": "Your order PO-BMS-26-0112 has shipped",
        "Message-ID": "<portal.20260219160700.3391@harrowgatefastener.example>"},
        html="""<html><body style="font-family:Arial,sans-serif">
<table width="600" cellpadding="8" style="border:1px solid #ccc">
<tr><td style="background:#1d3b5c;color:#fff;font-size:20px">Harrowgate Fastener Supply Co.</td></tr>
<tr><td>
<p>Hello Zo&euml; M&uuml;ller,</p>
<p>Good news &mdash; your order <b>PO-BMS-26-0112</b> left our Columbus warehouse today.</p>
<table cellpadding="4" style="border-collapse:collapse">
<tr><td><b>Carrier</b></td><td>Tri-County Freight Lines</td></tr>
<tr><td><b>Bill of lading</b></td><td>TCF-889104</td></tr>
<tr><td><b>PRO number</b></td><td>0047-2219-6</td></tr>
<tr><td><b>Pallets</b></td><td>1 (1,410 lb)</td></tr>
<tr><td><b>Estimated delivery</b></td><td>Tuesday, February 24, 2026</td></tr>
</table>
<p>Lines shipped: PN&nbsp;44-B/7 &times; 12,000 &middot; PN&nbsp;44-C/2 &times; 20,000 &middot; PN&nbsp;17-K.3/A &times; 20,000 &middot; PN&nbsp;90-T/12 &times; 120</p>
<p>Your invoice will follow separately. Track this order any time in the <a href="https://portal.harrowgatefastener.example/orders/PO-BMS-26-0112">order portal</a>.</p>
<p style="color:#777;font-size:11px">This is an automated message. Please don&rsquo;t reply &mdash; contact your account manager, Dana Okafor, instead.</p>
</td></tr></table></body></html>
""")

    # HTML-only: carrier delivery notification
    eml("okafor-d", "2026-02-24_delivered-PRO-0047-2219-6.eml", {
        "From": TCFL, "To": DANA, "Date": rfcdate(2026, 2, 24, 14, 49),
        "Subject": "Delivered: PRO 0047-2219-6 (BOL TCF-889104)",
        "Message-ID": "<tcfl.0047-2219-6.delivered@tricountyfreight.example>"},
        html="""<html><body>
<h3>Delivery notification</h3>
<p>PRO <b>0047-2219-6</b> was delivered.</p>
<ul>
<li>Consignee: BELLWETHER MARINE SYSTEMS INC, NORFOLK VA</li>
<li>Delivered: 02/24/2026 14:47 EST</li>
<li>Signed by: TK</li>
<li>Pieces: 1 &nbsp; Weight: 1,410</li>
</ul>
<p>Proof of delivery images are available on request from dispatch.</p>
<p><i>Tri&#8209;County Freight Lines &mdash; &ldquo;Your freight, on time.&rdquo;</i></p>
</body></html>
""")

    reminder = f"""Dear Bellwether Accounts Payable,

This is a friendly reminder that invoice INV-2026/0347 ({money(TOTAL)}, PO
PO-BMS-26-0112) is due tomorrow, March 7, 2026. A copy is attached.

If payment has already been sent, please disregard this note.

Regards,
Harrowgate Accounts Receivable
on behalf of Raymond Voss, Controller
"""
    eml("voss-r", "2026-03-06_INV-2026-0347_reminder.eml", {
        "From": AR, "To": AP, "Cc": f"{ZOE}, {VOSS}", "Date": rfcdate(2026, 3, 6, 9, 15),
        "Subject": "Payment reminder: INV-2026/0347 due 3/7/2026",
        "Message-ID": "<ar.20260306091500.0347@harrowgatefastener.example>"},
        text=reminder, attachments=[("INV-2026-0347.pdf", inv_pdf, "application", "pdf")])

    past_due = """Hi Zoë,

Following up on the note from our AR team on Friday — INV-2026/0347 for
$48,612.50 is now past due (it was due 3/7). Could you check with AP on the
status? Happy to resend anything they need.

Thanks,
Dana
"""
    zoe_reply = """Dana,

I’m afraid there is nothing to pay yet: the shipment was never received.
Our receiving team has no record of PO-BMS-26-0112 arriving at Dock 4, and
hull line 2 has been waiting on the 44-B/7 bolts since last week. I told you
as much when I called on the 2nd.

Can you send me the carrier’s proof of delivery? Until we see it, AP has
the invoice on hold.

Zoë
"""
    past_due_attr = "On Mon, Mar 9, 2026 at 10:14 AM Dana Okafor <dana.okafor@harrowgatefastener.example> wrote:"
    reply_body = zoe_reply + "\n" + past_due_attr + "\n" + quote(past_due) + "\n"
    reply_headers = {
        "From": ZOE, "To": DANA, "Cc": AP, "Date": rfcdate(2026, 3, 9, 10, 58),
        "Subject": "RE: INV-2026/0347 past due",
        "Message-ID": "<20260309105800.5b7e@bellwethermarine.example>",
        "In-Reply-To": "<20260309101400.8a03@harrowgatefastener.example>",
        "References": "<20260309101400.8a03@harrowgatefastener.example>"}
    # Same message in two mailboxes; the recipient's copy has transport headers on top.
    eml("muller-z", "2026-03-09_RE_INV-2026-0347_past-due.eml", reply_headers, text=reply_body,
        extra_top=[("X-Folder", "Sent Items")])
    eml("okafor-d", "2026-03-09_RE_INV-2026-0347_past-due.eml", reply_headers, text=reply_body,
        extra_top=[("Received", "from mx1.bellwethermarine.example (mx1.bellwethermarine.example [192.0.2.25])\r\n"
                                "\tby mail.harrowgatefastener.example with ESMTPS id 4Rk2Qm1Zx; Mon, 09 Mar 2026 10:58:04 -0400"),
                   ("X-Folder", "Inbox/Customers/Bellwether")])

    tom_check = """Zoë —

I went through the Dock 4 log for 2/19 through today. There is no receipt
for PO-BMS-26-0112 and nothing from Harrowgate since the 2/12 vendor-truck
delivery (PO-BMS-26-0098).

One thing you should know: on 2/24 at 14:52 José logged an unlabeled pallet
from Tri-County — no PO, no packing list — and put it in Q-3. The wrap had
“HFS” printed on it. I saw the discrepancy report on 3/2, had no PO to match
it to, and asked Tri-County to come get it. They took it on 3/4.

So: the shipment was never received against our PO, but it may have been
sitting in Q-3 for a week. Sorry. Let me know who needs to hear this.

Tom
"""
    eml("kettering-t", "2026-03-10_RE_dock-4-check-PO-BMS-26-0112.eml", {
        "From": TOM, "To": ZOE, "Date": rfcdate(2026, 3, 10, 16, 31),
        "Subject": "RE: Dock 4 check — PO-BMS-26-0112?",
        "Message-ID": "<20260310163100.c0d4@bellwethermarine.example>",
        "In-Reply-To": "<20260310081500.a9f0@bellwethermarine.example>",
        "References": "<20260310081500.a9f0@bellwethermarine.example>"},
        text=tom_check + "\nOn Tue, Mar 10, 2026 at 8:15 AM Zoë Müller wrote:\n" + quote(
            "Tom, can you check the Dock 4 log for anything from Harrowgate (PO-BMS-26-0112,\n"
            "BOL TCF-889104) since 2/19? They say it was delivered on 2/24 and signed “TK.”\n\nZ.") + "\n")

    eml("kettering-t", "2026-03-02_unclaimed-pallet-Q3.eml", {
        "From": TOM, "To": TCFL, "Date": rfcdate(2026, 3, 2, 13, 5),
        "Subject": "Unclaimed pallet at Bellwether Dock 4 - please pick up",
        "Message-ID": "<20260302130500.1e5f@bellwethermarine.example>"},
        text="""Dispatch,

You dropped a pallet at Bellwether Marine, 2200 Industrial Pkwy, Dock 4, on
2/24 (your truck 4471, PRO looks like 0047-2219-6). No PO, no packing list,
so it has been in our quarantine cage. We can't receive it. Please pick it
up and return it to the shipper.

Available weekdays 07:00-15:30. Ask for the receiving desk.

Thomas Kettering
Receiving Supervisor, Bellwether Marine Systems
""")

    pod_note = """Zoë,

Attached is the delivery receipt from Tri-County for PRO 0047-2219-6. It
shows delivery to Dock 4 on 2/24 at 2:47 pm, signed “TK” with no exceptions.
Can you check with receiving? Our controller would like to get this
resolved before the end of the month.

Dana
"""
    pod_headers = {
        "From": DANA, "To": ZOE, "Date": rfcdate(2026, 3, 12, 9, 41),
        "Subject": "Proof of delivery — PRO 0047-2219-6 / INV-2026/0347",
        "Message-ID": "<20260312094100.d1e2@harrowgatefastener.example>",
        "In-Reply-To": "<20260309105800.5b7e@bellwethermarine.example>",
        "References": "<20260309101400.8a03@harrowgatefastener.example> <20260309105800.5b7e@bellwethermarine.example>"}
    eml("okafor-d", "2026-03-12_proof-of-delivery.eml", pod_headers,
        text=pod_note + "\n" + "On Mon, Mar 9, 2026 at 10:58 AM Zoë Müller wrote:\n" + quote(reply_body) + "\n",
        attachments=[("pod-TCF-889104.pdf", pod_pdf, "application", "pdf")])

    zoe_17 = """Dana,

Thanks for the receipt. “TK” is not a signature anyone here would accept:
Tom Kettering (our receiving supervisor) did not sign it, and was offsite
that afternoon. The MSA requires a receipt signed by our receiving
personnel, and our PO requires a printed name.

In fairness I should tell you that an unlabeled pallet with HFS wrap was
quarantined that day and handed back to Tri-County on March 4 because it
had no PO or packing list. If that was ours, it needs to come back with
proper paperwork. Either way, the shipment was never received against
PO-BMS-26-0112, and we still need those bolts.

Zoë
"""
    eml("muller-z", "2026-03-17_RE_proof-of-delivery.eml", {
        "From": ZOE, "To": DANA, "Cc": TOM, "Date": rfcdate(2026, 3, 17, 11, 20),
        "Subject": "RE: Proof of delivery — PRO 0047-2219-6 / INV-2026/0347",
        "Message-ID": "<20260317112000.e3f4@bellwethermarine.example>",
        "In-Reply-To": "<20260312094100.d1e2@harrowgatefastener.example>",
        "References": "<20260309101400.8a03@harrowgatefastener.example> <20260309105800.5b7e@bellwethermarine.example> "
                      "<20260312094100.d1e2@harrowgatefastener.example>"},
        text=zoe_17 + "\nOn Thu, Mar 12, 2026 at 9:41 AM Dana Okafor wrote:\n" +
        quote(pod_note + "\nOn Mon, Mar 9, 2026 at 10:58 AM Zoë Müller wrote:\n" + quote(reply_body)) + "\n")

    eml("voss-r", "2026-03-25_demand-letter-Bellwether.eml", {
        "From": STROUD, "To": MARCUS, "Cc": VOSS, "Date": rfcdate(2026, 3, 25, 15, 32),
        "Subject": "Harrowgate Fastener Supply Co. / Bellwether Marine — demand for payment",
        "Message-ID": "<20260325153200.f00d@pellhamstroud.example>"},
        text="""Mr. Delacroix-Hale,

Please see the attached letter on behalf of Harrowgate Fastener Supply Co.
A copy follows by certified mail.

Evelyn Stroud
Pellham & Stroud LLP
""", html="""<html><body><p>Mr. Delacroix-Hale,</p>
<p>Please see the attached letter on behalf of <b>Harrowgate Fastener Supply Co.</b> A copy follows by certified mail.</p>
<p>Evelyn Stroud<br>Pellham &amp; Stroud LLP</p>
<p style="font-size:9px;color:#888">CONFIDENTIALITY NOTICE: This message may contain privileged information.</p></body></html>
""", attachments=[("demand-letter-2026-03-25.pdf", demand_pdf, "application", "pdf")])

    eml("delacroix-m", "2026-03-26_FW_demand-for-payment.eml", {
        "From": MARCUS, "To": ZOE, "Cc": TOM, "Date": rfcdate(2026, 3, 26, 8, 5),
        "Subject": "FW: Harrowgate Fastener Supply Co. / Bellwether Marine — demand for payment",
        "Message-ID": "<20260326080500.aa01@bellwethermarine.example>"},
        text="""Zoë, Tom — privileged & confidential.

Harrowgate has lawyered up. Please read the attached and do not discuss the
dispute with Harrowgate or Tri-County without me from here on. A litigation
hold notice will follow. I’d like 30 minutes with each of you tomorrow.

Marcus

-----Original Message-----
From: Evelyn Stroud <estroud@pellhamstroud.example>
Sent: Wednesday, March 25, 2026 3:32 PM
To: Marcus Delacroix-Hale <mdelacroix-hale@bellwethermarine.example>
Cc: Raymond Voss <rvoss@harrowgatefastener.example>
Subject: Harrowgate Fastener Supply Co. / Bellwether Marine — demand for payment

Mr. Delacroix-Hale,

Please see the attached letter on behalf of Harrowgate Fastener Supply Co.
A copy follows by certified mail.

Evelyn Stroud
Pellham & Stroud LLP
""", attachments=[("demand-letter-2026-03-25.pdf", demand_pdf, "application", "pdf")])

    eml("delacroix-m", "2026-03-27_litigation-hold.eml", {
        "From": MARCUS, "To": f"{ZOE}, {TOM}, {AP}", "Date": rfcdate(2026, 3, 27, 17, 45),
        "Subject": "LITIGATION HOLD — Harrowgate Fastener Supply Co. (PO-BMS-26-0112)",
        "Message-ID": "<20260327174500.ab02@bellwethermarine.example>"},
        text="""PRIVILEGED & CONFIDENTIAL - LITIGATION HOLD NOTICE

Bellwether has received a demand from Harrowgate Fastener Supply Co.
concerning PO-BMS-26-0112 and invoice INV-2026/0347. Effective immediately
you must preserve, and must not delete, alter or discard, any documents or
data relating to:

  - PO-BMS-26-0112, INV-2026/0347, BOL TCF-889104, PRO 0047-2219-6
  - Dock 4 receiving logs, discrepancy reports (incl. RDR-0224-02) and
    camera footage for February and March 2026
  - the pallet held in quarantine bay Q-3 from 2/24 to 3/4
  - communications with Harrowgate (Dana Okafor, Raymond Voss) or
    Tri-County Freight Lines

This applies to email, text messages, paper, and files on shared drives.
Normal deletion schedules are suspended for this material. Reply to
confirm you have read this notice.

Marcus Delacroix-Hale, General Counsel
""", html="""<html><body>
<p style="color:#a00"><b>PRIVILEGED &amp; CONFIDENTIAL &ndash; LITIGATION HOLD NOTICE</b></p>
<p>Bellwether has received a demand from Harrowgate Fastener Supply Co. concerning PO-BMS-26-0112 and invoice INV-2026/0347. Effective immediately you must preserve, and must not delete, alter or discard, any documents or data relating to:</p>
<ul><li>PO-BMS-26-0112, INV-2026/0347, BOL TCF-889104, PRO 0047-2219-6</li>
<li>Dock 4 receiving logs, discrepancy reports (incl. RDR-0224-02) and camera footage for February and March 2026</li>
<li>the pallet held in quarantine bay Q-3 from 2/24 to 3/4</li>
<li>communications with Harrowgate (Dana Okafor, Raymond Voss) or Tri-County Freight Lines</li></ul>
<p>This applies to email, text messages, paper, and files on shared drives. Normal deletion schedules are suspended for this material. Reply to confirm you have read this notice.</p>
<p>Marcus Delacroix-Hale, General Counsel</p></body></html>
""")

    eml("okafor-d", "2026-04-03_FW_Bellwether-response.eml", {
        "From": DANA, "To": VOSS, "Date": rfcdate(2026, 4, 3, 9, 12),
        "Subject": "FW: Bellwether response to demand (INV-2026/0347)",
        "Message-ID": "<20260403091200.b0b1@harrowgatefastener.example>"},
        text="""Ray,

Evelyn forwarded Bellwether’s response to me by mistake — attaching it here
for you. Headline: they admit the HFS pallet sat in their Q-3 for a week,
but say they could refuse it because there was no PO on the packing list.
I checked with the warehouse: the packing list sleeve was on the pallet
when it left Columbus. Not sure what happened to it.

They also claim Zoë told me on 3/2 the order was missing. My notes say she
only asked for an ETA.

Dana
""", attachments=[("response-to-demand-2026-04-02.docx", response_docx, "application",
                   "vnd.openxmlformats-officedocument.wordprocessingml.document")])


# ===================================================================== main


def main():
    inv = invoice_pdf()
    po = po_pdf()
    h1 = memo_pdf(False)
    h2 = memo_pdf(True)
    demand = demand_letter_pdf()
    deposition_notice_pdf()
    pod = pod_scan()
    discrepancy_report_scan()
    carrier_letter_scan()
    msa_docx()
    resp = response_letter_docx()
    legal_memo_docx()
    receiving_logs()
    harrowgate_csvs()
    texts()
    htmls()
    emails(inv, po, pod, demand, resp)
    dep_path = out("delacroix-m", "deposition-kettering-2026-04-28.pdf")
    dep_index = build_deposition(dep_path)
    note(dep_path)
    exhibit_list_csv(dep_index)
    print("hyphenated at line ends (memo v1):", h1)
    print("hyphenated at line ends (memo v2):", h2)
    print("deposition index:")
    for k, v in dep_index.items():
        print(f"  {k}: {v}")
    print(len(files_written), "files written")
    for f in sorted(files_written):
        print(" ", f)


if __name__ == "__main__":
    main()
