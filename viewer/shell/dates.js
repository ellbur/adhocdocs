// Dates for the date cards (DATES.md): placing a date mention ("March 9",
// "last Friday") by the document's own date, and day arithmetic. The same
// rules as resolve() in src/adhocdocs/dates.py; tests/date_cases.json holds
// both to them.
"use strict";

// "YYYY-MM-DD…" -> days since 1970-01-01, or null
function dayNumber(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || "");
  if (!m) return null;
  const t = Date.UTC(+m[1], +m[2] - 1, +m[3]);
  const d = new Date(t);
  if (d.getUTCMonth() !== +m[2] - 1) return null;
  return Math.round(t / 86400000);
}
function dayString(n) { return new Date(n * 86400000).toISOString().slice(0, 10); }
function validDay(y, mon, day) {
  const d = new Date(Date.UTC(y, mon - 1, day));
  return d.getUTCMonth() === mon - 1 ? Math.round(d.getTime() / 86400000) : null;
}

// A mention's date (YYYY-MM-DD or YYYY-MM), with "noyear" and "relative"
// mentions placed by anchor, the document's date; null if they can't be.
function resolveDate(x, anchor) {
  if (x.kind === "absolute" || x.kind === "month") return x.date;
  const a = dayNumber(anchor);
  if (a === null) return null;
  if (x.kind === "noyear") {
    const year = +anchor.slice(0, 4), mon = +x.date.slice(2, 4), day = +x.date.slice(5, 7);
    let d = validDay(year, mon, day) ?? validDay(year, mon, 28);
    if (d - a > 62) d = validDay(year - 1, mon, day) ?? d;
    return dayString(d);
  }
  const rel = x.rel || {};
  if ("days" in rel) return dayString(a + rel.days);
  const weekday = (new Date(a * 86400000).getUTCDay() + 6) % 7;     // Monday is 0
  return dayString(a - weekday + rel.weekday + 7 * rel.dir);
}

if (typeof module !== "undefined") module.exports = { resolveDate, dayNumber, dayString };
