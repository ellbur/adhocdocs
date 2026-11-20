# Findings: custodian voss-r (Raymond Voss, Controller, Harrowgate)
35 documents read; none screened; none lacked text. Quotes are copied from `text` output.

### 2025-11-03 — Master Supply Agreement date (referred to only)
- Source: INV-2026-0347.pdf (pdf, 2026-02-20), locator doc:sha256:c501f9ff86bf0ce529c62ef4feaec85f74d0ad80de826789b32c278ccfbe0324; also demand letter, memo 3/18
- Quote(s): "Late payments accrue interest at 1.5% per month under §4.3 of the Master Supply Agreement dated November 3, 2025."
- Notes: Agreement itself not in this custodian's set. Letter cites s.7.2 (10-day non-receipt notice) and s.12 (AAA arbitration).

### 2026-02-09 — Order acknowledged (later recollection)
- Source: memo-collection-bellwether-2026-03-16.pdf (pdf, 2026-03-16), locator doc:sha256:77d65e59e43fea7aff0ffb419e1fac49ed54cafea8adffb5c19b69dfbd10415f; same text in 3/18 memo
- Who: Voss memo to file, cc Dana Okafor
- Quote(s): "The order was acknowledged by Dana Okafor on February 9 and shipped on February 19 by Tri-County Freight Lines under bill of lading TCF-889104 (PRO 0047-2219-6)."
- Notes: Later recollection; no order acknowledgement itself in this set.

### 2026-02-19 — Shipment of PO-BMS-26-0112 by Tri-County Freight Lines
- Source: outbound-shipping-log-2026-02.csv (spreadsheet, undated), locator doc:sha256:5be65c5e20366de58d1995eebb46201d05850ecdc495154321b5e3d3671a2675; INV-2026-0347.pdf (pdf, 2026-02-20), locator doc:sha256:c501f9ff...0347 (copy at doc:sha256:c57b1aa59d0f292d5ac2524f1dd48320b1ecefebdb75d66bcc1bfc95f15410eb, identical text)
- Who: Harrowgate shipping log (contemporaneous business record, internal)
- Quote(s): log row: "2026-02-19	TCF-889104	0047-2219-6	Bellwether Marine Systems	PO-BMS-26-0112	INV-2026/0347	1	1410	Tri-County Freight Lines	DELIVERED	2026-02-24	TK". Invoice: "PO-BMS-26-0112 02/19/2026 Tri-County Freight Lines TCF-889104 0047-2219-6" and "Shipment: 1 pallet, 48″ × 40″ × 52″, 1,410 lb, shrink-wrapped, HFS label on two sides."
- Notes: Log's POD signer is "TK" (initials only), unlike other customers' rows (P. LIM, R. HALVORSEN, J. DOE, M.K.). Bellwether transit 5 days (2/19 to 2/24), longer than the other rows' 1-4 days (observation). Invoice INV-2026/0347 is out of sequence in a log otherwise numbered 0301-0335 (observation; invoice dated 2/20 but listed under 2/19 ship date).

### 2026-02-20 — Invoice INV-2026/0347 issued, $48,612.50
- Source: INV-2026-0347.pdf (pdf, 2026-02-20), two identical copies, locators doc:sha256:c501f9ff86bf0ce529c62ef4feaec85f74d0ad80de826789b32c278ccfbe0324 and doc:sha256:c57b1aa59d0f292d5ac2524f1dd48320b1ecefebdb75d66bcc1bfc95f15410eb
- Who: Harrowgate to Bellwether Marine Systems, Inc., Attn: Zoë Müller, Procurement, Receiving: Dock 4
- Quote(s): "Terms: Net 15 (due March 7, 2026)"; "Freight (FOB Destination, prepaid & added) $1,044.50"; "TOTAL DUE $48,612.50"
- Notes: Subtotal $47,568.00 + freight $1,044.50 = $48,612.50. Items: 12,000 PN 44-B/7 bolts, 20,000 PN 44-C/2 washers, 20,000 PN 17-K.3/A nuts, 120 PN 90-T/12 sealant. The "Net 15" term for a 2/20 invoice gives 3/7, consistent. Note the "Ship To" is Norfolk, VA (Dock 4).

