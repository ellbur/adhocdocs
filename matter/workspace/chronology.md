# Chronology — Harrowgate Fastener Supply Co. v. Bellwether Marine: PO-BMS-26-0112

*Prepared 2026-11-20 for the partner meeting. Covers the period from the order to Bellwether's April 2 response to the demand, with the carrier's later handling of the pallet and the deposition added for context.*

**How to read this.** Each entry gives the date, what happened, and what the people involved wrote or said **at the time**, quoted exactly from the document. Every document is cited by its file name and, where it helps, its custodian. Each entry is marked as one of two kinds:
- **[contemporaneous]**: a record made at or near the event, such as an email, log row, carrier notice or pickup order.
- **[later account]**: notes, memos, letters written as advocacy, or testimony.

Places where the documents disagree are marked **⚠ Conflict**, and my own reasoning is marked *Inference*. The parties are Harrowgate (**H**, the seller), Bellwether (**B**, the buyer) and Tri-County Freight Lines (**TCFL**, the carrier).

**The people:**

| Person | Side | Role |
|---|---|---|
| Dana Okafor | H | account manager |
| Raymond Voss | H | controller |
| Evelyn Stroud | H | outside counsel, Pellham & Stroud |
| Zoë Müller | B | procurement manager |
| Thomas (Tom) Kettering | B | receiving supervisor |
| José Ibáñez | B | dock worker, acting lead on the afternoon of Feb. 24 |
| Marcus Delacroix-Hale | B | general counsel |
| D. Pruitt | TCFL | delivery driver |
| R. Ames | TCFL | pickup driver |

---

## Before the order

**2025-11-03: Master Supply Agreement signed (Voss for H; Delacroix-Hale for B)** [contemporaneous]
- §5: "“Delivery” means physical tender at Buyer’s receiving dock during receiving hours, evidenced by a delivery receipt signed by Buyer’s receiving personnel."
- §7.2: "Any claim of non-receipt or shortage must be made in writing within ten (10) days after the invoice date, or it is waived."
- §6 allows Buyer to "quarantine or refuse any pallet" that lacks the required PO labelling.
- Terms are Net 15 and FOB Destination, with AAA arbitration.
- Source: `master-supply-agreement-2025-11-03.docx` (muller-z)

## The order and shipment

**2026-02-09, 08:41: Bellwether issues PO-BMS-26-0112 ($48,612.50; required by Feb. 27)** [contemporaneous]
- The PO: "Every pallet must carry the PO number PO-BMS-26-0112 on a packing list in a sleeve on the outside of the wrap. Pallets without a PO reference are quarantined in bay Q-3 and may be refused." It also says: "The receiving supervisor or a designated dock lead must sign the carrier’s delivery receipt and print their name."
- Müller's cover email to Okafor: "Please make sure the PO number is on the packing list; our receiving team has been strict about that since January."
- Sources: `PO-BMS-26-0112.pdf` (2 identical copies), `2026-02-09_PO-BMS-26-0112.eml` (muller-z)

**2026-02-09, 11:02: Okafor acknowledges the PO** [contemporaneous]
- Okafor to Müller: "We’ll ship on or about 2/19 via Tri-County, which puts it on your Dock 4 around 2/24. I’ve flagged the “PO on packing list” requirement with the warehouse."
- Source: `2026-02-09_RE_PO-BMS-26-0112.eml` (okafor-d)

**2026-02-19: Shipped from Columbus by TCFL (BOL TCF-889104, PRO 0047-2219-6, 1 pallet, 1,410 lb)** [contemporaneous]
- The portal notice to Müller (cc Okafor) says the order "left our Columbus warehouse today" and gives the "Estimated delivery Tuesday, February 24, 2026".
- Sources: `2026-02-19_shipped-PO-BMS-26-0112.eml` (okafor-d); H's `outbound-shipping-log-2026-02.csv` (voss-r)

**2026-02-20: Invoice INV-2026/0347 issued** [contemporaneous]
- The invoice reads "TOTAL DUE $48,612.50" and "Terms: Net 15 (due March 7, 2026)".
- It describes the pallet as "shrink-wrapped, HFS label on two sides". It does not mention a PO packing list.
- Under §7.2, the 10-day window for a written non-receipt claim ran to **March 2**.
- Source: `INV-2026-0347.pdf` (3 copies: voss-r, muller-z)

## February 24: the delivery

