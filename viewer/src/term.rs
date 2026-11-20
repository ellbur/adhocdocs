//! A terminal for when there is no terminal: the viewer runs a command (the
//! session's `podman run -it …`, or `adhoc setup`) on a pseudo-terminal and
//! shows it with xterm.js (shell/term.js). It is an ordinary terminal
//! emulator: what passes through is shown, typed keys are sent, and nothing
//! is read, kept or relayed anywhere else. The only copy is the last
//! [`REPLAY`] bytes of output, in memory, so the terminal can be redrawn when
//! it moves to another window.

use portable_pty::{native_pty_system, ChildKiller, CommandBuilder, MasterPty, PtySize};
use std::io::{Read, Write};

/// How much recent output is kept to redraw the terminal in a new window.
pub const REPLAY: usize = 2 << 20;

pub struct Term {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    killer: Box<dyn ChildKiller + Send + Sync>,
    replay: Vec<u8>,
    pub exited: Option<u32>,
}

fn size(cols: u16, rows: u16) -> PtySize {
    PtySize { cols: cols.max(10), rows: rows.max(3), pixel_width: 0, pixel_height: 0 }
}

impl Term {
    /// Start `argv` at `cols`×`rows`. Output arrives in `out` (on a thread of
    /// its own, in the order written); when the command ends, `exit` gets its
    /// exit code, after the last output.
    pub fn spawn(argv: &[String], cols: u16, rows: u16, out: impl Fn(Vec<u8>) + Send + 'static,
                 exit: impl FnOnce(u32) + Send + 'static) -> Result<Term, String> {
        let (prog, args) = argv.split_first().ok_or("no command")?;
        let pair = native_pty_system().openpty(size(cols, rows)).map_err(|e| e.to_string())?;
        let mut cmd = CommandBuilder::new(prog);
        cmd.args(args);
        if let Ok(dir) = std::env::current_dir() {
            cmd.cwd(dir);
        }
        cmd.env("TERM", "xterm-256color");
        cmd.env("COLORTERM", "truecolor");
        let mut child = pair.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
        drop(pair.slave);      // the child has it; the reader sees EOF when the child is gone
        let killer = child.clone_killer();
        let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
        let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
        std::thread::spawn(move || {
            let mut buf = vec![0u8; 64 << 10];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => out(buf[..n].to_vec()),
                }
            }
            let code = child.wait().map(|s| s.exit_code()).unwrap_or(1);
            exit(code);
        });
        Ok(Term { master: pair.master, writer, killer, replay: vec![], exited: None })
    }

    /// Output on its way to the screen: kept for redrawing.
    pub fn saw(&mut self, bytes: &[u8]) {
        self.replay.extend_from_slice(bytes);
        if self.replay.len() > REPLAY + REPLAY / 4 {
            // start the copy at a line, so the redraw doesn't begin inside an escape sequence
            let cut = self.replay.len() - REPLAY;
            let at = self.replay[cut..].iter().position(|&b| b == b'\n').map(|i| cut + i + 1).unwrap_or(cut);
            self.replay.drain(..at);
        }
    }

    pub fn replay(&self) -> &[u8] {
        &self.replay
    }

    pub fn input(&mut self, bytes: &[u8]) {
        if self.exited.is_none() {
            let _ = self.writer.write_all(bytes);
            let _ = self.writer.flush();
        }
    }

    pub fn resize(&self, cols: u16, rows: u16) {
        let _ = self.master.resize(size(cols, rows));
    }

    /// End the command, as closing a terminal window does.
    pub fn kill(&mut self) {
        if self.exited.is_none() {
            let _ = self.killer.kill();
        }
    }
}

impl Drop for Term {
    fn drop(&mut self) {
        self.kill();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    #[test]
    fn runs_a_command_and_reports_its_exit() {
        let (tx, rx) = mpsc::channel();
        let tx2 = tx.clone();
        let argv: Vec<String> = ["sh", "-c", "stty size; read x; echo got $x; exit 3"].iter().map(|s| s.to_string()).collect();
        let mut t = Term::spawn(&argv, 91, 27, move |b| { let _ = tx.send(Ok(b)); }, move |c| { let _ = tx2.send(Err(c)); }).unwrap();
        let mut seen = String::new();
        while !seen.contains("27 91") {
            seen += &String::from_utf8_lossy(&rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap().unwrap());
        }
        t.input(b"hello\r");
        let code = loop {
            match rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap() {
                Ok(b) => seen += &String::from_utf8_lossy(&b),
                Err(c) => break c,
            }
        };
        assert!(seen.contains("got hello"), "{:?}", seen);
        assert_eq!(code, 3);
    }

    #[test]
    fn replay_keeps_the_end_from_a_line_start() {
        let argv: Vec<String> = ["true"].iter().map(|s| s.to_string()).collect();
        let mut t = Term::spawn(&argv, 80, 24, |_| {}, |_| {}).unwrap();
        for i in 0..200_000 {
            t.saw(format!("line {}\n", i).as_bytes());
        }
        assert!(t.replay().len() <= REPLAY + REPLAY / 4);
        assert!(t.replay().starts_with(b"line "));
        assert!(t.replay().ends_with(b"line 199999\n"));
    }
}
