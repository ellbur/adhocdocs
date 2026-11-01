//! What the matter holds, for the landing page: the documents in summary,
//! the people in their email headers, the entity list and its suggestions,
//! and the work product in the workspace (pages, lists, productions,
//! exhibit sets), tags and notes.
//!
//! It shows state; it starts nothing. Work begins by asking Claude.

use crate::matter::Matter;
use crate::protocol::annotation_state;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::path::Path;

/// Rows shown per section; the counts are always complete.
const RECENT: usize = 8;
const TOP: usize = 10;
/// How far into the workspace to look for pages and lists.
const MAX_DEPTH: usize = 6;
const MAX_FILES: usize = 5000;

/// The document summary depends only on the store and workspace logs, so it
/// is kept until either grows. The rest is cheap and read every time.
#[derive(Default)]
pub struct Overview {
    docs: Option<(Vec<u64>, Value, Value)>,
}

impl Overview {
    pub fn get(&mut self, m: &Matter) -> Value {
        let gen = m.generation();
        if self.docs.as_ref().map(|(g, _, _)| g != &gen).unwrap_or(true) {
            let (docs, people) = documents(m);
            self.docs = Some((gen, docs, people));
        }
        let (_, docs, people) = self.docs.clone().unwrap();
        let ws = m.root.join("workspace");
        let (pages, lists) = workspace_files(&ws);
        json!({
            "matter": m.root.file_name().map(|n| n.to_string_lossy().to_string()),
            "documents": docs,
            "people": people,
            "pages": pages,
            "lists": lists,
            "productions": sets(&ws.join("productions"), "production.jsonl"),
            "exhibits": sets(&ws.join("exhibits"), "exhibits.jsonl"),
            "annotations": annotations(&m.root),
            "entities": entities(&m.root),
        })
    }
}

fn str_of(v: &Value) -> Option<String> {
    v.as_str().filter(|s| !s.is_empty()).map(String::from)
}

fn counted(map: HashMap<String, usize>) -> Vec<Value> {
    let mut v: Vec<(String, usize)> = map.into_iter().collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    v.into_iter().map(|(k, n)| json!({"name": k, "n": n})).collect()
}

/// Counts by kind, custodian and collection, the date range, the latest
/// additions; and from email headers, who wrote and received the most.
fn documents(m: &Matter) -> (Value, Value) {
    let s = &m.state;
    let live: Vec<&String> = s.order.iter().filter(|d| m.live(d)).collect();
    let (mut kinds, mut custodians, mut collections) = (HashMap::new(), HashMap::new(), HashMap::new());
    let (mut first, mut last): (Option<String>, Option<String>) = (None, None);
    let (mut attachments, mut no_text) = (0, 0);
    let mut people: HashMap<String, (usize, HashMap<String, usize>)> = HashMap::new();
    let mut rows = vec![];
    for d in &live {
        let meta = m.metadata(d);
        let obj = m.object(d).unwrap_or(Value::Null);
        let kind = str_of(&meta["kind"]).unwrap_or_else(|| "other".into());
        *kinds.entry(kind.clone()).or_insert(0) += 1;
        let custodian = str_of(&obj["custodian"]).map(|c| c.trim_start_matches("cust:").to_string());
        *custodians.entry(custodian.clone().unwrap_or_else(|| "(none)".into())).or_insert(0) += 1;
        if let Some(c) = str_of(&obj["source"]["collection"]) {
            *collections.entry(c).or_insert(0) += 1;
        }
        let child = s.parent.contains_key(*d);
        if child {
            attachments += 1;
        }
        let content = &s.documents[*d];
        if s.layers.get(content).map(|l| l.is_empty()).unwrap_or(true) {
            no_text += 1;
        }
        let date = str_of(&meta["date"]).map(|x| x.chars().take(10).collect::<String>());
        if let Some(day) = &date {
            if first.as_ref().map(|f| day < f).unwrap_or(true) {
                first = Some(day.clone());
            }
            if last.as_ref().map(|l| day > l).unwrap_or(true) {
                last = Some(day.clone());
            }
        }
        if kind == "email" {
            let mut seen = std::collections::HashSet::new();
            for field in ["from", "to", "cc"] {
                let addrs = meta[field].as_array().cloned().unwrap_or_default();
                let names = meta[format!("{}_names", field)].as_array().cloned().unwrap_or_default();
                for (i, a) in addrs.iter().enumerate() {
                    let Some(a) = a.as_str().map(|a| a.to_lowercase()) else { continue };
                    let e = people.entry(a.clone()).or_default();
                    if seen.insert(a.clone()) {
                        e.0 += 1;
                    }
                    if let Some(n) = names.get(i).and_then(|n| n.as_str()).filter(|n| !n.contains('@')) {
                        *e.1.entry(n.to_string()).or_insert(0) += 1;
                    }
                }
            }
        }
        if !child {
            rows.push(json!({"doc": d, "name": meta["name"], "kind": kind, "date": date,
                             "subject": meta["subject"], "custodian": custodian}));
        }
    }
    let recent: Vec<Value> = rows.into_iter().rev().take(RECENT).collect();
    let docs = json!({
        "total": live.len(), "attachments": attachments, "no_text": no_text,
        "withdrawn": s.withdrawn.iter().filter(|d| s.documents.contains_key(*d) && !s.superseded.contains_key(*d)).count(),
        "kinds": counted(kinds), "custodians": counted(custodians), "collections": counted(collections),
        "first_date": first, "last_date": last, "recent": recent,
    });
    let total = people.len();
    let mut ppl: Vec<(String, usize, Option<String>)> = people.into_iter().map(|(a, (n, names))| {
        let name = names.into_iter().max_by(|x, y| x.1.cmp(&y.1).then(y.0.cmp(&x.0))).map(|(n, _)| n);
        (a, n, name)
    }).collect();
    ppl.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    let top: Vec<Value> = ppl.into_iter().take(TOP).map(|(a, n, name)| json!({"address": a, "name": name, "emails": n})).collect();
    (docs, json!({"total": total, "top": top}))
}

