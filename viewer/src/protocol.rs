//! The `adhoc:` protocol: everything the window loads comes from here, and
//! nothing comes from the network. Requests run on one worker thread, which
//! owns the MuPDF renderer.
//!
//! Routes:
//!   /                          the shell (the viewer's own UI code)
//!   /api/doc/<doc>             a document's metadata, layers, pages, family
//!   /api/layer/<id>/text       layer text (also /segments, /geometry)
//!   /api/text?loc=…            text at a locator, checked
//!   /api/overview              what the matter holds, for the landing page
//!   /page/<doc>/<n>?w=…        a page image, rendered by MuPDF
//!   /presentation/<layer>      a layer's presentation, sanitized
//!   /pane/<content>/<path>     web pane content, sanitized when it arrived
//!
//! Every document and layer is checked against the matter's live documents.

use crate::matter::Matter;
use crate::render::{magic_for, Renderer};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

pub const CSP: &str = "default-src 'none'; img-src adhoc: http://adhoc.localhost data:; \
    style-src 'unsafe-inline' adhoc: http://adhoc.localhost; script-src 'none'; form-action 'none'; frame-src 'none'";

const SHELL_HTML: &str = include_str!("../shell/index.html");
const SHELL_JS: &str = include_str!("../shell/shell.js");
const SHELL_CSS: &str = include_str!("../shell/shell.css");
const ENTITIES_JS: &str = include_str!("../shell/entities.js");
const DATES_JS: &str = include_str!("../shell/dates.js");

pub struct Response {
    pub status: u16,
    pub mime: &'static str,
    pub body: Vec<u8>,
    pub csp: bool,
}

fn ok(mime: &'static str, body: impl Into<Vec<u8>>) -> Response {
    Response { status: 200, mime, body: body.into(), csp: false }
}

fn json_resp(v: Value) -> Response {
    ok("application/json", v.to_string())
}

fn not_found(why: &str) -> Response {
    Response { status: 404, mime: "text/plain", body: why.as_bytes().to_vec(), csp: false }
}

/// Pane content: content id -> path -> bytes.
pub type Panes = Arc<Mutex<HashMap<String, HashMap<String, Vec<u8>>>>>;

pub struct Handler {
    pub matter: Arc<Mutex<Matter>>,
    pub panes: Panes,
    renderer: Renderer,
    overview: crate::overview::Overview,
    chrono: crate::chrono::Chrono,
}

pub fn query(q: &str) -> HashMap<String, String> {
    q.split('&')
        .filter_map(|kv| kv.split_once('='))
        .map(|(k, v)| (k.to_string(), percent_encoding::percent_decode_str(&v.replace('+', " ")).decode_utf8_lossy().into_owned()))
        .collect()
}

/// A string locator: doc:<id>[@<layer>][#char=a,b|page=n].
pub struct Loc {
    pub doc: String,
    pub layer: Option<String>,
    pub chars: Option<(usize, usize)>,
    pub page: Option<i64>,
}

pub fn parse_loc(s: &str) -> Option<Loc> {
    let rest = s.strip_prefix("doc:")?;
    let (head, sel) = rest.split_once('#').unwrap_or((rest, ""));
    let (doc, layer) = match head.split_once('@') {
        Some((d, l)) => (d.to_string(), Some(l.to_string())),
        None => (head.to_string(), None),
    };
    let mut loc = Loc { doc, layer, chars: None, page: None };
    for part in sel.split('&').filter(|p| !p.is_empty()) {
        let (k, v) = part.split_once('=')?;
        match k {
            "char" => {
                let (a, b) = v.split_once(',')?;
                loc.chars = Some((a.parse().ok()?, b.parse().ok()?));
            }
            "page" => loc.page = v.split('-').next()?.parse().ok(),
            _ => {}
        }
    }
    Some(loc)
}

pub fn char_slice(text: &str, s: usize, e: usize) -> String {
    text.chars().skip(s).take(e.saturating_sub(s)).collect()
}

impl Handler {
    pub fn new(matter: Arc<Mutex<Matter>>, panes: Panes) -> Self {
        Handler { matter, panes, renderer: Renderer::new(), overview: Default::default(), chrono: Default::default() }
    }

