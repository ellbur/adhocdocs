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
}

pub struct Matter {
    pub root: PathBuf,
    roots: Vec<PathBuf>,
    logs: Vec<PathBuf>,
    log_sizes: Vec<u64>,
    pub state: State,
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
            logs: vec![root.join("store/log.jsonl"), root.join("workspace/.adhoc/log.jsonl")],
            log_sizes: vec![],
            root,
            state: State::default(),
        };
        m.refresh();
        Ok(m)
    }

    /// Replay the logs if either has grown since the last look.
    pub fn refresh(&mut self) {
        let sizes: Vec<u64> = self.logs.iter().map(|p| fs::metadata(p).map(|m| m.len()).unwrap_or(0)).collect();
        if sizes == self.log_sizes {
            return;
        }
        let mut s = State::default();
        for log in &self.logs {
            let Ok(text) = fs::read_to_string(log) else { continue };
            for line in text.lines().filter(|l| !l.trim().is_empty()) {
                if let Ok(ev) = serde_json::from_str::<Value>(line) {
                    apply(&mut s, &ev);
                }
            }
        }
        self.state = s;
        self.log_sizes = sizes;
    }

    fn path(&self, h: &str) -> Option<PathBuf> {
        let h = hex(h);
        if h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        self.roots.iter().map(|r| r.join(&h[..2]).join(&h[2..])).find(|p| p.exists())
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
            if !s.documents.contains_key(&doc) {
                s.order.push(doc.clone());
            }
            s.documents.insert(doc.clone(), str_("content"));
            s.withdrawn.remove(&doc);
            let parent = str_("parent");
            if !parent.is_empty() {
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
        _ => {}
    }
}
