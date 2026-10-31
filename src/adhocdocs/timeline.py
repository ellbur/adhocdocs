"""The timeline display: a list file's items laid out by date, in lanes.

It renders static HTML for a web pane, like the list display, so the
viewer treats each item as any ``data-locator`` element: click or j/k
selects, a linked document pane follows, and tag keys tag. Items run in
date order whatever the list's order (their DOM and sequence order too),
so stepping walks through time.

An item's date is its list field ``date`` when it has one, else the
document's date. ``YYYY-MM`` and ``YYYY`` are allowed, for entries known
only to the month or year. Its label is the field ``event`` (or
``label``), else the email subject or the document name. So a chronology
is a list file whose items carry ``{"fields": {"date": …, "event": …}}``
and a locator to the evidence for each entry.

Lanes are horizontal bands: the list's own groups (``analyze threads
--list`` groups by thread), or a document field (custodian, sender, kind),
or none. Long stretches with nothing in them are cut out of the axis and
shown as a break, so documents years apart don't spread over a mile of
screen. Items without a date are listed after the timeline.
"""

import base64
import html
from datetime import date, timedelta

from . import lists, locators

SCALES = {"day": 140.0, "week": 24.0, "month": 5.0, "year": 0.5}     # pixels per day
LANES = ("group", "custodian", "from", "kind", "none")
MAX_WIDTH = 2400        # "auto" picks the finest scale that fits in this
CHIP_W, ROW_H, LABEL_W, BREAK_W, AXIS_H = 150, 22, 120, 56, 30
MAX_ITEMS = 5000


def parse_date(v) -> date | None:
    if not v:
        return None
    s = str(v)
    for n, fill in ((10, ""), (7, "-01"), (4, "-01-01")):
        try:
            return date.fromisoformat(s[:n] + fill)
        except ValueError:
            continue
    return None


def entries(m, path, lanes: str) -> tuple[dict, list[dict]]:
    """The list's items with a date, label, lane and tooltip each, in date order."""
    from .index import open_index
    header, items = lists.read(path)
    fx = open_index(m, "fields")
    fx.update()
    rows = fx.rows()
    if lanes == "auto":
        lanes = "group" if any(it.get("group") for it in items) else "custodian"
    out = []
    for i, it in enumerate(items):
        loc = it["loc"] if isinstance(it["loc"], str) else locators.format(it["loc"])
        row = rows.get(locators.parse(loc)["doc"], {})
        f = it.get("fields") or {}
        when = parse_date(f.get("date")) or parse_date(row.get("date"))
        approx = bool(f.get("date")) and len(str(f["date"])) < 10           # known to the month or year only
        label = f.get("event") or f.get("label") or row.get("subject") or row.get("name") or loc[4:20]
        lane = {"group": it.get("group"), "custodian": row.get("custodian"), "kind": row.get("kind"),
                "from": (row.get("from") or [None])[0], "none": ""}[lanes]
        tip = " · ".join(str(x) for x in (f.get("date") or (row.get("date") or "")[:10], row.get("name"),
                                          row.get("custodian")) if x)
        if f.get("event") or f.get("label"):
            tip += "\n" + str(label)
        out.append({"loc": loc, "date": when, "label": str(label), "lane": lane if lane is not None else "(none)",
                    "kind": row.get("kind") or "", "tip": tip, "order": i, "approx": approx})
    out.sort(key=lambda e: (e["date"] is None, e["date"] or date.min, e["order"]))
    return {**header, "lanes": lanes}, out


def segments(dates: list[date], px: float) -> list[tuple[date, date]]:
    """Runs of dates with no gap wider than a screen or so; the axis breaks between them."""
    gap_px = 600
    segs = []
    for d in dates:
        if segs and (d - segs[-1][1]).days * px <= gap_px:
            segs[-1][1] = d
        else:
            segs.append([d, d])
    return [(a, b) for a, b in segs]


def seg_width(a: date, b: date, px: float) -> float:
    """A segment's span, plus room for an item at its last date."""
    return (b - a).days * px + CHIP_W + 8


def width_of(segs, px) -> float:
    return sum(seg_width(a, b, px) for a, b in segs) + BREAK_W * (len(segs) - 1)


