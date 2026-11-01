//! The viewer API: HTTP over a Unix socket that the connect mechanism
//! mounts into the container. It accepts commands with locators and static
//! HTML, and returns state. It can't open paths or change the scope.
//!
//! `POST /command` with `{"cmd": …}`. Web pane content arrives as
//! `"files": {"index.html": "<base64>", …}`; it is sanitized and stored here,
//! and the shell only ever sees a content ID.

use crate::endpoint::{self, Stream};
use crate::matter::Matter;
use crate::protocol::{char_slice, sanitize, Panes};
use crate::UserEvent;
use base64::Engine;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::time::Duration;
use tao::event_loop::EventLoopProxy;

const MAX_SOCKET_PATH: usize = 100;
static CONTENT: AtomicU64 = AtomicU64::new(0);

/// Same rule as the Python client: `$ADHOC_VIEW_SOCKET`, else
/// `<matter>/run/viewer.sock`, else a per-matter path under the runtime dir.
pub fn default_socket(root: &Path) -> PathBuf {
    if let Ok(p) = std::env::var("ADHOC_VIEW_SOCKET") {
        return p.into();
    }
    let p = root.join("run").join("viewer.sock");
    if p.to_string_lossy().len() <= MAX_SOCKET_PATH {
        return p;
    }
    // the same short hash the Python side computes
    let h = sha256_hex(root.to_string_lossy().as_bytes());
    runtime_dir().join("adhocdocs").join(&h[..16]).join("viewer.sock")
}