    pub fn handle(&mut self, path: &str, q: &str) -> Response {
        let decoded: Vec<String> = path.trim_start_matches('/').split('/')
            .map(|p| percent_encoding::percent_decode_str(p).decode_utf8_lossy().into_owned()).collect();
        let parts: Vec<&str> = decoded.iter().map(|s| s.as_str()).collect();
        match parts.as_slice() {
            [""] | ["index.html"] => ok("text/html", SHELL_HTML),
            ["shell.js"] => ok("text/javascript", SHELL_JS),
            ["shell.css"] => ok("text/css", SHELL_CSS),
            ["entities.js"] => ok("text/javascript", ENTITIES_JS),
            ["dates.js"] => ok("text/javascript", DATES_JS),
            ["api", "doc", doc] => self.doc_info(doc),
            ["api", "layer", id, "detected"] => self.detected(id),
            ["api", "layer", id, "dates"] => self.dates(id),
            ["api", "around"] => self.around(&query(q)),
            ["api", "layer", id, part] => self.layer_part(id, part),
            ["api", "text"] => self.text_at(&query(q)),
            ["api", "docs"] => self.docs(),
            ["api", "annotations"] => self.annotations(),
            ["api", "entity-candidates"] => self.entity_candidates(),
            ["api", "overview"] => self.overview(),
            ["page", doc, n] => self.page(doc, n.trim_end_matches(".png"), &query(q)),
            ["presentation", layer] => self.presentation(layer),
            ["pane", content, rest @ ..] => self.pane(content, &rest.join("/")),
            _ => not_found("no such route"),
        }
    }

    fn annotations(&self) -> Response {
        let root = self.matter.lock().unwrap().root.clone();
        json_resp(annotation_state(&root))
    }

    /// The entities index's detections for a layer's text (the entity layer's
    /// suggestions), or none if the index hasn't reached it yet.
    fn detected(&self, id: &str) -> Response {
        let m = self.matter.lock().unwrap();
        let Some(layer) = m.layer(id).cloned() else { return not_found("layer not in this matter") };
        let h = layer.text.strip_prefix("sha256:").unwrap_or(&layer.text).to_string();
        if h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
            return json_resp(json!([]));
        }
        let p = m.root.join("workspace/.adhoc/indexes/entities/texts").join(format!("{}.json", h));
        let v: Value = std::fs::read(p).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
        json_resp(v.get("detections").cloned().unwrap_or(json!([])))
    }

    /// The dates index's date mentions for a layer's text, or none if the
    /// index hasn't reached it yet.
    fn dates(&self, id: &str) -> Response {
        let m = self.matter.lock().unwrap();
        let Some(layer) = m.layer(id).cloned() else { return not_found("layer not in this matter") };
        let h = layer.text.strip_prefix("sha256:").unwrap_or(&layer.text).to_string();
        if h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
            return json_resp(json!([]));
        }
        let p = m.root.join("workspace/.adhoc/indexes/dates/texts").join(format!("{}.json", h));
        let v: Value = std::fs::read(p).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
        json_resp(v.get("dates").cloned().unwrap_or(json!([])))
    }

    /// What is around a date, for a date card: `date`, and optionally `doc`
    /// (the document it is shown in) and `who` (a quoted email's sender).
    fn around(&mut self, q: &HashMap<String, String>) -> Response {
        let Some(date) = q.get("date").filter(|d| d.len() >= 4 && d[..4].bytes().all(|b| b.is_ascii_digit())) else {
            return not_found("no date");
        };
        let matter = self.matter.clone();
        let mut m = matter.lock().unwrap();
        m.refresh();
        let doc = q.get("doc").and_then(|d| m.resolve_doc(d)).filter(|d| m.live(d));
        json_resp(self.chrono.around(&m, date, doc.as_deref(), q.get("who").map(|s| s.as_str())))
    }

    /// Every detected candidate across the matter: how many documents name it.
    fn entity_candidates(&self) -> Response {
        let m = self.matter.lock().unwrap();
        let p = m.root.join("workspace/.adhoc/indexes/entities/candidates.json");
        let v: Value = std::fs::read(p).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
        json_resp(v.get("candidates").cloned().unwrap_or(json!({})))
    }

    fn overview(&mut self) -> Response {
        let matter = self.matter.clone();
        let mut m = matter.lock().unwrap();
        m.refresh();
        json_resp(self.overview.get(&m))
    }
}