**2026-02-24, about 12:30: Kettering leaves the site** [later account]
- Kettering's camera notes say: "I was at the forklift recert in Chesapeake from 12:30 to 17:00". He reviewed the footage on 3/11, and the notes also mention a 3/27 handover, so they were edited after 3/27.
- The shift roster, exported 3/11, shows Kettering "(PM: forklift recert, offsite)" and Ibáñez as "acting lead PM". It also says: "Acting lead must print full name."
- Sources: `dock4-camera-review-notes.txt`, `receiving-shift-roster-2026-02-23.html` (kettering-t)
- ⚠ **Conflict:** B's own receiving log shows a different delivery (row RCV-26-0224-03, UPS Freight, dock 2) received by "T. Kettering" at **14:52 that day**, when he says he was in Chesapeake (`dock-receiving-log-2026-02.csv`).
- ⚠ **Conflict:** a Facilities email puts "The annual forklift recertification for Dock 4 operators" on **Thursday 3/5**, in the on-site training room (`Dock 4 forklift recertification - Thursday 3_5.eml`). That may be a different session; Tidewater's sign-in sheet would settle it, and it is not in the set.

**2026-02-24, 14:41–14:52: TCFL truck 4471 drops one pallet at Dock 4; Ibáñez signs the handheld; the pallet goes to quarantine** [contemporaneous records, with later footage notes]
- TCFL's notice to Okafor reads "Delivered: 02/24/2026 14:47 EST" and "Signed by: TK". It adds: "Proof of delivery images are available on request from dispatch." Source: `2026-02-24_delivered-PRO-0047-2219-6.eml` (okafor-d).
- B's receiving log row RCV-26-0224-07 (14:52):
  - PO field blank; vendor "UNKNOWN (wrap printed “HFS”)"
  - "UNLABELED PALLET - NO PO REF - no packing list", status QUARANTINE, bay Q-3, received by J. Ibáñez
  - Note: "signed carrier handheld as TK (supervisor of record); see RDR-0224-02"
  - Source: `dock-receiving-log-2026-02.csv` (kettering-t)
- The camera notes say "Jose signs handheld" and "Jose moves pallet into Q-3 cage". Source: `dock4-camera-review-notes.txt`, a later review.
- Ibáñez's discrepancy report (RDR-0224-02), as Kettering read it into the deposition: "Driver in a hurry, would not wait for supervisor. Driver had me sign his handheld, I wrote TK because Tom is the supervisor of record."
  - The report itself, `receiving-discrepancy-report-RDR-0224-02.pdf`, is a scan with **no text available** (no OCR), so I could not read it directly.
- ⚠ **Conflict:** "TK" is Kettering's initials, but on B's own records Ibáñez wrote them. The PO and the roster both require the signer to print a full name.
- *Inference:* the log note's cross-reference to RDR-0224-02 suggests it was written or edited after the report. The CSV carries no edit history.

## February 24 – March 9: the pallet sits in Q-3 and goes back to the carrier

**2026-03-02, about 11:40: Müller calls Okafor** [two later accounts that conflict]
- **H's version.** Okafor's call notes say Müller "Asked if the order had shipped yet, production line needs the 44-B/7 bolts by 3/9." Okafor "Told her it shipped 2/19, TCFL, BOL TCF-889104, should have delivered last week." Müller "Did NOT say it was missing - just asking for ETA." The notes add: "(Note: the 10-day notice window under the MSA closed today.)" Source: `call-notes-bellwether-2026-03.txt` (okafor-d).
  - The notes are undated and run to 3/17. *Inference:* the §7.2 aside reads like a later gloss.
- **B's version.** Müller wrote on 3/9: "I told you as much when I called on the 2nd." B's Apr. 2 letter says Müller "notified Ms. Okafor by telephone on March 2, 2026 that the order had not arrived."
- ⚠ **Conflict:** this is a direct dispute about the same call.
  - Müller's own notes give ground: "Called Dana to chase ETA — told her it hadn’t shown up. (She may remember this as just an ETA question. I’m sure I said it wasn’t here.)" Source: `dispute-timeline-notes.txt` (muller-z), which is undated and written after 2 Apr.
  - Either way the notice was oral, and §7.2 requires it "in writing".

