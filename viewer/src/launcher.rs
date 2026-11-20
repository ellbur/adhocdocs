//! The launcher (`adhoc-viewer --launcher`, started by `adhoc gui`): a window
//! for what otherwise takes a command line. Making a matter, adding
//! documents, opening a session, setting up: the page (shell/launcher.js)
//! asks for each by running the same `adhoc` command a user would type, and
//! this side runs it and passes back what it printed. It also has the
//! native file dialogs, and a terminal (term.rs) for `adhoc setup`.
//!
//! None of this is reachable from a session: the launcher has no API socket,
//! and the matters and documents it connects are the user's choice (docs/DESIGN.md,
//! principle 2).

use crate::term::Term;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader};
#[cfg(not(target_os = "linux"))]
use std::path::PathBuf;
use std::process::{Command, Stdio};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::window::WindowBuilder;
use wry::http::header::CONTENT_TYPE;
use wry::WebViewBuilder;

const HTML: &str = include_str!("../shell/launcher.html");
const JS: &str = include_str!("../shell/launcher.js");

enum Ev {
    Ipc(String),
    /// a request's answer, by its ID
    Done(Value, Value),
    /// a line a streaming request printed
    Progress(Value, Value),
    TermOut(Vec<u8>),
    TermExit(u32),
}

/// How to run `adhoc`: a JSON list of words, from `adhoc gui`.
fn adhoc(prefix: &[String], args: &[String]) -> Command {
    let mut c = Command::new(&prefix[0]);
    c.args(&prefix[1..]).args(args).stdin(Stdio::null());
    c
}

fn last_json(text: &str) -> Value {
    text.lines().rev().find_map(|l| serde_json::from_str::<Value>(l.trim()).ok()).unwrap_or(Value::Null)
}

/// What a command printed, for the page: its exit code, its last JSON line
/// on stdout, and its error (JSON when it said one, else its last lines).
fn outcome(code: i32, stdout: &str, stderr: &str) -> Value {
    let err = last_json(stderr);
    let err = if err.is_null() && code != 0 {
        let lines: Vec<&str> = stderr.lines().filter(|l| !l.trim().is_empty()).collect();
        json!({"error": lines[lines.len().saturating_sub(6)..].join("\n")})
    } else {
        err
    };
    json!({"code": code, "out": last_json(stdout), "err": err})
}

fn args_of(v: &Value) -> Vec<String> {
    v["args"].as_array().into_iter().flatten().filter_map(|a| a.as_str().map(String::from)).collect()
}

/// Open a folder in the desktop's file manager.
pub(crate) fn reveal(path: &str) {
    let opener = if cfg!(windows) { "explorer" } else if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
    let _ = Command::new(opener).arg(path).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
}