/// The annotation log replayed: tag definitions, tags and notes by document, and the entity list.
pub fn annotation_state(root: &std::path::Path) -> Value {
    let text = std::fs::read_to_string(root.join("annotations/log.jsonl")).unwrap_or_default();
    let mut defs: Vec<(String, Value)> = vec![];
    let mut tags: Vec<(String, Value)> = vec![]; // (target key + tag, application)
    let mut notes: Vec<(String, Value)> = vec![];
    let mut entities: Vec<(String, Value)> = vec![];
    let key = |t: &Value| json!({"doc": t["doc"], "layer": t["layer"], "select": t["select"]}).to_string();
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let Ok(ev) = serde_json::from_str::<Value>(line) else { continue };
        let tag = ev["tag"].as_str().unwrap_or("").to_string();
        match ev["op"].as_str().unwrap_or("") {
            "define-tag" => defs.push((tag, ev.clone())),
            "edit-tag" => {
                if let Some((_, d)) = defs.iter_mut().find(|(t, _)| *t == tag) {
                    for k in ["color", "hotkey", "group", "description"] {
                        if ev.get(k).is_some() { d[k] = ev[k].clone(); }
                    }
                }
            }
            "tag" => {
                let k = key(&ev["target"]) + &tag;
                tags.retain(|(x, _)| *x != k);
                tags.push((k, json!({"tag": tag, "target": ev["target"], "by": ev["author"], "at": ev["at"]})));
            }
            "untag" => {
                let k = key(&ev["target"]) + &tag;
                tags.retain(|(x, _)| *x != k);
            }
            "note" | "reply" => {
                let target = if ev["op"] == "reply" {
                    notes.iter().find(|(id, _)| Some(id.as_str()) == ev["parent"].as_str()).map(|(_, n)| n["target"].clone()).unwrap_or(Value::Null)
                } else { ev["target"].clone() };
                notes.push((ev["note"].as_str().unwrap_or("").to_string(), json!({"note": ev["note"], "parent": ev["parent"],
                    "target": target, "body": ev["body"], "by": ev["author"], "at": ev["at"]})));
            }
            "edit-note" => {
                if let Some((_, n)) = notes.iter_mut().find(|(id, _)| Some(id.as_str()) == ev["note"].as_str()) {
                    n["body"] = ev["body"].clone();
                    n["edited_at"] = ev["at"].clone();
                }
            }
            "delete-note" => notes.retain(|(id, _)| Some(id.as_str()) != ev["note"].as_str()),
            // the entity list (see entities.py and annotations.py, which this follows)
            "define-entity" => {
                let mut aliases: Vec<Value> = vec![];
                for a in std::iter::once(&ev["name"]).chain(ev["aliases"].as_array().into_iter().flatten()) {
                    let a = a.as_str().unwrap_or("").split_whitespace().collect::<Vec<_>>().join(" ");
                    if !a.is_empty() && !aliases.iter().any(|x| x == &json!(a)) { aliases.push(json!(a)); }
                }
                entities.push((ev["entity"].as_str().unwrap_or("").to_string(), json!({
                    "entity": ev["entity"], "name": ev["name"], "kind": ev.get("kind").filter(|k| !k.is_null()).cloned().unwrap_or(json!("other")),
                    "aliases": aliases, "summary": ev["summary"], "recap": ev["recap"],
                    "recap_at": if ev["recap"].is_string() { ev["at"].clone() } else { Value::Null },
                    "see": ev.get("see").cloned().unwrap_or(json!([])), "date": ev["date"], "by": ev["author"], "at": ev["at"]})));
            }
            "edit-entity" => {
                if let Some((_, e)) = entities.iter_mut().find(|(id, _)| Some(id.as_str()) == ev["entity"].as_str()) {
                    for k in ["name", "kind", "summary", "recap", "see", "date"] {
                        if ev.get(k).is_some() { e[k] = ev[k].clone(); }
                    }
                    if ev.get("recap").is_some() { e["recap_at"] = ev["at"].clone(); }
                    let mut aliases: Vec<Value> = e["aliases"].as_array().cloned().unwrap_or_default();
                    let removed: Vec<&Value> = ev["remove_aliases"].as_array().map(|a| a.iter().collect()).unwrap_or_default();
                    aliases.retain(|a| !removed.contains(&a));
                    for a in ev.get("name").into_iter().chain(ev["add_aliases"].as_array().into_iter().flatten()) {
                        let a = a.as_str().unwrap_or("").split_whitespace().collect::<Vec<_>>().join(" ");
                        if !a.is_empty() && !aliases.iter().any(|x| x == &json!(a)) { aliases.push(json!(a)); }
                    }
                    e["aliases"] = json!(aliases);
                    e["edited_by"] = ev["author"].clone();
                    e["edited_at"] = ev["at"].clone();
                }
            }
            "delete-entity" => entities.retain(|(id, _)| Some(id.as_str()) != ev["entity"].as_str()),
            _ => {}
        }
    }
    let defs: Vec<Value> = defs.into_iter().map(|(_, d)| json!({"tag": d["tag"], "color": d["color"], "hotkey": d["hotkey"],
        "group": d["group"], "description": d["description"], "by": d["author"]})).collect();
    json!({"definitions": defs, "tags": tags.into_iter().map(|(_, t)| t).collect::<Vec<_>>(),
           "notes": notes.into_iter().map(|(_, n)| n).collect::<Vec<_>>(),
           "entities": entities.into_iter().map(|(_, e)| e).collect::<Vec<_>>()})
}