### 2026-02-24 (2:47 p.m.) — Claimed delivery at Dock 4, signed "TK"
- Source: shipping log (above); memo 3/16 (locator doc:sha256:77d65e59...f10b... = 77d65e59e43fea7aff0ffb419e1fac49ed54cafea8adffb5c19b69dfbd10415f)
- Who: Carrier POD as relayed by Voss
- Quote(s): "the carrier’s proof of delivery shows the pallet was delivered to Dock 4 on February 24, 2026 at 2:47 p.m. and signed for with the ini-tials “TK.”"
- Notes: The actual carrier POD is not in this set; time and signer come only from Voss's memo (secondhand) and the log's "TK".

### 2026-03-04 — Pallet picked up from Bellwether as "unclaimed freight" (later account, unverified)
- Source: memo-collection-bellwether-2026-03-18-rev.pdf (pdf, 2026-03-18), locator doc:sha256:dbce1b4a023e84a9d664c058d9ff3f3d68e24fdb2a37f20ac264d2107f2e24c1; demand letter (locator doc:sha256:223bc158f82427ba3627bfd667b52f422013d7ca13926e23da6a506bee6f890d); credit-hold-notes.txt (locator doc:sha256:d2bb61763d2105bbf20e9bf7d0595b19417285918f56cb605f00eddcd0b5b6ee)
- Who: Tri-County dispatcher told Dana Okafor (3/17); Voss memo; Stroud letter
- Quote(s): Memo: "The dispatcher also mentioned, almost in passing, that a pallet was picked up from Bellwether on March 4 as “unclaimed freight.” If that is our pallet, Bellwether had it for eight days." Letter: "We understand, moreover, that on or about March 4, 2026 Bellwether released a Harrowgate pallet to the carrier as “unclaimed.”" Credit log 4/13: "Pallet at TCFL Richmond, storage RS-2026-0412, $35/day from 3/11."
- Notes: Voss was uncertain ("If that is our pallet"); the demand letter states it as an understanding, not a fact. Storage billing begins 3/11, a week after the claimed 3/4 pickup (gap unexplained; inference only). Same day, 3/4, Harrowgate shipped a different Bellwether order (PO-BMS-26-0131, INV-2026/0388).

### 2026-03-06 (09:15 -0500) — Payment reminder to Bellwether AP
- Source: 2026-03-06_INV-2026-0347_reminder.eml (email), locator doc:sha256:79fa272f6ba08fc07d9f73a034d4539a667f4e57252a1b156c488c1a47b9970a
- Who: Harrowgate AR (on behalf of Voss) to Bellwether AP, cc Zoë Müller and Voss
- Quote(s): "This is a friendly reminder that invoice INV-2026/0347 ($48,612.50, PO PO-BMS-26-0112) is due tomorrow, March 7, 2026."
- Notes: Contemporaneous. Müller was copied; no reply from her is in this set. Bellwether said nothing on 3/6 as far as this set shows.

### 2026-03-07 — Invoice due date; no payment
- Source: memo 3/16; ar-aging-2026-03-31.csv (spreadsheet, locator doc:sha256:bd54a5cf28ddec77b5cb074f06db966629ee5f06e6c730220391a8ccc495f527)
- Quote(s): "has not paid invoice INV-2026/0347 for $48,612.50, which was due on March 7, 2026."

### 2026-03-09 — Bellwether (Zoë Müller) first disputes receipt (as reported by Harrowgate)
- Source: memo 3/16 and 3/18-rev; demand letter 3/25; ar-aging note
- Who: Müller to Harrowgate (the email itself is not in this set)
- Quote(s): Memo: "Bellwether’s procurement man-ager, Zoë Müller, wrote on March 9 that “the shipment was never received.”" Letter: "Bellwether’s first communication disputing delivery was sent on March 9, 2026." AR aging: "customer says “never received” - see memo 3/18; CREDIT HOLD 3/19"
- Notes: Secondhand only; the underlying message is not in this custodian's documents.

