# Findings: custodian kettering-t (Bellwether dock/receiving)

Documents read: 11 of 13 candidates (2 screened, not read). Locators below are abbreviated to the first 12 hex digits of the sha256 (full form `doc:sha256:<hex>` is in kettering-t.txt).

### 2026-02-12 — Prior Harrowgate delivery at Dock 4 (comparison baseline)
- Source: dock-receiving-log-2026-02.csv (spreadsheet, no doc date; contemporaneous log), locator doc:sha256:d90ba2d59320
- Who / to whom: received by T. Kettering
- Quote(s): "RCV-26-0212-05	2026-02-12	10:05	4	Vendor truck	HFS-DT-0212	PO-BMS-26-0098	Harrowgate Fastener Supply	1	PN 44-B/9 x 4,000; PN 17-K.3/B x 6,000	RECEIVED	R-11	T. Kettering"
- Notes: An earlier Harrowgate delivery on a different PO (0098), by vendor truck, fully documented. Contrast with the 2/24 pallet (no PO, no packing list). Camera notes say the HFS logo should be compared with "PO-BMS-26-0098 delivery 2/12".

### 2026-02-09 — Log row bearing PO number PO-BMS-26-0112 but a different vendor/carrier (anomaly)
- Source: dock-receiving-log-2026-02.csv (spreadsheet), locator doc:sha256:d90ba2d59320
- Who / to whom: received by K. Osei, dock 2
- Quote(s): "RCV-26-0209-01	2026-02-09	11:58	2	UPS Freight	4955376	PO-BMS-26-0112	Norfolk Rope & Rigging	2	per packing list	RECEIVED	R-19	K. Osei"
- Notes: Same PO number, but vendor Norfolk Rope & Rigging, UPS Freight, 2 pallets, before the order existed as far as other documents show. Inference: probably an unrelated or mis-keyed row (the log is full of repeated PO numbers across vendors); flag for the lawyer, not a delivery of this order. Kettering's 3/10 check ("2/19 through today") would not have covered it.

### 2026-02-24 14:41-14:52 — Tri-County driver delivers one pallet to Dock 4; Jose Ibanez signs the handheld; pallet put in quarantine cage Q-3
- Source: dock4-camera-review-notes.txt (text, header date 2026-02-24, "reviewed by T. Kettering 2026-03-11"), locator doc:sha256:25e562439939
- Who / to whom: Tri-County driver and Jose Ibanez (podium); written later by T. Kettering from camera footage
- Quote(s): "14:46:40  Driver and Jose Ibanez at podium. Jose signs handheld." / "14:52:10  Jose moves pallet into Q-3 cage, attaches orange QUARANTINE tag." / "Logo looks like the Harrowgate "HFS" mark (compare PO-BMS-26-0098 delivery 2/12)."
- Notes: LATER REVIEW (2026-03-11), not contemporaneous. Truck is "Tri-County tractor #4471", arrival 14:41:06, departure 14:48:15 (same truck number as the 3/4 pickup). Kettering identifies the logo as probably Harrowgate's only on 3/11 (inference: he had linked HFS to Harrowgate by then; compare his 3/10 email, which already says the wrap had "HFS" printed on it). Original NVR footage retention 45 days (gone after 4/10); USB export "given to M. Delacroix-Hale 3/27".

### 2026-02-24 (alibi) — Kettering says he was offsite at forklift recert from 12:30
- Source: dock4-camera-review-notes.txt (text, reviewed 2026-03-11), locator doc:sha256:25e562439939; receiving-shift-roster-2026-02-23.html (html, exported 2026-03-11 08:14), locator doc:sha256:468579cfb849
- Who / to whom: T. Kettering (own account); WFM scheduler export
- Quote(s): Camera notes: "I was at the forklift recert in Chesapeake from 12:30 to 17:00 (sign-in sheet from Tidewater Safety Training on file)." Roster, Tue 2/24: "06:30–12:30" / "(PM: forklift recert, offsite)" for Thomas Kettering; for José Ibáñez "07:00–15:30" / "acting lead PM". Roster note: "Only the supervisor or the posted acting lead may sign carrier delivery receipts. Acting lead must print full name."
- Notes: Both are LATER (3/11), after the dispute surfaced (roster is an export of the scheduler, not a 2/24 document). Roster and camera notes agree Kettering left 12:30. The roster makes Ibanez acting lead that afternoon, but requires the acting lead to "print full name", whereas the log says the handheld was signed "TK" (see next entry and Contradictions).