**2026-03-02, 13:05: Kettering asks TCFL to collect the pallet** [contemporaneous]
- Kettering to TCFL dispatch: "You dropped a pallet at Bellwether Marine, 2200 Industrial Pkwy, Dock 4, on 2/24 (your truck 4471, PRO looks like 0047-2219-6). No PO, no packing list, so it has been in our quarantine cage. We can't receive it. Please pick it up and return it to the shipper."
- He does not mention Harrowgate. At deposition he said: "I didn't know it was Harrowgate's."
- Source: `2026-03-02_unclaimed-pallet-Q3.eml` (kettering-t; 2 more copies named `Unclaimed pallet at Bellwether Dock 4 - please pick up.eml`)
- *Inference:* Müller's call to Okafor and Kettering's email to TCFL came about 90 minutes apart. Nothing shows that either knew of the other.

**2026-03-02, 15:10: TCFL dispatch replies** [contemporaneous]
- "Pickup is scheduled for Wednesday 3/4 … driver R. Ames, truck 4471".
- "We will hold the pallet at our Richmond terminal as refused freight until the shipper tells us what to do with it."
- Source: `RE_ Unclaimed pallet at Bellwether Dock 4 - please pick up.eml` (kettering-t)

**2026-03-04, 10:20: TCFL takes the pallet back** [contemporaneous]
- The pickup order reads "Reference: PRO 0047-2219-6 (BOL TCF-889104)", "1 pallet, unlabeled, shrink wrap printed HFS Weight: 1,410 lb" and "Disposition: refused freight". Source: `pickup-order-PU-0304-118.pdf`.
  - This ties the Q-3 pallet to Harrowgate's BOL on the carrier's own paperwork.
- B's log reads "RETURNED TO CARRIER - unclaimed pallet from Q-3", status RELEASED. Source: `dock-receiving-log-2026-03.csv` (kettering-t).
- At deposition, Kettering agreed the pallet was in Q-3 for "Eight days." He also testified: "Quarantined freight isn't opened until it's identified." **No one has opened the pallet, so the documents never show that it held the PO goods.**
- ⚠ **Conflict:** the carrier calls it "refused freight", B's log calls it "unclaimed", and H's demand says B "released a Harrowgate pallet to the carrier as “unclaimed.”"
- Also on 3/4, H shipped a different Bellwether order (PO-BMS-26-0131, INV-2026/0388). Source: `credit-hold-notes.txt` (voss-r). *Inference:* this could cause confusion about which pallet is which.

## The demand for payment

**2026-03-06, 09:15: H's accounts receivable sends a payment reminder to B's accounts payable (cc Müller)** [contemporaneous]
- "This is a friendly reminder that invoice INV-2026/0347 ($48,612.50, PO PO-BMS-26-0112) is due tomorrow, March 7, 2026."
- No reply from B appears in the set.
- Source: `2026-03-06_INV-2026-0347_reminder.eml` (voss-r)

**2026-03-07:** the invoice falls due and is not paid. Source: `ar-aging-2026-03-31.csv` (voss-r).

**2026-03-09, 10:14 and 10:58: Okafor's past-due email, and Müller's first written non-receipt claim** [contemporaneous]
- Okafor: "INV-2026/0347 for $48,612.50 is now past due (it was due 3/7)."
- Müller: "I’m afraid there is nothing to pay yet: the shipment was never received. Our receiving team has no record of PO-BMS-26-0112 arriving at Dock 4, and hull line 2 has been waiting on the 44-B/7 bolts since last week." She adds: "I told you as much when I called on the 2nd." And: "Can you send me the carrier’s proof of delivery? Until we see it, AP has the invoice on hold."
- Source: `2026-03-09_RE_INV-2026-0347_past-due.eml` (muller-z; okafor-d)
- ⚠ **Conflict:** "never received" was written the day *before* Kettering's log check, and it does not mention the Q-3 pallet. That pallet had been on B's dock until five days earlier.
- H's demand letter calls 3/9 "Bellwether’s first communication disputing delivery". That is seven days after the §7.2 deadline.

