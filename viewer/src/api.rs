//! The viewer API: HTTP over a Unix socket that the connect mechanism
//! mounts into the container. It accepts commands with locators and static
//! HTML, and returns state. It can't open paths or change the scope.
//!
//! `POST /command` with `{"cmd": …}`. Web pane content arrives as
//! `"files": {"index.html": "<base64>", …}`; it is sanitized and stored here,
//! and the shell only ever sees a content ID.

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
    let run = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| format!("/tmp/adhocdocs-{}", unsafe { libc::getuid() }));
    // the same short hash the Python side computes
    let h = sha256_hex(root.to_string_lossy().as_bytes());
    PathBuf::from(run).join("adhocdocs").join(format!("{}.view.sock", &h[..16]))
}

fn sha256_hex(data: &[u8]) -> String {
    use sha2::Digest;
    sha2::Sha256::digest(data).iter().map(|b| format!("{:02x}", b)).collect()
}

fn store_files(panes: &Panes, files: &Value) -> Result<String, String> {
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

pub fn serve(path: PathBuf, proxy: EventLoopProxy<UserEvent>, panes: Panes) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::remove_file(&path);
    let server = match tiny_http::Server::http_unix(&path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("{}", json!({"error": format!("can't listen on {}: {}", path.display(), e)}));
            std::process::exit(2);
        }
    };
    std::thread::spawn(move || {
        for mut req in server.incoming_requests() {
            let mut body = String::new();
            let _ = req.as_reader().read_to_string(&mut body);
            let result = (|| -> Result<Value, String> {
                if req.url() != "/command" {
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
            let resp = tiny_http::Response::from_string(v.to_string())
                .with_status_code(status)
                .with_header("Content-Type: application/json".parse::<tiny_http::Header>().unwrap());
            let _ = req.respond(resp);
        }
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
pub fn annotate(cmd: Option<&str>, root: &Path, user: &str, request: &Value) -> Value {
    let cmd = cmd.unwrap_or("adhoc");
    let mut parts = cmd.split_whitespace();
    let Some(prog) = parts.next() else { return json!({"error": "no annotate command"}) };
    let child = Command::new(prog)
        .args(parts)
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