### 2026-03-16 — Voss memo to file (first version)
- Source: memo-collection-bellwether-2026-03-16.pdf (pdf, 2026-03-16), locator doc:sha256:77d65e59e43fea7aff0ffb419e1fac49ed54cafea8adffb5c19b69dfbd10415f
- Who: Voss to File; cc Dana Okafor
- Quote(s): "Under the Master Supply Agreement the terms are FOB Destination, so risk of loss stays with us until delivery; the question is therefore whether delivery happened, not whether the goods were later misplaced." / "Dana should ask Bellwether’s receiv-ing supervisor to check whether the signature on the delivery receipt belongs to anyone on his team." / "Its accounts receivable balance with us is otherwise current."
- Notes: Voss suggests the "TK" signature might not belong to Bellwether staff, which sits uneasily with the later firm assertion of a "signed delivery receipt". Recommends credit hold and referral for a demand letter. Written before the carrier contact on 3/17. Contemporaneous with the events of 3/9-3/16 but a recollection of 2/24.

### 2026-03-17 — Tri-County dispatcher call to Dana Okafor
- Source: memo-collection-bellwether-2026-03-18-rev.pdf (pdf, 2026-03-18), locator doc:sha256:dbce1b4a023e84a9d664c058d9ff3f3d68e24fdb2a37f20ac264d2107f2e24c1
- Who: Dispatcher to Okafor, relayed by Voss
- Quote(s): "the driver, D. Pruitt, remembers leaving the pallet with “a guy in a hi-vis vest” at Dock 4 and that the carrier’s scanner timestamp matches the proof of delivery."
- Notes: Double hearsay (driver to dispatcher to Okafor to Voss). Driver's description does not identify a named Bellwether receiver ("TK").

### 2026-03-18 — Voss memo (revised)
- Source: memo-collection-bellwether-2026-03-18-rev.pdf, locator doc:sha256:dbce1b4a023e84a9d664c058d9ff3f3d68e24fdb2a37f20ac264d2107f2e24c1
- Who: Voss to File; cc Okafor
- Quote(s): "We should not reship until the location of the original pallet is confirmed." / "(1) Place Bellwether on credit hold effective March 19; (2) ask Tri-County to confirm in writing what it picked up on March 4 and where that pallet is now; (3) refer the matter to Pellham & Stroud LLP for a demand letter if pay-ment is not received by March 24."
- Notes: Revision of 3/16. Dropped the 3/16 suggestion to check the signature with Bellwether's receiving supervisor; added the carrier position, the March 4 "unclaimed freight" pickup and the interest figure ("approximately $49,177.03" through March 31). The 3/16 version is silent on the March 4 pickup.

### 2026-03-19 — Credit hold placed on Bellwether
- Source: credit-hold-notes.txt (text, undated), locator doc:sha256:d2bb61763d2105bbf20e9bf7d0595b19417285918f56cb605f00eddcd0b5b6ee
- Who: Finance log, approved R. Voss
- Quote(s): "Hold reason: INV-2026/0347 disputed, $48,612.50, 12 days past due." / "Do not release PO-BMS-26-0140 without Controller sign-off."
- Notes: Contemporaneous log entry (may be updated over time; entries run to 4/20). Other open orders: PO-BMS-26-0131 (shipped 3/4, INV-2026/0388, $6,120.00), PO-BMS-26-0140 not yet released.

### 2026-03-24 — No payment; referred to outside counsel
- Source: credit-hold-notes.txt
- Quote(s): "BELLWETHER -- no payment. Referred to Pellham & Stroud (E. Stroud)."

### 2026-03-25 (15:32 -0400) — Demand letter to Bellwether GC
- Source: demand-letter-2026-03-25.pdf (pdf, two identical copies), locators doc:sha256:223bc158f82427ba3627bfd667b52f422013d7ca13926e23da6a506bee6f890d and doc:sha256:b7172f6ab8cfc2a07ef3d45a2bdcaa36dafd8503a1b3abc071b609a462d0dc42; cover email 2026-03-25_demand-letter-Bellwether.eml, locator doc:sha256:277ec343e013f13418cc33e47e47a38553eb2d2f4a61abd784125575e42fe75f
- Who: Evelyn Stroud, Pellham & Stroud LLP, to Marcus Delacroix-Hale, GC, Bellwether; cc Voss
- Quote(s): "were tendered by Tri-County Freight Lines and delivered to Bellwether’s Dock 4 on February 24, 2026, as shown by the signed delivery receipt for bill of lading TCF-889104." / "Section 7.2 of the Agreement required Bellwether to give written notice of any non-receipt or shortage within ten (10) days after the invoice date, i.e., by March 2, 2026. Harrowgate received no such notice." / "Demand is hereby made for payment of $48,612.50, plus interest at 1.5% per month under Section 4.3 of the Agreement, within ten (10) days of the date of this letter."
- Notes: Threatens AAA arbitration under s.12 and includes a preservation demand (receiving logs, dock camera footage, carrier communications). Email body: "Please see the attached letter on behalf of Harrowgate Fastener Supply Co."

