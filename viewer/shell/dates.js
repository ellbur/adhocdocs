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
  if ("weeks" in rel) return dayString(a - weekday + 7 * rel.weeks);
  const year = +anchor.slice(0, 4), month = +anchor.slice(5, 7);
  if ("months" in rel) {
    const n = year * 12 + month - 1 + rel.months;
    return `${String(Math.floor(n / 12)).padStart(4, "0")}-${String(n % 12 + 1).padStart(2, "0")}`;
  }
  if ("years" in rel) return String(year + rel.years).padStart(4, "0");
  if ("bare" in rel) {
    let past, future;
    if ("weekday" in rel) {
      past = a - ((((weekday - rel.weekday) % 7) + 7) % 7 || 7);
      future = a + ((((rel.weekday - weekday) % 7) + 7) % 7 || 7);
    } else {
      past = monthday(year, month, a, rel.monthday, -1);
      future = monthday(year, month, a, rel.monthday, 1);
    }
    let pick = rel.bare === "past" ? past : rel.bare === "future" ? future : null;
    if (rel.bare !== "past" && rel.bare !== "future")      // near: the nearer one, the coming one on a tie
      pick = past !== null && future !== null && a - past < future - a ? past : future ?? past;
    return pick === null || pick === undefined ? null : dayString(pick);
  }
  return dayString(a - weekday + rel.weekday + 7 * rel.dir);
}
// the nearest day `day` of a month strictly before (step -1) or after (step 1) day number a
function monthday(year, month, a, day, step) {
  const n = year * 12 + month - 1;
  for (let i = 0; Math.abs(i) < 13; i += step) {
    const d = validDay(Math.floor((n + i) / 12), ((n + i) % 12) + 1, day);
    if (d !== null && (d - a) * step > 0) return d;
  }
  return null;
}
// the last day of a range ("March 2–6") or a week ("last week"), placed like resolveDate; null for one date
function resolveUntil(x, anchor) {
  const start = resolveDate(x, anchor);
  if (!start) return null;
  if (x.kind === "relative" && "weeks" in (x.rel || {})) return dayString(dayNumber(start) + 6);
  if (!x.until) return null;
  if (!x.until.startsWith("--")) return x.until;
  let y = +start.slice(0, 4);
  const mon = +x.until.slice(2, 4), day = +x.until.slice(5, 7);
  if (mon * 100 + day < +start.slice(5, 7) * 100 + +start.slice(8, 10)) y += 1;
  const d = validDay(y, mon, day) ?? validDay(y, mon, 28);
  return d === null ? null : dayString(d);
}

if (typeof module !== "undefined") module.exports = { resolveDate, resolveUntil, dayNumber, dayString };