impl Handler {
    fn docs(&self) -> Response {
        let mut m = self.matter.lock().unwrap();
        m.refresh();
        let docs: Vec<Value> = m.state.order.iter().filter(|d| m.live(d)).map(|d| {
            let meta = m.metadata(d);
            json!({"doc": d, "name": meta["name"], "kind": meta["kind"], "date": meta["date"]})
        }).collect();
        json_resp(json!(docs))
    }

    fn doc_info(&mut self, id: &str) -> Response {
        let (doc, content, info, kind, name) = {
            let mut m = self.matter.lock().unwrap();
            m.refresh();
            let Some(doc) = m.resolve_doc(id) else { return not_found("document not in this matter") };
            let content = m.state.documents[&doc].clone();
            let meta = m.metadata(&doc);
            let primary = m.primary_layer(&content).map(|l| l.id.clone());
            let layers: Vec<Value> = m.state.layers.get(&content).map(|ls| ls.iter().map(|l| {
                let obj = m.object(&l.id).unwrap_or(Value::Null);
                json!({"id": l.id, "view": l.view, "primary": Some(&l.id) == primary.as_ref(),
                       "geometry": obj.get("geometry").is_some() || obj.get("derived_from").is_some(),
                       "presentation": obj.get("presentation").is_some()})
            }).collect()).unwrap_or_default();
            let children: Vec<Value> = m.state.children.get(&doc).map(|cs| cs.iter().filter(|c| m.live(c)).map(|c| {
                json!({"doc": c, "name": m.metadata(c)["name"]})
            }).collect()).unwrap_or_default();
            let parent = m.state.parent.get(&doc).filter(|p| m.live(p)).map(|p| json!({"doc": p, "name": m.metadata(p)["name"]}));
            let obj = m.object(&doc).unwrap_or(Value::Null);
            let kind = meta["kind"].as_str().unwrap_or("").to_string();
            let name = meta["name"].as_str().unwrap_or("").to_string();
            // printed page labels, from any layer that has them
            let mut labels = serde_json::Map::new();
            for l in m.state.layers.get(&content).into_iter().flatten() {
                let segs: Vec<Value> = m.segments(&l.id).into_iter().filter(|s| s["kind"] == "page-label").collect();
                if segs.is_empty() { continue; }
                let text = m.text(&l.text).unwrap_or_default();
                let chars: Vec<char> = text.chars().collect();
                for s in segs {
                    let at = s["start"].as_u64().unwrap_or(0) as usize;
                    let page = chars[..at.min(chars.len())].iter().filter(|c| **c == '\u{c}').count() + 1;
                    labels.entry(page.to_string()).or_insert(s["label"].clone());
                }
                break;
            }
            let info = json!({"doc": doc, "content": content, "meta": meta, "custodian": obj.get("custodian"),
                              "layers": layers, "children": children, "parent": parent,
                              "duplicates": m.duplicates(&doc), "page_labels": labels, "bates": bates_for(&m.root, &doc),
                              "dated": crate::chrono::strip(&m, &doc),
                              "exhibits": exhibits_for(&m.root, &doc)});
            (doc, content, info, kind, name)
        };
        let mut info = info;
        if kind == "pdf" || kind == "image" {
            let matter = self.matter.clone();
            let c = content.clone();
            match self.renderer.sizes(&content, || {
                let m = matter.lock().unwrap();
                m.bytes(&c).map(|b| (b, magic_for(&kind, &name)))
            }) {
                Ok(sizes) => info["pages"] = json!(sizes),
                Err(e) => info["render_error"] = json!(e),
            }
        }
        let _ = doc;
        json_resp(info)
    }

