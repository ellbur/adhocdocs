//! Read-only access to one matter: its object store and the state its logs
//! describe. The viewer's scope is exactly this matter's live documents; it
//! never opens a path it was not given at launch.

use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Layer {
    pub id: String,
    pub content: String,
    pub view: String,
    pub text: String,
}

#[derive(Default)]
pub struct State {
    pub documents: HashMap<String, String>, // doc -> content
    pub order: Vec<String>,
    pub withdrawn: HashSet<String>,
    pub superseded: HashMap<String, String>,
    pub layers: HashMap<String, Vec<Layer>>, // content -> layers
    pub primary: HashMap<String, String>,    // content -> layer
    pub metadata: HashMap<String, String>,   // doc -> metadata object
    pub children: HashMap<String, Vec<String>>,
    pub parent: HashMap<String, String>,
    pub purged: HashSet<String>,
}

pub struct Matter {
    pub root: PathBuf,
    roots: Vec<PathBuf>,
    log_sizes: Vec<(PathBuf, u64)>,
    pub state: State,
}

/// A feed's logs: one per installation (`<dir>/<origin>.jsonl`), plus the
/// single log of a matter made before that (`legacy`), in the order merging
/// breaks ties: by origin, the legacy log counting as origin "legacy". This
/// must match `Feed.logs` in adhocdocs/matter.py.
pub fn feed_logs(dir: &Path, legacy: &Path) -> Vec<PathBuf> {
    let mut found: Vec<(String, PathBuf)> = fs::read_dir(dir)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.extension().map(|x| x == "jsonl").unwrap_or(false))
                .filter_map(|p| Some((p.file_stem()?.to_string_lossy().to_string(), p)))
                .collect()
        })
        .unwrap_or_default();
    if legacy.exists() {
        found.push(("legacy".to_string(), legacy.to_path_buf()));
    }
    found.sort();
    found.into_iter().map(|(_, p)| p).collect()
}

fn read_log(p: &Path) -> Vec<Value> {
    let Ok(text) = fs::read_to_string(p) else { return vec![] };
    text.lines().filter(|l| !l.trim().is_empty()).filter_map(|l| serde_json::from_str(l).ok()).collect()
}

/// How many of each removed installation's events count, in one feed
/// (`pool/cutoffs.json`, written by adhocdocs.pool): what it wrote after its
/// removal is set aside, as `Feed.read` does.
pub fn cutoffs(root: &Path, feed: &str) -> HashMap<String, usize> {
    let v: Value = fs::read(root.join("pool/cutoffs.json")).ok()
        .and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
    v["feeds"][feed].as_object().map(|o| o.iter()
        .filter_map(|(k, n)| Some((k.clone(), n.as_u64()? as usize))).collect()).unwrap_or_default()
}

/// A feed's logs merged, each removed installation's cut where it was removed.
pub fn feed_events(root: &Path, feed: &str, dir: &Path, legacy: &Path) -> Vec<Value> {
    let cut = cutoffs(root, feed);
    let paths = feed_logs(dir, legacy);
    let limits: Vec<Option<usize>> = paths.iter()
        .map(|p| p.file_stem().and_then(|s| cut.get(s.to_string_lossy().as_ref()).copied())).collect();
    merged_limited(&paths, &limits)
}

/// Several logs' events in the order every installation replays them: at
/// each step, the earliest of the logs' next events by ID, ties going to the
/// earlier log. Each log keeps its own file order. (`heapq.merge` in
/// adhocdocs/matter.py makes the same choices.)
pub fn merged(paths: &[PathBuf]) -> Vec<Value> {
    merged_limited(paths, &vec![None; paths.len()])
}

fn merged_limited(paths: &[PathBuf], limits: &[Option<usize>]) -> Vec<Value> {
    let logs: Vec<Vec<Value>> = paths.iter().zip(limits)
        .map(|(p, l)| { let mut v = read_log(p); if let Some(n) = l { v.truncate(*n); } v }).collect();
    let mut pos = vec![0usize; logs.len()];
    let mut out = Vec::with_capacity(logs.iter().map(|l| l.len()).sum());
    loop {
        let mut best: Option<(usize, &str)> = None;
        for (i, log) in logs.iter().enumerate() {
            if let Some(ev) = log.get(pos[i]) {
                let id = ev.get("id").and_then(|v| v.as_str()).unwrap_or("");
                if best.map(|(_, b)| id < b).unwrap_or(true) {
                    best = Some((i, id));
                }
            }
        }
        let Some((i, _)) = best else { break };
        out.push(logs[i][pos[i]].clone());
        pos[i] += 1;
    }
    out
}

