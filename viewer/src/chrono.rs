//! Dates, for the date strip and the date cards (DATES.md): a document's own
//! date and where it came from, and what is around a date in the matter.
//!
//! A card has two parts. The context needs no judgment and is always there:
//! the document's place in its email thread, how many documents are dated
//! near the date, and, for a date in a quoted email's header, that email
//! (or that the matter doesn't have it).
//! The events are what someone already judged worth noting, nearest first on
//! either side: chronology entries (list items with an `event`), events in
//! the entity list with a `date`, documents in key sets, documents cited in
//! pages, and documents tagged with a tag named like "hot" or "key".
//! Nothing here decides what is important by itself.

use crate::matter::Matter;
use crate::overview::walk;
use crate::protocol::{annotation_state, parse_loc};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::time::{Duration, Instant};

/// Events shown on each side of the date.
const EACH_SIDE: usize = 3;
/// "Near": documents within this many days of a day.
const NEAR_DAYS: i64 = 3;
/// Documents kept for stepping through the near ones.
const NEAR_MAX: usize = 200;
/// Tags that mark a document as mattering, by name.
const SALIENT_TAGS: &[&str] = &["hot", "key", "important", "critical", "significant", "smoking"];
/// Work product is read again after this long.
const EVENTS_TTL: Duration = Duration::from_secs(3);

/// Event sources, strongest first: when one document is several, the
/// strongest names it.
const RANK_CHRONOLOGY: u8 = 0;
const RANK_ENTITY: u8 = 1;
const RANK_KEY_SET: u8 = 2;
const RANK_PAGE: u8 = 3;
const RANK_TAG: u8 = 4;

#[derive(Clone)]
struct Dated {
    doc: String,
    date: String,
    source: &'static str,
}

#[derive(Clone)]
struct Event {
    date: String,
    label: String,
    rank: u8,
    sources: Vec<String>,
    doc: Option<String>,
    loc: Value,
    entity: Option<Value>,
}

#[derive(Default)]
pub struct Chrono {
    docs: Option<(Vec<u64>, Vec<Dated>, HashMap<String, usize>)>,
    events: Option<(Instant, Vec<Event>)>,
}

/// `{date, source}` for a document: an email's sent date, an attachment's
/// email's, else its metadata's (with `date_source` when extraction recorded it).
pub fn doc_date(m: &Matter, doc: &str) -> Option<(String, &'static str)> {
    let meta = m.metadata(doc);
    let date = str_of(&meta["date"]);
    if meta["kind"] == "email" {
        if let Some(d) = date.clone() { return Some((d, "sent")); }
    }
    if let Some(p) = m.state.parent.get(doc) {
        let pm = m.metadata(p);
        if pm["kind"] == "email" {
            if let Some(d) = str_of(&pm["date"]) { return Some((d, "attached")); }
        }
    }
    let source = match meta["date_source"].as_str() {
        Some("created") => "created",
        Some("modified") => "modified",
        _ => "metadata",
    };
    date.map(|d| (d, source))
}

/// The date strip's facts: the date the document reflects, and the
/// metadata's created and modified dates when it has them.
pub fn strip(m: &Matter, doc: &str) -> Value {
    let meta = m.metadata(doc);
    let parent = m.state.parent.get(doc).map(|p| json!({"doc": p, "name": label(m, p)}));
    match doc_date(m, doc) {
        Some((date, source)) => json!({"date": date, "source": source, "created": meta["created"], "modified": meta["modified"],
                                       "parent": if source == "attached" { parent.unwrap_or(Value::Null) } else { Value::Null }}),
        None => json!({"date": null, "created": meta["created"], "modified": meta["modified"]}),
    }
}

fn str_of(v: &Value) -> Option<String> {
    v.as_str().filter(|s| !s.is_empty()).map(String::from)
}

/// A short name for a document: an email's subject and sender, else its title or file name.
fn label(m: &Matter, doc: &str) -> String {
    let meta = m.metadata(doc);
    if meta["kind"] == "email" {
        let subject = str_of(&meta["subject"]).unwrap_or_else(|| "(no subject)".into());
        let from = meta["from_names"][0].as_str().or(meta["from"][0].as_str()).map(String::from);
        return match from { Some(f) => format!("{} — {}", subject, f), None => subject };
    }
    str_of(&meta["title"]).or_else(|| str_of(&meta["name"])).unwrap_or_else(|| doc.chars().take(14).collect())
}

/// Days since 1970-01-01 for `YYYY-MM-DD…` (Howard Hinnant's days_from_civil).
fn day_number(s: &str) -> Option<i64> {
    let y: i64 = s.get(0..4)?.parse().ok()?;
    let mo: i64 = s.get(5..7)?.parse().ok()?;
    let d: i64 = s.get(8..10)?.parse().ok()?;
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) { return None; }
    let y = if mo <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146097 + doe - 719468)
}