    fn layer_part(&self, id: &str, part: &str) -> Response {
        let m = self.matter.lock().unwrap();
        let Some(layer) = m.layer(id).cloned() else { return not_found("layer not in this matter") };
        match part {
            "text" => ok("text/plain; charset=utf-8", m.text(&layer.text).unwrap_or_default()),
            "segments" => json_resp(json!(m.segments(id))),
            "geometry" => json_resp(json!(m.geometry(id))),
            _ => not_found("no such part"),
        }
    }

    fn text_at(&self, q: &HashMap<String, String>) -> Response {
        let m = self.matter.lock().unwrap();
        let broken = |e: &str| json_resp(json!({"status": "broken", "error": e}));
        let Some(loc) = q.get("loc").and_then(|s| parse_loc(s)) else { return broken("not a locator") };
        let Some(doc) = m.resolve_doc(&loc.doc) else { return broken("document not in this matter") };
        let content = &m.state.documents[&doc];
        let layer = match &loc.layer {
            Some(l) => m.state.layers.get(content).and_then(|ls| ls.iter().find(|x| &x.id == l)).cloned(),
            None => m.primary_layer(content).cloned(),
        };
        let Some(layer) = layer else { return broken("layer missing") };
        let text = m.text(&layer.text).unwrap_or_default();
        let n = text.chars().count();
        let (s, e) = match (loc.chars, loc.page) {
            (Some((s, e)), _) => (s, e),
            (None, Some(p)) => {
                let pages: Vec<&str> = text.split('\u{c}').collect();
                if p < 1 || p as usize > pages.len() {
                    return broken("no such page");
                }
                let s: usize = pages[..p as usize - 1].iter().map(|x| x.chars().count() + 1).sum();
                (s, s + pages[p as usize - 1].chars().count())
            }
            _ => (0, n),
        };
        if s > e || e > n {
            return broken("offsets outside the layer");
        }
        let ctx: usize = q.get("context").and_then(|c| c.parse().ok()).unwrap_or(0);
        let mut out = json!({"status": "exact", "doc": doc, "layer": layer.id, "start": s, "end": e,
                             "text": char_slice(&text, s, e)});
        if ctx > 0 {
            out["pre"] = json!(char_slice(&text, s.saturating_sub(ctx), s));
            out["post"] = json!(char_slice(&text, e, e + ctx));
        }
        json_resp(out)
    }

    fn page(&mut self, id: &str, n: &str, q: &HashMap<String, String>) -> Response {
        let (doc, content, kind, name) = {
            let m = self.matter.lock().unwrap();
            let Some(doc) = m.resolve_doc(id) else { return not_found("document not in this matter") };
            let meta = m.metadata(&doc);
            let content = m.state.documents[&doc].clone();
            (doc, content, meta["kind"].as_str().unwrap_or("").to_string(), meta["name"].as_str().unwrap_or("").to_string())
        };
        let _ = doc;
        let n: i32 = n.parse().unwrap_or(1);
        let w: u32 = q.get("w").and_then(|w| w.parse().ok()).unwrap_or(1000).clamp(100, 4000);
        let matter = self.matter.clone();
        let c = content.clone();
        match self.renderer.png(&content, n, w, || {
            let m = matter.lock().unwrap();
            m.bytes(&c).map(|b| (b, magic_for(&kind, &name)))
        }) {
            Ok(png) => ok("image/png", png),
            Err(e) => not_found(&e),
        }
    }