**2026-03-10: Müller asks Kettering to check; he finds the pallet** [contemporaneous]
- 08:15, Müller to Kettering: "can you check the Dock 4 log for anything from Harrowgate (PO-BMS-26-0112, BOL TCF-889104) since 2/19? They say it was delivered on 2/24 and signed “TK.”"
- 15:40, B's log entry: "LOG REVIEW - no receipt found for PO-BMS-26-0112", status NOT RECEIVED. It cross-refers to RCV-26-0224-07 and RDR-0224-02. Source: `dock-receiving-log-2026-03.csv`.
- 16:31, Kettering to Müller: "There is no receipt for PO-BMS-26-0112 and nothing from Harrowgate since the 2/12 vendor-truck delivery (PO-BMS-26-0098)." He continues: "on 2/24 at 14:52 José logged an unlabeled pallet from Tri-County — no PO, no packing list — and put it in Q-3. The wrap had “HFS” printed on it. I saw the discrepancy report on 3/2, had no PO to match it to, and asked Tri-County to come get it. They took it on 3/4." And: "So: the shipment was never received against our PO, but it may have been sitting in Q-3 for a week. Sorry."
- Source: `2026-03-10_RE_dock-4-check-PO-BMS-26-0112.eml` (kettering-t)
- ⚠ **Conflict:** the same email says "nothing from Harrowgate" and then describes the HFS pallet. It does not mention that "TK" was written on the handheld.
- At deposition, Kettering said of Müller's 3/10 request: "That was the first I heard of it."

**2026-03-12, 09:41: Okafor sends TCFL's proof of delivery** [contemporaneous]
- "Attached is the delivery receipt from Tri-County for PRO 0047-2219-6. It shows delivery to Dock 4 on 2/24 at 2:47 pm, signed “TK” with no exceptions. Can you check with receiving?"
- Source: `2026-03-12_proof-of-delivery.eml` (okafor-d)
- The attachment `pod-TCF-889104.pdf` (2 copies, okafor-d) is a scan with **no text available**, so I could not read the receipt itself.
- B's Apr. 2 letter calls it "only the initials “TK” and an illegible scrawl".