### 2026-02-24 14:52 — Receiving log entry for the unlabeled Tri-County pallet (quarantine), "signed ... as TK"
- Source: dock-receiving-log-2026-02.csv (spreadsheet; log contemporaneous in form, but see note), locator doc:sha256:d90ba2d59320
- Who / to whom: logged by/received_by J. Ibáñez; Tri-County driver
- Quote(s): "RCV-26-0224-07	2026-02-24	14:52	4	Tri-County Freight Lines	PRO 0047-2219-6 (from driver handheld)		UNKNOWN (wrap printed “HFS”)	1	UNLABELED PALLET - NO PO REF - no packing list	QUARANTINE	Q-3	J. Ibáñez	signed carrier handheld as TK (supervisor of record); see RDR-0224-02"
- Notes: PO field blank; vendor UNKNOWN. PRO taken "from driver handheld". Status QUARANTINE, not RECEIVED. The note records that Ibanez signed as "TK". Inference: the "RDR-0224-02" cross-reference and the "UNKNOWN (wrap printed HFS)" wording suggest the note may have been written or edited after the discrepancy report; the CSV carries no edit history, so authorship timing cannot be told from this copy. Sequence anomaly (inference): this is -07 while other 2/24 entries are -01 to -03 (no -04 to -06 in this export). Also at 14:52 on 2/24 the same log shows RCV-26-0224-03 (UPS Freight, dock 2) "received_by T. Kettering", although Kettering says he was in Chesapeake from 12:30 (see Contradictions).

### 2026-03-02 13:05 -0500 — Kettering emails Tri-County dispatch: pallet in quarantine, asks for pickup
- Source: 2026-03-02_unclaimed-pallet-Q3.eml (email, 2026-03-02), locator doc:sha256:9d0211c680
- Who / to whom: Thomas Kettering to Tri-County Freight Lines Dispatch
- Quote(s): "You dropped a pallet at Bellwether Marine, 2200 Industrial Pkwy, Dock 4, on 2/24 (your truck 4471, PRO looks like 0047-2219-6). No PO, no packing list, so it has been in our quarantine cage. We can't receive it. Please pick it up and return it to the shipper."
- Notes: CONTEMPORANEOUS. Shows Kettering knew of the 2/24 drop and its PRO by 3/2. He does not mention "HFS" or Harrowgate here. Two other files named "Unclaimed pallet at Bellwether Dock 4 - please pick up.eml" (locators doc:sha256:8892b07e5eb1 and doc:sha256:18c414d0c733) are the same email, dated "02 Mar 2026 18:05:00 +0000" (= 13:05 -0500), body identical, only the signature line lacks a trailing newline. Same as 9d0211c680.

### 2026-03-02 15:10 -0500 — Tri-County dispatch replies: pickup Wed 3/4, will hold as refused freight in Richmond
- Source: RE_ Unclaimed pallet at Bellwether Dock 4 - please pick up.eml (email, 2026-03-02), locator doc:sha256:4d9abce0f847
- Who / to whom: Tri-County Freight Lines Dispatch to Kettering
- Quote(s): "Pickup is scheduled for Wednesday 3/4, morning run: driver R. Ames, truck 4471, pickup order PU-0304-118 attached. Please have the pallet inside the Dock 4 door. The driver will need a signature for the pickup." / "We will hold the pallet at our Richmond terminal as refused freight until the shipper tells us what to do with it."
- Notes: CONTEMPORANEOUS. Carrier treats the pallet as "refused freight" from the outset. Dispatch phone (804) 555-0177.

### 2026-03-03 — Facilities notice: Dock 4 forklift recertification Thursday 3/5
- Source: Dock 4 forklift recertification - Thursday 3_5.eml (email, 2026-03-03), locator doc:sha256:3bc6cc4bab81
- Who / to whom: Bellwether Facilities to Kettering (receiving team)
- Quote(s): "The annual forklift recertification for Dock 4 operators is Thursday 3/5, 06:30-08:00, in the training room."
- Notes: Otherwise unrelated, but relevant to Kettering's 2/24 "recert in Chesapeake 12:30 to 17:00" account: this notice puts the annual Dock 4 recert on 3/5, 06:30-08:00, in the on-site training room, not offsite on 2/24 (see Contradictions; whether the 2/24 session was a different, outside course is unknown).