/// The annotation feed's events, merged.
pub fn annotation_events(root: &Path) -> Vec<Value> {
    feed_events(root, "annotations", &root.join("annotations/logs"), &root.join("annotations/log.jsonl"))
}

fn hex(h: &str) -> &str {
    h.strip_prefix("sha256:").unwrap_or(h)
}

impl Matter {
    pub fn open(root: &Path) -> Result<Matter, String> {
        let root = root.canonicalize().map_err(|e| format!("{}: {}", root.display(), e))?;
        if !root.join("store/objects").is_dir() {
            return Err(format!("{} is not a matter", root.display()));
        }
        let mut m = Matter {
            roots: vec![root.join("store/objects"), root.join("workspace/.adhoc/objects")],
            log_sizes: vec![],
            root,
            state: State::default(),
        };
        m.refresh();
        Ok(m)
    }

    /// Replay the logs if any has grown, or a new one appeared, since the
    /// last look. The store feed (every installation's log, merged), then the
    /// workspace's.
    pub fn refresh(&mut self) {
        let store = feed_logs(&self.root.join("store/logs"), &self.root.join("store/log.jsonl"));
        let workspace = self.root.join("workspace/.adhoc/log.jsonl");
        let cutoffs = self.root.join("pool/cutoffs.json");
        let sizes: Vec<(PathBuf, u64)> = store
            .iter()
            .chain(std::iter::once(&workspace))
            .chain(std::iter::once(&cutoffs))
            .map(|p| (p.clone(), fs::metadata(p).map(|m| m.len()).unwrap_or(0)))
            .collect();
        if sizes == self.log_sizes {
            return;
        }
        let mut s = State::default();
        let store_events = feed_events(&self.root, "store", &self.root.join("store/logs"), &self.root.join("store/log.jsonl"));
        for ev in store_events.iter().chain(read_log(&workspace).iter()) {
            apply(&mut s, ev);
        }
        self.state = s;
        self.log_sizes = sizes;
    }

    /// Changes whenever any log does.
    pub fn generation(&self) -> Vec<u64> {
        self.log_sizes.iter().map(|(_, n)| *n).collect()
    }

    fn path(&self, h: &str) -> Option<PathBuf> {
        let h = hex(h);
        if h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        self.roots.iter().map(|r| r.join(&h[..2]).join(&h[2..])).find(|p| p.exists())
    }

    /// The size of an object, without reading it.
    pub fn size(&self, h: &str) -> Option<u64> {
        fs::metadata(self.path(h)?).ok().map(|m| m.len())
    }

    /// Bytes [start, end) of an object (clipped to its end), without reading
    /// the rest. Callers check scope first.
    pub fn byte_range(&self, h: &str, start: u64, end: u64) -> Option<Vec<u8>> {
        use std::io::{Read, Seek, SeekFrom};
        let mut f = fs::File::open(self.path(h)?).ok()?;
        f.seek(SeekFrom::Start(start)).ok()?;
        let mut out = Vec::new();
        f.take(end.saturating_sub(start)).read_to_end(&mut out).ok()?;
        Some(out)
    }

    /// Bytes of an object. Callers check scope first.
    pub fn bytes(&self, h: &str) -> Option<Vec<u8>> {
        fs::read(self.path(h)?).ok()
    }

    pub fn text(&self, h: &str) -> Option<String> {
        String::from_utf8(self.bytes(h)?).ok()
    }

    pub fn object(&self, h: &str) -> Option<Value> {
        serde_json::from_slice(&self.bytes(h)?).ok()
    }

