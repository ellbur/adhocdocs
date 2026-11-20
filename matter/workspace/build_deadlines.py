"""Build deadlines.md (a literate page) from the readers' parts/*.limits.jsonl.

Quotations are references (::q{#id}); the refs are added afterwards with
`page ref add deadlines.md --batch parts/all.resolved.jsonl`.
"""
import json
import re
from pathlib import Path

W = Path(__file__).parent
PARTS = ["frcp-1", "frcp-2", "frcp-3", "frcp-4", "frcp-5", "frcp-6", "usc9"]

# Items the readers flagged as timing markers or definitions rather than time
# limits: cited at the end with the reason, not listed as deadlines.
NOT_LIMITS = {
    "r3": "Says when an action is commenced (on filing); it is the trigger for other periods, not a limit itself.",
    "r12h1-waiver": "A waiver rule keyed to the first Rule 12 motion or responsive pleading, with no period of its own.",
    "r13a1-time-of-service": "Fixes the moment at which a counterclaim is judged compulsory, not a time to act.",
    "r13a2A-when-commenced": "Fixes the moment at which another pending action is measured, not a time to act.",
    "r26a1a-nowait": "Says initial disclosures need not await a request; the timing is in Rule 26(a)(1)(C)–(D).",
    "r26a2b-10y": "A look-back period for what an expert report must list, not a deadline.",
    "r26a2b-4y": "A look-back period for what an expert report must list, not a deadline.",
    "r37a5a-before": "A condition on expenses (the motion came before a good-faith attempt), not a time limit.",
    "r5.1d": "Says a missed Rule 5.1 notice does not forfeit the claim; it sets no time.",
    "r52a1-close-evidence": "Says when findings may be stated, not a time anyone must act by.",
    "r52c-until-close": "A permission for the court to wait, not a limit.",
    "r53b2-diligence": "A standard of diligence for masters, with no period.",
    "r53g1-before-or-after": "Says compensation may be fixed before or after judgment; no limit.",
    "r57-speedy": "The court may order a speedy hearing; no period.",
    "r64a-throughout": "Says when state seizure remedies are available, not a time limit.",
    "r71.1j2-expedite": "A general direction to expedite, with no period.",
    "r77a-always-open": "Says the court is always open for filing; it bears on computing time (see Rule 6(a)(3)) but sets no limit.",
    "r77c1-hours": "The clerk's office hours; relevant to Rule 6(a)(4) but not a limit.",
    "suppE9a-delay": "Unreasonable delay is a ground for an interlocutory sale, not a time limit.",
    "suppG7c-upon": "Says what happens on entry of a forfeiture judgment; no time to act.",
    "s13-ext": "Lists papers to file with a motion (including any written extension of the time to make the award); the time is the agreement's, not the statute's.",
    "s14-1926": "An applicability cut-off by contract date (January 1, 1926), not a litigation deadline.",
    "s306-1988": "Identifies the rules of procedure by date (July 1, 1988), not a deadline.",
}

STAGES = [
    ("computing", "Computing and extending time"),
    ("commencement", "Commencing the action and service"),
    ("pleadings", "Pleadings, pleading motions and removal"),
    ("parties", "Parties: joinder, class actions, intervention, substitution"),
    ("scheduling", "Pretrial conferences, scheduling and magistrate judges"),
    ("discovery", "Disclosure and discovery (including subpoenas)"),
    ("dismissal-sj", "Dismissal, summary judgment and offers of judgment"),
    ("trial", "Jury demand and trial"),
    ("masters", "Masters"),
    ("judgment", "Judgment, costs, fees and post-trial motions"),
    ("remedies", "Stays, injunctions and enforcement"),
    ("condemnation", "Condemnation of property (Rule 71.1)"),
    ("court", "Court administration and emergencies"),
    ("admiralty", "Supplemental Rules: admiralty, maritime and asset forfeiture"),
    ("socsec", "Supplemental Rules: Social Security review actions"),
    ("arb-agreement", "Arbitration (9 U.S.C.): enforcing the agreement"),
    ("arb-award", "Arbitration (9 U.S.C.): confirming, vacating or modifying an award"),
    ("arb-intl", "Arbitration (9 U.S.C.): Convention awards and removal"),
    ("rulemaking", "Making and amending the rules (28 U.S.C. §§ 2073–2074)"),
]


def stage_of(lim):
    cite = lim["cite"]
    st = lim.get("stage", "")
    if st.startswith("arbitration"):
        return {"arbitration-enforcing-agreement": "arb-agreement",
                "arbitration-award-confirm-vacate-modify": "arb-award"}.get(st, "arb-intl")
    if cite.startswith("28 U.S.C."):
        return "rulemaking"
    if st == "admiralty-supplemental":
        return "admiralty"
    if st == "social-security-supplemental":
        return "socsec"
    m = re.search(r"P\. (\d+)(?:\.(\d+))?", cite)
    n = int(m.group(1))
    dot = m.group(2)
    if n == 4:
        return "commencement"
    if n == 5:
        return "computing" if re.search(r"5\(b\)", cite) else ("court" if "5(d)(2)" in cite else "pleadings")
    if n == 6:
        return "computing" if "6(c)" not in cite else "pleadings"
    if n in (7, 8, 9, 10, 11, 12, 13, 14, 15):
        return "pleadings"
    if n == 16:
        return "scheduling"
    if 17 <= n <= 25:
        return "parties"
    if 26 <= n <= 37 or n == 45:
        return "discovery"
    if n in (41, 56, 68):
        return "dismissal-sj"
    if n in (38, 39, 47, 48, 49, 50, 51, 52) and not re.search(r"50\(b\)|50\(d\)|52\(b\)", cite):
        return "trial"
    if n == 53:
        return "masters"
    if 54 <= n <= 61 or (n in (50, 52)) or (n == 62 and dot == "1"):
        return "judgment"
    if 62 <= n <= 70:
        return "remedies"
    if n == 71 and dot == "1":
        return "condemnation"
    if n == 72:
        return "scheduling"
    if n == 77:
        return "judgment" if "77(d)" in cite else "court"
    if n == 81:
        return "trial" if "81(c)(3)" in cite else "pleadings"
    if n == 87:
        return "judgment" if "87(c)(2)" in cite else "court"
    return "court"