/// Literate pages (`*.md`) and list files (`*.list.jsonl`) in the
/// workspace, newest first, skipping hidden directories.
fn workspace_files(ws: &Path) -> (Value, Value) {
    let mut found = vec![];
    walk(ws, ws, 0, &mut found);
    found.sort_by(|a, b| b.1.cmp(&a.1));
    let (mut pages, mut lists) = (vec![], vec![]);
    for (rel, modified, path) in found {
        let name = rel.rsplit('/').next().unwrap_or(&rel).to_string();
        if name.ends_with(".list.jsonl") {
            let text = fs::read_to_string(&path).unwrap_or_default();
            let mut lines = text.lines().filter(|l| !l.trim().is_empty());
            let header: Value = lines.next().and_then(|l| serde_json::from_str(l).ok()).unwrap_or(Value::Null);
            let items = lines.count();
            lists.push(json!({"file": rel, "title": header["title"], "question": header["question"],
                              "query": header["query"], "key_set": header.get("sources").is_some(), "items": items,
                              "modified": modified}));
        } else if name.ends_with(".md") {
            let text = fs::read_to_string(&path).unwrap_or_default();
            let title = text.lines().find_map(|l| l.strip_prefix("# ")).map(|t| t.trim().to_string());
            let (mut refs, mut inside) = (0, false);
            for l in text.lines() {
                let t = l.trim();
                if t.starts_with("```") {
                    inside = !inside && t.trim_start_matches('`').trim() == "refs";
                } else if inside && t.starts_with('{') {
                    refs += 1;
                }
            }
            pages.push(json!({"file": rel, "title": title, "refs": refs, "modified": modified}));
        }
    }
    (json!(pages), json!(lists))
}

fn walk(root: &Path, dir: &Path, depth: usize, out: &mut Vec<(String, u64, std::path::PathBuf)>) {
    if depth > MAX_DEPTH || out.len() >= MAX_FILES {
        return;
    }
    let Ok(entries) = fs::read_dir(dir) else { return };
    for e in entries.flatten() {
        let name = e.file_name().to_string_lossy().to_string();
        if name.starts_with('.') {
            continue;
        }
        let Ok(ft) = e.file_type() else { continue };
        let path = e.path();
        if ft.is_dir() {
            walk(root, &path, depth + 1, out);
        } else if ft.is_file() && (name.ends_with(".md") || name.ends_with(".list.jsonl")) {
            let modified = e.metadata().ok().and_then(|m| m.modified().ok())
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d| d.as_secs()).unwrap_or(0);
            let rel = path.strip_prefix(root).unwrap_or(&path).to_string_lossy().replace('\\', "/");
            out.push((rel, modified, path));
        }
    }
}

/// Productions or exhibit sets: each directory's header line names it, and
/// each further line is one document.
fn sets(dir: &Path, file: &str) -> Value {
    let Ok(entries) = fs::read_dir(dir) else { return json!([]) };
    let mut dirs: Vec<_> = entries.flatten().map(|e| e.path()).collect();
    dirs.sort();
    let out: Vec<Value> = dirs.iter().filter_map(|d| {
        let text = fs::read_to_string(d.join(file)).ok()?;
        let mut lines = text.lines().filter(|l| !l.trim().is_empty());
        let head: Value = serde_json::from_str(lines.next()?).ok()?;
        let n = lines.count();
        Some(json!({"name": head["name"].as_str().map(String::from)
                        .unwrap_or_else(|| d.file_name().unwrap_or_default().to_string_lossy().to_string()),
                    "documents": n}))
    }).collect();
    json!(out)
}