    pub fn jsonl(&self, h: Option<&str>) -> Vec<Value> {
        let Some(h) = h else { return vec![] };
        self.text(h)
            .map(|t| t.lines().filter_map(|l| serde_json::from_str(l).ok()).collect())
            .unwrap_or_default()
    }

    // -- scope ---------------------------------------------------------------

    pub fn live(&self, doc: &str) -> bool {
        self.state.documents.contains_key(doc) && !self.state.withdrawn.contains(doc)
    }

    /// The document a (possibly short or superseded) ID names, if it is in scope.
    pub fn resolve_doc(&self, id: &str) -> Option<String> {
        let id = if id.starts_with("sha256:") { id.to_string() } else { format!("sha256:{}", id) };
        let mut doc = if id.len() == 71 {
            id
        } else {
            let mut found = self.state.documents.keys().filter(|d| d.starts_with(&id));
            let first = found.next()?.clone();
            if found.next().is_some() {
                return None;
            }
            first
        };
        let mut seen = 0;
        while let Some(next) = self.state.superseded.get(&doc) {
            doc = next.clone();
            seen += 1;
            if seen > 100 {
                return None;
            }
        }
        self.live(&doc).then_some(doc)
    }

    /// A layer, if it belongs to a document in scope.
    pub fn layer(&self, id: &str) -> Option<&Layer> {
        let live: HashSet<&String> = self
            .state
            .documents
            .iter()
            .filter(|(d, _)| !self.state.withdrawn.contains(*d))
            .map(|(_, c)| c)
            .collect();
        self.state
            .layers
            .iter()
            .filter(|(c, _)| live.contains(c))
            .flat_map(|(_, ls)| ls.iter())
            .find(|l| l.id == id)
    }

    pub fn primary_layer(&self, content: &str) -> Option<&Layer> {
        let layers = self.state.layers.get(content)?;
        let p = self.state.primary.get(content);
        layers.iter().find(|l| Some(&l.id) == p).or(layers.first())
    }

    pub fn metadata(&self, doc: &str) -> Value {
        self.state.metadata.get(doc).and_then(|h| self.object(h)).unwrap_or(Value::Null)
    }

    /// Geometry for a layer. A derived layer (no geometry of its own) gets
    /// its source's geometry mapped through the offset map, so page mode can
    /// work in the derived layer's offsets directly.
    pub fn geometry(&self, layer_id: &str) -> Vec<Value> {
        let Some(obj) = self.object(layer_id) else { return vec![] };
        if let Some(g) = obj.get("geometry").and_then(|v| v.as_str()) {
            return self.jsonl(Some(g));
        }
        let (Some(src), Some(map)) = (obj.get("derived_from").and_then(|v| v.as_str()), obj.get("offset_map").and_then(|v| v.as_str())) else {
            return vec![];
        };
        let runs = self.jsonl(Some(map));
        let mut out = vec![];
        for g in self.geometry(src) {
            let (s, e) = (g["start"].as_i64().unwrap_or(0), g["end"].as_i64().unwrap_or(0));
            for r in &runs {
                let ss = r["source_start"].as_i64().unwrap_or(0);
                let len = r["end"].as_i64().unwrap_or(0) - r["start"].as_i64().unwrap_or(0);
                if s >= ss && e <= ss + len {
                    let mut g2 = g.clone();
                    let d = r["start"].as_i64().unwrap_or(0) - ss;
                    g2["start"] = (s + d).into();
                    g2["end"] = (e + d).into();
                    out.push(g2);
                    break;
                }
            }
        }
        out
    }

    pub fn segments(&self, layer_id: &str) -> Vec<Value> {
        let obj = self.object(layer_id).unwrap_or(Value::Null);
        self.jsonl(obj.get("segments").and_then(|v| v.as_str()))
    }

    /// Other live documents with the same content.
    pub fn duplicates(&self, doc: &str) -> Vec<String> {
        let Some(c) = self.state.documents.get(doc) else { return vec![] };
        self.state
            .order
            .iter()
            .filter(|d| *d != doc && self.live(d) && self.state.documents.get(*d) == Some(c))
            .cloned()
            .collect()
    }
}

