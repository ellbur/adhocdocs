//! Where the viewer's API listens, and how it reaches Claude's services: the
//! same rules as the Python side (`adhocdocs.endpoint`).
//!
//! On Linux a path names a Unix socket. On Windows and macOS (or with
//! `ADHOC_TRANSPORT=tcp`) the viewer listens on a random port on 127.0.0.1
//! instead and writes an endpoint file at the path,
//! `{"tcp": "127.0.0.1:PORT", "token": "…"}`, readable only by the user.
//! Every connection must start with the line `ADHOC1 <token>`, or it is
//! dropped: the token stands in for the socket directory's permissions.

use serde_json::{json, Value};
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

const PREAMBLE: &str = "ADHOC1";
const MAX_PREAMBLE: usize = 256;

pub trait Stream: Read + Write + Send {}
impl<T: Read + Write + Send> Stream for T {}

pub fn tcp_mode() -> bool {
    match std::env::var("ADHOC_TRANSPORT").as_deref() {
        Ok("tcp") => true,
        Ok("unix") => false,
        _ => !cfg!(target_os = "linux"),
    }
}

pub enum Listener {
    #[cfg(unix)]
    Unix(std::os::unix::net::UnixListener),
    Tcp(TcpListener, String),
}

pub fn new_token() -> io::Result<String> {
    let mut b = [0u8; 32];
    getrandom::getrandom(&mut b).map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))?;
    Ok(b.iter().map(|x| format!("{:02x}", x)).collect())
}

/// Write an endpoint file atomically, readable only by the user.
pub fn write_endpoint(path: &Path, address: &str, token: &str) -> io::Result<()> {
    let tmp = path.with_extension("tmp");
    let _ = std::fs::remove_file(&tmp);
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    // On Windows, the file takes the ACL of the user's own directory.
    o.open(&tmp)?.write_all(json!({"tcp": address, "token": token}).to_string().as_bytes())?;
    std::fs::rename(&tmp, path)
}

fn read_endpoint(path: &Path) -> Option<(String, String)> {
    if !path.is_file() {
        return None;
    }
    let v: Value = serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()?;
    Some((v["tcp"].as_str()?.to_string(), v["token"].as_str()?.to_string()))
}

pub fn listen(path: &Path) -> io::Result<Listener> {
    listen_with(path, tcp_mode())
}

pub fn listen_with(path: &Path, tcp: bool) -> io::Result<Listener> {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::remove_file(path);
    if tcp {
        let l = TcpListener::bind("127.0.0.1:0")?;
        let token = new_token()?;
        write_endpoint(path, &l.local_addr()?.to_string(), &token)?;
        return Ok(Listener::Tcp(l, token));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let l = std::os::unix::net::UnixListener::bind(path)?;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
        Ok(Listener::Unix(l))
    }
    #[cfg(not(unix))]
    Err(io::Error::new(io::ErrorKind::Unsupported, "no Unix sockets here; set ADHOC_TRANSPORT=tcp"))
}

/// Read one line, byte by byte, so nothing after it is consumed.
fn read_line(s: &mut impl Read) -> io::Result<String> {
    let mut buf = Vec::new();
    let mut b = [0u8; 1];
    while buf.len() < MAX_PREAMBLE {
        if s.read(&mut b)? == 0 {
            break;
        }
        if b[0] == b'\n' {
            break;
        }
        buf.push(b[0]);
    }
    Ok(String::from_utf8_lossy(&buf).trim_end_matches('\r').to_string())
}

/// The words after the token, or None if the line isn't ours.
pub fn parse_preamble(line: &str, token: &str) -> Option<Vec<String>> {
    let mut parts = line.split(' ');
    if parts.next()? != PREAMBLE {
        return None;
    }
    let given = parts.next()?.as_bytes();
    // constant-time comparison
    if given.len() != token.len() || given.iter().zip(token.as_bytes()).fold(0u8, |acc, (a, b)| acc | (a ^ b)) != 0 {
        return None;
    }
    Some(parts.map(String::from).collect())
}