### 2026-04-10 / 2026-04-13 — Carrier claim declined; pallet at Tri-County Richmond
- Source: credit-hold-notes.txt
- Quote(s): "BELLWETHER -- TCFL claim TC-26-01953 declined 4/10. Pallet at TCFL Richmond, storage RS-2026-0412, $35/day from 3/11. Asked counsel whether we should take the pallet back without prejudice."
- Notes: Unclear who filed claim TC-26-01953 (inference: Harrowgate). Storage at ~$35/day accrues; "TCFL" here versus "Tri-County Freight Lines" elsewhere (same carrier inferred).

### 2026-04-20 — Unrelated Ridgeway hold lifted (not about this shipment).

### 2026-03-31 — AR aging snapshot
- Source: ar-aging-2026-03-31.csv, locator doc:sha256:bd54a5cf28ddec77b5cb074f06db966629ee5f06e6c730220391a8ccc495f527
- Quote(s): "Bellwether Marine Systems	INV-2026/0347	2026-02-20	2026-03-07	48612.50	0.00	48612.50	0.00	0.00	DISPUTED	customer says “never received” - see memo 3/18; CREDIT HOLD 3/19"
- Notes: Bucketed in 1-30 days past due. Also lists INV-2026/0388 $6,120.00 OPEN, PO-BMS-26-0131.

## Contradictions noticed
1. Memo 3/16 says Bellwether "has not paid" and asks whether the "TK" signature belongs to anyone on Bellwether's team; the demand letter 3/25 relies on a "signed delivery receipt" with no hint of that doubt. Nothing in this set identifies "TK".
2. Memo 3/16 versus 3/18-rev: the 3/18 version adds the March 4 "unclaimed freight" pickup and the driver's "guy in a hi-vis vest"; the 3/16 version omits both. Voss hedges ("If that is our pallet"); the demand letter states Bellwether "released a Harrowgate pallet to the carrier as 'unclaimed'" as fact-like. Bellwether's own possession of the pallet for "eight days" is argued from an unconfirmed identification.
3. Pickup 3/4 (memo/letter) versus storage billed "from 3/11" (credit log): a one-week gap. Inference only.
4. Delivery time (2:47 p.m.) and signer "TK" appear only in Voss's memo and a log; the POD itself is absent. Log shows 5 transit days for this shipment against 1-4 for the rest.
5. Letter's s.7.2 notice deadline "March 2, 2026" assumes 10 days from the 2/20 invoice date; Harrowgate's own reminder on 3/6 went to Bellwether AP and Müller without any dispute reply in this set.
6. Memo 3/16 and 3/18 say the rest of Bellwether's account is "otherwise current", but INV-2026/0388 (shipped 3/4) was sent while the dispute was building; the credit-hold log treats it as an affected open order.

## Screened (not read)
None. All 35 documents opened with exit status 0.

## No text available
None.

## Unrelated
Company mail not about this shipment (all-staff notices, newsletters, desk move, fridge cleanout, survey, close checklists, sales tax, PBC audit lists, AP run, forklift training, "Whose car?", lost water bottle, ugly sweater contest, holiday schedule): 2025-08-19 Survey, 2025-08-21 Ugly sweater, 2025-09-23 Survey, 2025-10-10 Herald, 2025-10-23 October close, 2025-11-03 November close, 2026-01-16 Herald, 2026-01-19 Desk move, 2026-01-23 Lost bottle, 2026-01-23 Herald, 2026-01-29 Desk move, 2026-02-16 Whose car, 2026-02-20 Sales tax, 2026-02-27 PBC, 2026-03-13 Holiday schedule, 2026-03-18 PBC, 2026-03-24 Fridge, 2026-03-30 March close, 2026-05-15 Herald, 2026-06-05 AP run, 2026-06-05 Fridge, 2026-06-16 Desk move, 2026-06-23 forklift training, 2026-07-14 Sales tax.
