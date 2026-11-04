//! Read-only access to one matter: its object store and the state its logs
//! describe. The viewer's scope is exactly this matter's live documents; it
//! never opens a path it was not given at launch.

use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

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
    /// The host tool (`adhoc`), which fetches a limited copy's originals
    /// (`adhoc fetch-original`, adhocdocs.fetch). None: never fetch.
    pub fetch_cmd: Option<Vec<String>>,
    /// Originals being fetched, or that couldn't be lately. Shared with the
    /// threads that fetch, so nothing waits for one under the matter's lock.
    fetches: Arc<Mutex<HashMap<String, Fetch>>>,
    /// Told when a fetch ends, with the content and "here" or "failed", on the
    /// fetching thread: the viewer passes it on to every window's shell.
    pub on_fetched: Option<OnFetched>,
}

pub type OnFetched = Arc<dyn Fn(&str, &str) + Send + Sync>;

#[derive(Clone, Copy)]
enum Fetch {
    Running,
    Failed(Instant),
}

/// Where a limited copy's original stands (`/api/fetch`).
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum FetchState {
    Here,
    Fetching,
    /// couldn't be had lately: tried again after FETCH_RETRY
    Failed,
    /// not here, and not to be fetched (not a limited copy, or not an original)
    Absent,
}

impl FetchState {
    pub fn name(self) -> &'static str {
        match self {
            FetchState::Here => "here",
            FetchState::Fetching => "fetching",
            FetchState::Failed => "failed",
            FetchState::Absent => "absent",
        }
    }
}

/// How long an original that couldn't be fetched is left before trying again,
/// so a viewer with no hub in reach doesn't start a fetch on every look.
const FETCH_RETRY: Duration = Duration::from_secs(30);
/// How long a fetch may take before it's given up: a minute, and half a
/// minute more for each original in it.
const FETCH_WAIT: Duration = Duration::from_secs(60);
const FETCH_WAIT_EACH: Duration = Duration::from_secs(30);

fn locate(roots: &[PathBuf], h: &str) -> Option<PathBuf> {
    let h = hex(h);
    if h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    roots.iter().map(|r| r.join(&h[..2]).join(&h[2..])).find(|p| p.exists())
}