### 2026-03-04 (AM run; 10:20 logged) — Tri-County picks up the pallet; log shows "RETURNED TO CARRIER"
- Source: pickup-order-PU-0304-118.pdf (pdf, 2026-03-02), locator doc:sha256:92d00a688833; dock-receiving-log-2026-03.csv (spreadsheet), locator doc:sha256:46b3f8424
- Who / to whom: driver R. Ames (truck 4471); T. Kettering (contact/logger)
- Quote(s): Pickup order: "Reference: PRO 0047-2219-6 (BOL TCF-889104)" / "Pieces: 1 pallet, unlabeled, shrink wrap printed HFS Weight: 1,410 lb" / "Disposition: refused freight, hold at Richmond terminal pending shipper instructions". Log: "RCV-26-0304-01	2026-03-04	10:20	4	Tri-County Freight Lines	PRO 0047-2219-6		UNKNOWN (wrap printed “HFS”)	-1	RETURNED TO CARRIER - unclaimed pallet from Q-3	RELEASED	Q-3	T. Kettering	driver R. Ames; per TK email to TCFL dispatch 3/2"
- Notes: Both CONTEMPORANEOUS in form. The pickup order ties PRO 0047-2219-6 to BOL TCF-889104, i.e. the pallet in Q-3 is the same BOL Harrowgate says was delivered (BOL TCF-889104 appears in Muller's 3/10 request below). Pickup order is dated 03/04/2026 (PDF doc date 3/2, the date dispatch emailed it). No signature or time on the order itself in the extracted text; the log gives 10:20. Weight 1,410 lb vs log "1" pallet. The log entry's wording ("unclaimed pallet") is Bellwether's description.

### 2026-03-10 08:15 -0400 — Z. Muller asks Kettering to check Dock 4 log; relays that Harrowgate says it was delivered 2/24 and signed "TK"
- Source: 2026-03-10_RE_dock-4-check-PO-BMS-26-0112.eml (email, 2026-03-10; quoted lower message), locator doc:sha256:40bf5c1d1af2
- Who / to whom: Zoe Muller to Kettering
- Quote(s): "can you check the Dock 4 log for anything from Harrowgate (PO-BMS-26-0112, BOL TCF-889104) since 2/19? They say it was delivered on 2/24 and signed “TK.”"
- Notes: CONTEMPORANEOUS (quoted in Kettering's reply). Shows Harrowgate's claim of a "TK" signature was known inside Bellwether by 3/10, before the 3/25 demand letter appears in the matter.

### 2026-03-10 15:40 — Kettering log-review entry: no receipt for PO-BMS-26-0112
- Source: dock-receiving-log-2026-03.csv (spreadsheet), locator doc:sha256:46b3f8424
- Who / to whom: T. Kettering, "requested by Z. Müller"
- Quote(s): "RCV-26-0310-04	2026-03-10	15:40	4			PO-BMS-26-0112	Harrowgate Fastener Supply	0	LOG REVIEW - no receipt found for PO-BMS-26-0112	NOT RECEIVED		T. Kettering	requested by Z. Müller; see RCV-26-0224-07 / RDR-0224-02"
- Notes: A log entry created on 3/10 about the 3/10 review (so the log is not purely transaction-time; it contains after-the-fact entries). Points back to the 2/24 quarantine entry and the discrepancy report.

### 2026-03-10 16:31 -0400 — Kettering to Muller: no receipt, but an "HFS" pallet sat in Q-3 and was taken back 3/4
- Source: 2026-03-10_RE_dock-4-check-PO-BMS-26-0112.eml (email, 2026-03-10), locator doc:sha256:40bf5c1d1af2
- Who / to whom: Thomas Kettering to Zoe Muller
- Quote(s): "There is no receipt for PO-BMS-26-0112 and nothing from Harrowgate since the 2/12 vendor-truck delivery (PO-BMS-26-0098)." / "on 2/24 at 14:52 José logged an unlabeled pallet from Tri-County — no PO, no packing list — and put it in Q-3. The wrap had “HFS” printed on it. I saw the discrepancy report on 3/2, had no PO to match it to, and asked Tri-County to come get it. They took it on 3/4." / "So: the shipment was never received against our PO, but it may have been sitting in Q-3 for a week. Sorry."
- Notes: CONTEMPORANEOUS (written the day after the demand-related inquiry; before counsel involvement shown in this set). Admits the HFS pallet was physically at Dock 4 and that Kettering saw the discrepancy report on 3/2. Attributes logging and placing in Q-3 to José, and does NOT mention that "TK" was signed on the handheld nor that he himself was offsite that afternoon (inference: he may not yet have known of the "TK" signature detail, although Muller's quoted message states it; an omission worth putting to him). Says "nothing from Harrowgate since the 2/12 ... delivery", which is in tension with his own admission of the HFS pallet in the same email. "a week" vs actual 2/24 to 3/4 = 8 days (minor).

### 2026-03-11 — Kettering reviews camera footage; roster exported
- Source: dock4-camera-review-notes.txt (text), locator doc:sha256:25e562439939; receiving-shift-roster-2026-02-23.html (html, "Exported from WFM scheduler 2026-03-11 08:14"), locator doc:sha256:468579cfb849
- Who / to whom: T. Kettering (author); no addressee
- Quote(s): "reviewed by T. Kettering 2026-03-11" / "Footage exported to USB, given to M. Delacroix-Hale 3/27."
- Notes: Both are LATER, litigation-period creations. The camera notes mention a 3/27 handover, so the notes were edited at least after 3/27 (inference) even though headed with the 3/11 review date.

### 2026-03-26 12:52 UTC and 2026-03-27 21:45 UTC — Two emails not read (screened)
- See "Screened (not read)". Mailbox path per screen: kettering-t-mailbox-2026-04.pst (collected). Dates and counsel involvement only; nothing about content is stated.

## Contradictions noticed
1. Who signed for the pallet on 2/24. Log RCV-26-0224-07: "signed carrier handheld as TK (supervisor of record)". Camera notes: "Jose signs handheld." Roster: only "the supervisor or the posted acting lead may sign" and "Acting lead must print full name", with Ibáñez posted "acting lead PM" and Kettering offsite. Harrowgate says it was "signed “TK.”" (per Muller's 3/10 email). So the "TK" signature was written by someone other than Kettering (per Bellwether's own records), and by a person not printing his full name.
2. Kettering's presence at 14:52 on 2/24. Camera notes and roster: offsite at recert from 12:30. Yet log RCV-26-0224-03 (14:52, dock 2, UPS Freight, PO-BMS-26-0150) shows received_by "T. Kettering". Inference: log names the supervisor of record rather than the person present, or the entry was keyed later; unresolved.
3. Recert date/place. Camera notes: recert "in Chesapeake" 2/24 12:30-17:00 (Tidewater Safety Training). Facilities 3/3 email: annual Dock 4 recert Thursday 3/5, 06:30-08:00, "in the training room". Not necessarily inconsistent (different sessions), but the 2/24 attendance should be verified against the sign-in sheet; also the roster lists Kettering at 06:30-15:00 on Thu 2/26 and 3/5 is outside the roster week.
4. Kettering 3/10 email: "nothing from Harrowgate since the 2/12 vendor-truck delivery" versus the same email's account of the 2/24 "HFS" pallet; and log 3/10 entry "no receipt found" (accurate on its terms: nothing received "against our PO").
5. Time of pallet handling: camera has the driver leaving at 14:48:15 and the pallet moved to Q-3 at 14:52:10; log time 14:52 matches the Q-3 move, not the signature at 14:46:40.
6. Knowledge: 3/2 email shows Kettering knew of the drop (PRO, truck, "No PO, no packing list") and called the PRO "looks like 0047-2219-6" even though the log says PRO came "from driver handheld"; he later wrote "I saw the discrepancy report on 3/2". Whether he associated it with Harrowgate before 3/10 is not stated in 3/2 email (inference: not before 3/4 pickup order, which already says "printed HFS").
7. Dispatch: "refused freight" (pickup order, dispatch email) versus Kettering's "unclaimed"/"We can't receive it" (Bellwether never formally refused on 2/24; it was signed for and quarantined). Inference only.
8. PO-BMS-26-0112 appears in a 2/9 log row under a different vendor (Norfolk Rope & Rigging, UPS Freight) (see entry above).

## Screened (not read)
- RE_ FW_ Harrowgate Fastener Supply Co. _ Bellwether Marine _ demand for payment.eml — 2026-03-26 — reason: counsel (email within bellwethermarine.example with counsel mdelacroix-hale@ on it, about demand; "signs 6 of the 12 emails they sent as counsel"); from Thomas Kettering to mdelacroix-hale@; locator doc:sha256:e3994e58f1a5
- LITIGATION HOLD _ Harrowgate Fastener Supply Co. (PO-BMS-26-0112).eml — 2026-03-27 — reason: worded ("privileged & confidential") and counsel (mdelacroix-hale@; about demand, hold, litigation); from Marcus Delacroix-Hale to Z. Muller, T. Kettering, AP; locator doc:sha256:d1d66241f1ad

## No text available
None.

## Unrelated
No document was wholly unrelated; the Dock 4 forklift recertification email (2026-03-03) is only marginally relevant (see above). Within the two CSV logs, all rows other than RCV-26-0212-05, -0209-01, -0224-03, -0224-07, -0304-01 and -0310-04 are unrelated other-vendor receipts (including 3/9 UPS Freight RCV-26-0309-01 for PO-BMS-26-0076); there is no 3/9 row for this PO or the carrier PRO.