def choose_scale(dates, scale: str) -> float:
    """The finest scale that fits, without cutting the axis into so many
    pieces that the distances between dates stop meaning anything."""
    if scale != "auto":
        return SCALES[scale]
    pieces = max(3, len(set(dates)) // 4)
    for name in ("day", "week", "month", "year"):
        segs = segments(dates, SCALES[name])
        if width_of(segs, SCALES[name]) <= MAX_WIDTH and len(segs) <= pieces:
            return SCALES[name]
    return SCALES["year"]


def _span(days: int) -> str:
    if days >= 730:
        return f"{days // 365} yr"
    if days >= 60:
        return f"{days // 30} mo"
    return f"{days} d"


def _ticks(a: date, b: date, px: float):
    """(date, label) ticks for a segment, at a spacing that suits the scale."""
    if px >= 60:
        step, fmt = "day", "%b %-d"
    elif px >= 8:
        step, fmt = "week", "%b %-d"
    elif px >= 1.5:
        step, fmt = "month", "%b %Y"
    else:
        step, fmt = "year", "%Y"
    d = a
    if step == "week":
        d = a - timedelta(days=a.weekday())
    elif step == "month":
        d = a.replace(day=1)
    elif step == "year":
        d = a.replace(month=1, day=1)
    yield a, a.strftime("%b %-d, %Y" if step in ("day", "week") else fmt)     # where the segment starts
    while d <= b:
        if d > a:
            yield d, d.strftime(fmt)
        if step == "day":
            d += timedelta(days=1)
        elif step == "week":
            d += timedelta(days=7)
        elif step == "month":
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            d = d.replace(year=d.year + 1)


def timeline_html(m, path, lanes="auto", scale="auto", title=None) -> tuple[dict, list[str], dict]:
    """(files, sequence, summary): the pane's HTML, its items' locators in
    date order, and what was shown."""
    header, es = entries(m, path, lanes)
    truncated = len(es) > MAX_ITEMS
    es = es[:MAX_ITEMS]
    dated = [e for e in es if e["date"]]
    undated = [e for e in es if not e["date"]]
    px = choose_scale([e["date"] for e in dated], scale) if dated else SCALES["month"]
    segs = segments([e["date"] for e in dated], px)

    # x for a date: its segment's start plus days into it, with breaks between segments
    starts, x0 = [], LABEL_W
    for a, b in segs:
        starts.append(x0)
        x0 += seg_width(a, b, px) + BREAK_W
    width = x0 - BREAK_W + 20 if segs else LABEL_W + 200

    def x_of(d):
        for (a, b), s in zip(segs, starts):
            if a <= d <= b:
                return s + (d - a).days * px
        raise ValueError(d)

    # lanes in order of first appearance; within a lane, items stack into rows where they'd overlap
    lane_names = list(dict.fromkeys(e["lane"] for e in dated))
    rows_end = {ln: [] for ln in lane_names}
    for e in dated:
        e["x"] = x_of(e["date"])
        ends = rows_end[e["lane"]]
        r = next((i for i, end in enumerate(ends) if end <= e["x"]), len(ends))
        if r == len(ends):
            ends.append(0)
        ends[r] = e["x"] + CHIP_W + 4
        e["row"] = r
    lane_top, y = {}, 0
    for ln in lane_names:
        lane_top[ln] = y
        y += max(1, len(rows_end[ln])) * ROW_H + 10
    height = y

    out = [f"<h2>{html.escape(title or header.get('title') or 'Timeline')}</h2>"]
    span = f"{dated[0]['date']} – {dated[-1]['date']}" if dated else "no dates"
    lanes_note = "" if header["lanes"] == "none" else f" · lanes: {html.escape(header['lanes'])}"
    out.append(f'<p class="adhoc-query">{len(es)} items · {span}{lanes_note}'
               + (f" · {len(undated)} undated" if undated else "")
               + (f" · only the first {MAX_ITEMS}" if truncated else "") + "</p>")
    out.append(f'<div class="adhoc-tl" style="width:{width:.0f}px">')
    out.append(f'<div class="adhoc-tl-axis" style="width:{width:.0f}px;height:{AXIS_H}px">')
    last = -1e9
    for (a, b), s in zip(segs, starts):
        for d, text in _ticks(a, b, px):
            x = s + (d - a).days * px
            if x - last >= 70:
                out.append(f'<span class="adhoc-tl-tick" style="left:{x:.0f}px">{html.escape(text)}</span>')
                last = x
    out.append("</div>")
    out.append(f'<div class="adhoc-tl-body" style="height:{height}px">')
    for ln in lane_names:
        h = max(1, len(rows_end[ln])) * ROW_H + 10
        label = "" if header["lanes"] == "none" else html.escape(str(ln))
        out.append(f'<div class="adhoc-tl-lane" style="top:{lane_top[ln]}px;height:{h}px;width:{width:.0f}px">'
                   f'<span class="adhoc-tl-lanelabel" title="{label}">{label}</span></div>')
    for (a, b), (c, d), s in zip(segs, segs[1:], starts[1:]):
        out.append(f'<div class="adhoc-tl-break" style="left:{s - BREAK_W + 4:.0f}px;height:{height}px">'
                   f'<span>{_span((c - b).days)}</span></div>')
    for e in dated:
        top = lane_top[e["lane"]] + 5 + e["row"] * ROW_H
        cls = f'adhoc-tl-item kind-{html.escape(e["kind"])}' + (" approx" if e["approx"] else "")
        out.append(f'<div class="{cls}" data-locator="{html.escape(e["loc"])}" '
                   f'title="{html.escape(e["tip"])}" style="left:{e["x"]:.0f}px;top:{top}px">{html.escape(e["label"])}</div>')
    out.append("</div></div>")
    if undated:
        out.append(f'<h3 class="adhoc-tl-undated-h">Undated ({len(undated)})</h3><div class="adhoc-tl-undated">')
        for e in undated:
            out.append(f'<span class="adhoc-tl-item kind-{html.escape(e["kind"])}" data-locator="{html.escape(e["loc"])}" '
                       f'title="{html.escape(e["tip"])}">{html.escape(e["label"])}</span>')
        out.append("</div>")
    files = {"index.html": base64.b64encode("\n".join(out).encode()).decode()}
    summary = {"items": len(es), "truncated": truncated, "dated": len(dated), "undated": len(undated), "lanes": header["lanes"],
               "lane_names": lane_names, "segments": [[str(a), str(b)] for a, b in segs],
               "px_per_day": px, "width": round(width)}
    return files, [e["loc"] for e in es], summary