/// A command line given as a JSON list of arguments (a path may have spaces), or words.
pub fn command_parts(cmd: &str) -> Vec<String> {
    match serde_json::from_str::<Vec<String>>(cmd) {
        Ok(list) => list,
        Err(_) => cmd.split_whitespace().map(String::from).collect(),
    }
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
            fetch_cmd: None,
            fetches: Arc::new(Mutex::new(HashMap::new())),
            on_fetched: None,
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

    fn find(&self, h: &str) -> Option<PathBuf> {
        locate(&self.roots, h)
    }

    /// Where an object is, if it's here. A limited copy's original that isn't
    /// starts being fetched, in the background, and this answers None at once:
    /// nothing in the viewer waits on the network (`/api/fetch` says when it's
    /// here).
    fn path(&self, h: &str) -> Option<PathBuf> {
        if let Some(p) = self.find(h) {
            return Some(p);
        }
        let full = format!("sha256:{}", hex(h));
        if self.fetchable(&full) {
            self.start_fetch(vec![full]);
        }
        None
    }

    /// Whether this is (or was, and is still filling in) a limited copy:
    /// the host tool leaves `sync/was-limited` until every original is here.
    pub fn limited(&self) -> bool {
        self.fetch_cmd.is_some() && self.root.join("sync/was-limited").exists()
    }

    fn fetchable(&self, content: &str) -> bool {
        self.limited() && self.state.documents.values().any(|c| c == content)
    }

    /// The size of an object that's here, without fetching it.
    pub fn size_here(&self, h: &str) -> Option<u64> {
        fs::metadata(self.find(h)?).ok().map(|m| m.len())
    }

    /// Where an original stands. With `start`, one that isn't here (and
    /// didn't fail lately) starts being fetched.
    pub fn fetch_state(&self, content: &str, start: bool) -> FetchState {
        if self.find(content).is_some() {
            return FetchState::Here;
        }
        if !self.fetchable(content) {
            return FetchState::Absent;
        }
        if start {
            self.start_fetch(vec![content.to_string()]);
        }
        match self.fetches.lock().unwrap().get(content) {
            Some(Fetch::Running) => FetchState::Fetching,
            Some(Fetch::Failed(t)) if t.elapsed() < FETCH_RETRY => FetchState::Failed,
            _ => FetchState::Absent,
        }
    }

    /// Forget that an original couldn't be fetched, so it's tried again now
    /// (the user asked to), not after FETCH_RETRY.
    pub fn forget_failure(&self, content: &str) {
        let mut f = self.fetches.lock().unwrap();
        if matches!(f.get(content), Some(Fetch::Failed(_))) {
            f.remove(content);
        }
    }

    /// Start fetching these originals on a thread of its own: those that
    /// aren't here, aren't being fetched, and didn't fail lately. Returns how
    /// many it started on. Never waits.
    fn start_fetch(&self, contents: Vec<String>) -> usize {
        let Some((prog, rest)) = self.fetch_cmd.clone().and_then(|c| c.split_first().map(|(p, r)| (p.clone(), r.to_vec())))
        else { return 0 };
        let todo: Vec<String> = {
            let mut f = self.fetches.lock().unwrap();
            let todo: Vec<String> = contents
                .into_iter()
                .filter(|c| self.find(c).is_none())
                .filter(|c| match f.get(c) {
                    Some(Fetch::Running) => false,
                    Some(Fetch::Failed(t)) => t.elapsed() >= FETCH_RETRY,
                    None => true,
                })
                .collect();
            for c in &todo {
                f.insert(c.clone(), Fetch::Running);
            }
            todo
        };
        if todo.is_empty() {
            return 0;
        }
        let (root, roots, fetches, n) = (self.root.clone(), self.roots.clone(), self.fetches.clone(), todo.len());
        let on_fetched = self.on_fetched.clone();
        std::thread::spawn(move || {
            let limit = FETCH_WAIT + FETCH_WAIT_EACH * todo.len() as u32;
            let child = Command::new(&prog)
                .args(&rest)
                .args(["--matter", &root.to_string_lossy(), "fetch-original"])
                .args(&todo)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();
            if let Ok(mut child) = child {
                let start = Instant::now();
                loop {
                    match child.try_wait() {
                        Ok(Some(_)) => break,
                        Ok(None) if start.elapsed() < limit => std::thread::sleep(Duration::from_millis(50)),
                        _ => {
                            let _ = child.kill();
                            let _ = child.wait();
                            break;
                        }
                    }
                }
            }
            let mut ended = vec![];
            {
                let mut f = fetches.lock().unwrap();
                for c in todo {
                    if locate(&roots, &c).is_some() {
                        f.remove(&c);
                        ended.push((c, "here"));
                    } else {
                        f.insert(c.clone(), Fetch::Failed(Instant::now()));
                        ended.push((c, "failed"));
                    }
                }
            }
            // after the state is recorded, so a shell that asks on hearing this gets the same answer
            if let Some(tell) = on_fetched {
                for (c, state) in ended {
                    tell(&c, state);
                }
            }
        });
        n
    }

    /// Start fetching these documents' originals, if this is a limited copy
    /// and they aren't here, without waiting: what's opened next is ready.
    /// Returns how many it started on.
    pub fn prefetch(&self, docs: &[String]) -> usize {
        if !self.limited() {
            return 0;
        }
        let todo: Vec<String> = docs.iter().filter_map(|d| self.state.documents.get(d)).cloned().collect();
        self.start_fetch(todo)
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

    /// A layer's word boxes as a JSON array, for the page view. Stored boxes
    /// are one JSON object a line, so they are joined as they are, not parsed
    /// and written out again: a long document has tens of thousands, and
    /// they are asked for as it opens. A derived layer's are mapped (`geometry`).
    pub fn geometry_json(&self, layer_id: &str) -> String {
        let stored = self.object(layer_id).and_then(|o| o.get("geometry").and_then(|v| v.as_str()).map(String::from));
        if let Some(t) = stored.and_then(|g| self.text(&g)) {
            let mut out = String::with_capacity(t.len() + 2);
            out.push('[');
            for (i, line) in t.lines().map(str::trim).filter(|l| l.starts_with('{')).enumerate() {
                if i > 0 { out.push(','); }
                out.push_str(line);
            }
            out.push(']');
            return out;
        }
        Value::from(self.geometry(layer_id)).to_string()
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

    /// A matter with one document, whose original isn't here, and a stand-in
    /// for `adhoc` that says what it was asked, waits `delay`, and then writes
    /// the original where the store keeps it (or, with `fail`, doesn't).
    fn limited_matter(name: &str, delay: &str, fail: bool) -> (PathBuf, Matter, String) {
        let dir = std::env::temp_dir().join(format!("adhoc-fetch-{}-{}", name, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("store/objects")).unwrap();
        fs::create_dir_all(dir.join("store/logs")).unwrap();
        fs::create_dir_all(dir.join("sync")).unwrap();
        let content = format!("sha256:{}", "ab".repeat(32));
        let ev = serde_json::json!({"id": "1", "op": "add-document", "doc": format!("sha256:{}", "cd".repeat(32)),
                                    "content": content, "at": "1"});
        fs::write(dir.join("store/logs/o_x.jsonl"), format!("{}\n", ev)).unwrap();
        let write = if fail { "exit 1".to_string() } else {
            format!("mkdir -p {o}/ab && printf original > {o}/ab/{rest}", o = dir.join("store/objects").display(),
                    rest = "ab".repeat(31))
        };
        let script = dir.join("fake-adhoc.sh");
        fs::write(&script, format!("#!/bin/sh\necho \"$@\" >> {log}\nsleep {delay}\n{write}\n",
                                   log = dir.join("asked").display())).unwrap();
        let mut m = Matter::open(&dir).unwrap();
        m.fetch_cmd = Some(vec!["sh".into(), script.to_string_lossy().into_owned()]);
        (dir, m, content)
    }

    /// Record what `on_fetched` is told.
    fn listen(m: &mut Matter) -> Arc<Mutex<Vec<(String, String)>>> {
        let heard = Arc::new(Mutex::new(vec![]));
        let h = heard.clone();
        m.on_fetched = Some(Arc::new(move |c: &str, s: &str| h.lock().unwrap().push((c.to_string(), s.to_string()))));
        heard
    }

    fn eventually(m: &Matter, content: &str, want: FetchState) -> bool {
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(10) {
            if m.fetch_state(content, false) == want {
                return true;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        false
    }

    #[test]
    fn a_limited_copy_fetches_a_missing_original_without_waiting_for_it() {
        let (dir, mut m, content) = limited_matter("ok", "1", false);
        let heard = listen(&mut m);
        assert!(m.bytes(&content).is_none(), "a full copy (no sync/was-limited) doesn't fetch");
        assert_eq!(m.fetch_state(&content, false), FetchState::Absent);
        assert!(!dir.join("asked").exists());
        fs::write(dir.join("sync/was-limited"), "").unwrap();
        // the fetch takes a second: reading answers at once, and the fetch goes on
        let t = Instant::now();
        assert!(m.bytes(&content).is_none());
        assert!(t.elapsed() < Duration::from_millis(500), "reading waited for the fetch");
        assert_eq!(m.fetch_state(&content, false), FetchState::Fetching);
        assert!(m.bytes(&content).is_none());          // asked again meanwhile: not fetched twice
        assert!(eventually(&m, &content, FetchState::Here));
        assert_eq!(m.bytes(&content).as_deref(), Some(&b"original"[..]));
        // the viewer was told, once, and only once the fetch had ended
        let start = Instant::now();
        while heard.lock().unwrap().is_empty() && start.elapsed() < Duration::from_secs(5) {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(*heard.lock().unwrap(), vec![(content.clone(), "here".to_string())]);
        let asked = fs::read_to_string(dir.join("asked")).unwrap();
        assert_eq!(asked.lines().count(), 1);
        assert!(asked.contains("fetch-original") && asked.contains(&content));
        // not an original: never asked for
        assert!(m.bytes(&format!("sha256:{}", "ef".repeat(32))).is_none());
        assert_eq!(m.fetch_state(&format!("sha256:{}", "ef".repeat(32)), true), FetchState::Absent);
        assert_eq!(fs::read_to_string(dir.join("asked")).unwrap().lines().count(), 1);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_original_that_cant_be_had_isnt_asked_for_again_at_once() {
        let (dir, mut m, content) = limited_matter("fail", "0", true);
        let heard = listen(&mut m);
        fs::write(dir.join("sync/was-limited"), "").unwrap();
        assert_eq!(m.fetch_state(&content, true), FetchState::Fetching);
        assert!(eventually(&m, &content, FetchState::Failed));
        let start = Instant::now();
        while heard.lock().unwrap().is_empty() && start.elapsed() < Duration::from_secs(5) {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(*heard.lock().unwrap(), vec![(content.clone(), "failed".to_string())]);
        assert!(m.bytes(&content).is_none());
        assert_eq!(m.prefetch(&[format!("sha256:{}", "cd".repeat(32))]), 0);
        assert_eq!(fs::read_to_string(dir.join("asked")).unwrap().lines().count(), 1);
        // unless the user asks to try again
        m.forget_failure(&content);
        assert_eq!(m.fetch_state(&content, true), FetchState::Fetching);
        assert!(eventually(&m, &content, FetchState::Failed));
        assert_eq!(fs::read_to_string(dir.join("asked")).unwrap().lines().count(), 2);
        let _ = fs::remove_dir_all(&dir);
    }

    fn ev(id: &str, op: &str, doc: &str) -> String {
        format!("{{\"id\": \"{}\", \"op\": \"{}\", \"doc\": \"{}\", \"content\": \"c-{}\"}}\n", id, op, doc, doc)
    }

    #[test]
    fn geometry_is_served_as_stored() {
        // the fast path (lines joined, not parsed) gives what parsing would
        let dir = std::env::temp_dir().join(format!("adhoc-geo-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("store/logs")).unwrap();
        let put = |h: &str, body: &str| {
            let d = dir.join("store/objects").join(&h[7..9]);
            fs::create_dir_all(&d).unwrap();
            fs::write(d.join(&h[9..]), body).unwrap();
        };
        let (layer, geo) = (format!("sha256:{}", "11".repeat(32)), format!("sha256:{}", "22".repeat(32)));
        put(&geo, "{\"start\": 0, \"end\": 3, \"page\": 1, \"box\": [0.1, 0.1, 0.2, 0.12]}\n\
                   {\"start\": 4, \"end\": 9, \"page\": 2, \"box\": [0.1, 0.2, 0.3, 0.22], \"conf\": 0.9}\n");
        put(&layer, &serde_json::json!({"geometry": geo}).to_string());
        let m = Matter::open(&dir).unwrap();
        let fast: Value = serde_json::from_str(&m.geometry_json(&layer)).unwrap();
        assert_eq!(fast, Value::from(m.geometry(&layer)));
        assert_eq!(fast.as_array().unwrap().len(), 2);
        assert_eq!(m.geometry_json(&format!("sha256:{}", "33".repeat(32))), "[]");      // no such layer
        let _ = fs::remove_dir_all(&dir);
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