/// A native file dialog, over the launcher's window: the paths chosen (none if cancelled).
#[cfg(target_os = "linux")]
pub(crate) fn pick(v: &Value, parent: &tao::window::Window) -> Vec<String> {
    use gtk::prelude::*;
    use gtk::FileChooserAction as A;
    use tao::platform::unix::WindowExtUnix;
    let kind = v["kind"].as_str().unwrap_or("");
    let (action, accept) = match kind {
        "save" => (A::Save, "_Create"),
        "folder" => (A::SelectFolder, "_Choose"),
        "folders" => (A::SelectFolder, "_Add"),
        _ => (A::Open, if kind == "manifest" { "_Choose" } else { "_Add" }),
    };
    let d = gtk::FileChooserNative::new(Some(v["title"].as_str().unwrap_or("Choose")), Some(parent.gtk_window()),
                                        action, Some(accept), Some("_Cancel"));
    d.set_modal(true);
    d.set_select_multiple(kind == "files" || kind == "folders");
    if let Some(dir) = v["start"].as_str().filter(|s| !s.is_empty()) {
        d.set_current_folder(dir);
    }
    if kind == "save" {
        d.set_current_name(v["name"].as_str().unwrap_or(""));
    }
    if kind == "manifest" {
        let f = gtk::FileFilter::new();
        f.set_name(Some("Manifests (.toml)"));
        f.add_pattern("*.toml");
        d.add_filter(f);
    }
    let chosen = if d.run() == gtk::ResponseType::Accept {
        d.filenames().into_iter().map(|p| p.to_string_lossy().to_string()).collect()
    } else {
        vec![]
    };
    d.destroy();
    chosen
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn pick(v: &Value, parent: &tao::window::Window) -> Vec<String> {
    let mut d = rfd::FileDialog::new().set_title(v["title"].as_str().unwrap_or("Choose")).set_parent(parent);
    if let Some(dir) = v["start"].as_str().filter(|s| !s.is_empty()) {
        d = d.set_directory(dir);
    }
    if let Some(name) = v["name"].as_str() {
        d = d.set_file_name(name);
    }
    let to_s = |p: PathBuf| p.to_string_lossy().to_string();
    match v["kind"].as_str().unwrap_or("") {
        "save" => d.save_file().map(to_s).into_iter().collect(),
        "folder" => d.pick_folder().map(to_s).into_iter().collect(),
        "folders" => d.pick_folders().unwrap_or_default().into_iter().map(to_s).collect(),
        "manifest" => d.add_filter("Manifest", &["toml"]).pick_file().map(to_s).into_iter().collect(),
        _ => d.pick_files().unwrap_or_default().into_iter().map(to_s).collect(),
    }
}

pub fn run(adhoc_json: String) -> ! {
    let prefix: Vec<String> = serde_json::from_str(&adhoc_json).unwrap_or_default();
    if prefix.is_empty() {
        eprintln!("{}", json!({"error": "--launcher needs --annotate <the adhoc command, as a JSON list>"}));
        std::process::exit(2);
    }
    let event_loop = EventLoopBuilder::<Ev>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let window = WindowBuilder::new()
        .with_title("adhocdocs")
        .with_inner_size(tao::dpi::LogicalSize::new(1000.0, 760.0))
        .build(&event_loop)
        .unwrap();
    let ipc_proxy = proxy.clone();
    let builder = WebViewBuilder::new()
        .with_custom_protocol("adhoc".into(), |_id, req| {
            let name = req.uri().path().trim_start_matches('/').to_string();
            let (mime, body): (&str, Vec<u8>) = match name.as_str() {
                "" | "index.html" => ("text/html", HTML.as_bytes().to_vec()),
                "launcher.js" => ("text/javascript", JS.as_bytes().to_vec()),
                n => match crate::protocol::common_file(n) {
                    Some((m, b)) => (m, b.to_vec()),
                    None => return wry::http::Response::builder().status(404).body(Vec::new().into()).unwrap(),
                },
            };
            wry::http::Response::builder().header(CONTENT_TYPE, mime).body(body.into()).unwrap()
        })
        .with_ipc_handler(move |req| { let _ = ipc_proxy.send_event(Ev::Ipc(req.body().clone())); })
        .with_navigation_handler(|url| url.starts_with("adhoc:") || url.starts_with("http://adhoc.localhost"))
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

    // the terminal, for `adhoc setup`: its command waits for the page to say the terminal's size
    let mut term: Option<Term> = None;
    let mut term_cmd: Option<Vec<String>> = None;
    let eval = move |w: &wry::WebView, s: String| { let _ = w.evaluate_script(&s); };

    event_loop.run(move |event, _target, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => {
                if term.as_ref().is_some_and(|t| t.exited.is_none()) {
                    let choices = json!([{"label": "Stop it and close", "action": "end", "primary": true}, {"label": "Cancel", "action": "cancel"}]);
                    eval(&webview, format!("adhocTerm.confirmClose({}, {})", json!("Setup is still running."), choices));
                } else {
                    *control_flow = ControlFlow::Exit;
                }
            }
            Event::UserEvent(Ev::Done(id, result)) => eval(&webview, format!("launcher.done({}, {})", id, result)),
            Event::UserEvent(Ev::Progress(id, line)) => eval(&webview, format!("launcher.progress({}, {})", id, line)),
            Event::UserEvent(Ev::TermOut(b)) => {
                if let Some(t) = term.as_mut() { t.saw(&b) }
                use base64::Engine;
                eval(&webview, format!("adhocTerm.out(\"{}\")", base64::engine::general_purpose::STANDARD.encode(&b)));
            }
            Event::UserEvent(Ev::TermExit(code)) => {
                if let Some(t) = term.as_mut() { t.exited = Some(code) }
                eval(&webview, format!("adhocTerm.exited({}); launcher.termDone({})", code, code));
            }
            Event::UserEvent(Ev::Ipc(msg)) => {
                let Ok(v) = serde_json::from_str::<Value>(&msg) else { return };
                if std::env::var("ADHOC_VIEWER_DEBUG").is_ok() {
                    eprintln!("{}", json!({"launcher": v}));
                }
                let id = v["id"].clone();
                match v["type"].as_str().unwrap_or("") {
                    // an adhoc command, answered with what it printed
                    "run" => {
                        let (mut cmd, proxy) = (adhoc(&prefix, &args_of(&v)), proxy.clone());
                        std::thread::spawn(move || {
                            let r = match cmd.output() {
                                Ok(o) => outcome(o.status.code().unwrap_or(1), &String::from_utf8_lossy(&o.stdout), &String::from_utf8_lossy(&o.stderr)),
                                Err(e) => json!({"code": 1, "out": null, "err": {"error": e.to_string()}}),
                            };
                            let _ = proxy.send_event(Ev::Done(id, r));
                        });
                    }
                    // one that prints a JSON line per step (ingest): each line as it comes
                    "stream" => {
                        let (mut cmd, proxy) = (adhoc(&prefix, &args_of(&v)), proxy.clone());
                        std::thread::spawn(move || {
                            let child = cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn();
                            let mut child = match child {
                                Ok(c) => c,
                                Err(e) => { let _ = proxy.send_event(Ev::Done(id, json!({"code": 1, "err": {"error": e.to_string()}}))); return; }
                            };
                            let mut stderr = child.stderr.take().unwrap();
                            let err = std::thread::spawn(move || { let mut s = String::new(); let _ = std::io::Read::read_to_string(&mut stderr, &mut s); s });
                            for line in BufReader::new(child.stdout.take().unwrap()).lines().map_while(Result::ok) {
                                if let Ok(x) = serde_json::from_str::<Value>(&line) {
                                    let _ = proxy.send_event(Ev::Progress(id.clone(), x));
                                }
                            }
                            let code = child.wait().ok().and_then(|s| s.code()).unwrap_or(1);
                            let _ = proxy.send_event(Ev::Done(id, outcome(code, "", &err.join().unwrap_or_default())));
                        });
                    }
                    // one that keeps running on its own (a session), answered when it ends;
                    // in its own process group, so it outlives the launcher
                    "spawn" => {
                        let (mut cmd, proxy) = (adhoc(&prefix, &args_of(&v)), proxy.clone());
                        let log = std::env::temp_dir().join(format!("adhoc-launcher-{}-{}.err", std::process::id(), id));
                        std::thread::spawn(move || {
                            #[cfg(unix)]
                            std::os::unix::process::CommandExt::process_group(&mut cmd, 0);
                            let child = std::fs::File::create(&log).map_err(|e| e.to_string())
                                .and_then(|f| cmd.stdout(Stdio::null()).stderr(f).spawn().map_err(|e| e.to_string()));
                            let r = match child {
                                Ok(mut c) => {
                                    let code = c.wait().ok().and_then(|s| s.code()).unwrap_or(1);
                                    outcome(code, "", &std::fs::read_to_string(&log).unwrap_or_default())
                                }
                                Err(e) => json!({"code": 1, "err": {"error": e}}),
                            };
                            let _ = std::fs::remove_file(&log);
                            let _ = proxy.send_event(Ev::Done(id, r));
                        });
                    }
                    "pick" => {
                        let paths = pick(&v, &window);
                        eval(&webview, format!("launcher.done({}, {})", id, json!({"paths": paths})));
                    }
                    "reveal" => reveal(v["path"].as_str().unwrap_or("")),
                    "term-run" => {
                        term = None;
                        term_cmd = Some(prefix.iter().cloned().chain(args_of(&v)).collect());
                    }
                    "term-size" => {
                        let (cols, rows) = (v["cols"].as_u64().unwrap_or(80) as u16, v["rows"].as_u64().unwrap_or(24) as u16);
                        if let Some(t) = &term {
                            t.resize(cols, rows);
                        } else if let Some(argv) = term_cmd.take() {
                            let (out, exit) = (proxy.clone(), proxy.clone());
                            match Term::spawn(&argv, cols, rows, move |b| { let _ = out.send_event(Ev::TermOut(b)); },
                                              move |c| { let _ = exit.send_event(Ev::TermExit(c)); }) {
                                Ok(t) => term = Some(t),
                                Err(e) => eval(&webview, format!("adhocTerm.exited(1, {}); launcher.termDone(1)", json!(e))),
                            }
                        }
                    }
                    "term-in" => {
                        let data = v["data"].as_str().unwrap_or("");
                        let bytes: Vec<u8> = if v["binary"].as_bool().unwrap_or(false) { data.chars().map(|c| c as u8).collect() } else { data.as_bytes().to_vec() };
                        if let Some(t) = term.as_mut() { t.input(&bytes) }
                    }
                    "term-close" => {
                        if v["action"] == "end" {
                            if let Some(t) = term.as_mut() { t.kill() }
                            *control_flow = ControlFlow::Exit;
                        }
                    }
                    "term-hide" => {
                        if term.as_ref().map_or(true, |t| t.exited.is_some()) {
                            term = None;
                            eval(&webview, "adhocTerm.detach()".into());
                        }
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn outcomes_say_what_went_wrong() {
        let r = outcome(0, "{\"a\": 1}\n", "Checking…\n");
        assert_eq!(r["out"]["a"], 1);
        assert!(r["err"].is_null());
        let r = outcome(1, "", "{\"error\": \"no matter here\"}\n");
        assert_eq!(r["err"]["error"], "no matter here");
        let r = outcome(2, "", "Traceback\n  File x\nOSError: boom\n");
        assert!(r["err"]["error"].as_str().unwrap().ends_with("OSError: boom"));
    }
}
