"""Generate the irrelevant documents of the adhocdocs test corpus.

A real collection is mostly not about the dispute. Each custodian's
directory gets several hundred documents that have nothing to do with
PO-BMS-26-0112: marketing email, newsletters, calendar invitations, office
chatter, other customers and other matters, and above all drafts of unclear
origin and purpose, often in several versions. They sit beside the story
documents, named the way their sources would name them.

The output is reproducible from SEED. Every file written is listed in
noise-files.txt beside this script; a rerun deletes those first, so the
story documents written by build_synthetic.py are never touched. Tests that
need only the story documents leave out the files on that list.

The noise is kept from changing the known facts in SOURCES.md: no text here
may contain the story's search terms (see FORBIDDEN), and none of Zoë's own
messages is dated March 2026.

Run:  uv run python tools/corpus/build_noise.py
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import random
import re
from email.utils import format_datetime
from pathlib import Path

import docx

from common import CORPUS, PdfWriter, email_msg, quote, save_docx_reproducibly, write_eml

SEED = 20261031
HERE = Path(__file__).resolve().parent
LISTING = HERE / "noise-files.txt"

rng = random.Random(SEED)

# Text that would change a known fact in SOURCES.md, or tie noise to the story.
FORBIDDEN = [
    r"never\W+received", r"nothing to pay yet", r"INV-2026", r"0347", r"44-B", r"44-C", r"17-K", r"90-T",
    r"TCF-?\d", r"0047-2219", r"PO-BMS-26-0112", r"BMS-26-01", r"Ibáñez", r"Iba[nñ]ez", r"Jos[eé]\b",
    r"\bZoe Muller\b", r"unclaimed", r"Pruitt", r"bill of lading", r"arbitrat", r"Carmack", r"\bQ-3\b",
    r"RDR-", r"Dock 4", r"\bHFS\b", r"Tri-County", r"\bTK\b", r"Pellham", r"Stroud", r"quarantin",
    r"demand letter", r"proof of delivery",
]
_FORBIDDEN = re.compile("|".join(FORBIDDEN), re.I)


def check(text: str, where: str):
    m = _FORBIDDEN.search(text)
    if m:
        raise ValueError(f"{where}: noise text contains {m.group(0)!r}")


# ---------------------------------------------------------------- time

def tz_for(d: dt.date):
    # US Eastern: DST ended 2025-11-02, began 2026-03-08, ends 2026-11-01
    edt = (dt.date(2025, 3, 9) <= d < dt.date(2025, 11, 2)) or (dt.date(2026, 3, 8) <= d < dt.date(2026, 11, 1))
    return dt.timezone(dt.timedelta(hours=-4 if edt else -5))


START, END = dt.date(2025, 8, 4), dt.date(2026, 7, 17)


def rand_day(lo=START, hi=END, weekend=0.06) -> dt.date:
    span = (hi - lo).days
    while True:
        d = lo + dt.timedelta(days=rng.randrange(span + 1))
        if d.weekday() < 5 or rng.random() < weekend:
            return d


def at(d: dt.date, h=None, mi=None) -> dt.datetime:
    if h is None:
        h = rng.choice([6, 7, 7, 8, 8, 8, 9, 9, 10, 10, 11, 12, 13, 13, 14, 14, 15, 15, 16, 17, 19, 21, 23])
    if mi is None:
        mi = rng.randrange(60)
    return dt.datetime(d.year, d.month, d.day, h, mi, rng.randrange(60), tzinfo=tz_for(d))


def nice_date(d: dt.date) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def short_date(d: dt.date) -> str:
    return f"{d:%B} {d.day}"


def next_weekday(d: dt.date, k=1) -> dt.date:
    while True:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            k -= 1
            if k == 0:
                return d


# ---------------------------------------------------------------- people

class P:
    def __init__(self, name, email, title=""):
        self.name, self.email, self.title = name, email, title
        self.first = name.split()[0]

    def __str__(self):
        return f"{self.name} <{self.email}>"


HG, BW = "harrowgatefastener.example", "bellwethermarine.example"

DANA = P("Dana Okafor", f"dana.okafor@{HG}", "Account Manager")
VOSS = P("Raymond Voss", f"rvoss@{HG}", "Controller")
ZOE = P("Zoë Müller", f"zoe.muller@{BW}", "Procurement Manager")
TOM = P("Thomas Kettering", f"tkettering@{BW}", "Receiving Supervisor")
MARCUS = P("Marcus Delacroix-Hale", f"mdelacroix-hale@{BW}", "General Counsel")

HG_STAFF = [
    P("Priya Raman", f"priya.raman@{HG}", "Sales Operations"),
    P("Kip Danforth", f"kdanforth@{HG}", "VP Sales"),
    P("Gwen Abernathy", f"gabernathy@{HG}", "HR Manager"),
    P("Ted Halvorsen", f"thalvorsen@{HG}", "IT"),
    P("Marisol Vance", f"mvance@{HG}", "Marketing Coordinator"),
    P("Luis Ferreira", f"lferreira@{HG}", "Warehouse Lead"),
    P("Bonnie Szymanski", f"bszymanski@{HG}", "Accounts Payable"),
    P("Arthur Pell", f"apell@{HG}", "President"),
    P("Nadia Corbett", f"ncorbett@{HG}", "Inside Sales"),
    P("Wes Tanaka", f"wtanaka@{HG}", "Purchasing"),
]
BW_STAFF = [
    P("Hollis Brandt", f"hbrandt@{BW}", "Plant Manager"),
    P("Renata Szabo", f"rszabo@{BW}", "HR Director"),
    P("Desmond Pryor", f"dpryor@{BW}", "IT Manager"),
    P("Ama Boateng", f"aboateng@{BW}", "Quality Engineer"),
    P("Gus Whitcomb", f"gwhitcomb@{BW}", "Facilities"),
    P("Lena Fairbanks", f"lfairbanks@{BW}", "Executive Assistant"),
    P("Corinne Mbeki", f"cmbeki@{BW}", "CFO"),
    P("Fritz Albrecht", f"falbrecht@{BW}", "Production Scheduler"),
    P("Shonda Rhee", f"srhee@{BW}", "Buyer"),
    P("Pete Navarro", f"pnavarro@{BW}", "Dock Lead"),
    P("Ivy Castellano", f"icastellano@{BW}", "Paralegal"),
    P("Owen Sato", f"osato@{BW}", "Safety Coordinator"),
]

ALL_HG = P("All Harrowgate Staff", f"all-staff@{HG}")
ALL_BW = P("Bellwether Everyone", f"everyone@{BW}")
HG_IT = P("Harrowgate IT Helpdesk", f"helpdesk@{HG}")
BW_IT = P("Bellwether IT Service Desk", f"servicedesk@{BW}")
HG_HR = P("Harrowgate People Team", f"people@{HG}")
BW_HR = P("Bellwether Human Resources", f"hr@{BW}")

CUSTODIANS = {
    "okafor-d": dict(me=DANA, co="hg", home="Columbus", hobbies=["running", "book club", "soccer mom"]),
    "voss-r": dict(me=VOSS, co="hg", home="Westerville", hobbies=["golf", "HOA", "woodworking"]),
    "muller-z": dict(me=ZOE, co="bw", home="Virginia Beach", hobbies=["sailing", "choir", "baking"]),
    "kettering-t": dict(me=TOM, co="bw", home="Chesapeake", hobbies=["fishing", "fantasy football", "grilling"]),
    "delacroix-m": dict(me=MARCUS, co="bw", home="Norfolk", hobbies=["cycling", "wine", "church"]),
}


def staff(c):
    return HG_STAFF if c["co"] == "hg" else BW_STAFF


def company(c):
    return "Harrowgate Fastener Supply Co." if c["co"] == "hg" else "Bellwether Marine Systems, Inc."


def short_co(c):
    return "Harrowgate" if c["co"] == "hg" else "Bellwether"


def domain(c):
    return HG if c["co"] == "hg" else BW


# ---------------------------------------------------------------- output

written: list[Path] = []
taken: set[Path] = set()


def unique(cust: str, name: str) -> Path:
    d = CORPUS / cust
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    stem, dot, ext = name.rpartition(".")
    n = 2
    while p in taken or p.exists():
        p = d / f"{stem} ({n}).{ext}"
        n += 1
    if cust == "voss-r" and p.name.startswith("memo-") and p.suffix == ".pdf":
        raise ValueError("voss-r/memo-*.pdf is the story's memo glob")
    taken.add(p)
    written.append(p)
    return p


def slug(s: str, n=48) -> str:
    s = re.sub(r"^((re|fw|fwd|accepted|declined|tentative|canceled|updated invitation|invitation)\s*:\s*)+", "", s, flags=re.I)
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s[:n].rstrip("-") or "message"


_msgid = 0


def msgid(when: dt.datetime, dom: str) -> str:
    global _msgid
    _msgid += 1
    return f"<{when:%Y%m%d%H%M%S}.{rng.getrandbits(32):08x}.{_msgid}@{dom}>"


def mail(cust, when, frm, to, subject, text=None, html=None, cc=None, attachments=(), extra=None,
         name=None, reply_to=None):
    check(subject + (text or "") + (html or ""), f"{cust}: {subject}")
    while "zoe.muller@" in str(frm) and (when.year, when.month) == (2026, 3):
        when += dt.timedelta(days=9)   # none of Zoë's own messages in March 2026 (SOURCES.md, fact 12)
    tos = to if isinstance(to, list) else [to]
    dom = str(frm).rsplit("@", 1)[-1].rstrip(">")
    h = {"From": str(frm), "To": ", ".join(map(str, tos)),
         "Cc": ", ".join(map(str, cc)) if cc else None,
         "Date": format_datetime(when), "Subject": subject,
         "Message-ID": msgid(when, dom), "Reply-To": reply_to}
    h.update(extra or {})
    msg = email_msg(h, text=text, html=html, attachments=attachments, boundary_seed=f"n{_msgid}")
    p = unique(cust, name or f"{when:%Y-%m-%d}_{slug(subject)}.eml")
    write_eml(p, msg)
    return p


AUTHORS_ODD = ["User", "Owner", "Admin", "Microsoft Office User", "HP Authorized Customer", "Windows User",
               "Front Desk", "Conference Room B", "Temp", "", "", "kiosk", "Receiving PC", "Laptop-07"]


def doc_author(c):
    return c["me"].name if rng.random() < 0.45 else rng.choice(AUTHORS_ODD + [p.name for p in staff(c)])


def write_docx(cust, name, title, blocks, when, author):
    """blocks: list of (style, text); style in h1 h2 p bullet number"""
    check(title + "".join(t for _, t in blocks), f"{cust}/{name}")
    d = docx.Document()
    for style, text in blocks:
        if style == "h1":
            d.add_heading(text, level=1)
        elif style == "h2":
            d.add_heading(text, level=2)
        elif style == "bullet":
            d.add_paragraph(text, style="List Bullet")
        elif style == "number":
            d.add_paragraph(text, style="List Number")
        else:
            d.add_paragraph(text)
    d.core_properties.author = author
    d.core_properties.last_modified_by = rng.choice([author, author, "", c_name_guess()])
    d.core_properties.title = title if rng.random() < 0.3 else ""
    p = unique(cust, name)
    save_docx_reproducibly(d, p, when.replace(tzinfo=None))
    return p


def c_name_guess():
    return rng.choice([p.name for p in HG_STAFF + BW_STAFF] + AUTHORS_ODD)


def write_text(cust, name, text):
    check(text, f"{cust}/{name}")
    p = unique(cust, name)
    p.write_text(text, encoding="utf-8", newline="\r\n" if rng.random() < 0.4 else "\n")
    return p


def write_pdf(cust, name, title, blocks, when, author):
    check(title + "".join(t for _, t in blocks), f"{cust}/{name}")
    w = PdfWriter(title, author, when.strftime("%Y%m%d%H%M%S"), margin=rng.choice([72, 72, 90]))
    for style, text in blocks:
        if style == "h1":
            w.line(text, "sans-bold", 14)
            w.space(4)
        elif style == "h2":
            w.space(4)
            w.line(text, "sans-bold", 11)
        elif style in ("bullet", "number"):
            w.para("•  " + text, "serif", 11, indent=14, after=2)
        else:
            w.para(text, "serif", 11)
    p = unique(cust, name)
    w.save(p)
    return p


def write_csv(cust, name, rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    check(buf.getvalue(), f"{cust}/{name}")
    p = unique(cust, name)
    p.write_text(buf.getvalue(), encoding="utf-8")
    return p


def write_html(cust, name, html):
    check(html, f"{cust}/{name}")
    p = unique(cust, name)
    p.write_text(html, encoding="utf-8")
    return p


def pick(xs, k=None):
    return rng.choice(xs) if k is None else rng.sample(xs, min(k, len(xs)))


def maybe(p=0.5):
    return rng.random() < p


# ===================================================================== marketing

# audience: all, hg, bw, legal, finance, dock, buyer
VENDORS = [
    dict(name="Bramblecrest Office Goods", email="deals@email.bramblecrest.example", aud="all",
         subjects=["{first}, your toner is calling", "Up to {pct}% off break-room essentials", "Last chance: free desk chair with orders over $499",
                   "The {month} office refresh sale is ON", "Restock reminder: sticky notes, pens & more", "Flash sale ends at midnight ⏰"],
         paras=["Keep your team stocked without the trip to the store. Our {month} specials include copy paper by the case, ergonomic accessories and the coffee pods everyone fights over.",
                "Business Rewards members earn 3x points on ink and toner through the end of the month.",
                "Free next-day delivery on orders over $75 in most metro areas. Some exclusions apply; see site for details.",
                "New: compostable cups and plates for your next all-hands lunch.",
                "Your account manager can set up automatic replenishment so you never run out of the basics."],
         cta="Shop the sale"),
    dict(name="Lumenfold Analytics", email="hello@lumenfold.example", aud="all",
         subjects=["Your dashboards could be doing more", "Webinar: 5 reports every ops leader should automate", "[Case study] How a regional distributor cut month-end by 3 days",
                   "{first}, quick question", "You're invited: Lumenfold Live {year}", "We noticed you haven't logged in"],
         paras=["Lumenfold connects to the systems you already use and turns them into reports your team will actually read.",
                "Join product lead Harriet Osei for a 30-minute walkthrough, with time for Q&A at the end. Can't make it live? Register and we'll send the recording.",
                "Teams using Lumenfold report saving an average of 6.5 hours a week on manual spreadsheet work.",
                "Your trial workspace is still available. Pick up where you left off, or book 15 minutes with a solutions engineer.",
                "Early-bird pricing for Lumenfold Live ends Friday. Sessions on forecasting, inventory health and AI-assisted reporting."],
         cta="Save my seat"),
    dict(name="SkyHarbor Airways", email="skymiles@news.skyharbor.example", aud="all",
         subjects=["{first}, fares from $79 one way", "Your HarborMiles statement for {month}", "Fall getaways: the weekend fares are back",
                   "Upgrade for less on your next trip", "You're {pct}% of the way to Silver status"],
         paras=["Escape for the weekend with nonstop fares from Norfolk, Columbus and 40 other cities. Book by Tuesday for travel through mid-December.",
                "Your HarborMiles balance is shown below. Miles don't expire as long as you have qualifying activity every 24 months.",
                "Bid on an upgrade to Harbor Plus up to 72 hours before departure. You'll only pay if your bid is accepted.",
                "Use the SkyHarbor app to check in, track your bags and get gate-change alerts."],
         cta="Find fares"),
    dict(name="Calderline Workwear", email="crew@calderline.example", aud="dock",
         subjects=["New hi-vis arrivals for {month}", "Steel-toe boots: buy 2, save {pct}%", "Cold weather gear is here",
                   "Is your crew's PPE compliant?", "Embroider your logo free this week"],
         paras=["Class 2 and Class 3 hi-vis vests, jackets and shirts in sizes S through 5XL, ready to ship.",
                "Our bestselling composite-toe boot is now available in wide widths. Electrical hazard rated and slip resistant.",
                "Insulated bibs, thermal liners and hand warmers for crews working loading doors and yards all winter.",
                "Volume pricing available for orders of 25 units or more. Ask about payroll deduction programs."],
         cta="Gear up"),
    dict(name="Tidewater Safety Training", email="register@tidewatersafety.example", aud="dock",
         subjects=["Forklift recertification dates for {month}", "OSHA 10 & 30 classes now enrolling", "Reminder: your operators' cards expire soon",
                   "New course: dock edge and trailer safety"],
         paras=["Classes are held at our Chesapeake training center and on-site at your facility for groups of six or more.",
                "Operator evaluations must be repeated at least every three years. Let us track the dates for you.",
                "Our trainers bring the equipment, the paperwork and the coffee.",
                "Seats fill quickly in the first quarter. Reserve now and pay at the class."],
         cta="See the schedule"),
    dict(name="Ledgerwise", email="team@ledgerwise.example", aud="finance",
         subjects=["Close the books in half the time", "Your AP inbox, on autopilot", "Invitation: Controllers' Roundtable ({month})",
                   "What's new in Ledgerwise {year}.3", "{first}, are your accruals still in Excel?", "[Guide] Getting ready for audit season"],
         paras=["Ledgerwise matches bank lines to invoices automatically and flags only the exceptions your team needs to review.",
                "Join controllers from manufacturing and distribution companies for an off-the-record conversation about close, staffing and systems.",
                "Version {year}.3 adds multi-entity consolidation, a new approvals mobile app and faster bank feeds.",
                "Download our free checklist: 27 things auditors will ask for, and where to find them."],
         cta="Get the guide"),
    dict(name="First Merrimack Bank", email="alerts@firstmerrimack.example", aud="finance",
         subjects=["Treasury Insights: rates outlook for {month}", "Introducing positive pay for ACH", "Your business banking statement is ready",
                   "Scheduled maintenance this weekend"],
         paras=["Online banking will be unavailable from 11 p.m. Saturday to 5 a.m. Sunday Eastern while we perform scheduled maintenance.",
                "Protect your accounts from check and ACH fraud with Positive Pay. Ask your relationship manager to enroll.",
                "Our economists expect short-term rates to remain range-bound through the next two meetings.",
                "This is a service message. Please do not reply to this email."],
         cta="Log in"),
    dict(name="Clausemark", email="news@clausemark.example", aud="legal",
         subjects=["Contract review in minutes, not days", "CLE webinar: Drafting enforceable limitation-of-liability clauses", "In-house counsel survey: the {year} results",
                   "{first}, your free trial ends in 3 days", "The legal ops playbook for small departments"],
         paras=["Clausemark reads your third-party paper, compares it to your playbook and suggests redlines your business teams can understand.",
                "Earn 1.0 CLE credit (pending approval in VA, OH, NC and MD) in this one-hour program.",
                "We surveyed 412 in-house lawyers at companies with legal departments of five or fewer. Here's what keeps them up at night.",
                "Your trial includes unlimited reviews and our standard NDA playbook."],
         cta="Register now"),
    dict(name="Virginia Corporate Counsel Forum", email="events@vaccforum.example", aud="legal",
         subjects=["Join us: {month} luncheon in Norfolk", "Call for speakers: spring program", "Member spotlight and job board",
                   "Annual dues reminder"],
         paras=["Our {month} luncheon features a panel on privacy compliance for mid-sized manufacturers. Lunch is included for members.",
                "We're looking for speakers on employment law, supply chain contracting and outside counsel management.",
                "Dues for the coming year are $175. Members receive discounted CLE and access to the member directory.",
                "Parking validation is available at the Granby Street garage."],
         cta="RSVP"),
    dict(name="Fastener Industry Weekly", email="newsletter@fastenerweekly.example", aud="hg",
         subjects=["This week: steel prices, tariffs and a new plant in Ohio", "FIW Digest — {month} {day}", "Distributor survey: inventory is up again",
                   "Who's hiring in the fastener trade"],
         paras=["Wire rod prices ticked up again this week, and three distributors told us they've pushed price increases into the next quarter.",
                "A Midwest manufacturer announced a $40 million expansion that will add 120 jobs over two years.",
                "Our quarterly survey found distributors holding an average of 94 days of inventory, up from 81 a year ago.",
                "Opinion: the case for a shared industry part-number standard, again."],
         cta="Read the full issue"),
    dict(name="Marine Systems Monthly", email="editor@marinesystemsmonthly.example", aud="bw",
         subjects=["Shipyard backlog hits new high", "MSM Briefing — {month} {year}", "Electrification on the waterfront: who's buying",
                   "Coatings, corrosion and the cost of downtime"],
         paras=["Repair yards on the East Coast report backlogs stretching into next year, with labor still the main constraint.",
                "Hybrid propulsion retrofits are moving from pilot projects into fleet programs, according to three operators we spoke with.",
                "A new study puts the average cost of an unplanned day in drydock at over $60,000 for a mid-sized workboat.",
                "Events calendar: trade shows, standards meetings and webinars for the coming month."],
         cta="Read more"),
    dict(name="Portside Purchasing Network", email="members@portsidepurchasing.example", aud="buyer",
         subjects=["Supplier diversity summit: registration open", "Negotiation skills workshop, {month}", "PPN member pricing on freight audit services",
                   "Your CPSM renewal is due"],
         paras=["Our two-day workshop covers preparation, anchoring and walk-away planning, with role-play sessions in small groups.",
                "Meet more than 60 certified small and diverse suppliers in one afternoon.",
                "Members save 15% on audit and recovery services from our partner network.",
                "Continuing education hours from PPN events count toward certification renewal."],
         cta="Learn more"),
    dict(name="Marrowbone Coffee Roasters", email="beans@marrowbone.example", aud="all",
         subjects=["Your office coffee subscription ships Thursday", "New single origin: Huila, Colombia", "{pct}% off your next bag, {first}",
                   "Holiday gift boxes are back"],
         paras=["Bright and juicy with notes of red apple and panela. Roasted Monday, on your desk by Thursday.",
                "Skip, swap or pause your subscription any time from your account page.",
                "Gift boxes ship in recyclable packaging with a handwritten card.",
                "Office plans start at five pounds a month, with a free grinder at ten."],
         cta="Shop coffee"),
    dict(name="Larkspur Suites", email="stay@larkspursuites.example", aud="all",
         subjects=["{first}, members save up to {pct}%", "Your points are waiting", "Weekend rates are falling", "Complete your profile for faster check-in"],
         paras=["Book direct for our best rate, free Wi-Fi and late checkout when available.",
                "Your Larkspur Rewards points can be redeemed starting at 8,000 a night.",
                "Business travelers: ask about our corporate rate program.",
                "Mobile key is now available at 140 locations."],
         cta="Book now"),
    dict(name="PortaVault Cloud", email="noreply@portavault.example", aud="all",
         subjects=["Your storage is 92% full", "Shared with you: Q-review deck", "Security alert: new sign-in from Chrome on Windows", "PortaVault price changes"],
         paras=["Upgrade to PortaVault Team for 2 TB per user, version history and admin controls.",
                "If this was you, you don't need to do anything. If not, reset your password right away.",
                "Starting next month, the Basic plan will cost $8.99 per user per month.",
                "Files in the trash are deleted permanently after 30 days."],
         cta="Manage storage"),
    dict(name="Fennick Conference Group", email="info@fennickevents.example", aud="all",
         subjects=["Early bird ends Friday: Supply Chain Leaders Summit", "Agenda announced for {month}", "{first}, we saved you a seat",
                   "Exhibitor opportunities still available"],
         paras=["Three days, 60 speakers, and the best networking reception on the East Coast.",
                "Group rates are available for teams of four or more.",
                "Our hotel block at the Harborview Convention Center fills quickly.",
                "Sessions include resilient sourcing, workforce development and warehouse automation."],
         cta="Register"),
    dict(name="Glenhollow Golf Club", email="proshop@glenhollowgolf.example", aud="finance",
         subjects=["Member-guest tee times now open", "Pro shop clearance: up to {pct}% off", "Course conditions update", "Annual dinner dance — save the date"],
         paras=["The back nine reopens Saturday after aeration. Carts are path-only through Tuesday.",
                "Clearance pricing on last season's apparel, bags and gloves while supplies last.",
                "Member-guest is limited to 64 teams. Sign up at the pro shop or online.",
                "Dinner dance tickets are $65 per person and include two drink tickets."],
         cta="Book a tee time"),
    dict(name="Tidewater Youth Soccer", email="registrar@tidewateryouthsoccer.example", aud="bw",
         subjects=["Spring registration is open", "Volunteer coaches needed", "Picture day schedule", "Field closures due to weather"],
         paras=["Registration closes on the 15th. Late registrations are placed on a waitlist.",
                "No experience needed: we provide training, a coaching guide and a bag of balls.",
                "Picture day is Saturday at the Kempsville fields. Order forms are due at check-in.",
                "All fields are closed today due to standing water. Check the app before you drive out."],
         cta="Register"),
    dict(name="Bluefin Benefits", email="noreply@bluefinbenefits.example", aud="all",
         subjects=["Open enrollment starts soon", "Your FSA balance: use it or lose it", "Reminder: complete your wellness screening", "Your EOB is available"],
         paras=["Review your elections before the enrollment window closes. If you do nothing, most current elections carry over.",
                "You have an FSA balance remaining. Eligible expenses include glasses, prescriptions and copays.",
                "Complete your screening by the deadline to earn the premium discount.",
                "Log in to view your Explanation of Benefits. This is not a bill."],
         cta="Log in"),
    dict(name="Ohio Valley Distributors Association", email="ovda@ovda.example", aud="hg",
         subjects=["OVDA fall meeting: Cincinnati", "Legislative update", "Member survey: wages and benefits", "Scholarship applications open"],
         paras=["Join members for two days of education, a supplier showcase and the annual awards dinner.",
                "A summary of state bills affecting distributors, including sales-tax changes and workforce grants.",
                "Your responses are confidential and reported only in aggregate.",
                "Scholarships of $2,500 are available to children of employees of member companies."],
         cta="Register"),
]

AUD = {"okafor-d": {"all", "hg", "buyer"}, "voss-r": {"all", "hg", "finance"}, "muller-z": {"all", "bw", "buyer"},
       "kettering-t": {"all", "bw", "dock"}, "delacroix-m": {"all", "bw", "legal"}}


def marketing(cust, c, n):
    vendors = [v for v in VENDORS if v["aud"] in AUD[cust]]
    for _ in range(n):
        v = pick(vendors)
        d = rand_day(weekend=0.3)
        when = at(d, pick([5, 6, 7, 10, 11, 14, 18, 20]))
        fill = dict(first=c["me"].first, pct=pick([10, 15, 20, 25, 30, 40, 50]), month=f"{d:%B}", year=d.year,
                    day=d.day)
        subject = pick(v["subjects"]).format(**fill)
        paras = [p.format(**fill) for p in pick(v["paras"], rng.randint(2, len(v["paras"])))]
        dom = v["email"].split("@")[1]
        unsub = f"https://{dom}/u/{rng.getrandbits(40):010x}"
        addr = f"{rng.randint(100, 9999)} {pick(['Commerce', 'Harbor', 'Market', 'Ridge', 'Mill', 'Front'])} " \
               f"{pick(['Street', 'Way', 'Avenue', 'Boulevard'])}, Suite {rng.randint(100, 900)}"
        html = (f"<html><head><meta charset='utf-8'><title>{v['name']}</title></head>"
                f"<body style='margin:0;background:#f4f4f4'><table width='100%'><tr><td align='center'>"
                f"<table width='600' style='background:#fff;font-family:Arial'>"
                f"<tr><td style='padding:12px;font-size:11px;color:#888'>View this email in your browser</td></tr>"
                f"<tr><td style='padding:20px;font-size:22px'><b>{v['name']}</b></td></tr>"
                f"<tr><td style='padding:0 20px'><h2>{subject}</h2>"
                + "".join(f"<p>{p}</p>" for p in paras)
                + f"<p><a href='https://{dom}/c/{rng.getrandbits(32):08x}' style='background:#1a5;color:#fff;padding:10px 18px'>"
                f"{v['cta']}</a></p></td></tr>"
                f"<tr><td style='padding:20px;font-size:10px;color:#999'>You are receiving this email because "
                f"{c['me'].email} is subscribed to updates from {v['name']}. {addr}. "
                f"<a href='{unsub}'>Unsubscribe</a> | <a href='https://{dom}/prefs'>Update preferences</a> | "
                f"<a href='https://{dom}/privacy'>Privacy</a></td></tr></table></td></tr></table></body></html>")
        text = None
        if maybe(0.6):
            text = f"{v['name']}\n\n{subject}\n\n" + "\n\n".join(paras) + \
                   f"\n\n{v['cta']}: https://{dom}/c\n\n--\n{addr}\nUnsubscribe: {unsub}\n"
        mail(cust, when, f"{v['name']} <{v['email']}>", c["me"], subject, text=text, html=html,
             extra={"List-Unsubscribe": f"<{unsub}>", "Precedence": "bulk"})


# ===================================================================== newsletters (internal)

NEWS_ITEMS_HG = [
    ("Welcome aboard", "Please welcome {new}, who joins {dept} this month. Stop by and say hello, and go easy on the acronyms."),
    ("Safety corner", "{days} days without a lost-time injury in the warehouse. Reminder: the rolling ladders in aisle 9 are tagged out until the new casters arrive."),
    ("From the president's desk", "Thank you all for a strong {quarter}. Our fill rate stayed above 97% while we moved the bulk stainless line to the new racking."),
    ("Kudos", "Shout-out to {who} for staying late to get the Ashgrove Pumps rush order out the door. That's the Harrowgate way."),
    ("Wellness", "Step challenge results: Sales beat Warehouse by 41,000 steps. Warehouse has requested a recount."),
    ("Tech tip", "You can now reset your own password from the login screen. Ted would like to never hear the words 'I'm locked out' again."),
    ("Birthdays", "Happy birthday this month to {b1}, {b2} and {b3}. Cake in the break room on the last Friday."),
    ("Community", "Our food drive collected 1,180 pounds for the Mid-Ohio food pantry. Thanks to everyone who donated."),
    ("Housekeeping", "The break-room fridge will be cleaned out Friday at 3 p.m. Anything unlabeled goes. Yes, including the mystery soup."),
]
NEWS_ITEMS_BW = [
    ("Plant update", "Line 2 is back at full rate after the hydraulic press rebuild. Thanks to maintenance for working through the weekend."),
    ("New faces", "Welcome to {new}, joining {dept}. Badge photos are taken in HR on Tuesdays and Thursdays."),
    ("Safety first", "Hearing protection is required past the yellow line in Bay C. Owen has extra earplugs at the safety station."),
    ("Spotlight", "This issue's spotlight: {who}, who has been with Bellwether for {years} years and still remembers the old building."),
    ("Quality corner", "First-pass yield for the quarter was 96.2%. Ama's team will share the top three defect causes at the town hall."),
    ("Around the yard", "The east lot will be restriped next weekend. Please park in the overflow lot by the water tower."),
    ("Celebrations", "Happy anniversaries to {b1} (10 years) and {b2} (5 years). Congratulations to {b3} on the new baby!"),
    ("Giving back", "Bellwether volunteers cleaned up 2 miles of shoreline at the Lafayette River. Next cleanup is in the spring."),
    ("IT reminder", "Phishing tests continue. If an email asks you to 'verify your payroll', it's probably Desmond. Report it anyway."),
]


def newsletter(cust, c, n):
    items = NEWS_ITEMS_HG if c["co"] == "hg" else NEWS_ITEMS_BW
    title = "The Harrowgate Herald" if c["co"] == "hg" else "Bellwether Bearings"
    sender = HG_HR if c["co"] == "hg" else BW_HR
    people = staff(c)
    for i in range(n):
        d = rand_day()
        d = d - dt.timedelta(days=d.weekday()) + dt.timedelta(days=4)  # Fridays
        if d > END:
            d -= dt.timedelta(days=7)
        names = pick(people, 4)
        fill = dict(new=pick(["Corey Lindqvist", "Hana Ibsen", "Marcus Teal", "Rosa Delgado", "Jun Park", "Abby Fenwick"]),
                    dept=pick(["Customer Service", "the warehouse", "Accounting", "Quality", "Maintenance", "Sales"]),
                    days=rng.randint(60, 400), quarter=pick(["first quarter", "second quarter", "third quarter", "year"]),
                    who=names[0].name, years=rng.randint(8, 31), b1=names[1].name, b2=names[2].name, b3=names[3].name)
        chosen = pick(items, rng.randint(3, 5))
        body_t = "\n\n".join(f"{h.upper()}\n{t.format(**fill)}" for h, t in chosen)
        body_h = "".join(f"<h3>{h}</h3><p>{t.format(**fill)}</p>" for h, t in chosen)
        subject = f"{title} — {d:%B} {d.year}" if maybe() else f"{title}: Vol. {rng.randint(3, 9)}, No. {rng.randint(1, 24)}"
        mail(cust, at(d, 10), sender, ALL_HG if c["co"] == "hg" else ALL_BW, subject,
             text=f"{title}\n\n{body_t}\n", html=f"<html><body><h1>{title}</h1>{body_h}</body></html>")


# ===================================================================== calendar

MEETINGS_COMMON = [
    "Weekly staff meeting", "1:1", "Budget review", "Benefits open enrollment info session", "Fire drill (all staff)",
    "IT: mandatory security awareness training", "Lunch & learn: Excel tips", "All-hands / town hall", "Holiday party planning committee",
    "Quarterly business review", "Team lunch", "Coffee chat", "Hold — do not book", "Focus time", "Dentist (personal)",
    "Pick up kids", "Interview: candidate for {role}", "Retirement send-off", "Performance review", "Vendor demo",
    "Strategic planning offsite", "Year-end close kickoff", "Parking lot repaving — move cars", "Blood drive",
    "Wellness screening", "Ops sync", "Process improvement workshop", "New hire orientation", "CPR / first aid class",
    "Planning: next fiscal year", "Office move walkthrough", "Webinar", "Monthly safety committee", "Leadership offsite debrief",
]
MEETINGS_BY = {
    "okafor-d": ["Ashgrove Pumps account review", "Call w/ Quillon Boatworks re: Q-forecast", "Sales pipeline review",
                 "Trade show booth planning", "CRM training", "Pricing committee", "Ride-along with Kip", "Lunch w/ Calloway Marine buyer"],
    "voss-r": ["Month-end close", "Audit planning w/ Hartwell & Lowe", "Cash flow forecast", "Insurance renewal",
               "Sales tax filing", "Bank covenant review", "AP run review", "Inventory count prep"],
    "muller-z": ["Supplier review: Atlantic Gasket Works", "RFQ kickoff: hydraulic hose", "S&OP meeting", "MRP exceptions review",
                 "Supplier scorecards", "New vendor onboarding: Selkirk Seals", "Cost reduction brainstorm", "Purchasing team huddle"],
    "kettering-t": ["Receiving huddle", "Forklift inspection", "Cycle count", "Staffing plan w/ temp agency", "Yard check",
                    "Dock door maintenance", "5S audit", "Shift handoff"],
    "delacroix-m": ["Board meeting prep", "Contract review: sensor vendor NDA", "Insurance broker call", "Trademark renewal docket",
                    "Employment law update (outside counsel)", "Lease amendment call", "Compliance committee", "CLE: ethics hour"],
}
ROOMS = ["Conference Room A", "Conference Room B", "Break room", "Training room", "Microsoft Teams Meeting", "Zoom",
         "Main lobby", "Plant floor — meet at Bay C", "Front office", "Boardroom", "Phone"]


def ics(uid, start, end, summary, organizer, attendees, location, method="REQUEST", status="CONFIRMED", seq=0,
        description="", rrule=None):
    def fmt(t):
        return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "PRODID:-//Microsoft Corporation//Outlook 16.0 MIMEDIR//EN", "VERSION:2.0",
             f"METHOD:{method}", "BEGIN:VEVENT", f"UID:{uid}", f"SEQUENCE:{seq}", f"DTSTAMP:{fmt(start - dt.timedelta(days=3))}",
             f"DTSTART:{fmt(start)}", f"DTEND:{fmt(end)}", f"SUMMARY:{summary}", f"LOCATION:{location}",
             f"ORGANIZER;CN=\"{organizer.name}\":mailto:{organizer.email}"]
    for a in attendees:
        lines.append(f"ATTENDEE;CN=\"{a.name}\";ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{a.email}")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if description:
        lines.append("DESCRIPTION:" + description.replace("\n", "\\n"))
    lines += [f"STATUS:{status}", "TRANSP:OPAQUE", "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY",
              "DESCRIPTION:Reminder", "END:VALARM", "END:VEVENT", "END:VCALENDAR", ""]
    return "\r\n".join(lines)


AGENDA_BITS = ["Please come with your updates.", "Agenda to follow.", "Lunch will be provided.", "Bring your laptop.",
               "Dial-in details below.", "If you can't make it, send a delegate.", "Moved from last week.",
               "Short one, promise.", "See attached deck (sent separately).", "Recurring — let me know if this time doesn't work.",
               "Camera optional.", "We'll start promptly.", ""]


def calendar(cust, c, n_invites, n_ics):
    me = c["me"]
    people = staff(c)
    titles = MEETINGS_COMMON + MEETINGS_BY[cust] * 2
    for i in range(n_invites + n_ics):
        d = rand_day(weekend=0)
        h = pick([8, 9, 9, 10, 10, 11, 13, 13, 14, 15, 16])
        start = at(d, h, pick([0, 0, 0, 30, 30, 15]))
        start = start.replace(second=0)
        end = start + dt.timedelta(minutes=pick([15, 30, 30, 30, 45, 60, 60, 90, 120]))
        title = pick(titles).format(role=pick(["inside sales", "AP clerk", "dock associate", "buyer", "paralegal", "maintenance tech"]))
        org = me if maybe(0.3) else pick(people)
        att = [p for p in pick(people, rng.randint(1, 6)) if p is not org]
        if me is not org:
            att.append(me)
        uid = f"{rng.getrandbits(64):016X}{rng.getrandbits(64):016X}"
        loc = pick(ROOMS)
        desc = " ".join(pick(AGENDA_BITS, rng.randint(0, 3))).strip()
        if loc in ("Microsoft Teams Meeting", "Zoom"):
            desc += f"\n\nJoin: https://meet.{domain(c)}/j/{rng.randint(10**9, 10**10 - 1)}\nPasscode: {rng.randint(100000, 999999)}"
        rrule = "FREQ=WEEKLY;BYDAY=" + "MO TU WE TH FR".split()[d.weekday()] if maybe(0.15) else None
        if i >= n_invites:  # saved .ics file
            body = ics(uid, start, end, title, org, att, loc, method="PUBLISH", description=desc, rrule=rrule)
            check(body, f"{cust} ics")
            name = pick([f"{slug(title, 30)}.ics", "invite.ics", "meeting.ics", f"{slug(title, 30)}-{d:%Y%m%d}.ics", "calendar.ics"])
            write_text(cust, name, body)
            continue
        kind = pick(["invite", "invite", "invite", "accepted", "declined", "tentative", "canceled", "updated", "forwarded"])
        sent = start - dt.timedelta(days=rng.randint(1, 20), hours=rng.randint(0, 6))
        sent = at(sent.date()) if sent.date() >= START else at(START)
        when_line = f"When: {nice_date(d)} {start:%-I:%M %p}-{end:%-I:%M %p} (UTC{start:%z}) Eastern Time (US & Canada)"
        where_line = f"Where: {loc}"
        if kind in ("invite", "updated", "canceled", "forwarded"):
            method = "CANCEL" if kind == "canceled" else "REQUEST"
            subject = {"invite": title, "updated": f"Updated invitation: {title}", "canceled": f"Canceled: {title}",
                       "forwarded": f"FW: {title}"}[kind]
            frm = org if kind != "forwarded" else pick([p for p in people if p is not me] or people)
            text = f"{when_line}\n{where_line}\n\n{'*' * 30}\n\n{desc}\n"
            if kind == "canceled":
                text = f"{when_line}\n{where_line}\n\n{pick(['Sorry, need to cancel this one.', 'Canceling — will reschedule.', 'No longer needed.', ''])}\n"
            body = ics(uid, start, end, title, org, att, loc, method=method,
                       status="CANCELLED" if kind == "canceled" else "CONFIRMED", seq=1 if kind == "updated" else 0,
                       description=desc, rrule=rrule)
            to = me if frm is not me else att
            mail(cust, sent, frm, to, subject, text=text,
                 attachments=[("invite.ics", body.encode(), "text", "calendar")])
        else:
            resp = {"accepted": "Accepted", "declined": "Declined", "tentative": "Tentative"}[kind]
            who = pick(att) if org is me and att else me
            note = pick(["", "", "Running 5 min late.", "Can we push to 30 min?", "Out that day, sorry.",
                         "I'll dial in.", "Will send someone from my team."]) if kind != "accepted" else ""
            text = f"{who.name} has {resp.lower()} this meeting.\n\n{when_line}\n{where_line}\n\n{note}\n"
            body = ics(uid, start, end, title, org, [who], loc, method="REPLY")
            mail(cust, sent + dt.timedelta(hours=rng.randint(1, 30)), who, org, f"{resp}: {title}", text=text,
                 attachments=[("invite.ics", body.encode(), "text", "calendar")])


# ===================================================================== office chatter

def sig(p, c):
    if maybe(0.3):
        return f"\n\n{p.first}"
    return f"\n\n{p.name}\n{p.title}\n{company(c)}\n" + (f"(757) 555-01{rng.randint(10, 99)}" if c["co"] == "bw" else f"(614) 555-01{rng.randint(10, 99)}")


def thread(cust, c, d, subject, msgs, write="last"):
    """msgs: [(from P, to P or list, body)]. Writes the last message with the history quoted,
    and sometimes earlier ones too."""
    t = at(d)
    history = ""
    out = []
    for i, (frm, to, body) in enumerate(msgs):
        subj = subject if i == 0 else ("RE: " + subject)
        text = body + sig(frm, c)
        if history:
            prev_frm, prev_t, prev_body = history
            text += f"\n\nFrom: {prev_frm.name}\nSent: {format_datetime(prev_t)}\nSubject: {subject}\n\n" + quote(prev_body)
        out.append((t, frm, to, subj, text))
        history = (frm, t, text)
        t = t + dt.timedelta(minutes=rng.randint(4, 300))
    chosen = out[-1:] if write == "last" or len(out) == 1 else out
    for t, frm, to, subj, text in chosen:
        mail(cust, t, frm, to, subj, text=text)


def chatter_templates(cust, c):
    me, people = c["me"], staff(c)
    a, b, x = pick(people, 3)
    allc = ALL_HG if c["co"] == "hg" else ALL_BW
    it = HG_IT if c["co"] == "hg" else BW_IT
    hr = HG_HR if c["co"] == "hg" else BW_HR
    food = pick(["chili", "tacos", "lasagna", "barbecue", "soup", "pizza", "Thai", "breakfast burritos"])
    return [
        ("Potluck sign-up", [(a, allc, f"Hi all — potluck is next Friday at noon in the break room. Theme this time is {food}. Sign-up sheet is on the fridge. Please bring serving spoons, we never have enough."),
                             (me, a, pick(["I'll bring a salad so there's at least one vegetable.", "Put me down for dessert.", "Can I bring rolls? Cooking isn't my thing.", "Count me in for drinks and ice."]))]),
        ("Whose car?", [(b, allc, f"Silver {pick(['Honda', 'Toyota', 'Ford', 'Subaru', 'Hyundai'])} in the visitor lot with the lights on, plate starts with {rng.choice('KTVWX')}{rng.choice('ABCDEFGHJ')}{rng.choice('LMNPQRS')}. Not sure whose it is.")]),
        ("Printer on 2 is jammed AGAIN", [(a, it, "The big printer by accounting is showing error 13.B2 again. I opened every door I could find. There is toner on my sleeve now."),
                                          (it, a, "Ticket #" + str(rng.randint(40000, 49999)) + " created. A technician will be dispatched. Please do not attempt to open the fuser unit."),
                                          (me, a, "Fwiw the one in the copy room works if you print to tray 2.")]),
        ("Password expires in 3 days", [(it, me, f"Your network password will expire in 3 days. To change it, press Ctrl+Alt+Del and choose Change a password. Passwords must be at least 14 characters and may not reuse your last 12 passwords.\n\nThis is an automated message from the {short_co(c)} IT department.")]),
        ("Phishing test results", [(it, allc, f"Thank you to everyone who reported last week's simulated phishing email ('Your package could not be delivered'). {rng.randint(61, 88)}% of you reported it. {rng.randint(4, 17)}% clicked. You know who you are. Short refresher training has been assigned to those folks.")]),
        ("Open enrollment reminder", [(hr, allc, f"Open enrollment runs through {short_date(rand_day())}. Medical premiums are increasing about {rng.randint(4, 9)}% this year; dental and vision are flat. Benefits fair Thursday 11–1 in the training room, with snacks.")]),
        ("Holiday schedule", [(hr, allc, "The office will be closed Thursday and Friday for Thanksgiving, December 24–25, and January 1. The warehouse will run a skeleton crew on December 26; see your supervisor.")]),
        ("Retirement card for Walt", [(x, [a, b, me], "Walt's last day is the 19th after 31 years. There's a card going around in a manila envelope — it's on Lena's desk right now. Also collecting for a gift, $10 suggested, cash or Venmo."),
                                      (me, x, "Signed it. Dropped a $20 in the envelope. Can't believe he's actually going.")]),
        ("Fridge cleanout FRIDAY", [(pick(people), allc, "Friendly reminder that the fridge will be emptied Friday at 3:00. If it is yours and you want it, label it. There is a container in there that has achieved sentience.")]),
        ("Thermostat", [(b, [me, a], "Is it just me or is it 58 degrees in here? I'm wearing my coat at my desk."),
                        (a, [me, b], "Gus says the controller on the east side is busted. Parts on order. Bring a sweater."),
                        (me, [a, b], "There's a space heater in the supply closet but you didn't hear it from me.")]),
        ("Fantasy league — week " + str(rng.randint(2, 16)), [(pick(people), [me, a, b, x], f"Standings attached in my head. {a.first} is in first and insufferable about it. Trade deadline is Tuesday. Whoever is in last buys donuts, per the bylaws."),
                                                           (me, [a, b, x], pick(["I demand a recount.", "My kicker scored more than my running backs. Again.", "Accepting trade offers for literally anyone on my bench.", "The bylaws were never ratified."]))]),
        ("Lunch?", [(a, me, pick(["Taco truck is here. You in?", "Going to grab sandwiches — want anything?", "Lunch at noon? Anywhere but the sub place.", "Pho?"])),
                    (me, a, pick(["Yes. Give me 5.", "Can't, stuck on a call. Bring me back something?", "Already ate, sorry!", "Always yes to pho."]))]),
        ("Lost: blue water bottle", [(pick(people), allc, "Lost a navy blue insulated water bottle with stickers on it (a lighthouse and a dog). Last seen in training room. Sentimental value. Please return to front desk!")]),
        ("United Way campaign", [(pick(people), allc, f"Our United Way campaign kicks off Monday. Last year we raised ${rng.randint(8, 19)},{rng.randint(100, 999)}. Pledge cards in your mailbox; payroll deduction is easiest. Jeans day for anyone who pledges.")]),
        ("Parking lot repaving", [(pick(people), allc, f"The {pick(['east', 'north', 'front'])} lot will be repaved on {short_date(rand_day())} and the following day. Cars left in the lot after 6 a.m. will be towed at the owner's expense. Please use the overflow lot.")]),
        ("Coffee machine", [(b, allc, "The new coffee machine takes pods only. The old drip machine has gone to a better place (Gus's garage). Please don't put the reusable pod in upside down."),
                            (me, b, "This is the worst news I've received all year.")]),
        ("Expense report reminder", [(pick(people), me, f"Hi {me.first}, your expense report for {pick(['September', 'October', 'November', 'January', 'February', 'April', 'May'])} is still open in the system. Please submit by the 5th so it gets into this pay cycle. Missing receipt for a ${rng.randint(12, 140)}.{rng.randint(10, 99)} charge at {pick(['Larkspur Suites', 'a gas station', 'Harborside Grill', 'the airport parking garage'])}.")]),
        ("Out of office", [(me, allc if maybe(0.2) else [a], f"I'll be out {short_date(rand_day())} through the following Monday. {a.first} is covering anything urgent. I'll have my phone but limited email.")]),
        ("Desk move", [(pick(people), [me, a, b], "Facilities is moving desks on Saturday. Please pack anything you care about into the boxes by Friday afternoon and label them. Your phone and monitor will be moved for you."),
                       (a, [me, b], "Does anyone know if we get to keep the window spots?")]),
        ("Training: forklift awareness for office staff", [(pick(people), allc, "Everyone who walks the warehouse floor (including office staff) needs the 20-minute pedestrian safety module by month end. Link in your training portal.")]),
        ("Cake in the break room", [(pick(people), allc, f"There is cake in the break room for {pick(people).first}'s birthday. It's chocolate. Come before the warehouse finds out.")]),
        ("Survey: engagement", [(hr, allc, "Please take 10 minutes to complete the annual engagement survey. It's anonymous (really). The link closes Friday.")]),
        ("Water main — no water until 2pm", [(pick(people), allc, "The city is working on a water main on our street. No running water in the building until about 2 p.m. Bottled water is in the break room. Restrooms at the gas station across the street are available. Sorry.")]),
        ("Ugly sweater contest", [(pick(people), allc, "Ugly sweater contest on the Friday before the holiday party. Prizes for ugliest, most creative, and 'most likely to be a fire hazard'. Lights encouraged.")]),
    ]


def chatter(cust, c, n):
    for _ in range(n):
        subject, msgs = pick(chatter_templates(cust, c))
        thread(cust, c, rand_day(), subject, msgs, write=pick(["last", "last", "all"]))


# ===================================================================== other business

OTHER_CUSTOMERS = [("Ashgrove Pumps", "ashgrovepumps.example", "Carla Jessup"), ("Quillon Boatworks", "quillonboat.example", "Rob Ferrante"),
                   ("Calloway Marine", "callowaymarine.example", "Denise Okoye"), ("Stonebridge HVAC", "stonebridgehvac.example", "Luke Barras"),
                   ("Westfall Agricultural", "westfallag.example", "Maggie Thorn"), ("Pinecrest Trailer Co.", "pinecresttrailer.example", "Hal Stoltz")]
OTHER_PARTS = [("SS hex cap screw, 1/2-13 x 2, 18-8", 0.62), ("Carriage bolt, 3/8-16 x 3, zinc", 0.21), ("Nyloc nut, 5/16-18, 18-8", 0.09),
               ("Flat washer, M8, A4", 0.04), ("Socket head cap screw, M6 x 20, 12.9", 0.14), ("U-bolt, 2 in., galvanized", 1.85),
               ("Threaded rod, 1/2-13 x 36, zinc", 3.40), ("Eye bolt, 3/8-16, forged", 2.75), ("Wing nut, 1/4-20, 316", 0.33)]
OTHER_VENDORS_BW = [("Atlantic Gasket Works", "atlanticgasket.example", "Bev Lomax"), ("Selkirk Seals", "selkirkseals.example", "Tariq Aziz"),
                    ("Hydrafield Hose & Fitting", "hydrafield.example", "Jimmy Crandall"), ("Norland Bearings", "norlandbearings.example", "Sunita Rao"),
                    ("Coastline Electric Supply", "coastlineelectric.example", "Mo Pearce")]


def other_business(cust, c, n):
    me = c["me"]
    for _ in range(n):
        d = rand_day()
        if cust == "okafor-d":
            co, dom, who = pick(OTHER_CUSTOMERS)
            them = P(who, f"{who.split()[0].lower()}@{dom}", "Purchasing")
            part, price = pick(OTHER_PARTS)
            qty = pick([500, 1000, 2500, 5000, 10000])
            po = f"PO-{co[:3].upper()}-{d:%y}-{rng.randint(10, 999):04d}"
            kind = pick(["quote", "ack", "forecast", "visit", "price"])
            if kind == "quote":
                thread(cust, c, d, f"Quote request — {part}", [
                    (them, me, f"Hi {me.first}, can you quote {qty:,} pcs of {part}? Need by end of next month. Usual ship-to."),
                    (me, them, f"Hi {them.first}, happy to. ${price * pick([0.95, 1.0, 1.04]):.3f}/pc, {pick(['stock', '2 weeks', '3–4 weeks'])}, FOB origin. Quote valid 30 days. Want me to book it?")])
            elif kind == "ack":
                mail(cust, at(d), me, them, f"Order acknowledgment {po}",
                     text=f"{them.first},\n\nThanks for order {po}. Confirming {qty:,} pcs {part} at ${price:.3f}, ship date {short_date(next_weekday(d, 5))}.\n\n{me.first}")
            elif kind == "forecast":
                mail(cust, at(d), them, me, f"{co} — rolling forecast",
                     text=f"Dana,\n\nUpdated 13-week forecast below. Volumes are down a little on the carriage bolts and up on stainless.\n\n" +
                     "\n".join(f"Wk {i}: {rng.randint(200, 4000):,}" for i in range(1, 9)) + f"\n\nThanks,\n{them.name}")
            elif kind == "visit":
                thread(cust, c, d, f"Visit to {co}", [(me, them, f"Hi {them.first}, I'll be in your area {short_date(next_weekday(d, 6))}. Could I stop in for 30 minutes to go over next year's pricing? Coffee's on me."),
                                                      (them, me, pick(["Works for me. 10am?", "That week is bad — can we do the following Tuesday?", "Sure, I'll book the small conference room."]))])
            else:
                mail(cust, at(d), pick(HG_STAFF), me, "Price increase letters — review before they go out",
                     text=f"Dana — draft letters for your accounts are in the shared drive. Increase is {rng.randint(3, 7)}% on carbon steel, effective the first of the quarter. Please review {co} by Thursday.")
        elif cust == "voss-r":
            kind = pick(["close", "audit", "bank", "ap", "insurance", "tax", "otherar"])
            subj, body, frm = {
                "close": (f"{d:%B} close checklist", "Reminder of close deadlines: accruals by WD2, inventory reconciliation by WD3, flash P&L to Arthur by WD5. Please flag anything that will be late.", me),
                "audit": ("PBC list — interim fieldwork", "Attached is our updated request list (items 1–46). Most urgent: fixed asset rollforward, inventory reserve analysis, and the bank confirmations. — Hartwell & Lowe LLP", P("Janine Hartwell", "jhartwell@hartwelllowe.example")),
                "bank": ("Line of credit — covenant certificate", f"Raymond, please send the quarterly compliance certificate by the 20th. Current availability on the line is ${rng.randint(400, 900)},000.", P("Doug Merriman", "dmerriman@firstmerrimack.example")),
                "ap": ("AP run Thursday", f"Raymond — the AP run is ${rng.randint(80, 240)},{rng.randint(100, 999)}.{rng.randint(10, 99)}. Steel invoices are the bulk. Please approve in the system by noon.", P("Bonnie Szymanski", f"bszymanski@{HG}")),
                "insurance": ("Property & casualty renewal", "Renewal quotes are in. Property is up 11% (the whole market is), GL is flat, auto up 6%. Let's set a call to review options before the 1st.", P("Neil Ashby", "nashby@beaconriskpartners.example")),
                "tax": ("Sales tax filings", "All states filed for the month. Ohio and Indiana paid by ACH; Kentucky by check because their portal is still down.", P("Bonnie Szymanski", f"bszymanski@{HG}")),
                "otherar": (f"{pick(OTHER_CUSTOMERS)[0]} — slow pay", f"They're at {rng.randint(40, 70)} days on the last three invoices. Not alarming yet, but worth a friendly call from sales.", me),
            }[kind]
            to = pick(HG_STAFF) if frm is me else me
            mail(cust, at(d), frm, to, subj, text=body + sig(frm, c))
        elif cust == "muller-z":
            co, dom, who = pick(OTHER_VENDORS_BW)
            them = P(who, f"{who.split()[0].lower()}@{dom}", "Sales")
            kind = pick(["rfq", "late", "onboard", "price", "scorecard"])
            if kind == "rfq":
                mail(cust, at(d), me, them, f"RFQ {d:%y}-{rng.randint(100, 399)}: {pick(['EPDM gaskets', 'hydraulic hose assemblies', 'sealed bearings', 'cable glands', 'O-ring kits'])}",
                     text=f"Hello {them.first},\n\nPlease quote the attached quantities by {short_date(next_weekday(d, 7))}. Include lead time and minimums.\n\nBest regards,\nZoë")
            elif kind == "late":
                thread(cust, c, d, f"{co} order — status?", [(me, them, f"{them.first}, our order is now {rng.randint(3, 15)} days past the promised date. Can you give me a firm ship date?"),
                                                             (them, me, "Apologies — raw material delay at our end. Shipping Thursday, tracking to follow. I've expedited at no charge.")])
            elif kind == "onboard":
                mail(cust, at(d), pick(BW_STAFF), me, f"New vendor setup: {co}", text=f"Zoë, W-9 and insurance certificate for {co} received. Banking details verified by phone. Vendor is active in the system.")
            elif kind == "price":
                mail(cust, at(d), them, me, "Price adjustment notice", text=f"Dear Customer,\n\nDue to rising material costs, {co} will adjust prices by {rng.randint(2, 8)}% effective next month. We appreciate your business.\n\n{them.name}")
            else:
                mail(cust, at(d), me, [pick(BW_STAFF), pick(BW_STAFF)], "Supplier scorecards — quarter",
                     text="Scorecards are posted. " + ", ".join(f"{v[0]} {rng.randint(78, 99)}" for v in pick(OTHER_VENDORS_BW, 4)) + ". Atlantic Gasket on-time still below target.")
        elif cust == "kettering-t":
            kind = pick(["temp", "forklift", "door", "count", "carrier", "schedule"])
            subj, body, frm = {
                "temp": ("Temp coverage next week", f"Tom, I can send {rng.randint(1, 3)} associates for second shift Monday–Wednesday. Same rate as last time. Please confirm by Friday.", P("Kelsey Marsh", "kmarsh@harborstaffing.example")),
                "forklift": (f"Forklift #{rng.randint(1, 9)} out of service", "Hydraulic leak on the mast. Tagged out. Tech from Coastal Lift coming Thursday.", P("Pete Navarro", f"pnavarro@{BW}")),
                "door": (f"Door {pick([1, 2, 3, 5, 6])} leveler", "The leveler on that door won't stay up. Facilities has a work order in. Use the other doors for now.", P("Gus Whitcomb", f"gwhitcomb@{BW}")),
                "count": ("Cycle count results", f"Bin accuracy {rng.randint(93, 99)}.{rng.randint(0, 9)}% this week. Biggest miss was zip ties again — someone is pulling without scanning.", me),
                "carrier": ("Appointment request", f"Requesting a delivery appointment for {short_date(next_weekday(d, 2))}, {rng.randint(1, 26)} pallets, Blue Heron Logistics, load {rng.randint(100000, 999999)}.", P("Blue Heron Scheduling", "appointments@blueheronlogistics.example")),
                "schedule": ("Holiday shift schedule", "Posted on the board by the time clock. Swaps need my OK in writing. First come first served on the Friday after Thanksgiving.", me),
            }[kind]
            to = pick(BW_STAFF) if frm is me else me
            mail(cust, at(d), frm, to, subj, text=body + sig(frm, c))
        else:
            kind = pick(["nda", "tm", "emp", "lease", "claim", "board", "bar", "policy"])
            subj, body, frm = {
                "nda": ("NDA — Orrery Sensor Technologies", "Marcus, their mutual NDA has a 7-year term and a residuals clause. I'd push back on the residuals. Redline attached in the matter folder.", P("Ivy Castellano", f"icastellano@{BW}")),
                "tm": ("Trademark maintenance — BELLWETHER (stylized)", "Section 8 declaration window opens next month. We'll need current specimens of use. Please confirm the mark is still used on all listed goods.", P("Paula Strand", "pstrand@strandip.example")),
                "emp": ("Question re: FMLA leave", "Marcus, a second-shift employee has asked about intermittent leave. HR wants to make sure we handle the certification correctly. Can we talk Tuesday?", P("Renata Szabo", f"rszabo@{BW}")),
                "lease": ("Warehouse lease — second amendment", "Landlord has agreed to the extension at a 3% annual escalator. They want us to take the HVAC maintenance. I think we can live with that if they replace the rooftop unit first.", P("Lionel Petrakis", "lpetrakis@petrakislaw.example")),
                "claim": ("Slip-and-fall claim (2025 visitor)", "The carrier has offered to settle within the deductible. I recommend we accept. Release language to follow.", P("Hannah Voigt", "hvoigt@commonwealthmutual.example")),
                "board": ("Board deck — legal section", "Please send your legal update for the board deck by Friday: open matters, insurance, compliance training completion.", P("Lena Fairbanks", f"lfairbanks@{BW}")),
                "bar": ("Virginia State Bar — annual dues and MCLE", "Your annual dues statement is available. MCLE compliance deadline: October 31. You have reported 9.0 of 12.0 required hours.", P("VSB Membership", "membership@vsb-notices.example")),
                "policy": ("Updated code of conduct — review", "Draft of the revised code of conduct is ready for your review. Main changes: gifts policy, social media, and the hotline vendor.", me),
            }[kind]
            to = pick(BW_STAFF) if frm is me else me
            mail(cust, at(d), frm, to, subj, text=body + sig(frm, c))


# ===================================================================== drafts

# Each topic: (title choices, generator(c) -> list of (style, text)). Drafts come in
# versions: later versions drop, reorder or reword a few blocks, and pick up comments.

def t_mission(c):
    co = short_co(c)
    verbs = ["deliver", "provide", "build", "earn", "supply", "engineer"]
    nouns = ["quality", "reliability", "integrity", "trust", "excellence", "partnership", "safety", "innovation"]
    out = [("h1", pick(["Mission statement", "Our Mission (DRAFT)", "Mission / Vision / Values", "mission v?"]))]
    for _ in range(rng.randint(2, 4)):
        out.append(("p", f"{co} exists to {pick(verbs)} {pick(['products', 'solutions', 'parts', 'service'])} our customers can count on, "
                         f"through {pick(nouns)}, {pick(nouns)} and {pick(nouns)}{pick(['.', ' — every day.', ', one order at a time.', '.'])}"))
    out.append(("h2", "Values"))
    for v in pick(nouns, rng.randint(3, 6)):
        out.append(("bullet", f"{v.capitalize()}: {pick(['we do what we say', 'we own our mistakes', 'we leave it better than we found it', 'no shortcuts', 'the customer is the boss', 'safety before speed', 'say thank you'])}"))
    if maybe(0.4):
        out.append(("p", pick(["[too long? Arthur wants one sentence]", "NOTE: check with marketing re: tagline", "do we say 'customers' or 'partners'??", "(Hollis hates the word 'synergy', take it out)"])))
    return out


def t_toast(c):
    name = pick(["Walt", "Barb", "Denny", "Carol", "Ernie", "Pat"])
    yrs = rng.randint(22, 38)
    return [("p", f"{pick(['Okay, everybody', 'If I could have everyone’s attention', 'Can I say a few words'])} — {name}, {yrs} years. When {name} started here we still had {pick(['a fax machine in every office', 'paper time cards', 'a pay phone in the lobby', 'one forklift'])}."),
            ("p", f"{pick(['There are a lot of stories I could tell', 'I asked around for stories', 'I was warned not to tell the story about the Christmas party'])}. "
                  f"{pick(['The time the power went out and ' + name + ' hand-wrote every packing list for a week.', 'The legendary chili cook-off of 2014.', 'How ' + name + ' knew every truck driver by name, and their kids’ names.'])}"),
            ("p", f"{name}, we’re going to miss you. {pick(['Enjoy the lake.', 'Go catch some fish.', 'The grandkids are lucky.', 'Don’t call us — we’ll call you (we will definitely call you).'])} Raise your glasses."),
            ("p", pick(["[pause for laughs]", "[TOO LONG - cut the middle]", "", "", "(mention his wife Jeanine?)"]))]


def t_linkedin(c):
    return [("p", pick(["I’ve been thinking a lot about leadership lately.", "Unpopular opinion:", "20 years in this industry taught me one thing.", "Something happened on the shop floor last week that I can’t stop thinking about."])),
            ("p", pick(["A new hire asked me why we do things the way we do.", "We missed a deadline. Here’s what happened next.", "A forklift driver taught me more about process than any MBA class.", "Our best idea this year came from someone who’d been here three weeks."])),
            ("p", pick(["The answer wasn’t in a manual.", "Nobody blamed anyone. We fixed the process.", "Listen to the people closest to the work.", "Culture is what happens when nobody’s watching."])),
            ("p", " ".join(pick(["#leadership", "#manufacturing", "#supplychain", "#culture", "#gratitude", "#operations", "#mondaymotivation"], 3)))]


def t_blog(c):
    topic = pick(["galvanic corrosion", "torque specs", "choosing stainless grades", "the hidden cost of cheap hardware", "winterizing your boat", "thread galling"])
    paras = {
        "galvanic corrosion": ["When two dissimilar metals touch in seawater, the less noble one gives itself up. That’s galvanic corrosion, and it’s why your aluminum bracket looks like oatmeal.",
                               "Isolating washers, careful material choices and sacrificial anodes all help. So does not mixing hardware from the junk drawer.",
                               "Rule of thumb: the fastener should be the more noble metal. A small anode and a big cathode is a recipe for trouble."],
        "torque specs": ["Torque is a proxy for tension, and a lousy one. Friction eats most of what you put into the wrench.",
                         "Lubricated threads can reach the same clamp load at a fraction of the torque. Use the published values for the condition you actually have.",
                         "If it matters, measure elongation. If it really matters, call an engineer."],
        "choosing stainless grades": ["304 and 316 look the same on the shelf. They are not the same in a salt spray.",
                                      "316 has molybdenum, which helps against chlorides. It costs more. It is usually worth it below the waterline.",
                                      "Don’t forget the nut and washer. A 316 bolt with a 304 nut is a 304 joint."],
        "the hidden cost of cheap hardware": ["A fastener is the cheapest part of the assembly and the most expensive part of the failure.",
                                              "Counterfeit and out-of-spec parts are more common than most buyers think. Ask for certifications and actually read them.",
                                              "Total cost includes downtime, rework, and the phone call you don’t want to make."],
        "winterizing your boat": ["Stabilize the fuel, change the oil while it’s warm, and fog the cylinders.",
                                  "Drain the raw-water system or fill it with the pink stuff. Don’t forget the washdown pump.",
                                  "Check every hose clamp while you’re down there. Replace anything that isn’t stainless all the way through."],
        "thread galling": ["Stainless on stainless can seize up mid-turn. That’s galling, and once it starts, the joint is done.",
                           "Slow down, use an anti-seize, and consider mixing grades or a coated nut.",
                           "Power tools and stainless don’t always get along."],
    }[topic]
    out = [("h1", pick([f"Blog draft: {topic}", topic.title(), f"{topic} — post idea", "Untitled post"]))]
    out += [("p", p) for p in paras]
    if maybe():
        out.append(("p", pick(["[need a photo here]", "[link to product page?]", "Marisol: SEO keywords?", "[cite source — someone said 60%?]", "[TBD]"])))
    return out


def t_hoa(c):
    return [("p", f"Dear {pick(['Board Members', 'Members of the Board', 'Sir or Madam', 'Ms. Penrose'])},"),
            ("p", f"I am writing about {pick(['the fence at 412', 'the boat parked on Willow Court', 'the new paint color next door', 'the dog at the corner lot', 'the streetlight that has been out since August'])}. "
                  f"{pick(['This has now gone on for several months.', 'I raised this at the last meeting and nothing has happened.', 'I understand everyone is busy, but', 'I want to be a good neighbor about this.'])}"),
            ("p", pick(["Section 7.3 of the covenants is clear on this point.", "I have attached photos.", "Several other neighbors have mentioned it to me as well.", "I would appreciate a response before the next meeting."])),
            ("p", "Sincerely,"),
            ("p", pick(["[don’t send this angry — sleep on it]", "", "(tone it down?)", ""]))]


def t_soccer(c):
    return [("h1", pick(["Fundraiser letter", "Tidewater Youth Soccer — sponsor ask", "spring fundraiser"])),
            ("p", "Dear Friends and Neighbors,"),
            ("p", f"This season the {pick(['Kempsville Kickers', 'U10 Blue Sharks', 'Lynnhaven Lightning', 'Green Dragons'])} are raising money for new uniforms, goals and field time."),
            ("p", f"A sponsorship of ${pick([100, 150, 250, 500])} puts your business name on our banner and the back of the jerseys."),
            ("p", pick(["Thank you for supporting kids in our community!", "Every dollar goes straight to the team.", "Go Sharks!"])),
            ("p", pick(["[ask Hollis if Bellwether will sponsor?]", "[check spelling of coach’s name]", ""]))]


def t_jobdesc(c):
    role = pick(["Inside Sales Representative", "Accounts Payable Clerk", "Dock Associate (2nd shift)", "Buyer I", "Paralegal", "Maintenance Technician", "Customer Service Rep"])
    return [("h1", role), ("h2", "About the role"),
            ("p", f"{company(c)} is looking for a {role} to join our growing team. {pick(['Competitive pay.', 'Great benefits.', 'Family atmosphere.', 'Room to grow.'])}"),
            ("h2", "Responsibilities")] + [("bullet", r) for r in pick([
                "Answer customer calls and emails", "Enter and process orders", "Load and unload trucks", "Match invoices to purchase orders",
                "Maintain accurate records", "Support month-end close", "Operate forklift and pallet jack", "Draft routine correspondence",
                "Work with vendors on pricing and lead times", "Other duties as assigned"], rng.randint(4, 7))] + [
            ("h2", "Requirements")] + [("bullet", r) for r in pick([
                "High school diploma or equivalent", "2+ years related experience", "Proficient in Excel", "Able to lift 50 lbs",
                "Forklift certification preferred", "Strong attention to detail", "Bachelor’s degree a plus"], rng.randint(3, 5))] + [
            ("p", pick(["[salary range?? ask Gwen]", "[Renata — EEO statement here]", "", "PAY: $__ - $__"]))]


def t_sop(c):
    name = pick(["Visitor sign-in", "Forklift battery charging", "Opening the building", "Closing procedure", "Petty cash", "Conference room booking",
                 "Handling a spill", "Shipping samples", "Badge replacement", "Severe weather"])
    steps = {
        "Visitor sign-in": ["Greet visitor at front desk", "Ask for photo ID", "Print visitor badge", "Call host to escort", "Collect badge at exit"],
        "Forklift battery charging": ["Park truck at charging station", "Set brake, lower forks", "Wear face shield and gloves", "Check water level", "Connect charger", "Log charge start time"],
        "Opening the building": ["Disarm alarm (code in sealed envelope)", "Turn on lights in panel B", "Unlock front door at 7:30", "Start coffee", "Check voicemail"],
        "Closing procedure": ["Walk the floor", "Close all overhead doors", "Turn off compressors", "Set alarm", "Lock gate"],
        "Petty cash": ["Count drawer", "Match receipts", "Record in log", "Get second signature", "Lock in safe"],
        "Conference room booking": ["Open calendar", "Choose room", "Add attendees", "Add catering if needed", "Cancel if not needed!!"],
        "Handling a spill": ["Stop the source if safe", "Put out cones", "Use spill kit", "Bag waste", "Notify supervisor", "Fill out report"],
        "Shipping samples": ["Get sample request form", "Pull parts", "Bag and label", "Enter no-charge order", "Ship ground"],
        "Badge replacement": ["Report lost badge to HR", "Deactivate old badge", "Take photo", "Print new badge", "$10 fee after the first"],
        "Severe weather": ["Monitor alerts", "Supervisors decide by 5 a.m.", "Call tree", "Post on voicemail line", "Notify carriers"],
    }[name]
    out = [("h1", f"SOP — {name}"), ("p", f"Rev {pick(['A', 'B', 'C', '0.3', 'draft'])}. Owner: {pick(['TBD', '???', pick(staff(c)).name])}")]
    out += [("number", s) for s in steps]
    if maybe():
        out.append(("p", pick(["[photos]", "[step 3 — confirm w/ Gus]", "Is this still how we do it?", "NOTE: old version in the binder is wrong"])))
    return out


def t_agenda(c):
    items = pick(["Welcome / safety moment", "Review last meeting’s actions", "Budget", "Hiring update", "Customer feedback", "Systems upgrade",
                  "Inventory levels", "Holiday schedule", "Open floor", "Wrap-up / next steps", "Lunch", "Facilities", "Training plan", "KPIs"], rng.randint(4, 8))
    return [("h1", pick(["Agenda", "AGENDA — offsite", "Meeting agenda (draft)", "Planning session"])),
            ("p", pick(["Date: ___", "Time: 9:00–", "Location: TBD", "When: ??"]))] + [("number", i) for i in items]


def t_fiction(c):
    return [("p", pick(["The lighthouse had not been lit in forty years, but every night at nine Agnes climbed the stairs anyway.",
                         "It was the kind of fog that made the harbor bells sound like they were underwater.",
                         "Nobody in Port Callis remembered who had painted the boat yellow.",
                         "The letter arrived on a Tuesday, which was the first strange thing."])),
            ("p", pick(["She counted the steps out of habit: one hundred and twelve.", "Her father had always said the sea keeps what it wants.",
                        "Down at the dock, someone was whistling a song she hadn’t heard since she was a girl.", "He folded the paper twice and put it in his coat."])),
            ("p", pick(["", "[what happens next??]", "(chapter 2?)", "...", "She didn’t"]))]


def t_survey(c):
    qs = pick(["I have the tools I need to do my job.", "My supervisor gives me useful feedback.", "I would recommend this company as a place to work.",
               "Communication between departments is effective.", "I understand how my work contributes to company goals.",
               "I feel safe at work.", "I see myself working here in two years.", "What is one thing we should start doing?",
               "What is one thing we should stop doing?"], rng.randint(4, 8))
    return [("h1", "Employee survey — questions (draft)"), ("p", "Scale: Strongly disagree (1) — Strongly agree (5)")] + [("number", q) for q in qs]


def t_party(c):
    return [("h1", pick(["Holiday party", "Summer picnic", "Party planning notes"])),
            ("bullet", f"Venue: {pick(['Harborside Grill back room', 'the warehouse (cleared out)', 'Lakeview Pavilion', 'VFW hall'])} — deposit ${rng.randint(2, 9)}00"),
            ("bullet", f"Food: {pick(['taco bar', 'BBQ', 'Italian buffet', 'potluck + company brings ham'])}"),
            ("bullet", f"Budget: ${rng.randint(18, 60)}/head x ~{rng.randint(40, 140)}"),
            ("bullet", pick(["DJ vs. playlist?", "Raffle prizes: TV, gift cards, extra PTO day", "Ugly sweater contest", "Kids welcome? decide"])),
            ("bullet", pick(["Need volunteers for setup", "Ask Gus re: tables", "No karaoke this year (per Arthur)", "Shuttle?"]))]


def t_product(c):
    name = pick(["SeaGrip", "TideLock", "HarborLine", "ShoreTite", "MarinaPro", "Keelson"])
    return [("h1", f"{name} — product copy"), ("p", f"{name} {pick(['hardware', 'fasteners', 'fittings', 'brackets'])} are made for the harshest saltwater environments."),
            ("p", pick(["Engineered for strength. Built for the long haul.", "Because failure isn’t an option at sea.", "Trusted by boatbuilders up and down the coast."])),
            ("bullet", pick(["316 stainless construction", "Passivated finish", "Available in metric and inch sizes", "Bulk and retail packaging"])),
            ("bullet", pick(["Made in USA", "Lot traceable", "RoHS compliant", "Ships same day"])),
            ("p", pick(["[legal: can we say 'never rusts'?]", "[need photos from Marisol]", "", "[pricing TBD]"]))]


def t_nomination(c):
    who = pick(staff(c))
    return [("h1", "Employee of the Quarter — nomination"), ("p", f"I would like to nominate {who.name} ({who.title})."),
            ("p", f"{who.first} {pick(['always goes the extra mile', 'trained three new people this quarter', 'came in on a Saturday to help us catch up', 'found a billing error that saved us thousands', 'keeps everyone laughing on bad days'])}."),
            ("p", pick(["Without being asked.", "And never complains.", "That’s rare.", ""])),
            ("p", pick(["[add example from the October rush]", "", "Submitted by:"]))]


def t_recipe(c):
    return [("h1", pick(["Chili (cook-off)", "Mom’s cornbread", "Crab dip", "Banana bread", "Brunswick stew"])),
            ("h2", "Ingredients")] + [("bullet", i) for i in pick(["2 lb ground beef", "1 onion, diced", "3 cloves garlic", "2 cans kidney beans", "1 can crushed tomatoes",
                                                                    "2 tbsp chili powder", "1 tsp cumin", "salt & pepper", "1 cup cornmeal", "2 eggs", "1 lb lump crab", "8 oz cream cheese",
                                                                    "Old Bay to taste", "3 ripe bananas", "1 stick butter"], rng.randint(5, 9))] + [
            ("h2", "Steps"), ("p", pick(["Brown the meat, add everything else, simmer 2 hours. Better the next day.", "Mix wet, mix dry, combine. 350° for 25 min.",
                                         "Mix, top with cheese, bake at 375° until bubbly.", "[ask Mom about the secret ingredient]"]))]


def t_list(c):
    items = pick(["milk", "eggs", "call dentist", "oil change", "renew registration", "birthday card for Jen", "dry cleaning", "return library books",
                  "cat food", "light bulbs (60W)", "fix gutter", "pay water bill", "sign permission slip", "pick up prescription", "gift for Secret Santa ($25 max)",
                  "book flights", "cancel streaming trial", "batteries AA", "printer paper", "lawn guy — call back"], rng.randint(4, 10))
    return [("p", pick(["TODO", "to do", "list", "Saturday", "errands", "don't forget"]))] + [("bullet", i) for i in items]


def t_fragment(c):
    return [("p", pick(["asdf", "test", "call back re: the thing", "pw hint: dog's name + year??", "see notes", "ok so",
                        "Per our conversation,", "Hi —", "Dear", "Thanks for your patience while we", "Following up on",
                        "agenda?", "1.", "TBD", "...", "Q: why does the report show two totals", "ext 214", "lunch Thursday?",
                        "remember to ask about the parking validation", "copy of copy of", "do not delete"]))]


def t_notes(c):
    ppl = pick(staff(c), 3)
    ini = [f"{p.name.split()[0][0]}{p.name.split()[-1][0]}" for p in ppl]
    lines = [f"{pick(ini)}: {pick(['thinks we should wait', 'disagrees', 'will check numbers', 'wants more data', 'ok with it', 'out next week', 'raised the budget issue again', 'not convinced'])}"
             for _ in range(rng.randint(3, 7))]
    return [("p", pick(["notes", "Mtg notes", "call notes", "notes from Tues", "?? meeting"]))] + [("bullet", l) for l in lines] + [
        ("p", f"ACTION: {pick(['circle back', 'revisit next month', 'send summary', 'get quote', 'schedule follow-up', 'nobody owns this — fix'])}")]


def t_rfp(c):
    if c["co"] == "hg":
        client, subject = "City of Chesapeake Department of Public Works", "Annual supply of galvanized and stainless fasteners"
    else:
        client, subject = "Hampton Roads Ferry Authority", "Repower of two passenger vessels: auxiliary systems"
    return [("h1", f"Response to RFP — {subject}"), ("p", f"Submitted to: {client}"), ("h2", "1. Company overview"),
            ("p", f"{company(c)} has served customers in the region for over {rng.randint(18, 45)} years."), ("h2", "2. Approach"),
            ("p", pick(["[Kip to write]", "[TBD — need input from ops]", "We will assign a dedicated account team and hold quarterly reviews.", "Our approach rests on three principles: ..."])),
            ("h2", "3. Pricing"), ("p", pick(["See Attachment B.", "[DO NOT SEND — placeholder numbers]", "$_____"])),
            ("h2", "4. References"), ("p", pick(["[get permission from Ashgrove]", "1.\n2.\n3.", "TBD"]))]


def t_selfreview(c):
    return [("h1", pick(["Self-assessment", "Annual review — my comments", "self eval"])),
            ("p", f"This year I {pick(['took on more responsibility', 'focused on process improvement', 'trained two new team members', 'cleaned up a lot of old records', 'worked on cross-department communication'])}."),
            ("p", f"Goal for next year: {pick(['get certified', 'delegate more', 'reduce overtime', 'improve forecasting', 'learn the new system', 'take a vacation'])}."),
            ("p", pick(["Areas for improvement: saying no.", "[ask for raise here?]", "(don’t mention the reorg)", ""]))]


def t_press(c):
    return [("p", "FOR IMMEDIATE RELEASE"), ("h1", f"{company(c)} {pick(['opens new distribution center', 'names new vice president', 'celebrates anniversary', 'earns ISO 9001 recertification', 'donates to local food bank'])}"),
            ("p", f"{pick(['COLUMBUS, Ohio', 'NORFOLK, Va.'] if True else [])} — {company(c)} today announced {pick(['an expansion that will add 20 jobs', 'a new partnership with local schools', 'a milestone year', 'investments in new equipment'])}."),
            ("p", f"“{pick(['We’re proud of our people', 'This is a great day for our team', 'Our customers deserve the best', 'We’re just getting started'])},” said {pick(['Arthur Pell, president', 'Hollis Brandt, plant manager', 'a company spokesperson'])}."),
            ("p", "###"), ("p", pick(["[quote approved?]", "[media contact]", "[date]", ""]))]


def t_itinerary(c):
    place = pick(["Outer Banks", "Asheville", "Lisbon", "Myrtle Beach", "Lake Erie", "Charleston", "Montreal"])
    return [("h1", f"{place} trip")] + [("bullet", f"Day {i}: {pick(['drive down', 'beach', 'brewery tour', 'museum', 'rain day — movies', 'hike', 'dinner reservation 7pm', 'fly home', 'nothing planned!!'])}") for i in range(1, rng.randint(3, 7))] + [
        ("p", pick(["confirm rental car", "dog sitter: Kara?", "pack charger", "hotel conf # in email"]))]


def t_wedding(c):
    return [("p", pick(["For those who don’t know me, I’m the cousin who taught Ben to drive. I apologize to everyone on Route 13.",
                        "Good evening. I’ve known Sarah since we were six, when she bit me at a birthday party.",
                        "I was told to keep this short, so I wrote it on an index card. A small one."])),
            ("p", pick(["When they met, neither of them could cook. Now one of them can.", "They are the only two people I know who argue about the right way to load a dishwasher and both win.",
                        "Watching them together, you just know."])),
            ("p", "So please raise your glasses —"), ("p", pick(["[name of venue?]", "[joke about the boat?]", ""]))]


def t_boothtalk(c):
    return [("h1", pick(["Trade show talking points", "Booth script", "Show — what to say"]))] + [("bullet", b) for b in pick([
        "Ask what they’re working on before pitching", "Scan every badge", "Mention same-day shipping", "Hand out the stress-ball bolts",
        "Don’t eat at the booth", "Lead with service, not price", "Get a follow-up date", "Raffle sign-up for the cooler",
        "Know the three new product lines", "Two people at the booth at all times"], rng.randint(4, 7))]


def t_bookclub(c):
    book = pick(["The Salt Road", "A Harbor in Winter", "Kitchens of the North", "The Tenth Bridge", "What the River Took"])
    return [("h1", f"Book club — {book}")] + [("number", q) for q in pick([
        "What did you think of the ending?", "Which character did you relate to most?", "Did the time jumps work for you?",
        "Was the narrator reliable?", "What would you have done in her place?", "Did the title make sense by the end?", "Rate it 1–5"], rng.randint(3, 6))] + [
        ("p", pick(["Host: Linda. Bring wine.", "next month: pick something shorter please", ""]))]


def t_complaint(c):
    return [("p", pick(["To whom it may concern,", "Hello,", "Dear Customer Service,"])),
            ("p", f"I am writing about my {pick(['dishwasher repair', 'cable bill', 'contractor invoice for the deck', 'rental car charge', 'delayed furniture delivery'])}. "
                  f"{pick(['I was charged twice.', 'The technician never showed up and I took the day off.', 'It has been six weeks.', 'This is the third time I have called.'])}"),
            ("p", pick(["Please refund the charge.", "I would like this resolved by the end of the month.", "I expect a call back.", "I have been a loyal customer for 12 years."])),
            ("p", pick(["[account # in email]", "[too angry? soften]", "Regards,", ""]))]


def t_unsent(c):
    who = pick(staff(c))
    return [("p", f"{who.first},"),
            ("p", pick(["I want to be honest about how that meeting felt.", "I’ve tried to raise this a few times now.", "Can we please stop scheduling things at 4:30 on Fridays.",
                        "The parking situation is out of hand.", "I didn’t appreciate being cc’d on that."])),
            ("p", pick(["I think we can fix it.", "Let’s talk.", "Just my two cents.", "This isn’t personal."])),
            ("p", pick(["[DON’T SEND]", "(draft — sleep on it)", "", "never mind"]))]


def t_church(c):
    return [("h1", pick(["Bulletin blurb", "Newsletter — volunteer corner", "St. Brendan’s announcements"])),
            ("p", pick(["Coat drive continues through the end of the month. Bins are in the narthex.", "We need two more drivers for the Meals on Wheels route on Thursdays.",
                        "Choir rehearsal moves to Wednesday at 7 starting next week.", "Thank you to everyone who helped with the fall festival!"])),
            ("p", pick(["Questions? Call the office.", "All are welcome.", "[check dates with Fr. Tom]", ""]))]


def t_townhall(c):
    return [("h1", "Town hall — likely questions")] + [("p", f"Q: {q}\nA: {pick(['[TBD]', 'We’ll share more soon.', 'Good question —', 'Ask Corinne', 'No plans to change that.'])}")
                                                       for q in pick(["Are there layoffs coming?", "Why did the parking change?", "When do raises happen?",
                                                                      "Is the 401(k) match changing?", "Will we get the day after Thanksgiving off?",
                                                                      "What’s happening with the old building?", "Why don’t we have a cafeteria?"], rng.randint(3, 6))]


DRAFT_TOPICS = {
    "mission": t_mission, "toast": t_toast, "linkedin": t_linkedin, "blog": t_blog, "hoa": t_hoa, "soccer": t_soccer,
    "jobdesc": t_jobdesc, "sop": t_sop, "agenda": t_agenda, "fiction": t_fiction, "survey": t_survey, "party": t_party,
    "product": t_product, "nomination": t_nomination, "recipe": t_recipe, "list": t_list, "fragment": t_fragment,
    "notes": t_notes, "rfp": t_rfp, "selfreview": t_selfreview, "press": t_press, "itinerary": t_itinerary,
    "wedding": t_wedding, "boothtalk": t_boothtalk, "bookclub": t_bookclub, "complaint": t_complaint, "unsent": t_unsent,
    "church": t_church, "townhall": t_townhall,
}
WEIGHTS = {
    "okafor-d": dict(linkedin=3, boothtalk=3, product=3, rfp=2, soccer=2, bookclub=3),
    "voss-r": dict(hoa=4, complaint=3, selfreview=2, agenda=3, party=2),
    "muller-z": dict(recipe=3, church=2, fiction=3, wedding=2, survey=2),
    "kettering-t": dict(sop=5, toast=3, nomination=3, recipe=2, list=2),
    "delacroix-m": dict(townhall=3, mission=3, unsent=2, press=2, church=2, itinerary=2),
}

NAMES = ["Document{n}.docx", "Doc{n}.docx", "draft.docx", "draft{n}.docx", "untitled.txt", "untitled ({n}).txt", "New Text Document.txt",
         "New Text Document ({n}).txt", "notes.txt", "notes{n}.txt", "Copy of {t}.docx", "{t}.docx", "{t} DRAFT.docx", "{t}_v{v}.docx",
         "{t} v{v} final.docx", "{t}_FINAL.docx", "{t}_FINAL_final.docx", "{t} (edits).docx", "{t} - Copy.docx", "{t}.txt", "{t}.md",
         "temp.txt", "scratch.txt", "{t}.pdf", "Microsoft Word - {t}.pdf", "scan{n}.pdf", "{t} rev{v}.docx", "~{t}.docx",
         "{t} OLD.docx", "{t} - do not use.docx", "{t} (1).docx", "{t} (Autosaved).docx", "{t}_{d}.docx"]
TOPIC_WORDS = {"mission": ["mission", "mission statement", "values"], "toast": ["toast", "retirement speech", "walt speech"],
               "linkedin": ["linkedin post", "post", "LI draft"], "blog": ["blog", "blog post", "article"], "hoa": ["HOA letter", "letter to board", "letter"],
               "soccer": ["soccer letter", "sponsor letter", "fundraiser"], "jobdesc": ["job description", "JD", "posting"], "sop": ["SOP", "procedure", "process"],
               "agenda": ["agenda", "offsite agenda"], "fiction": ["story", "chapter 1", "idea"], "survey": ["survey", "survey questions"],
               "party": ["party", "party planning", "picnic"], "product": ["product copy", "copy", "web copy"], "nomination": ["nomination", "EOQ"],
               "recipe": ["recipe", "chili"], "list": ["list", "todo"], "fragment": ["temp", "x", "new"], "notes": ["notes", "mtg notes"],
               "rfp": ["RFP response", "proposal", "RFP"], "selfreview": ["self review", "review"], "press": ["press release", "PR"],
               "itinerary": ["trip", "itinerary"], "wedding": ["speech", "toast"], "boothtalk": ["show notes", "booth"], "bookclub": ["book club"],
               "complaint": ["complaint", "letter"], "unsent": ["email draft", "draft"], "church": ["bulletin", "newsletter blurb"],
               "townhall": ["town hall QA", "FAQ"]}


def revise(blocks):
    """A later version: drop, swap, reword or annotate a block or two."""
    b = list(blocks)
    for _ in range(rng.randint(1, 3)):
        op = pick(["drop", "swap", "note", "edit", "dup"])
        body = [i for i, (s, _) in enumerate(b) if s != "h1"]
        if not body:
            break
        i = pick(body)
        if op == "drop" and len(body) > 2:
            b.pop(i)
        elif op == "swap" and len(body) > 1:
            j = pick(body)
            b[i], b[j] = b[j], b[i]
        elif op == "note":
            b.insert(i, ("p", pick(["[revised per comments]", "[moved this up]", "<<check>>", "[cut?]", "XX", "[new para]", "*** see email ***"])))
        elif op == "edit":
            s, t = b[i]
            b[i] = (s, t.replace(".", pick([".", "!", " — really.", "."]), 1) + pick(["", " (revised)", " Thanks.", ""]))
        else:
            b.insert(i, b[i])
    return b


def drafts(cust, c, n):
    weights = {k: 1 for k in DRAFT_TOPICS}
    weights.update(WEIGHTS[cust])
    weights["fragment"] = weights.get("fragment", 1) + 3
    weights["notes"] = weights.get("notes", 1) + 2
    keys = list(weights)
    made = 0
    while made < n:
        topic = rng.choices(keys, [weights[k] for k in keys])[0]
        blocks = DRAFT_TOPICS[topic](c)
        versions = 1 if topic in ("fragment", "list") else rng.choices([1, 2, 3, 4, 5], [5, 3, 2, 1, 1])[0]
        d = rand_day(weekend=0.25)
        base = pick(TOPIC_WORDS[topic])
        fmt = None
        for v in range(1, versions + 1):
            if made >= n:
                break
            when = at(d)
            pattern = pick(NAMES)
            name = pattern.format(n=rng.randint(1, 14), t=base, v=v, d=f"{d:%m%d%y}")
            if fmt and versions > 1:                         # versions keep their format
                name = name.rsplit(".", 1)[0] + "." + fmt
            fmt = name.rsplit(".", 1)[1]
            title = base if maybe(0.5) else ""
            author = doc_author(c)
            if fmt == "docx":
                write_docx(cust, name, title, blocks, when, author)
            elif fmt == "pdf":
                write_pdf(cust, name, pick([f"Microsoft Word - {base}", base, "untitled", ""]), blocks, when, author)
            else:
                text = render_text(blocks, md=fmt == "md")
                write_text(cust, name, text)
            made += 1
            blocks = revise(blocks)
            d = d + dt.timedelta(days=rng.randint(0, 20))


def render_text(blocks, md=False):
    out, k = [], 0
    bullet = pick(["- ", "* ", "• ", "  - "])
    for s, t in blocks:
        if s == "number":
            k += 1
            out.append(f"{k}. {t}")
            continue
        k = 0
        if s == "h1":
            out.append(("# " if md else "") + (t if md or maybe() else t.upper()))
        elif s == "h2":
            out.append(("## " if md else "") + t)
        elif s == "bullet":
            out.append(bullet + t)
        else:
            out.append(t)
    sep = "\n\n" if maybe(0.6) else "\n"
    return sep.join(out).strip() + ("\n" if maybe(0.7) else "")


# ===================================================================== spreadsheets and saved pages

def spreadsheets(cust, c, n):
    people = staff(c)
    makers = [
        ("potluck-signup.csv", lambda: [["Name", "Bringing", "Serves"]] + [[p.name, pick(["chips", "salad", "brownies", "wings", "mac & cheese", "drinks", "plates/cups", "cookies", ""]), str(rng.randint(4, 20))] for p in pick(people, rng.randint(5, 10))]),
        ("mileage-log.csv", lambda: [["Date", "From", "To", "Purpose", "Miles"]] + [[f"{rand_day():%m/%d/%Y}", c["home"], pick(["customer visit", "bank", "post office", "training", "trade show", "supplier"]), pick(["", "mtg", "drop off", "pick up"]), f"{rng.randint(4, 220)}"] for _ in range(rng.randint(5, 14))]),
        ("office-chairs.csv", lambda: [["Asset tag", "Location", "Condition", "Notes"]] + [[f"FA-{rng.randint(1000, 1999)}", pick(["Rm 101", "Rm 104", "open area", "conf A", "storage"]), pick(["good", "fair", "broken arm", "squeaks", "missing wheel"]), ""] for _ in range(rng.randint(6, 18))]),
        ("fantasy-standings.csv", lambda: [["Team", "Owner", "W", "L", "PF"]] + [[pick(["Forklift Frenzy", "Dock Rockets", "The Accruals", "Torque Masters", "Pallet Town", "Net 30", "Bolt Action"]), p.first, str(rng.randint(0, 12)), str(rng.randint(0, 12)), f"{rng.uniform(800, 1500):.1f}"] for p in pick(people, 6)]),
        ("secret-santa.csv", lambda: (lambda g: [["Giver", "Receiver"]] + [[g[i].first, g[(i + 1) % len(g)].first] for i in range(len(g))])(pick(people, rng.randint(5, 9)))),
        ("training-roster.csv", lambda: [["Employee", "Course", "Completed", "Expires"]] + [[p.name, pick(["Hazcom", "Forklift", "CPR", "Harassment prevention", "Fire extinguisher", "Phishing 101"]), f"{rand_day():%Y-%m-%d}", pick(["", f"{rand_day(dt.date(2026, 8, 1), dt.date(2028, 12, 31)):%Y-%m-%d}"])] for p in pick(people, rng.randint(5, 12))]),
        ("show-leads.csv", lambda: [["Company", "Contact", "Interest", "Follow up"]] + [[pick(["Marlow Dredging", "Keystone Docks", "Harbor Point Marina", "Seaview Yacht Service", "Brightwater Pumps", "Tolliver HVAC"]), pick(["Jim", "Angela", "Sam R.", "Pat", "?"]), pick(["stainless", "catalog", "pricing", "just browsing", "wants a call"]), pick(["Y", "N", "maybe"])] for _ in range(rng.randint(6, 15))]),
        ("budget-draft.csv", lambda: [["Category", "Last year", "This year", "Notes"]] + [[k, f"{rng.randint(5, 300) * 100}", f"{rng.randint(5, 320) * 100}", pick(["", "?", "check w/ Corinne", "cut?"])] for k in pick(["Travel", "Training", "Supplies", "Software", "Consulting", "Equipment", "Meals", "Subscriptions", "Repairs"], rng.randint(4, 8))]),
        ("phone-list.csv", lambda: [["Name", "Ext", "Mobile"]] + [[p.name, str(rng.randint(200, 299)), f"555-01{rng.randint(10, 99)}"] for p in pick(people, len(people))]),
        ("lunch-order.csv", lambda: [["Name", "Order", "Paid"]] + [[p.first, pick(["turkey club", "veggie wrap", "Italian sub no onions", "chicken caesar", "BLT", "soup + half"]), pick(["Y", "N", "venmo'd"])] for p in pick(people, rng.randint(4, 9))]),
    ]
    for _ in range(n):
        name, make = pick(makers)
        write_csv(cust, name, make())


HTML_PAGES = [
    ("weather.html", "Weather — 10-Day Forecast", lambda c: "<h1>10-Day Forecast: " + c["home"] + "</h1><table>" +
     "".join(f"<tr><td>{d}</td><td>{pick(['Sunny', 'Cloudy', 'Rain', 'T-storms', 'Partly cloudy'])}</td><td>{rng.randint(40, 92)}°/{rng.randint(25, 70)}°</td></tr>" for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) + "</table>"),
    ("hotel-confirmation.html", "Your reservation is confirmed", lambda c: f"<h1>Larkspur Suites — Reservation confirmed</h1><p>Confirmation number: LS{rng.randint(10**7, 10**8 - 1)}</p>"
     f"<p>Guest: {c['me'].name}</p><p>Check-in: {short_date(rand_day())} after 3:00 PM</p><p>Room: King, non-smoking</p><p>Rate: ${rng.randint(119, 249)}.00 per night plus tax</p>"),
    ("recipe.html", "The Best Slow Cooker Pulled Pork", lambda c: "<h1>The Best Slow Cooker Pulled Pork</h1><p>Jump to recipe</p><p>When I was a little girl, my grandmother would make this every Sunday... "
     "(scroll scroll scroll)</p><h2>Ingredients</h2><ul><li>4 lb pork shoulder</li><li>1 cup barbecue sauce</li><li>1 onion</li><li>brown sugar</li></ul><h2>Instructions</h2><p>Low for 8 hours. Shred. Serve.</p><p>342 comments</p>"),
    ("webinar-registration.html", "Registration confirmed", lambda c: f"<h1>You're registered!</h1><p>Lumenfold Live: Five reports every operations leader should automate</p><p>{nice_date(rand_day())}, 1:00 PM ET</p><p>Add to calendar: Google | Outlook | iCal</p>"),
    ("order-status.html", "Order status", lambda c: f"<h1>Bramblecrest Office Goods — Order #{rng.randint(10**6, 10**7)}</h1><table><tr><th>Item</th><th>Qty</th><th>Status</th></tr>"
     + "".join(f"<tr><td>{i}</td><td>{rng.randint(1, 12)}</td><td>{pick(['Shipped', 'Backordered', 'Processing'])}</td></tr>" for i in pick(["Copy paper, case", "Blue pens, 12-pk", "Desk organizer", "Toner 87X", "Dry-erase markers", "Hand soap"], 3)) + "</table>"),
    ("parking-map.html", "Visitor parking", lambda c: f"<h1>{short_co(c)} — Visitor Parking</h1><p>Visitors park in the front lot, spaces marked V. Overflow parking is across the street. Do not park in the fire lane.</p>"),
    ("benefits-summary.html", "Benefits at a glance", lambda c: "<h1>Benefits at a Glance</h1><table><tr><th>Plan</th><th>Employee</th><th>Family</th></tr>"
     + "".join(f"<tr><td>{p}</td><td>${rng.randint(40, 180)}</td><td>${rng.randint(300, 900)}</td></tr>" for p in ["PPO", "HDHP", "Dental", "Vision"]) + "</table>"),
]


def html_pages(cust, c, n):
    for _ in range(n):
        name, title, body = pick(HTML_PAGES)
        write_html(cust, name, f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title></head>"
                               f"<body><div class='nav'>Home | Account | Help | Sign out</div>{body(c)}"
                               f"<div class='footer'>© {pick([2025, 2026])} All rights reserved. Privacy | Terms | Cookies</div></body></html>")


# ===================================================================== main

PLAN = dict(drafts=56, marketing=26, calendar=16, ics=3, chatter=14, other=10, newsletter=4, csv=4, html=2)


def clear_previous():
    if not LISTING.exists():
        return
    for line in LISTING.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            (CORPUS / line).unlink(missing_ok=True)


def main():
    clear_previous()
    for cust, c in CUSTODIANS.items():
        drafts(cust, c, PLAN["drafts"])
        marketing(cust, c, PLAN["marketing"])
        calendar(cust, c, PLAN["calendar"], PLAN["ics"])
        chatter(cust, c, PLAN["chatter"])
        other_business(cust, c, PLAN["other"])
        newsletter(cust, c, PLAN["newsletter"])
        spreadsheets(cust, c, PLAN["csv"])
        html_pages(cust, c, PLAN["html"])
    names = sorted(str(p.relative_to(CORPUS)) for p in written)
    LISTING.write_text("# Written by build_noise.py: the corpus's irrelevant documents. Do not edit.\n"
                       + "\n".join(names) + "\n", encoding="utf-8", newline="\n")
    size = sum(p.stat().st_size for p in written)
    print(f"{len(written)} noise files, {size / 1e6:.1f} MB")
    for cust in CUSTODIANS:
        print(f"  {cust}: {sum(1 for x in names if x.startswith(cust + '/'))}")


if __name__ == "__main__":
    main()