/// Each tag with how many places it is on (and by whom), and the notes.
fn annotations(root: &Path) -> Value {
    let a = annotation_state(root);
    let mut counts: BTreeMap<String, (usize, usize)> = BTreeMap::new();
    for t in a["tags"].as_array().into_iter().flatten() {
        let e = counts.entry(t["tag"].as_str().unwrap_or("").to_string()).or_default();
        if t["by"].as_str().map(|b| b.starts_with("user:")).unwrap_or(false) { e.0 += 1 } else { e.1 += 1 }
    }
    let tags: Vec<Value> = a["definitions"].as_array().into_iter().flatten().map(|d| {
        let (user, claude) = counts.get(d["tag"].as_str().unwrap_or("")).copied().unwrap_or_default();
        json!({"tag": d["tag"], "color": d["color"], "description": d["description"], "hotkey": d["hotkey"],
               "by_user": user, "by_claude": claude})
    }).collect();
    let notes = a["notes"].as_array().cloned().unwrap_or_default();
    let threads = notes.iter().filter(|n| n["parent"].is_null()).count();
    let mut recent: Vec<Value> = notes.iter().rev().take(5).map(|n| json!({"note": n["note"], "body": n["body"],
        "by": n["by"], "at": n["at"], "doc": n["target"]["doc"]})).collect();
    recent.retain(|n| !n["doc"].is_null());
    json!({"tags": tags, "notes": notes.len(), "threads": threads, "recent_notes": recent})
}

/// The entity list (people, organizations, terms the user or Claude added),
/// and the suggestions the entities index found that aren't on it yet.
fn entities(root: &Path) -> Value {
    let a = annotation_state(root);
    let known = a["entities"].as_array().cloned().unwrap_or_default();
    let mut aliases = std::collections::HashSet::new();
    let mut kinds: HashMap<String, usize> = HashMap::new();
    for e in &known {
        *kinds.entry(e["kind"].as_str().unwrap_or("other").to_string()).or_insert(0) += 1;
        for x in e["aliases"].as_array().into_iter().flatten() {
            if let Some(x) = x.as_str() { aliases.insert(x.to_lowercase()); }
        }
    }
    let mut rows: Vec<Value> = known.iter().rev().take(TOP).map(|e| json!({"entity": e["entity"], "name": e["name"], "kind": e["kind"],
        "summary": e["summary"], "by": e["by"]})).collect();
    rows.sort_by(|x, y| x["name"].as_str().unwrap_or("").to_lowercase().cmp(&y["name"].as_str().unwrap_or("").to_lowercase()));
    let p = root.join("workspace/.adhoc/indexes/entities/candidates.json");
    let cands: Value = fs::read(p).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
    let indexed = cands.get("candidates").is_some();
    let mut sugg: Vec<(String, String, u64)> = cands["candidates"].as_object().into_iter().flatten().filter_map(|(_, c)| {
        let name = c["name"].as_str()?.to_string();
        let forms = c["forms"].as_array().cloned().unwrap_or_default();
        let known = aliases.contains(&name.to_lowercase())
            || forms.iter().any(|f| f.as_str().map(|f| aliases.contains(&f.to_lowercase())).unwrap_or(false));
        (!known).then(|| (name, c["kind"].as_str().unwrap_or("").to_string(), c["docs"].as_u64().unwrap_or(0)))
    }).collect();
    sugg.sort_by(|a, b| b.2.cmp(&a.2).then(a.0.cmp(&b.0)));
    json!({"total": known.len(), "kinds": counted(kinds), "known": rows, "indexed": indexed, "suggested": sugg.len(),
           "top_suggested": sugg.into_iter().take(6).map(|(n, k, d)| json!({"name": n, "kind": k, "docs": d})).collect::<Vec<_>>()})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pages_and_lists_in_the_workspace() {
        let dir = std::env::temp_dir().join(format!("adhoc-overview-{}", std::process::id()));
        let ws = dir.join("ws");
        fs::create_dir_all(ws.join("memos")).unwrap();
        fs::create_dir_all(ws.join(".adhoc")).unwrap();
        fs::write(ws.join("memos/chron.md"), "# Chronology\n\nText :q{#a}\n\n```refs\n{\"id\": \"a\"}\n{\"id\": \"b\"}\n```\n").unwrap();
        fs::write(ws.join(".adhoc/hidden.md"), "# Hidden\n").unwrap();
        fs::write(ws.join("hot.list.jsonl"), "{\"title\": \"Hot\", \"sources\": []}\n{\"loc\": \"doc:x\"}\n{\"loc\": \"doc:y\"}\n").unwrap();
        let (pages, lists) = workspace_files(&ws);
        assert_eq!(pages.as_array().unwrap().len(), 1);
        assert_eq!(pages[0]["file"], "memos/chron.md");
        assert_eq!(pages[0]["title"], "Chronology");
        assert_eq!(pages[0]["refs"], 2);
        assert_eq!(lists[0]["title"], "Hot");
        assert_eq!(lists[0]["items"], 2);
        assert_eq!(lists[0]["key_set"], true);
        fs::remove_dir_all(&dir).unwrap();
    }
}