def sort_key(lim):
    """Rule order inside a stage: numbers, then subdivision letters."""
    c = lim["cite"]
    m = re.search(r"(?:P\.|§|Soc\. Sec\.|Supp\. R\.)\s*([0-9A-G]+)(?:\.(\d+))?(.*)", c)
    if not m:
        return (999, 0, c)
    head = m.group(1)
    num = int(head) if head.isdigit() else ord(head[0])
    return (num, int(m.group(2) or 0), m.group(3))


def nid(i):
    return re.sub(r"[^a-z0-9-]", "-", i.lower())


def clean(s):
    s = str(s).strip().rstrip(".")
    return s.replace("\n", " ")


def yesno(v):
    return "Yes" if v else "No"


def main():
    lims = []
    for p in PARTS:
        for line in (W / "parts" / f"{p}.limits.jsonl").read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                d["_part"] = p
                lims.append(d)
    ids = [d["id"] for d in lims]
    assert len(ids) == len(set(ids)), "duplicate ids across parts"
    assert len({nid(i) for i in ids}) == len(ids), "ids collide after normalizing"

    listed = [d for d in lims if d["id"] not in NOT_LIMITS]
    skipped = [d for d in lims if d["id"] in NOT_LIMITS]
    by = {}
    for d in listed:
        by.setdefault(stage_of(d), []).append(d)

    out = []
    out.append("---")
    out.append("title: Deadlines in the FRCP and Title 9")
    out.append("cite-style: filename")
    out.append("---")
    out.append("")
    out.append("# Deadlines in the Federal Rules of Civil Procedure and Title 9, U.S. Code")
    out.append("")
    out.append(
        f"This reference lists {len(listed)} time limits set by the Federal Rules of Civil Procedure "
        "(as amended to December 1, 2025, including the Supplemental Rules and the authority provisions "
        "of 28 U.S.C. §§ 2072–2074 printed with them) and by Title 9 of the U.S. Code (arbitration), "
        "grouped by stage of the case. Each entry gives the rule or section, the period, what starts "
        "the clock, what it applies to and who it binds, followed by the language that sets the limit, "
        "quoted from and cited to the document's file name."
    )
    out.append("")
    out.append("How to read it:")
    out.append("")
    out.append("- The quotation under each entry is the document's own text. The fields above it "
               "(period, clock, applies to, binds, court may change, notes) are our paraphrase, made "
               "to help find the right rule; rely on the quotation and the full rule, not the paraphrase.")
    out.append("- Time limits include fixed periods, \"before/after\" sequence requirements and "
               "non-numeric standards (promptly, timely, a reasonable time, at any time before …). "
               "Fixed periods are computed under Rule 6(a), with 3 days added under Rule 6(d) "
               "after some kinds of service; see the first section.")
    out.append("- \"Court may change\" records only what the quoted rule itself allows (a court order setting "
               "another time, a stipulation, good cause). Rule 6(b) separately lets the court extend "
               "most periods, except those Rule 6(b)(2) names.")
    out.append("- Title 9 is in the matter twice (usc-title9-arbitration-2023.pdf and .htm); the copies "
               "were compared limit by limit and agree, so the PDF is quoted.")
    out.append("- Provisions read but not listed as time limits are cited, with the reason, in the last section.")
    out.append("- This is a finding aid compiled from these two documents only; it does not cover local "
               "rules, the Federal Rules of Appellate Procedure, other statutes or case law on these periods.")
    out.append("")

    for key, title in STAGES:
        items = sorted(by.get(key, []), key=sort_key)
        if not items:
            continue
        out.append(f"## {title}")
        out.append("")
        for d in items:
            line = f"**{clean(d['cite'])}: {clean(d['period'])}.**"
            out.append(line)
            out.append(f"Clock: {clean(d.get('trigger', '—'))}. "
                       f"Applies to: {clean(d.get('applies_to', '—'))}. "
                       f"Binds: {clean(d.get('binds', '—'))}. "
                       f"Court may change: {yesno(d.get('court_can_change'))}.")
            if d.get("notes"):
                out.append(f"Notes: {clean(d['notes'])}.")
            out.append("")
            out.append(f"::q{{#{nid(d['id'])}}}")
            out.append("")

    out.append("## Read but not listed as time limits")
    out.append("")
    out.append("These provisions were picked out while reading because they mention timing, but they set no "
               "time within which anyone must act.")
    out.append("")
    for d in sorted(skipped, key=lambda d: (PARTS.index(d["_part"]), sort_key(d))):
        out.append(f"- **{clean(d['cite'])}** :cite{{#{nid(d['id'])}}}: {NOT_LIMITS[d['id']]}")
    out.append("")
    (W / "deadlines.md").write_text("\n".join(out) + "\n")

    with open(W / "parts" / "all.resolved.jsonl", "w") as f:
        for p in PARTS:
            for line in (W / "parts" / f"{p}.resolved.jsonl").read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    r["id"] = nid(r["id"])
                    f.write(json.dumps(r) + "\n")
    print(len(listed), "listed;", len(skipped), "not limits;",
          {k: len(v) for k, v in by.items()})


if __name__ == "__main__":
    main()