**2026-03-13, 15:05: Müller tells Okafor that TK is not Kettering** [later account: Okafor's notes]
- "TK would be Tom Kettering (receiving supvr) but he says he didn't sign and he was offsite that afternoon."
- Okafor "suggested they check for the pallet in their yard".
- Source: `call-notes-bellwether-2026-03.txt` (okafor-d)

**2026-03-16: Voss's memo to file (first version)** [contemporaneous internal memo]
- "the question is therefore whether delivery happened, not whether the goods were later misplaced."
- "Dana should ask Bellwether’s receiv-ing supervisor to check whether the signature on the delivery receipt belongs to anyone on his team." (The hyphen is in the original.)
- Source: `memo-collection-bellwether-2026-03-16.pdf` (voss-r)
- ⚠ **Conflict:** Voss doubted the signature internally. The 3/25 demand relies on "the signed delivery receipt" without qualification.

**2026-03-17, 11:20: Müller rejects the receipt but discloses the quarantined pallet** [contemporaneous]
- Müller to Okafor, cc Kettering: "“TK” is not a signature anyone here would accept: Tom Kettering (our receiving supervisor) did not sign it, and was offsite that afternoon."
- She goes on: "In fairness I should tell you that an unlabeled pallet with HFS wrap was quarantined that day and handed back to Tri-County on March 4 because it had no PO or packing list. If that was ours, it needs to come back with proper paperwork."
- Source: `2026-03-17_RE_proof-of-delivery.eml` (muller-z)
- This is B's first disclosure to H of the pallet. Her later notes summarize this email without mentioning the pallet.

**2026-03-17, 14:20: TCFL dispatch tells Okafor the driver remembers the drop** [later account: Okafor's notes]
- "driver Pruitt remembers the drop."
- "TCFL picked up an "unclaimed" pallet at Bellwether on 3/4 !!  Told Ray."
- Voss's 3/18 memo relays that the driver "remembers leaving the pallet with “a guy in a hi-vis vest” at Dock 4". This is several steps removed from the driver (driver → dispatcher → Okafor → Voss).
- Sources: `call-notes-bellwether-2026-03.txt` (okafor-d); `memo-collection-bellwether-2026-03-18-rev.pdf` (voss-r)

**2026-03-18: Voss's revised memo** [contemporaneous internal memo]
- "If that is our pallet, Bellwether had it for eight days."
- "We should not reship until the location of the original pallet is confirmed."
- It recommends a credit hold from 3/19 and referral to Pellham & Stroud if B hasn't paid by 3/24.
- Unlike the 3/16 memo, it drops the suggestion to check the signature with Kettering.
- Source: `memo-collection-bellwether-2026-03-18-rev.pdf` (voss-r)

**2026-03-19 and 03-24: credit hold, then referral to counsel** [contemporaneous log]
- "Hold reason: INV-2026/0347 disputed, $48,612.50, 12 days past due."
- Then: "BELLWETHER -- no payment. Referred to Pellham & Stroud (E. Stroud)."
- Source: `credit-hold-notes.txt` (voss-r)

**2026-03-25: demand letter, Stroud to Delacroix-Hale (cc Voss)** [advocacy]
- It says the goods "were tendered by Tri-County Freight Lines and delivered to Bellwether’s Dock 4 on February 24, 2026, as shown by the signed delivery receipt for bill of lading TCF-889104."
- On notice: "Section 7.2 of the Agreement required Bellwether to give written notice of any non-receipt or shortage within ten (10) days after the invoice date, i.e., by March 2, 2026. Harrowgate received no such notice."
- On the pallet: "We understand, moreover, that on or about March 4, 2026 Bellwether released a Harrowgate pallet to the carrier as “unclaimed.”"
- "Demand is hereby made for payment of $48,612.50, plus interest at 1.5% per month under Section 4.3 of the Agreement, within ten (10) days of the date of this letter."
- It also demands preservation of records and threatens AAA arbitration.
- Sources: `demand-letter-2026-03-25.pdf` (3 copies: voss-r, delacroix-m); cover email `2026-03-25_demand-letter-Bellwether.eml` (voss-r)
- ⚠ Müller's notes date the letter's arrival as "26 Mar". *Inference:* that is the date she received it.

**2026-03-26 to 03-27: B's internal handling (not read)**
- These are Delacroix-Hale's forward of the demand to Müller, Kettering's reply to him, the litigation hold and a Delacroix-Hale memo. All are held back as possibly privileged; see the last section.
- Kettering's camera notes say "Footage exported to USB, given to M. Delacroix-Hale 3/27." They also note a 45-day retention period on the recorder, which would put the original footage past its overwrite date around 4/10.

## The response to the demand

**2026-04-02: Delacroix-Hale's response to Stroud (cc Müller)** [advocacy]
- "Bellwether disputes the invoice in full."
- "No pallet bearing purchase order PO-BMS-26-0112 was received at Dock 4 on February 24, 2026 or on any other date."
- "Our receiving supervisor, Thomas Kettering, did not sign it; he was attending an offsite forklift-safety recertification that afternoon."
- "The pallet your letter refers to carried neither. It was quarantined in bay Q-3 in accordance with Section 6 and returned to Tri-County Freight Lines on March 4, 2026 as unidentified freight."
- On notice: "In any event Ms. Müller notified Ms. Okafor by telephone on March 2, 2026 that the order had not arrived."
- "Bellwether remains willing to accept a conforming delivery of the goods ordered under PO-BMS-26-0112 and to pay for them on the Agreement’s terms."
- Source: `response-to-demand-2026-04-02.docx` (muller-z; okafor-d)
- ⚠ **Conflicts:**
  - The letter says no PO pallet was received "on any other date", yet concedes the Q-3 pallet was on the dock.
  - It does not say who *did* write "TK". B's own log and discrepancy report say Ibáñez did.
  - It relies on an oral notice on 3/2. §7.2 requires notice in writing, and Okafor's notes deny the call was a notice at all.
- Müller's notes add that B bought replacement bolts at a higher price: "bought 3,000 from a distributor at $4.10 to bridge" (`dispute-timeline-notes.txt`).

**2026-04-03, 09:12: Okafor forwards the response to Voss** [later account]
- "they admit the HFS pallet sat in their Q-3 for a week, but say they could refuse it because there was no PO on the packing list."
- "I checked with the warehouse: the packing list sleeve was on the pallet when it left Columbus. Not sure what happened to it."
- "My notes say she only asked for an ETA."
- Source: `2026-04-03_FW_Bellwether-response.eml` (okafor-d)
- ⚠ **Conflict:** B says the pallet carried no packing list. H's evidence that it did is secondhand, from its warehouse, and nobody from the warehouse speaks to it in the set.
- The email says Stroud forwarded B's letter to Okafor "by mistake". It was not screened.

## After the response (context)

- **2026-04-10 and 04-13: TCFL declines a freight claim; the pallet is still in Richmond.**
  - The log reads: "TCFL claim TC-26-01953 declined 4/10. Pallet at TCFL Richmond, storage RS-2026-0412, $35/day from 3/11. Asked counsel whether we should take the pallet back without prejudice." Source: `credit-hold-notes.txt` (voss-r).
  - The claim letter itself, `tcfl-claim-letter-2026-04-10.pdf`, is a scan with **no text available**.
  - ⚠ Storage is billed from 3/11, a week after the 3/4 pickup. Nothing in the set explains the gap.
- **2026-04-14:** notice of Kettering's deposition in AAA Case No. 01-26-0004-1187 (`notice-of-deposition-kettering-2026-04-14.pdf`, delacroix-m).
- **2026-04-28: Kettering deposition** [testimony] (`deposition-kettering-2026-04-28.pdf`, delacroix-m)
  - "A pallet came off that truck. The log shows that. What I don't know is whose it was. There was no PO and no packing list on it."
  - On the TK initials: "I didn't write them. I wasn't there."
  - "I left at about 12:30"
  - "Acting lead signs in their own name."
  - "I didn't see it until March 2"
  - "The shipment was never received against that PO, in our system or on paper."
  - "My understanding is Tri-County still has it in Richmond."
  - The transcript text has no line numbers. Exhibit first-reference pages are in `deposition-exhibits-kettering.csv`.

---

## The main points of disagreement

1. **Was it delivered?** H relies on the carrier's notice (14:47, "Signed by: TK") and the proof of delivery. B's own contemporaneous records show the same pallet: same PRO, same BOL on TCFL's pickup order, HFS wrap and 1,410 lb. It sat on B's dock in Q-3 from 2/24 to 3/4. B's position is that it was never received *against the PO*, because it had no PO or packing list. That position rests on §6 and the PO's quarantine terms. Nobody has opened the pallet.
2. **Who signed?** It was not Kettering. B's own log, the discrepancy report as read at deposition, and the camera notes all say Ibáñez wrote "TK", as acting lead and under time pressure from the driver. The PO, the roster and Kettering's testimony all say the signer must print a full name. Kettering's own alibi is undercut by the 14:52 dock 2 log row in his name, and possibly by the 3/5 recertification notice.
3. **Notice under §7.2 (deadline March 2).** The first written non-receipt claim is 3/9. B relies on the 3/2 phone call, which the two sides' notes describe differently; Müller's own notes concede some doubt.
4. **What B knew, and when.** Kettering knew of the quarantined pallet by 3/2 and had it sent back on 3/4 without contacting H. Müller wrote "never received" on 3/9, and Kettering told her about the Q-3 pallet on 3/10. B first disclosed the pallet to H on 3/17.
5. **Packing list.** B says there was none. H's only contrary evidence is Okafor's secondhand report from the warehouse on 4/3. The invoice describes only an "HFS label on two sides".

## What this chronology does not cover

- **Searched:** a keyword search for the PO number, invoice number, BOL/PRO numbers, discrepancy report number, "Harrowgate", "Tri-County", "pallet", "Dock 4", "proof of delivery", "POD" and "demand letter". It matched 105 documents across all five custodians. All 105 were read, apart from the six screened documents below. 821 of the 826 documents in the matter could be searched.
- **No text, not read:** four scanned PDFs could not be read because OCR is not installed here: `pod-TCF-889104.pdf` (2 copies), `receiving-discrepancy-report-RDR-0224-02.pdf` and `tcfl-claim-letter-2026-04-10.pdf`. Their contents above come only from how other documents describe them. They should be checked, especially the receipt's signature and printed name.
- **Held back as possibly privileged, not read:** these are a privilege check, not a privilege ruling; a lawyer should decide.
  - `2026-03-26_FW_demand-for-payment.eml`
  - `RE_ FW_ Harrowgate Fastener Supply Co. _ Bellwether Marine _ demand for payment.eml`
  - `2026-03-27_litigation-hold.eml` and its copy `LITIGATION HOLD _ Harrowgate Fastener Supply Co. (PO-BMS-26-0112).eml`
  - `RE LITIGATION HOLD - Harrowgate.msg`
  - `privileged-memo-harrowgate-2026-03-27.docx`
- **Not in the set:** the AR "note … on Friday" (3/6) as Okafor saw it, Tidewater's sign-in sheet, the camera footage itself, and any statement from Ibáñez, Pruitt or Harrowgate's warehouse.
- **Probably unrelated:** a 2/9 receiving-log row carries "PO-BMS-26-0112" under a different vendor (Norfolk Rope & Rigging, UPS Freight). *Inference:* it is probably mis-keyed, but it is worth a look (`dock-receiving-log-2026-02.csv`).

*Each reader's working notes, with full locators, are in `workspace/chron/*.findings.md`. Every quotation above was checked word for word against the document's text.*
