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
//!
//! With `--run`, the viewer is also the session's terminal (term.rs): it runs
//! the session's command on a pseudo-terminal and shows it beside the
//! documents or in a window of its own, for when `adhoc open` was started
//! without a terminal (from the launcher, `--launcher`: launcher.rs).

mod api;
mod chrono;
mod endpoint;
mod matter;
mod overview;
mod export;
mod launcher;
mod term;
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
    /// A download's outcome: the window, the request ID and the result.
    Exported(String, Value, Value),
    /// The terminal's output, and its program's exit code.
    TermOut(Vec<u8>),
    TermExit(u32),
}

struct Args {
    matter: PathBuf,
    socket: Option<PathBuf>,
    user: String,
    annotate: Option<String>,
    /// the session's command, run in the viewer's own terminal
    run: Vec<String>,
    /// shown in the terminal before the command starts
    banner: String,
    /// where the terminal starts: "beside" the documents, or in a "window" of its own
    dock: String,
    launcher: bool,
}

fn args() -> Result<Args, String> {
    let mut a = Args { matter: PathBuf::new(), socket: None, user: std::env::var("USER").or_else(|_| std::env::var("USERNAME")).unwrap_or("user".into()), annotate: None,
                      run: vec![], banner: String::new(), dock: "beside".into(), launcher: false };
    let mut it = std::env::args().skip(1);
    while let Some(x) = it.next() {
        match x.as_str() {
            "--matter" => a.matter = it.next().ok_or("--matter needs a path")?.into(),
            "--socket" => a.socket = Some(it.next().ok_or("--socket needs a path")?.into()),
            "--user" => a.user = it.next().ok_or("--user needs an id")?,
            "--annotate" => a.annotate = Some(it.next().ok_or("--annotate needs a command")?),
            "--banner" => a.banner = it.next().ok_or("--banner needs text")?,
            "--launcher" => a.launcher = true,
            "--dock" => a.dock = it.next().ok_or("--dock needs beside or window")?,
            "--run" => { a.run = it.by_ref().collect(); }
            "-h" | "--help" => {
                println!("adhoc-viewer --matter <dir> [--socket <path>] [--user <id>] [--annotate <cmd>] [--banner <text>] [--run <command>…]\n\
                          adhoc-viewer --launcher --annotate <adhoc command>");
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument {}", x)),
        }
    }
    if a.matter.as_os_str().is_empty() && !a.launcher {
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

/// Work for the worker that owns MuPDF: an adhoc: request, or a download
/// the user asked for (answered to the window by its request ID).
enum Job {
    Request(String, String, wry::RequestAsyncResponder),
    Export(String, Value, Value),
}

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
    term: Option<Session>,
}

/// The session's terminal, when the viewer runs it (`--run`).
struct Session {
    argv: Vec<String>,
    banner: String,
    /// started when the first window says how big the terminal is
    term: Option<term::Term>,
    /// the window showing it, and the window made for it alone (if it has one)
    win: String,
    alone: Option<String>,
    attached_once: bool,
    ended_by_user: bool,
}

impl Session {
    fn running(&self) -> bool {
        self.term.as_ref().map_or(true, |t| t.exited.is_none())
    }
    fn exit_code(&self) -> i32 {
        self.term.as_ref().and_then(|t| t.exited).unwrap_or(0) as i32
    }
}

fn b64(bytes: &[u8]) -> String {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

impl App {
    fn open_window(&mut self, target: &EventLoopWindowTarget<UserEvent>) -> String {
        self.open_window_as(target, None, (1400.0, 900.0))
    }

    fn open_window_as(&mut self, target: &EventLoopWindowTarget<UserEvent>, title: Option<String>, size: (f64, f64)) -> String {
        self.next_window += 1;
        let id = format!("w{}", self.next_window);
        let window = WindowBuilder::new()
            .with_title(title.unwrap_or_else(|| if id == "w1" { self.title.clone() } else { format!("{} ({})", self.title, id) }))
            .with_inner_size(tao::dpi::LogicalSize::new(size.0, size.1))
            .build(target)
            .unwrap();
        let job_tx = Mutex::new(self.job_tx.clone());
        let (ipc_proxy, ipc_id) = (self.proxy.clone(), id.clone());
        let builder = WebViewBuilder::new()
            .with_asynchronous_custom_protocol("adhoc".into(), move |_id, req, responder| {
                let path = req.uri().path().to_string();
                let q = req.uri().query().unwrap_or("").to_string();
                let _ = job_tx.lock().unwrap().send(Job::Request(path, q, responder));
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

    // -- the session's terminal ---------------------------------------------------------------

    /// Show the terminal in its window, redrawn from its output so far.
    fn term_attach(&mut self) {
        let Some(s) = self.term.as_mut() else { return };
        let info = json!({
            "title": "Claude Code", "alone": s.alone.as_deref() == Some(s.win.as_str()), "first": !s.attached_once,
            "exited": s.term.as_ref().and_then(|t| t.exited).filter(|c| *c != 0).map(|c| json!({"code": c})),
        });
        s.attached_once = true;
        let replay = s.term.as_ref().map(|t| b64(t.replay())).unwrap_or_default();
        let win = s.win.clone();
        if self.windows.get(&win).is_some_and(|w| w.ready) {
            self.eval(&win, &format!("adhocTerm.attach({}, {})", json!(replay), info));
        }
    }

    /// Move the terminal: into a new window of its own, or back beside the documents.
    fn term_move(&mut self, target: &EventLoopWindowTarget<UserEvent>, to: &str) {
        let Some(s) = self.term.as_ref() else { return };
        let from = s.win.clone();
        let dest = if to == "window" {
            if s.alone.as_deref() == Some(from.as_str()) { return }
            let title = format!("{}: Claude Code", self.title);
            let w = self.open_window_as(target, Some(title), (900.0, 860.0));
            self.term.as_mut().unwrap().alone = Some(w.clone());
            // the documents' window stays the one Claude's commands go to
            self.focused = from.clone();
            w
        } else {
            let others = |w: &&String| **w != from && Some(w.as_str()) != s.alone.as_deref();
            let Some(w) = self.windows.keys().find(others).or_else(|| s.alone.as_ref().filter(|a| **a != from)).cloned() else { return };
            w
        };
        self.eval(&from, "adhocTerm.detach()");
        self.term.as_mut().unwrap().win = dest.clone();
        self.term_attach();
        if to != "window" {
            let alone = self.term.as_mut().unwrap().alone.take();
            // the window made for the terminal goes, unless documents were moved into it
            if let Some(w) = alone.filter(|w| !self.owners.values().any(|(x, _)| x == w)) {
                self.close_window(&w);
            }
        }
    }

    /// The terminal's program ended.
    fn term_exited(&mut self, code: u32) {
        let Some(s) = self.term.as_mut() else { return };
        if let Some(t) = s.term.as_mut() { t.exited = Some(code) }
        if code == 0 || s.ended_by_user {
            quit(code as i32);
        }
        let win = s.win.clone();
        let msg = format!("The session ended with an error (exit {}). Its output is above; close the window when you're done.", code);
        self.eval(&win, &format!("adhocTerm.exited({}, {})", code, json!(msg)));
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

/// Leave, with the session's exit code (the launcher passes it on).
fn quit(code: i32) -> ! {
    if let Some(s) = SOCKET.get() {
        let _ = std::fs::remove_file(s);
    }
    std::process::exit(code)
}

static SOCKET: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();

fn main() {
    let a = match args() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{}", json!({"error": e}));
            std::process::exit(2);
        }
    };
    if a.launcher {
        launcher::run(a.annotate.unwrap_or_default());
    }
    let matter = match Matter::open(&a.matter) {
        Ok(m) => Arc::new(Mutex::new(m)),
        Err(e) => {
            eprintln!("{}", json!({"error": e}));
            std::process::exit(2);
        }
    };
    let panes: Panes = Arc::new(Mutex::new(HashMap::new()));

    // One worker owns MuPDF and answers every adhoc: request, from every window, in order.
    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let (job_tx, job_rx) = mpsc::channel::<Job>();
    {
        let (matter, panes, proxy) = (matter.clone(), panes.clone(), proxy.clone());
        std::thread::spawn(move || {
            let mut h = Handler::new(matter, panes);
            for job in job_rx {
                let (path, q, responder) = match job {
                    Job::Request(path, q, responder) => (path, q, responder),
                    Job::Export(win, id, req) => {
                        let _ = proxy.send_event(UserEvent::Exported(win, id, h.export(&req)));
                        continue;
                    }
                };
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

    let root_name = a.matter.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let mut app = App {
        windows: BTreeMap::new(), by_tao: HashMap::new(), focused: String::new(), next_window: 0,
        owners: BTreeMap::new(), waiting: HashMap::new(), gathers: HashMap::new(), history: Default::default(), seq: 0,
        title: format!("adhocdocs: {}", root_name), user: a.user.clone(), job_tx, proxy: proxy.clone(),
        term: (!a.run.is_empty()).then(|| Session { argv: a.run.clone(), banner: a.banner.clone(), term: None, win: "w1".into(),
                                                     alone: None, attached_once: false, ended_by_user: false }),
    };
    app.open_window(&event_loop);
    if a.dock == "window" && app.term.is_some() {
        let w = app.open_window_as(&event_loop, Some(format!("{}: Claude Code", app.title)), (900.0, 860.0));
        let s = app.term.as_mut().unwrap();
        s.win = w.clone();
        s.alone = Some(w);
        app.focused = "w1".into();
    }

    // The API: commands go to the shells, results come back over IPC.
    let socket = a.socket.clone().unwrap_or_else(|| api::default_socket(&matter.lock().unwrap().root));
    api::serve(socket.clone(), proxy.clone(), panes.clone());
    let _ = SOCKET.set(socket.clone());
    eprintln!("{}", json!({"viewer": "running", "socket": socket}));
    let annotate = a.annotate.clone();

    event_loop.run(move |event, target, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent { window_id, event: WindowEvent::CloseRequested, .. } => {
                let Some(w) = app.by_tao.get(&window_id).cloned() else { return };
                if let Some(s) = app.term.as_ref().filter(|s| s.running() && s.win == w) {
                    // closing the terminal ends the session: ask first
                    let mut choices = vec![json!({"label": "End the session", "action": "end", "primary": true})];
                    if s.alone.as_deref() == Some(w.as_str()) && app.windows.len() > 1 {
                        choices.push(json!({"label": "Put it back beside the documents", "action": "back"}));
                    }
                    choices.push(json!({"label": "Cancel", "action": "cancel"}));
                    let text = "Claude Code is still running. Closing its window ends the session. \
                                (To continue the conversation later: claude --resume.)";
                    app.eval(&w, &format!("adhocTerm.confirmClose({}, {})", json!(text), json!(choices)));
                } else if app.windows.len() > 1 {
                    if app.term.as_ref().is_some_and(|s| s.win == w) {
                        app.term_move(target, "back");
                    }
                    app.close_window(&w);
                } else {
                    quit(app.term.as_ref().map_or(0, |s| s.exit_code()));
                }
            }
            Event::WindowEvent { window_id, event: WindowEvent::Focused(true), .. } => {
                // Claude's commands go to the window the user last looked at documents in
                if let Some(w) = app.by_tao.get(&window_id).filter(|w| app.term.as_ref().and_then(|s| s.alone.as_ref()) != Some(*w)) {
                    app.focused = w.clone();
                }
            }
            Event::UserEvent(UserEvent::TermOut(bytes)) => {
                let Some(s) = app.term.as_mut() else { return };
                if let Some(t) = s.term.as_mut() { t.saw(&bytes) }
                let win = s.win.clone();
                app.eval(&win, &format!("adhocTerm.out(\"{}\")", b64(&bytes)));
            }
            Event::UserEvent(UserEvent::TermExit(code)) => app.term_exited(code),
            Event::UserEvent(UserEvent::Command(cmd, reply)) => app.dispatch(target, cmd, reply),
            Event::UserEvent(UserEvent::Service(win, id, result)) => {
                app.eval(&win, &format!("adhoc.served({}, {})", id, result));
            }
            Event::UserEvent(UserEvent::Exported(win, id, result)) => {
                app.eval(&win, &format!("adhoc.exported({}, {})", id, result));
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
                        if app.term.as_ref().is_some_and(|s| s.win == win) {
                            app.term_attach();
                        }
                    }
                    "term-size" => {
                        let (cols, rows) = (v["cols"].as_u64().unwrap_or(80) as u16, v["rows"].as_u64().unwrap_or(24) as u16);
                        let Some(s) = app.term.as_mut().filter(|s| s.win == win) else { return };
                        if let Some(t) = &s.term {
                            t.resize(cols, rows);
                            return;
                        }
                        // the first size: start the session's command at it
                        let (out, exit) = (proxy.clone(), proxy.clone());
                        let banner = s.banner.replace('\n', "\r\n");
                        match term::Term::spawn(&s.argv, cols, rows, move |b| { let _ = out.send_event(UserEvent::TermOut(b)); },
                                                move |c| { let _ = exit.send_event(UserEvent::TermExit(c)); }) {
                            Ok(mut t) => {
                                if !banner.is_empty() {
                                    let b = format!("\x1b[2m{}\x1b[0m\r\n", banner.trim_end());
                                    t.saw(b.as_bytes());
                                    app.eval(&win, &format!("adhocTerm.out(\"{}\")", b64(b.as_bytes())));
                                }
                                app.term.as_mut().unwrap().term = Some(t);
                            }
                            Err(e) => {
                                eprintln!("{}", json!({"error": format!("the session's command did not start: {}", e)}));
                                quit(1);
                            }
                        }
                    }
                    "term-in" => {
                        let data = v["data"].as_str().unwrap_or("");
                        let bytes: Vec<u8> = if v["binary"].as_bool().unwrap_or(false) { data.chars().map(|c| c as u8).collect() } else { data.as_bytes().to_vec() };
                        if let Some(t) = app.term.as_mut().and_then(|s| s.term.as_mut()) { t.input(&bytes) }
                    }
                    "term-move" => app.term_move(target, v["to"].as_str().unwrap_or("")),
                    "term-close" => match v["action"].as_str().unwrap_or("") {
                        "end" => {
                            let Some(s) = app.term.as_mut() else { return };
                            s.ended_by_user = true;
                            match s.term.as_mut() {
                                Some(t) if t.exited.is_none() => t.kill(),
                                _ => quit(s.exit_code()),
                            }
                        }
                        "back" => {
                            let w = win.clone();
                            app.term_move(target, "back");
                            if app.windows.len() > 1 && app.windows.contains_key(&w) { app.close_window(&w); }
                        }
                        _ => {}
                    },
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
                    "export" => {
                        // A download the user asked for, made by the worker that owns MuPDF.
                        let _ = app.job_tx.send(Job::Export(win.clone(), v["id"].clone(), v["request"].clone()));
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
