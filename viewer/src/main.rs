//! adhoc-viewer: shows one matter's documents. Launched on the host by the
//! connect mechanism, never by Claude. Claude drives it through a small API
//! on a Unix socket (`view` CLI in the container).
//!
//! The window is one web view running the viewer's own code (shell/).
//! Document pages are rendered natively by MuPDF and served as images; all
//! other HTML (presentations, pages, lists, Claude's panes) is shown in
//! sandboxed iframes where no script can run.

mod api;
mod matter;
mod protocol;
mod render;

use matter::Matter;
use protocol::{Handler, Panes, Response};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::window::WindowBuilder;
use wry::http::header::{CONTENT_SECURITY_POLICY, CONTENT_TYPE};
use wry::WebViewBuilder;

pub enum UserEvent {
    /// A command from the API, with where to send the result.
    Command(Value, mpsc::Sender<Value>),
    Ipc(String),
    /// A service's answer for a pane: the request ID and the result.
    Service(Value, Value),
}

struct Args {
    matter: PathBuf,
    socket: Option<PathBuf>,
    user: String,
    annotate: Option<String>,
}

fn args() -> Result<Args, String> {
    let mut a = Args { matter: PathBuf::new(), socket: None, user: std::env::var("USER").unwrap_or("user".into()), annotate: None };
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

    // One worker owns MuPDF and answers every adhoc: request in order.
    type Job = (String, String, wry::RequestAsyncResponder);
    let (job_tx, job_rx) = mpsc::channel::<Job>();
    {
        let (matter, panes) = (matter.clone(), panes.clone());
        std::thread::spawn(move || {
            let mut h = Handler::new(matter, panes);
            for (path, q, responder) in job_rx {
                let r = h.handle(&path, &q);
                if r.status != 200 || std::env::var("ADHOC_VIEWER_DEBUG").is_ok() {
                    eprintln!("{}", json!({"request": path, "status": r.status,
                        "error": if r.status != 200 { String::from_utf8_lossy(&r.body).to_string() } else { String::new() }}));
                }
                responder.respond(http_response(r));
            }
        });
    }

    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let root_name = a.matter.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let window = WindowBuilder::new()
        .with_title(format!("adhocdocs: {}", root_name))
        .with_inner_size(tao::dpi::LogicalSize::new(1400.0, 900.0))
        .build(&event_loop)
        .unwrap();

    let ipc_proxy = proxy.clone();
    let job_tx2 = Mutex::new(job_tx);
    let builder = WebViewBuilder::new()
        .with_asynchronous_custom_protocol("adhoc".into(), move |_id, req, responder| {
            let path = req.uri().path().to_string();
            let q = req.uri().query().unwrap_or("").to_string();
            let _ = job_tx2.lock().unwrap().send((path, q, responder));
        })
        .with_ipc_handler(move |req| {
            let _ = ipc_proxy.send_event(UserEvent::Ipc(req.body().clone()));
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

    // The API: commands go to the shell, results come back over IPC.
    let socket = a.socket.clone().unwrap_or_else(|| api::default_socket(&matter.lock().unwrap().root));
    api::serve(socket.clone(), proxy.clone(), panes.clone());
    eprintln!("{}", json!({"viewer": "running", "socket": socket}));

    let mut waiting: HashMap<u64, mpsc::Sender<Value>> = HashMap::new();
    let mut seq: u64 = 0;
    let mut ready = false;
    let mut queued: Vec<(Value, mpsc::Sender<Value>)> = vec![];
    let user = a.user.clone();
    let annotate = a.annotate.clone();

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => {
                let _ = std::fs::remove_file(&socket);
                *control_flow = ControlFlow::Exit
            }
            Event::UserEvent(UserEvent::Command(cmd, reply)) => {
                if !ready {
                    queued.push((cmd, reply));
                    return;
                }
                seq += 1;
                waiting.insert(seq, reply);
                let _ = webview.evaluate_script(&format!("adhoc.command({}, {})", seq, cmd));
            }
            Event::UserEvent(UserEvent::Service(id, result)) => {
                let _ = webview.evaluate_script(&format!("adhoc.served({}, {})", id, result));
            }
            Event::UserEvent(UserEvent::Ipc(msg)) => {
                let Ok(v) = serde_json::from_str::<Value>(&msg) else { return };
                match v["type"].as_str().unwrap_or("") {
                    "ready" => {
                        ready = true;
                        let _ = webview.evaluate_script(&format!("adhoc.config({})", json!({"user": format!("user:{}", user)})));
                        for (cmd, reply) in queued.drain(..) {
                            seq += 1;
                            waiting.insert(seq, reply);
                            let _ = webview.evaluate_script(&format!("adhoc.command({}, {})", seq, cmd));
                        }
                    }
                    "result" => {
                        if let Some(tx) = waiting.remove(&v["seq"].as_u64().unwrap_or(0)) {
                            let mut result = v["result"].clone();
                            api::add_quotes(&matter.lock().unwrap(), &mut result);
                            let _ = tx.send(result);
                        }
                    }
                    "svc" => {
                        // A pane's form, submitted to a service Claude runs in the container.
                        let (dir, panes, proxy) = (socket.parent().map(|p| p.to_path_buf()), panes.clone(), proxy.clone());
                        let (id, name, values) = (v["id"].clone(), v["name"].as_str().unwrap_or("").to_string(), v["values"].clone());
                        std::thread::spawn(move || {
                            let result = match dir {
                                Some(d) => api::call_service(&d, &name, &values, &panes),
                                None => json!({"error": "no socket directory"}),
                            };
                            let _ = proxy.send_event(UserEvent::Service(id, result));
                        });
                    }
                    "annotate" => {
                        // The user's own tags and notes, written as the user by the host tool.
                        let result = api::annotate(annotate.as_deref(), &matter.lock().unwrap().root, &user, &v["request"]);
                        let _ = webview.evaluate_script(&format!("adhoc.annotated({}, {})", v["id"], result));
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    });
}