fn apply(s: &mut State, ev: &Value) {
    let str_ = |k: &str| ev.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    match ev.get("op").and_then(|v| v.as_str()).unwrap_or("") {
        "add-document" => {
            let doc = str_("doc");
            let new = !s.documents.contains_key(&doc);
            if new {
                s.order.push(doc.clone());
            }
            s.documents.insert(doc.clone(), str_("content"));
            if !s.purged.contains(&doc) {
                s.withdrawn.remove(&doc); // ingesting a purged document again doesn't bring it back
            }
            let parent = str_("parent");
            // the same document ingested on two installations is one document
            if new && !parent.is_empty() {
                s.children.entry(parent.clone()).or_default().push(doc.clone());
                s.parent.insert(doc, parent);
            }
        }
        "add-layer" => {
            let l = Layer { id: str_("layer"), content: str_("content"), view: str_("view"), text: str_("text") };
            let v = s.layers.entry(l.content.clone()).or_default();
            if !v.iter().any(|x| x.id == l.id) {
                v.push(l);
            }
        }
        "set-primary" => {
            s.primary.insert(str_("content"), str_("layer"));
        }
        "set-metadata" => {
            s.metadata.insert(str_("doc"), str_("metadata"));
        }
        "supersede" => {
            s.superseded.insert(str_("old"), str_("new"));
            s.withdrawn.insert(str_("old"));
        }
        "withdraw" => {
            s.withdrawn.insert(str_("doc"));
        }
        // gone for good (adhocdocs.purge): its objects are deleted too
        "purge" if str_("author").starts_with("user:") => {
            for d in ev["docs"].as_array().into_iter().flatten().filter_map(|d| d.as_str()) {
                s.purged.insert(d.to_string());
                s.withdrawn.insert(d.to_string());
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(id: &str, op: &str, doc: &str) -> String {
        format!("{{\"id\": \"{}\", \"op\": \"{}\", \"doc\": \"{}\", \"content\": \"c-{}\"}}\n", id, op, doc, doc)
    }

    #[test]
    fn logs_of_every_installation_merge_by_id() {
        let dir = std::env::temp_dir().join(format!("adhoc-merge-{}", std::process::id()));
        let logs = dir.join("store/logs");
        fs::create_dir_all(&logs).unwrap();
        fs::create_dir_all(dir.join("store/objects")).unwrap();
        // two installations; B's withdraw sorts between A's adds
        fs::write(logs.join("o_B.jsonl"), ev("ev_2", "withdraw", "x")).unwrap();
        fs::write(logs.join("o_A.jsonl"), ev("ev_1", "add-document", "x") + &ev("ev_3", "add-document", "y")).unwrap();
        fs::write(logs.join("notes.txt"), "not a log").unwrap();
        let paths = feed_logs(&logs, &dir.join("store/log.jsonl"));
        assert_eq!(paths.len(), 2);
        let ids: Vec<String> = merged(&paths).iter().map(|e| e["id"].as_str().unwrap().to_string()).collect();
        assert_eq!(ids, ["ev_1", "ev_2", "ev_3"]);
        let mut m = Matter::open(&dir).unwrap();
        assert!(!m.live("x") && m.live("y"));
        // a log arriving from a third installation is picked up
        fs::write(logs.join("o_C.jsonl"), ev("ev_4", "add-document", "x")).unwrap();
        m.refresh();
        assert!(m.live("x"));
        assert_eq!(m.state.order, ["x", "y"]);
        // C is removed from the pool, having written nothing that counts
        fs::create_dir_all(dir.join("pool")).unwrap();
        fs::write(dir.join("pool/cutoffs.json"), r#"{"v": 1, "feeds": {"store": {"o_C": 0}}}"#).unwrap();
        m.refresh();
        assert!(!m.live("x"));
        // a purge, and the same document added again: still gone
        fs::write(dir.join("pool/cutoffs.json"), "{}").unwrap();
        fs::write(logs.join("o_D.jsonl"), r#"{"id": "ev_5", "op": "purge", "author": "user:a", "docs": ["y"]}"#.to_string() + "\n"
            + &ev("ev_6", "add-document", "y")).unwrap();
        m.refresh();
        assert!(!m.live("y") && m.live("x"));
        fs::remove_dir_all(&dir).unwrap();
    }
}