/// The same as the Python side's `portable.runtime_dir()`.
fn runtime_dir() -> PathBuf {
    #[cfg(windows)]
    {
        let base = std::env::var("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|_| std::env::temp_dir());
        base.join("adhocdocs").join("run")
    }
    #[cfg(unix)]
    {
        std::env::var("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(format!("/tmp/adhocdocs-{}", unsafe { libc::getuid() })))
    }
}

fn sha256_hex(data: &[u8]) -> String {
    use sha2::Digest;
    sha2::Sha256::digest(data).iter().map(|b| format!("{:02x}", b)).collect()
}

pub fn store_files(panes: &Panes, files: &Value) -> Result<String, String> {
    let mut out = HashMap::new();
    for (path, b64) in files.as_object().ok_or("files must be an object")? {
        if path.contains("..") || path.starts_with('/') {
            return Err(format!("bad path {}", path));
        }
        let bytes = base64::engine::general_purpose::STANDARD.decode(b64.as_str().unwrap_or("")).map_err(|e| e.to_string())?;
        let bytes = if path.ends_with(".html") || path.ends_with(".htm") {
            let doc = sanitize(&String::from_utf8_lossy(&bytes));
            format!("<!doctype html><html><head><meta charset=\"utf-8\"><link rel=\"stylesheet\" href=\"/shell.css\"></head><body class=\"adhoc-pane\">{}</body></html>", doc).into_bytes()
        } else if path.ends_with(".css") || path.ends_with(".png") || path.ends_with(".jpg") || path.ends_with(".svg") {
            bytes
        } else {
            continue;
        };
        out.insert(path.clone(), bytes);
    }
    let id = format!("c{}", CONTENT.fetch_add(1, Ordering::SeqCst) + 1);
    panes.lock().unwrap().insert(id.clone(), out);
    Ok(id)
}

/// One HTTP request: (method, path, body). Enough for the API's clients
/// (the `view` CLI and the bridge), which send one request per connection.
fn read_request(s: &mut dyn Stream) -> Option<(String, String, String)> {
    let mut head = Vec::new();
    let mut b = [0u8; 1];
    while !head.ends_with(b"\r\n\r\n") {
        if head.len() > 65536 || s.read(&mut b).ok()? == 0 {
            return None;
        }
        head.push(b[0]);
    }
    let head = String::from_utf8_lossy(&head).to_string();
    let mut lines = head.split("\r\n");
    let mut first = lines.next()?.split(' ');
    let (method, path) = (first.next()?.to_string(), first.next()?.to_string());
    let len = lines
        .filter_map(|l| l.split_once(':'))
        .find(|(k, _)| k.trim().eq_ignore_ascii_case("content-length"))
        .and_then(|(_, v)| v.trim().parse::<usize>().ok())
        .unwrap_or(0);
    if len > 256 << 20 {
        return None;
    }
    let mut body = vec![0u8; len];
    s.read_exact(&mut body).ok()?;
    Some((method, path, String::from_utf8_lossy(&body).to_string()))
}

fn respond(s: &mut dyn Stream, status: u16, body: &str) {
    let reason = if status == 200 { "OK" } else { "Bad Request" };
    let _ = write!(s, "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                   status, reason, body.len(), body);
    let _ = s.flush();
}

pub fn serve(path: PathBuf, proxy: EventLoopProxy<UserEvent>, panes: Panes) {
    let listener = match endpoint::listen(&path) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("{}", json!({"error": format!("can't listen on {}: {}", path.display(), e)}));
            std::process::exit(2);
        }
    };
    std::thread::spawn(move || loop {
        let Ok(conn) = listener.accept() else { continue };
        let (proxy, panes) = (proxy.clone(), panes.clone());
        std::thread::spawn(move || {
            let Some(mut s) = conn.checked() else { return };
            let Some((_method, url, body)) = read_request(&mut *s) else { return };
            let result = (|| -> Result<Value, String> {
                if url != "/command" {
                    return Err("POST /command".into());
                }
                let mut cmd: Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
                if let Some(files) = cmd.get("files").cloned() {
                    cmd["content"] = json!(store_files(&panes, &files)?);
                    cmd.as_object_mut().unwrap().remove("files");
                }
                let (tx, rx) = mpsc::channel();
                proxy.send_event(UserEvent::Command(cmd, tx)).map_err(|_| "viewer is closing")?;
                rx.recv_timeout(Duration::from_secs(20)).map_err(|_| "the viewer did not answer".to_string())
            })();
            let (status, v) = match result {
                Ok(v) if v.get("error").is_some() => (400, v),
                Ok(v) => (200, v),
                Err(e) => (400, json!({"error": e})),
            };
            respond(&mut *s, status, &v.to_string());
        });
    });
}

/// Give every text selection and mark its quote fingerprint, from the store,
/// so it can go straight into a page's refs block, a tag or a note.
pub fn add_quotes(m: &Matter, v: &mut Value) {
    match v {
        Value::Object(map) => {
            let is_range = map.get("layer").is_some() && map.get("doc").is_some() && map.get("quote").is_none()
                && map.get("select").and_then(|s| s.get("char")).is_some();
            if is_range {
                let layer = map["layer"].as_str().unwrap_or("").to_string();
                let s = map["select"]["char"][0].as_u64().unwrap_or(0) as usize;
                let e = map["select"]["char"][1].as_u64().unwrap_or(0) as usize;
                if let Some(l) = m.layer(&layer) {
                    if let Some(text) = m.text(&l.text) {
                        map.insert("quote".into(), json!({
                            "exact": char_slice(&text, s, e),
                            "prefix": char_slice(&text, s.saturating_sub(32), s),
                            "suffix": char_slice(&text, e, e + 32),
                        }));
                    }
                }
            }
            for (_, x) in map.iter_mut() {
                add_quotes(m, x);
            }
        }
        Value::Array(a) => a.iter_mut().for_each(|x| add_quotes(m, x)),
        _ => {}
    }
}

/// Write the user's own annotation through the host tool, which stamps it
/// as the user and applies the rules. Returns its JSON reply.
/// `cmd` is a JSON list of arguments (a path may have spaces), or words.
pub fn annotate(cmd: Option<&str>, root: &Path, user: &str, request: &Value) -> Value {
    let cmd = cmd.unwrap_or("adhoc");
    let parts: Vec<String> = match serde_json::from_str::<Vec<String>>(cmd) {
        Ok(list) => list,
        Err(_) => cmd.split_whitespace().map(String::from).collect(),
    };
    let Some((prog, rest)) = parts.split_first() else { return json!({"error": "no annotate command"}) };
    let child = Command::new(prog)
        .args(rest)
        .args(["--matter", &root.to_string_lossy(), "annotate-as-user", "--user", user])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let Ok(mut child) = child else { return json!({"error": format!("can't run {}", cmd)}) };
    let _ = child.stdin.take().unwrap().write_all(request.to_string().as_bytes());
    let out = child.wait_with_output();
    match out {
        Ok(o) if o.status.success() => serde_json::from_slice(&o.stdout).unwrap_or(json!({"error": "bad reply"})),
        Ok(o) => serde_json::from_slice(&o.stderr).unwrap_or_else(|_| json!({"error": String::from_utf8_lossy(&o.stderr).trim()})),
        Err(e) => json!({"error": e.to_string()}),
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn socket_hash_matches_python() {
        // hashlib.sha256(b"/srv/matters/smith-v-jones").hexdigest()[:16], as the view CLI computes it
        assert_eq!(&super::sha256_hex(b"/srv/matters/smith-v-jones")[..16], "5013a75b82377f5a");
    }
}

/// Send a pane's form values to the service `name`, which listens on
/// `svc-<name>.sock` in the viewer's socket directory (shared with the
/// session). Its reply is HTML for the pane, sanitized and stored like any
/// other pane content. Returns `{"content": id}` or `{"error": …}`.
pub fn call_service(dir: &Path, name: &str, values: &Value, panes: &Panes) -> Value {
    if name.is_empty() || !name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') {
        return json!({"error": format!("bad service name {:?}", name)});
    }
    // The service answers within its own 55-second limit (`view serve`).
    let mut stream = match endpoint::connect_service(dir, name) {
        Ok(s) => s,
        Err(_) => return json!({"error": format!("no service {} is running (view serve {} -- …)", name, name)}),
    };
    let body = values.to_string();
    let req = format!("POST / HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}", body.len(), body);
    if stream.write_all(req.as_bytes()).is_err() {
        return json!({"error": "could not send to the service"});
    }
    let mut resp = Vec::new();
    use std::io::Read;
    if stream.read_to_end(&mut resp).is_err() {
        return json!({"error": "the service did not answer"});
    }
    let text = String::from_utf8_lossy(&resp);
    let Some((head, html)) = text.split_once("\r\n\r\n") else { return json!({"error": "bad reply from the service"}) };
    if !head.starts_with("HTTP/1.0 200") && !head.starts_with("HTTP/1.1 200") {
        return json!({"error": format!("the service failed: {}", html.trim().chars().take(300).collect::<String>())});
    }
    let b64 = base64::engine::general_purpose::STANDARD.encode(html.as_bytes());
    match store_files(panes, &json!({"index.html": b64})) {
        Ok(id) => json!({"content": id}),
        Err(e) => json!({"error": e}),
    }
}
