//! adhoc-viewer: shows one matter's documents. Launched on the host by the
//! connect mechanism, never by Claude. Claude drives it through a small API
//! on a Unix socket, or on Windows and macOS a port with a token (`view`
//! CLI in the container; see endpoint.rs).
//!
//! Each window is one web view running the viewer's own code (shell/), with
//! its own panes. The host routes each API command to the window that has
//! the pane it names (else the one the user last focused), merges state
//! from all of them, and relays calls between panes in different windows,
//! so a list in one window can drive a document pane in another.
//! Document pages are rendered natively by MuPDF and served as images; all
//! other HTML (presentations, pages, lists, Claude's panes) is shown in
//! sandboxed iframes where no script can run.

mod api;
mod endpoint;
mod matter;
mod overview;
mod protocol;
mod render;

use matter::Matter;
use protocol::{Handler, Panes, Response};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy, EventLoopWindowTarget};
use tao::window::{Window, WindowBuilder, WindowId};
use wry::http::header::{CONTENT_SECURITY_POLICY, CONTENT_TYPE};
use wry::WebViewBuilder;

pub enum UserEvent {
    /// A command from the API, with where to send the result.
    Command(Value, mpsc::Sender<Value>),
    /// A message from a window's shell: the window and the message.
    Ipc(String, String),
    /// A service's answer for a pane: the window, the request ID and the result.
    Service(String, Value, Value),
}

struct Args {
    matter: PathBuf,
    socket: Option<PathBuf>,
    user: String,
    annotate: Option<String>,
}