impl Listener {
    /// The next connection; for TCP, one whose first line has the token.
    /// Call `checked` on the result in the connection's own thread.
    pub fn accept(&self) -> io::Result<Accepted> {
        match self {
            #[cfg(unix)]
            Listener::Unix(l) => Ok(Accepted::Ready(Box::new(l.accept()?.0))),
            Listener::Tcp(l, token) => Ok(Accepted::Unchecked(l.accept()?.0, token.clone())),
        }
    }
}

#[cfg_attr(not(unix), allow(dead_code))]
pub enum Accepted {
    Ready(Box<dyn Stream>),
    Unchecked(TcpStream, String),
}

impl Accepted {
    /// The stream, once its first line checks out; None to drop it.
    pub fn checked(self) -> Option<Box<dyn Stream>> {
        match self {
            Accepted::Ready(s) => Some(s),
            Accepted::Unchecked(mut s, token) => {
                let _ = s.set_read_timeout(Some(Duration::from_secs(10)));
                let line = read_line(&mut s).ok()?;
                parse_preamble(&line, &token)?;
                let _ = s.set_read_timeout(None);
                Some(Box::new(s))
            }
        }
    }
}

/// Connect to `path`: an endpoint file (then `words` follow the token), or a Unix socket.
pub fn connect(path: &Path, words: &[&str]) -> io::Result<Box<dyn Stream>> {
    if let Some((addr, token)) = read_endpoint(path) {
        let mut s = TcpStream::connect(&addr)?;
        let mut line = vec![PREAMBLE, token.as_str()];
        line.extend_from_slice(words);
        s.write_all(format!("{}\n", line.join(" ")).as_bytes())?;
        return Ok(Box::new(s));
    }
    #[cfg(unix)]
    {
        Ok(Box::new(std::os::unix::net::UnixStream::connect(path)?))
    }
    #[cfg(not(unix))]
    Err(io::Error::new(io::ErrorKind::NotFound, format!("no endpoint file at {}", path.display())))
}

/// Claude's service `name` (`view serve`): its socket in the session's
/// directory, or, through the bridge on Windows and macOS, the
/// `svc.endpoint` the launcher writes there.
pub fn connect_service(dir: &Path, name: &str) -> io::Result<Box<dyn Stream>> {
    let sock: PathBuf = dir.join(format!("svc-{}.sock", name));
    let bridged = dir.join("svc.endpoint");
    if !sock.exists() && bridged.is_file() {
        return connect(&bridged, &["svc", name]);
    }
    connect(&sock, &[])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preamble() {
        let t = "ab".repeat(32);
        assert_eq!(parse_preamble(&format!("ADHOC1 {}", t), &t), Some(vec![]));
        assert_eq!(parse_preamble(&format!("ADHOC1 {} svc x", t), &t), Some(vec!["svc".into(), "x".into()]));
        assert_eq!(parse_preamble(&format!("ADHOC1 {}", "cd".repeat(32)), &t), None);
        assert_eq!(parse_preamble("GET / HTTP/1.1", &t), None);
    }

    #[test]
    fn tcp_listener_checks_the_token() {
        let dir = std::env::temp_dir().join(format!("adhoc-ep-{}", std::process::id()));
        let path = dir.join("viewer.sock");
        let l = listen_with(&path, true).unwrap();
        let (addr, token) = read_endpoint(&path).unwrap();
        assert!(addr.starts_with("127.0.0.1:") && token.len() == 64);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o077, 0);
        }
        let t = std::thread::spawn(move || {
            let good = l.accept().unwrap().checked();
            let bad = l.accept().unwrap().checked();
            (good.is_some(), bad.is_some())
        });
        let mut c = connect(&path, &[]).unwrap();
        c.write_all(b"hi").unwrap();
        let mut b = TcpStream::connect(&addr).unwrap();
        b.write_all(b"ADHOC1 nope\n").unwrap();
        assert_eq!(t.join().unwrap(), (true, false));
        let _ = std::fs::remove_dir_all(dir);
    }
}