/// A date as a sortable key: `2026-03` and `2026` sort before that month's or year's days.
fn key(date: &str) -> String {
    let d: String = date.chars().take(10).collect();
    match d.len() {
        4 => d + "-00-00",
        7 => d + "-00",
        _ => d,
    }
}

/// Whether two dates of any precision overlap: "2026-03" holds "2026-03-09".
fn same(a: &str, b: &str) -> bool {
    let (a, b): (String, String) = (a.chars().take(10).collect(), b.chars().take(10).collect());
    a.starts_with(&b) || b.starts_with(&a)
}

fn doc_of(loc: &Value) -> Option<String> {
    match loc {
        Value::String(s) => parse_loc(s).map(|l| l.doc),
        Value::Object(o) => o.get("doc").and_then(|d| d.as_str()).map(String::from),
        _ => None,
    }
}

impl Chrono {
    fn docs<'a>(&'a mut self, m: &Matter) -> &'a (Vec<u64>, Vec<Dated>, HashMap<String, usize>) {
        let gen = m.generation();
        if self.docs.as_ref().map(|(g, _, _)| g != &gen).unwrap_or(true) {
            let mut v: Vec<Dated> = m.state.order.iter().filter(|d| m.live(d))
                .filter_map(|d| doc_date(m, d).map(|(date, source)| Dated { doc: d.clone(), date, source })).collect();
            v.sort_by(|a, b| a.date.cmp(&b.date).then(a.doc.cmp(&b.doc)));
            let at = v.iter().enumerate().map(|(i, x)| (x.doc.clone(), i)).collect();
            self.docs = Some((gen, v, at));
        }
        self.docs.as_ref().unwrap()
    }

    /// What is around `date` (`YYYY-MM-DD`, `YYYY-MM` or `YYYY`), for a card
    /// shown in document `doc`, optionally for a quoted email from `who`.
    pub fn around(&mut self, m: &Matter, date: &str, doc: Option<&str>, who: Option<&str>) -> Value {
        let date: String = date.chars().take(10).collect();
        let here = doc.and_then(|d| doc_date(m, d)).map(|(d, s)| json!({"date": d, "source": s}));
        let item = |m: &Matter, d: &str, date: &str| json!({"doc": d, "date": date, "label": label(m, d), "kind": m.metadata(d)["kind"]});

        // the document's place in its email thread
        let threads: Value = fs::read(m.root.join("workspace/.adhoc/indexes/dates/threads.json")).ok()
            .and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
        let thread = doc.and_then(|d| {
            let t = threads["of"][d][0].as_str()?;
            let i = threads["of"][d][1].as_u64()? as usize;
            let msgs = threads["threads"][t].as_array()?;
            let at = |j: usize| msgs.get(j).and_then(|x| x.as_str()).map(|x| item(m, x, &doc_date(m, x).map(|d| d.0).unwrap_or_default()));
            Some(json!({"size": msgs.len(), "position": i + 1, "prev": i.checked_sub(1).and_then(at), "next": at(i + 1)}))
        });

        // documents dated near it (attachments go with their email)
        let (_, all, _) = self.docs(m);
        let center = day_number(&date);
        let near: Vec<&Dated> = all.iter().filter(|x| x.source != "attached").filter(|x| match center {
            Some(c) => day_number(&x.date).map(|d| (d - c).abs() <= NEAR_DAYS).unwrap_or(false),
            None => x.date.starts_with(&date),
        }).collect();
        let same_day = near.iter().filter(|x| x.date.starts_with(&date)).count();
        let near_json = json!({"days": if center.is_some() { NEAR_DAYS } else { 0 }, "count": near.len(), "same_day": same_day,
                               "docs": near.iter().take(NEAR_MAX).map(|x| x.doc.clone()).collect::<Vec<_>>()});

        // for a date in a quoted email's header: that email, if the matter has it
        let quoted: Vec<Value> = match who.map(|w| w.to_lowercase()).filter(|w| w.len() > 1 && center.is_some()) {
            Some(w) => all.iter().filter(|x| x.date.starts_with(&date) && Some(x.doc.as_str()) != doc && x.source == "sent")
                .filter(|x| {
                    let meta = m.metadata(&x.doc);
                    let names = [&meta["from_names"], &meta["from"]].iter()
                        .flat_map(|v| v.as_array().cloned().unwrap_or_default()).filter_map(|v| v.as_str().map(|s| s.to_lowercase()))
                        .collect::<Vec<_>>();
                    names.iter().any(|n| n.contains(&w) || w.contains(n.as_str()) || surname_match(n, &w))
                }).take(5).map(|x| item(m, &x.doc, &x.date)).collect(),
            None => vec![],
        };
        // a quoted email the matter doesn't have is worth knowing about
        let quoted_missing = who.is_some() && center.is_some() && quoted.is_empty();

        // what someone already judged worth noting, nearest on either side
        let events = self.events(m);
        let total = events.len();
        let as_json = |e: &Event| json!({"date": e.date, "label": e.label, "sources": e.sources, "doc": e.doc, "loc": e.loc,
                                         "entity": e.entity, "here": e.doc.as_deref() == doc && doc.is_some()});
        let k = key(&date);
        let mut before: Vec<&Event> = events.iter().filter(|e| !same(&e.date, &date) && key(&e.date) < k).collect();
        before.sort_by(|a, b| key(&b.date).cmp(&key(&a.date)).then(a.rank.cmp(&b.rank)));
        let mut after: Vec<&Event> = events.iter().filter(|e| !same(&e.date, &date) && key(&e.date) > k).collect();
        after.sort_by(|a, b| key(&a.date).cmp(&key(&b.date)).then(a.rank.cmp(&b.rank)));
        let mut on: Vec<&Event> = events.iter().filter(|e| same(&e.date, &date)).collect();
        on.sort_by(|a, b| a.rank.cmp(&b.rank).then(a.date.cmp(&b.date)));
        let (nb, na) = (before.len(), after.len());
        json!({
            "date": date, "here": here, "thread": thread, "near": near_json, "quoted": quoted, "quoted_missing": quoted_missing,
            "before": before.into_iter().take(EACH_SIDE).rev().map(as_json).collect::<Vec<_>>(),
            "on": on.into_iter().take(EACH_SIDE).map(as_json).collect::<Vec<_>>(),
            "after": after.into_iter().take(EACH_SIDE).map(as_json).collect::<Vec<_>>(),
            "more_before": nb.saturating_sub(EACH_SIDE), "more_after": na.saturating_sub(EACH_SIDE),
            "events": total,
        })
    }

    /// Every event, from the work product and the annotation log, one per
    /// document (its sources joined) plus entity events with no document.
    fn events(&mut self, m: &Matter) -> Vec<Event> {
        if let Some((at, ev)) = &self.events {
            if at.elapsed() < EVENTS_TTL && self.docs.as_ref().map(|(g, _, _)| g == &m.generation()).unwrap_or(false) {
                return ev.clone();
            }
        }
        let dates: HashMap<String, String> = self.docs(m).1.iter().map(|x| (x.doc.clone(), x.date.clone())).collect();
        let mut by_doc: HashMap<String, Event> = HashMap::new();
        let mut loose: Vec<Event> = vec![];
        let add = |e: Event, by_doc: &mut HashMap<String, Event>, loose: &mut Vec<Event>| {
            let Some(d) = e.doc.clone() else { loose.push(e); return };
            match by_doc.get_mut(&d) {
                Some(x) => {
                    for s in &e.sources { if !x.sources.contains(s) { x.sources.push(s.clone()); } }
                    if e.rank < x.rank {
                        let sources = std::mem::take(&mut x.sources);
                        *x = Event { sources, ..e };
                    }
                }
                None => { by_doc.insert(d, e); }
            }
        };
        let resolve = |s: Option<String>| s.and_then(|d| m.resolve_doc(&d)).filter(|d| m.live(d));

        // chronologies, key sets and pages in the workspace
        let ws = m.root.join("workspace");
        let mut files = vec![];
        walk(&ws, &ws, 0, &mut files);
        files.sort_by(|a, b| b.1.cmp(&a.1));
        for (rel, _, path) in files {
            let text = fs::read_to_string(&path).unwrap_or_default();
            if rel.ends_with(".list.jsonl") {
                let mut lines = text.lines().filter(|l| !l.trim().is_empty());
                let header: Value = lines.next().and_then(|l| serde_json::from_str(l).ok()).unwrap_or(Value::Null);
                if header["type"] != "list" { continue; }
                let title = str_of(&header["title"]).unwrap_or_else(|| rel.clone());
                let key_set = header.get("sources").is_some();
                for l in lines {
                    let Ok(it) = serde_json::from_str::<Value>(l) else { continue };
                    let doc = resolve(doc_of(&it["loc"]));
                    let f = &it["fields"];
                    if let Some(ev) = str_of(&f["event"]) {
                        let date = str_of(&f["date"]).filter(|d| d.len() >= 4 && d[..4].bytes().all(|b| b.is_ascii_digit()))
                            .or_else(|| doc.as_ref().and_then(|d| dates.get(d).cloned()));
                        let Some(date) = date else { continue };
                        add(Event { date: date.chars().take(10).collect(), label: ev, rank: RANK_CHRONOLOGY, sources: vec![format!("in {}", title)],
                                    doc, loc: it["loc"].clone(), entity: None }, &mut by_doc, &mut loose);
                    } else if key_set {
                        let Some(d) = doc else { continue };
                        let Some(date) = dates.get(&d) else { continue };
                        add(Event { date: date.chars().take(10).collect(), label: label(m, &d), rank: RANK_KEY_SET,
                                    sources: vec![format!("in key set {}", title)], doc: Some(d), loc: it["loc"].clone(), entity: None },
                            &mut by_doc, &mut loose);
                    }
                }
            } else if rel.ends_with(".md") {
                let title = text.lines().find_map(|l| l.strip_prefix("# ")).map(|t| t.trim().to_string()).unwrap_or_else(|| rel.clone());
                let mut inside = false;
                for l in text.lines() {
                    let t = l.trim();
                    if t.starts_with("```") {
                        inside = !inside && t.trim_start_matches('`').trim() == "refs";
                        continue;
                    }
                    if !inside || !t.starts_with('{') { continue; }
                    let Ok(r) = serde_json::from_str::<Value>(t) else { continue };
                    let Some(d) = resolve(str_of(&r["doc"])) else { continue };
                    let Some(date) = dates.get(&d) else { continue };
                    add(Event { date: date.chars().take(10).collect(), label: label(m, &d), rank: RANK_PAGE,
                                sources: vec![format!("cited in {}", title)], doc: Some(d.clone()), loc: json!(format!("doc:{}", d)), entity: None },
                        &mut by_doc, &mut loose);
                }
            }
        }

        // the entity list's events, and documents tagged as mattering
        let a = annotation_state(&m.root);
        for e in a["entities"].as_array().into_iter().flatten() {
            let Some(date) = str_of(&e["date"]) else { continue };
            let see = e["see"][0].clone();
            let doc = resolve(see.get("doc").and_then(|d| d.as_str()).map(String::from));
            add(Event { date, label: str_of(&e["name"]).unwrap_or_default(), rank: RANK_ENTITY, sources: vec!["in the entity list".into()],
                        doc, loc: see, entity: Some(json!({"entity": e["entity"], "summary": e["summary"]})) }, &mut by_doc, &mut loose);
        }
        for t in a["tags"].as_array().into_iter().flatten() {
            let tag = t["tag"].as_str().unwrap_or("");
            let lower = tag.to_lowercase();
            if !SALIENT_TAGS.iter().any(|s| lower.split(|c: char| !c.is_alphanumeric()).any(|w| w == *s)) { continue; }
            let Some(d) = resolve(str_of(&t["target"]["doc"])) else { continue };
            let Some(date) = dates.get(&d) else { continue };
            add(Event { date: date.chars().take(10).collect(), label: label(m, &d), rank: RANK_TAG, sources: vec![format!("tagged {}", tag)],
                        doc: Some(d.clone()), loc: json!(format!("doc:{}", d)), entity: None }, &mut by_doc, &mut loose);
        }
        let mut all: Vec<Event> = by_doc.into_values().chain(loose).collect();
        all.sort_by(|a, b| key(&a.date).cmp(&key(&b.date)).then(a.rank.cmp(&b.rank)));
        self.events = Some((Instant::now(), all.clone()));
        all
    }
}

/// "R. Voss" in a header and "Raymond Voss" (or raymond.voss@…) as the
/// sender: the header's last word is one of the sender's words.
fn surname_match(name: &str, who: &str) -> bool {
    let words = |s: &str| s.split(|c: char| !c.is_alphanumeric()).filter(|w| w.chars().count() > 1).map(String::from).collect::<Vec<_>>();
    // "Ames, Rita": the surname comes first
    let who_last = if who.contains(',') { words(who).into_iter().next() } else { words(who).into_iter().last() };
    match who_last {
        Some(w) => words(name).contains(&w),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn days_and_keys() {
        assert_eq!(day_number("1970-01-01"), Some(0));
        assert_eq!(day_number("2026-03-09").unwrap() - day_number("2026-02-27").unwrap(), 10);
        assert!(key("2026-03") < key("2026-03-01"));
        assert!(same("2026-03", "2026-03-09") && !same("2026-03-08", "2026-03-09"));
        assert!(surname_match("raymond voss", "r. voss"));
        assert!(surname_match("dana.okafor@harrowgatefastener.example", "dana okafor"));
        assert!(surname_match("rita ames", "ames, rita"));
        assert!(!surname_match("rita ames", "dana okafor"));
    }
}