fn args() -> Result<Args, String> {
    let mut a = Args { matter: PathBuf::new(), socket: None, user: std::env::var("USER").or_else(|_| std::env::var("USERNAME")).unwrap_or("user".into()), annotate: None };
    let mut it = std::env::args().skip(1);
    while let Some(x) = it.next() {
        match x.as_str() {
            "--matter" => a.matter = it.next().ok_or("--matter needs a path")?.into(),
            "--socket" => a.socket = Some(it.next().ok_or("--socket needs a path")?.into()),
            "--user" => a.user = it.next().ok_or("--user needs an id")?,
            "--annotate" => a.annotate = Some(it.next().ok_or("--annotate needs a command")?),
            "-h" | "--help" => {
                println!("adhoc-viewer --matter <dir> [--socket <path>] [--user <id>] [--annotate <cmd>]");
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument {}", x)),
        }
    }
    if a.matter.as_os_str().is_empty() {
        return Err("--matter is required".into());
    }
    Ok(a)
}

fn http_response(r: Response) -> wry::http::Response<Vec<u8>> {
    let mut b = wry::http::Response::builder().status(r.status).header(CONTENT_TYPE, r.mime);
    if r.csp {
        b = b.header(CONTENT_SECURITY_POLICY, protocol::CSP);
    }
    b.body(r.body).unwrap()
}

type Job = (String, String, wry::RequestAsyncResponder);

/// One top-level window: its web view, and commands waiting for it to load.
struct Win {
    window: Window,
    webview: wry::WebView,
    ready: bool,
    queued: Vec<(u64, Value)>,
}

/// What to do with a command's result when its window answers.
enum Waiter {
    /// Answer the API.
    Reply(mpsc::Sender<Value>),
    /// One window's part of a command sent to all of them.
    Part(u64),
    /// A pane taken out of its window: put it in window `to`.
    Detach { to: String, reply: Option<mpsc::Sender<Value>> },
}

impl Waiter {
    /// For results nobody waits on.
    fn ignore() -> Waiter {
        Waiter::Reply(mpsc::channel().0)
    }
}

/// A command sent to every window, answered when all have.
struct Gather {
    cmd: Value,
    remaining: usize,
    parts: Vec<(String, Value)>,
    reply: mpsc::Sender<Value>,
}

struct App {
    windows: BTreeMap<String, Win>,
    by_tao: HashMap<WindowId, String>,
    focused: String,
    next_window: u64,
    /// pane ID -> (window, kind), from the windows' state messages
    owners: BTreeMap<String, (String, String)>,
    waiting: HashMap<u64, (String, Waiter)>,
    gathers: HashMap<u64, Gather>,
    /// where the user (and Claude, and panes) went, in every window, kept here so closing a window keeps it
    history: std::collections::VecDeque<Value>,
    seq: u64,
    title: String,
    user: String,
    job_tx: mpsc::Sender<Job>,
    proxy: EventLoopProxy<UserEvent>,
}

impl App {
    fn open_window(&mut self, target: &EventLoopWindowTarget<UserEvent>) -> String {
        self.next_window += 1;
        let id = format!("w{}", self.next_window);
        let window = WindowBuilder::new()
            .with_title(if id == "w1" { self.title.clone() } else { format!("{} ({})", self.title, id) })
            .with_inner_size(tao::dpi::LogicalSize::new(1400.0, 900.0))
            .build(target)
            .unwrap();
        let job_tx = Mutex::new(self.job_tx.clone());
        let (ipc_proxy, ipc_id) = (self.proxy.clone(), id.clone());
        let builder = WebViewBuilder::new()
            .with_asynchronous_custom_protocol("adhoc".into(), move |_id, req, responder| {
                let path = req.uri().path().to_string();
                let q = req.uri().query().unwrap_or("").to_string();
                let _ = job_tx.lock().unwrap().send((path, q, responder));
            })
            .with_ipc_handler(move |req| {
                let _ = ipc_proxy.send_event(UserEvent::Ipc(ipc_id.clone(), req.body().clone()));
            })
            .with_navigation_handler(|url| url.starts_with("adhoc:") || url.starts_with("http://adhoc.localhost") || url == "about:blank")
            .with_new_window_req_handler(|_url, _features| wry::NewWindowResponse::Deny)
            .with_devtools(std::env::var("ADHOC_VIEWER_DEVTOOLS").is_ok())
            .with_url(if cfg!(windows) { "http://adhoc.localhost/" } else { "adhoc://localhost/" });
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        let webview = {
            use tao::platform::unix::WindowExtUnix;
            use wry::WebViewBuilderExtUnix;
            builder.build_gtk(window.default_vbox().unwrap()).unwrap()
        };
        #[cfg(any(target_os = "windows", target_os = "macos"))]
        let webview = builder.build(&window).unwrap();
        self.by_tao.insert(window.id(), id.clone());
        self.windows.insert(id.clone(), Win { window, webview, ready: false, queued: vec![] });
        self.focused = id.clone();
        id
    }

    /// Send a command to one window's shell (or queue it until the shell is up).
    fn send(&mut self, win: &str, cmd: Value, waiter: Waiter) {
        let Some(w) = self.windows.get_mut(win) else {
            return self.fail(waiter, &format!("no window {}", win));
        };
        self.seq += 1;
        self.waiting.insert(self.seq, (win.to_string(), waiter));
        if w.ready {
            let _ = w.webview.evaluate_script(&format!("adhoc.command({}, {})", self.seq, cmd));
        } else {
            w.queued.push((self.seq, cmd));
        }
    }

    fn eval(&self, win: &str, script: &str) {
        if let Some(w) = self.windows.get(win) {
            let _ = w.webview.evaluate_script(script);
        }
    }

    fn fail(&mut self, waiter: Waiter, why: &str) {
        match waiter {
            Waiter::Reply(tx) => { let _ = tx.send(json!({"error": why})); }
            Waiter::Detach { reply: Some(tx), .. } => { let _ = tx.send(json!({"error": why})); }
            Waiter::Detach { reply: None, .. } => {}
            Waiter::Part(g) => self.part(g, None),
        }
    }

    fn owner(&self, pane: &str) -> Option<String> {
        self.owners.get(pane).map(|(w, _)| w.clone())
    }

    /// An API command: some are the host's own, the rest go to a window.
    fn dispatch(&mut self, target: &EventLoopWindowTarget<UserEvent>, mut cmd: Value, reply: mpsc::Sender<Value>) {
        let name = cmd["cmd"].as_str().unwrap_or("").to_string();
        let pane_arg = ["pane", "update"].iter().find_map(|k| cmd[*k].as_str().map(String::from));
        match name.as_str() {
            "window-new" => {
                if let Some(p) = pane_arg.as_deref().filter(|p| self.owner(p).is_none()) {
                    let _ = reply.send(json!({"error": format!("no pane {}", p)}));
                    return;
                }
                let w = self.open_window(target);
                if let Some(layout) = cmd.get("layout").cloned() {
                    self.send(&w, json!({"cmd": "layout", "layout": layout}), Waiter::ignore());
                }
                match pane_arg {
                    Some(p) => self.move_pane(&p, &w, Some(reply)),
                    None => { let _ = reply.send(json!({"window": w})); }
                }
            }
            "window-move" => {
                let (Some(p), Some(w)) = (pane_arg, cmd["window"].as_str().map(String::from)) else {
                    let _ = reply.send(json!({"error": "window-move needs a pane and a window"}));
                    return;
                };
                self.move_pane(&p, &w, Some(reply));
            }
            "window-close" => {
                let w = cmd["window"].as_str().unwrap_or("").to_string();
                let result = if !self.windows.contains_key(&w) {
                    json!({"error": format!("no window {}", w)})
                } else if self.windows.len() == 1 {
                    json!({"error": "that is the only window; the user closes the viewer"})
                } else {
                    self.close_window(&w);
                    json!({})
                };
                let _ = reply.send(result);
            }
            "history" => {
                let limit = cmd["limit"].as_u64().unwrap_or(50) as usize;
                let h: Vec<&Value> = self.history.iter().skip(self.history.len().saturating_sub(limit)).collect();
                let _ = reply.send(json!({"history": h}));
            }
            "state" => self.gather(cmd, reply),
            "clear-marks" | "clear-highlights" if pane_arg.is_none() => self.gather(cmd, reply),
            _ => {
                let win = if let Some(p) = &pane_arg {
                    match self.owner(p) {
                        Some(w) => w,
                        None => { let _ = reply.send(json!({"error": format!("no pane {}", p)})); return; }
                    }
                } else if cmd["new_window"].as_bool().unwrap_or(false) {
                    cmd.as_object_mut().unwrap().remove("new_window");
                    self.open_window(target)
                } else if let Some(w) = cmd["window"].as_str() {
                    w.to_string()
                } else {
                    self.focused.clone()
                };
                self.send(&win, cmd, Waiter::Reply(reply));
            }
        }
    }

    fn gather(&mut self, cmd: Value, reply: mpsc::Sender<Value>) {
        self.seq += 1;
        let g = self.seq;
        let wins: Vec<String> = self.windows.keys().cloned().collect();
        self.gathers.insert(g, Gather { cmd: cmd.clone(), remaining: wins.len(), parts: vec![], reply });
        for w in wins {
            self.send(&w, cmd.clone(), Waiter::Part(g));
        }
    }

    /// One window's answer to a gathered command (None: the window went away).
    fn part(&mut self, g: u64, part: Option<(String, Value)>) {
        let Some(gather) = self.gathers.get_mut(&g) else { return };
        gather.remaining -= 1;
        gather.parts.extend(part);
        if gather.remaining > 0 {
            return;
        }
        let gather = self.gathers.remove(&g).unwrap();
        let _ = gather.reply.send(self.merge(&gather));
    }

    fn merge(&self, g: &Gather) -> Value {
        match g.cmd["cmd"].as_str().unwrap_or("") {
            "state" => {
                let (mut windows, mut panes, mut focused) = (vec![], vec![], Value::Null);
                let mut parts: Vec<&(String, Value)> = g.parts.iter().collect();
                parts.sort_by_key(|(w, _)| w[1..].parse::<u64>().unwrap_or(0));     // w1, w2, … w10
                for (w, part) in parts {
                    for x in part["windows"].as_array().into_iter().flatten() {
                        let mut x = x.clone();
                        x["focused_pane"] = part["focused"].clone();
                        windows.push(x);
                    }
                    panes.extend(part["panes"].as_array().cloned().unwrap_or_default());
                    if *w == self.focused {
                        focused = part["focused"].clone();
                    }
                }
                json!({"running": true, "focused": focused, "focused_window": self.focused, "windows": windows, "panes": panes})
            }
            _ => json!({}),
        }
    }

    /// Take a pane out of its window and put it in window `to`.
    fn move_pane(&mut self, pane: &str, to: &str, reply: Option<mpsc::Sender<Value>>) {
        let Some(from) = self.owner(pane) else {
            if let Some(tx) = reply { let _ = tx.send(json!({"error": format!("no pane {}", pane)})); }
            return;
        };
        if !self.windows.contains_key(to) {
            if let Some(tx) = reply { let _ = tx.send(json!({"error": format!("no window {}", to)})); }
            return;
        }
        self.send(&from, json!({"cmd": "detach", "pane": pane}), Waiter::Detach { to: to.to_string(), reply });
    }

    fn close_window(&mut self, w: &str) {
        let Some(win) = self.windows.remove(w) else { return };
        self.by_tao.remove(&win.window.id());
        drop(win);
        let gone: Vec<u64> = self.waiting.iter().filter(|(_, (x, _))| x == w).map(|(s, _)| *s).collect();
        for s in gone {
            let (_, waiter) = self.waiting.remove(&s).unwrap();
            self.fail(waiter, "the window was closed");
        }
        self.owners.retain(|_, (x, _)| x != w);
        if self.focused == w {
            self.focused = self.windows.keys().next().cloned().unwrap_or_default();
        }
        self.broadcast_panes();
    }

    /// Every window learns which panes the others have, for links between windows.
    fn broadcast_panes(&self) {
        let map: serde_json::Map<String, Value> = self.owners.iter()
            .map(|(p, (w, k))| (p.clone(), json!({"window": w, "kind": k}))).collect();
        let script = format!("adhoc.elsewhere({})", Value::Object(map));
        for w in self.windows.values().filter(|w| w.ready) {
            let _ = w.webview.evaluate_script(&script);
        }
    }

    fn result(&mut self, win: &str, seq: u64, mut result: Value, matter: &Mutex<Matter>) {
        let Some((_, waiter)) = self.waiting.remove(&seq) else { return };
        match waiter {
            Waiter::Reply(tx) => {
                api::add_quotes(&matter.lock().unwrap(), &mut result);
                let _ = tx.send(result);
            }
            Waiter::Part(g) => {
                api::add_quotes(&matter.lock().unwrap(), &mut result);
                self.part(g, Some((win.to_string(), result)));
            }
            Waiter::Detach { to, reply } => {
                if result.get("error").is_some() {
                    if let Some(tx) = reply { let _ = tx.send(result); }
                    return;
                }
                // The pane belongs to its new window from now on, before that window reports.
                if let (Some(id), Some(kind)) = (result["id"].as_str(), result["kind"].as_str()) {
                    self.owners.insert(id.to_string(), (to.clone(), kind.to_string()));
                }
                // None: the user tore the pane off, and nobody waits for the answer
                let waiter = reply.map(Waiter::Reply).unwrap_or_else(Waiter::ignore);
                self.send(&to, json!({"cmd": "adopt", "spec": result}), waiter);
            }
        }
    }
}

fn main() {
    let a = match args() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{}", json!({"error": e}));
            std::process::exit(2);
        }
    };
    let matter = match Matter::open(&a.matter) {
        Ok(m) => Arc::new(Mutex::new(m)),
        Err(e) => {
            eprintln!("{}", json!({"error": e}));
            std::process::exit(2);
        }
    };
    let panes: Panes = Arc::new(Mutex::new(HashMap::new()));

    // One worker owns MuPDF and answers every adhoc: request, from every window, in order.
    let (job_tx, job_rx) = mpsc::channel::<Job>();
    {
        let (matter, panes) = (matter.clone(), panes.clone());
        std::thread::spawn(move || {
            let mut h = Handler::new(matter, panes);
            for (path, q, responder) in job_rx {
                let t = std::time::Instant::now();
                let r = h.handle(&path, &q);
                if r.status != 200 || std::env::var("ADHOC_VIEWER_DEBUG").is_ok() {
                    eprintln!("{}", json!({"request": path, "status": r.status, "ms": t.elapsed().as_secs_f64() * 1000.0,
                        "error": if r.status != 200 { String::from_utf8_lossy(&r.body).to_string() } else { String::new() }}));
                }
                responder.respond(http_response(r));
            }
        });
    }

    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let root_name = a.matter.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let mut app = App {
        windows: BTreeMap::new(), by_tao: HashMap::new(), focused: String::new(), next_window: 0,
        owners: BTreeMap::new(), waiting: HashMap::new(), gathers: HashMap::new(), history: Default::default(), seq: 0,
        title: format!("adhocdocs: {}", root_name), user: a.user.clone(), job_tx, proxy: proxy.clone(),
    };
    app.open_window(&event_loop);

    // The API: commands go to the shells, results come back over IPC.
    let socket = a.socket.clone().unwrap_or_else(|| api::default_socket(&matter.lock().unwrap().root));
    api::serve(socket.clone(), proxy.clone(), panes.clone());
    eprintln!("{}", json!({"viewer": "running", "socket": socket}));
    let annotate = a.annotate.clone();

    event_loop.run(move |event, target, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent { window_id, event: WindowEvent::CloseRequested, .. } => {
                let Some(w) = app.by_tao.get(&window_id).cloned() else { return };
                if app.windows.len() > 1 {
                    app.close_window(&w);
                } else {
                    let _ = std::fs::remove_file(&socket);
                    *control_flow = ControlFlow::Exit
                }
            }
            Event::WindowEvent { window_id, event: WindowEvent::Focused(true), .. } => {
                if let Some(w) = app.by_tao.get(&window_id) {
                    app.focused = w.clone();
                }
            }
            Event::UserEvent(UserEvent::Command(cmd, reply)) => app.dispatch(target, cmd, reply),
            Event::UserEvent(UserEvent::Service(win, id, result)) => {
                app.eval(&win, &format!("adhoc.served({}, {})", id, result));
            }
            Event::UserEvent(UserEvent::Ipc(win, msg)) => {
                let Ok(v) = serde_json::from_str::<Value>(&msg) else { return };
                match v["type"].as_str().unwrap_or("") {
                    "ready" => {
                        let Some(w) = app.windows.get_mut(&win) else { return };
                        w.ready = true;
                        let _ = w.webview.evaluate_script(&format!("adhoc.config({})",
                            json!({"user": format!("user:{}", app.user), "window": win})));
                        for (seq, cmd) in w.queued.drain(..) {
                            let _ = w.webview.evaluate_script(&format!("adhoc.command({}, {})", seq, cmd));
                        }
                        app.broadcast_panes();
                    }
                    "result" => app.result(&win, v["seq"].as_u64().unwrap_or(0), v["result"].clone(), &matter),
                    "state" => {
                        // which panes this window has now
                        let mut mine = BTreeMap::new();
                        for p in v["state"]["panes"].as_array().into_iter().flatten() {
                            if let Some(id) = p["id"].as_str() {
                                mine.insert(id.to_string(), (win.clone(), p["kind"].as_str().unwrap_or("").to_string()));
                            }
                        }
                        let before: Vec<_> = app.owners.iter().filter(|(_, (w, _))| *w == win).map(|(p, x)| (p.clone(), x.clone())).collect();
                        let now: Vec<_> = mine.iter().map(|(p, x)| (p.clone(), x.clone())).collect();
                        if before != now {
                            app.owners.retain(|_, (w, _)| *w != win);
                            app.owners.extend(mine);
                            app.broadcast_panes();
                        }
                    }
                    "history" => {
                        let mut entry = v["entry"].clone();
                        entry["window"] = json!(win);
                        // other windows' lists mark what was opened here as visited
                        for (id, w) in &app.windows {
                            if *id != win && w.ready {
                                let _ = w.webview.evaluate_script(&format!("adhoc.visited({})", entry));
                            }
                        }
                        app.history.push_back(entry);
                        if app.history.len() > 1000 {
                            app.history.pop_front();
                        }
                    }
                    "relay" => {
                        // a call on a pane that lives in another window
                        let Some(owner) = v["pane"].as_str().and_then(|p| app.owner(p)) else { return };
                        app.eval(&owner, &format!("adhoc.relayed({}, {}, {})", v["pane"], v["method"], v["args"]));
                    }
                    "tearoff" => {
                        // the user moves a pane into a new window of its own
                        let Some(p) = v["pane"].as_str().map(String::from) else { return };
                        if app.owner(&p).is_none() { return }
                        let w = app.open_window(target);
                        app.move_pane(&p, &w, None);
                    }
                    "svc" => {
                        // A pane's form, submitted to a service Claude runs in the container.
                        let (dir, panes, proxy) = (socket.parent().map(|p| p.to_path_buf()), panes.clone(), proxy.clone());
                        let (id, name, values) = (v["id"].clone(), v["name"].as_str().unwrap_or("").to_string(), v["values"].clone());
                        let win = win.clone();
                        std::thread::spawn(move || {
                            let result = match dir {
                                Some(d) => api::call_service(&d, &name, &values, &panes),
                                None => json!({"error": "no socket directory"}),
                            };
                            let _ = proxy.send_event(UserEvent::Service(win, id, result));
                        });
                    }
                    "annotate" => {
                        // The user's own tags and notes, written as the user by the host tool.
                        let result = api::annotate(annotate.as_deref(), &matter.lock().unwrap().root, &app.user, &v["request"]);
                        app.eval(&win, &format!("adhoc.annotated({}, {})", v["id"], result));
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    });
}