    fn presentation(&self, id: &str) -> Response {
        let m = self.matter.lock().unwrap();
        if m.layer(id).is_none() {
            return not_found("layer not in this matter");
        }
        let obj = m.object(id).unwrap_or(Value::Null);
        let Some(h) = obj.get("presentation").and_then(|v| v.as_str()) else { return not_found("no presentation") };
        let html = sanitize(&m.text(h).unwrap_or_default());
        let page = format!("<!doctype html><html><head><meta charset=\"utf-8\"><link rel=\"stylesheet\" href=\"/shell.css\"></head><body class=\"adhoc-presentation\">{}</body></html>", html);
        Response { status: 200, mime: "text/html; charset=utf-8", body: page.into_bytes(), csp: true }
    }

    fn pane(&self, content: &str, path: &str) -> Response {
        let panes = self.panes.lock().unwrap();
        let Some(files) = panes.get(content) else { return not_found("no such pane content") };
        let path = if path.is_empty() { "index.html" } else { path };
        let Some(body) = files.get(path) else { return not_found("no such file in pane") };
        let mime = match path.rsplit('.').next().unwrap_or("") {
            "html" | "htm" => "text/html; charset=utf-8",
            "css" => "text/css",
            "png" => "image/png",
            "jpg" | "jpeg" => "image/jpeg",
            "svg" => "image/svg+xml",
            _ => "application/octet-stream",
        };
        Response { status: 200, mime, body: body.clone(), csp: true }
    }
}

/// Where a document was produced: its records from every production in the
/// workspace (Bates range and the map from Bates pages to physical pages).
fn bates_for(root: &std::path::Path, doc: &str) -> Vec<Value> {
    let mut out = vec![];
    let Ok(dirs) = std::fs::read_dir(root.join("workspace/productions")) else { return out };
    let mut dirs: Vec<_> = dirs.flatten().map(|d| d.path()).collect();
    dirs.sort();
    for d in dirs {
        let Ok(text) = std::fs::read_to_string(d.join("production.jsonl")) else { continue };
        let mut lines = text.lines().filter_map(|l| serde_json::from_str::<Value>(l).ok());
        let Some(head) = lines.next() else { continue };
        for r in lines.filter(|r| r["doc"] == doc) {
            out.push(json!({"production": head["name"], "prefix": head["prefix"], "digits": head["digits"],
                            "begin": r["begin"], "end": r["end"], "page_map": r["page_map"]}));
        }
    }
    out
}

/// The exhibits a document became (workspace/exhibits/*/exhibits.jsonl), oldest set first.
fn exhibits_for(root: &std::path::Path, doc: &str) -> Vec<Value> {
    let mut out = vec![];
    let Ok(dirs) = std::fs::read_dir(root.join("workspace/exhibits")) else { return out };
    let mut dirs: Vec<_> = dirs.flatten().map(|d| d.path()).collect();
    dirs.sort();
    for d in dirs {
        let Ok(text) = std::fs::read_to_string(d.join("exhibits.jsonl")) else { continue };
        let mut lines = text.lines().filter_map(|l| serde_json::from_str::<Value>(l).ok());
        let Some(head) = lines.next() else { continue };
        for r in lines.filter(|r| r["doc"] == doc) {
            out.push(json!({"set": head["name"], "label": r["label"], "page_map": r["page_map"]}));
        }
    }
    out
}

/// Remove scripts, event handlers, frames, objects and remote resources.
/// Panes keep forms, data-* attributes and loc:/svc: links.
pub fn sanitize(html: &str) -> String {
    let html = remote_images(html);
    let mut b = ammonia::Builder::default();
    b.add_tags(&["form", "input", "select", "option", "textarea", "button", "label", "fieldset", "legend",
                 "article", "section", "header", "footer", "nav", "main", "figure", "figcaption", "mark", "cite", "q", "style", "link", "meta", "title", "head", "body", "html"])
        .rm_clean_content_tags(&["style"])
        .add_generic_attributes(&["class", "id", "style", "title", "lang", "dir"])
        .add_generic_attribute_prefixes(&["data-"])
        .add_tag_attributes("input", &["type", "name", "value", "checked", "placeholder", "disabled"])
        .add_tag_attributes("select", &["name", "multiple"])
        .add_tag_attributes("option", &["value", "selected"])
        .add_tag_attributes("textarea", &["name", "rows", "cols"])
        .add_tag_attributes("button", &["type", "name", "value"])
        .add_tag_attributes("form", &["action", "method"])
        .add_tag_attributes("label", &["for"])
        .add_tag_attributes("link", &["rel", "href"])
        .add_tag_attributes("meta", &["charset"])
        .url_schemes(["loc", "svc"].into_iter().collect())
        .url_relative(ammonia::UrlRelative::PassThrough)
        .link_rel(None)
        .strip_comments(true);
    b.clean(&html).to_string()
}

/// Remote images are never fetched, but their presence may be evidence: each
/// becomes a visible placeholder naming its URL.
fn remote_images(html: &str) -> String {
    let lower = html.to_ascii_lowercase();
    let mut out = String::with_capacity(html.len());
    let mut pos = 0;
    while let Some(i) = lower[pos..].find("<img").map(|i| i + pos) {
        let Some(end) = lower[i..].find('>').map(|e| e + i + 1) else { break };
        let tag = &html[i..end];
        let tag_lower = &lower[i..end];
        let src = tag_lower.find("src=").and_then(|k| {
            let rest = &tag[k + 4..];
            let q = rest.chars().next()?;
            if q == '"' || q == '\'' {
                rest[1..].find(q).map(|e| rest[1..1 + e].to_string())
            } else {
                Some(rest.split(|c: char| c.is_whitespace() || c == '>').next()?.to_string())
            }
        });
        out.push_str(&html[pos..i]);
        match src {
            Some(u) if u.to_ascii_lowercase().starts_with("http") => {
                let esc = u.replace('&', "&amp;").replace('<', "&lt;").replace('"', "&quot;");
                out.push_str(&format!("<span class=\"adhoc-remote\">[remote image, not loaded: {}]</span>", esc));
            }
            _ => out.push_str(tag),
        }
        pos = end;
    }
    out.push_str(&html[pos..]);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_removes_scripts_and_keeps_locators() {
        let out = sanitize(r#"<script>x()</script><p onclick="x()">a</p><a href="javascript:x()">j</a>
            <a href="loc:doc:sha256:ab@sha256:cd#char=1,2">q</a><img src="https://e.com/x.png"><iframe src="https://e.com"></iframe>
            <span data-text="doc:sha256:ab">t</span><form action="svc:triage"><input type="checkbox" name="p" value="a"></form>"#);
        assert!(!out.contains("script") && !out.contains("onclick") && !out.contains("javascript") && !out.contains("iframe"));
        assert!(!out.contains("src=\"https://e.com"));
        assert!(out.contains("remote image, not loaded: https://e.com/x.png"));
        assert!(out.contains(r#"href="loc:doc:sha256:ab@sha256:cd#char=1,2""#), "{}", out);
        assert!(out.contains(r#"data-text="doc:sha256:ab""#) && out.contains(r#"action="svc:triage""#) && out.contains("checkbox"));
    }

    #[test]
    fn locators_parse() {
        let l = parse_loc("doc:sha256:ab@sha256:cd#char=10,20").unwrap();
        assert_eq!((l.doc.as_str(), l.layer.as_deref(), l.chars), ("sha256:ab", Some("sha256:cd"), Some((10, 20))));
        assert_eq!(parse_loc("doc:sha256:ab#page=3").unwrap().page, Some(3));
        assert!(parse_loc("file:/etc/passwd").is_none());
        assert_eq!(char_slice("Zoë Müller", 2, 7), "ë Mül");
    }
}
