//! Loose files: files the user hands Claude, and files Claude hands back,
//! that are not discovery documents. They aren't ingested, have no document
//! IDs, aren't synced and aren't tracked: they're plain files in the
//! workspace, which Claude can read, change and delete.
//!
//! - `workspace/files/uploads/`: what the user gave Claude (dropped on a
//!   window or the terminal, or chosen in the Files pane);
//! - `workspace/files/from-claude/`: what Claude made for the user (`view
//!   deliver`), with Claude's notes on them in `.notes.json`.
//!
//! In the session they are under `/matter/workspace/files/`. The host does
//! the copying in and the saving out (to the Downloads folder), on the
//! user's say-so; Claude reaches them only through the workspace mount.

use crate::export::{downloads_dir, safe_name, unique_path};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

pub const DIR: &str = "workspace/files";
/// Where the workspace is in the session.
pub const IN_SESSION: &str = "/matter/workspace/files";
pub const KINDS: [&str; 2] = ["uploads", "from-claude"];
/// More than this many files in one drop (a folder of folders, say) is refused.
const MAX_FILES: usize = 10_000;

fn kind_dir(root: &Path, kind: &str) -> Result<PathBuf, String> {
    if !KINDS.contains(&kind) {
        return Err(format!("no such folder {}", kind));
    }
    Ok(root.join(DIR).join(kind))
}

/// A name as listed: one entry of the folder, no path in it.
fn entry(root: &Path, kind: &str, name: &str) -> Result<PathBuf, String> {
    if name.is_empty() || name.starts_with('.') || name.contains('/') || name.contains('\\') || name == ".." {
        return Err(format!("not a file here: {}", name));
    }
    let p = kind_dir(root, kind)?.join(name);
    if p.symlink_metadata().is_err() {
        return Err(format!("{} is gone", name));
    }
    Ok(p)
}

fn secs(m: &fs::Metadata) -> u64 {
    m.modified().ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d| d.as_secs()).unwrap_or(0)
}

/// A folder's size and file count, not following links.
fn tally(p: &Path) -> (u64, usize) {
    let Ok(m) = p.symlink_metadata() else { return (0, 0) };
    if m.is_file() {
        return (m.len(), 1);
    }
    if !m.is_dir() {
        return (0, 0);
    }
    let mut out = (0, 0);
    for e in fs::read_dir(p).into_iter().flatten().flatten() {
        let (s, n) = tally(&e.path());
        out = (out.0 + s, out.1 + n);
    }
    out
}

fn notes(dir: &Path) -> Value {
    fs::read(dir.join(".notes.json")).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(json!({}))
}

/// Both folders, newest first: `{dir, uploads: [...], "from-claude": [...]}`,
/// each `{name, size, modified, path}` (`path` as the session sees it), a
/// folder with `files`, and Claude's `note` if it left one.
pub fn list(root: &Path) -> Value {
    let mut out = json!({"dir": root.join(DIR).to_string_lossy(), "in_session": IN_SESSION});
    for kind in KINDS {
        let dir = root.join(DIR).join(kind);
        let notes = notes(&dir);
        let mut rows: Vec<Value> = fs::read_dir(&dir).into_iter().flatten().flatten().filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            if name.starts_with('.') {
                return None;          // hidden: the notes, and copies still being made
            }
            let m = e.path().symlink_metadata().ok()?;
            let mut row = json!({"name": name, "modified": secs(&m), "path": format!("{}/{}/{}", IN_SESSION, kind, name)});
            if m.is_dir() {
                let (size, files) = tally(&e.path());
                row["size"] = json!(size);
                row["files"] = json!(files);
            } else if m.is_file() {
                row["size"] = json!(m.len());
            } else {
                return None;          // a link or the like: not offered
            }
            if let Some(n) = notes.get(&name).and_then(|n| n.as_str()) {
                row["note"] = json!(n);
            }
            Some(row)
        }).collect();
        rows.sort_by(|a, b| b["modified"].as_u64().cmp(&a["modified"].as_u64()).then(a["name"].as_str().cmp(&b["name"].as_str())));
        out[kind] = json!(rows);
    }
    out
}

fn files_in(p: &Path, n: &mut usize) -> Result<(), String> {
    let m = p.symlink_metadata().map_err(|e| format!("{}: {}", p.display(), e))?;
    if m.is_dir() {
        for e in fs::read_dir(p).map_err(|e| format!("{}: {}", p.display(), e))?.flatten() {
            files_in(&e.path(), n)?;
        }
    } else if m.is_file() {
        *n += 1;
        if *n > MAX_FILES {
            return Err(format!("more than {} files: too many to hand over at once", MAX_FILES));
        }
    }
    Ok(())
}

/// Copy a file or folder (its files and folders; links aren't followed).
fn copy_tree(from: &Path, to: &Path) -> std::io::Result<()> {
    let m = from.symlink_metadata()?;
    if m.is_dir() {
        fs::create_dir(to)?;
        for e in fs::read_dir(from)?.flatten() {
            let name = safe_name(&e.file_name().to_string_lossy());
            copy_tree(&e.path(), &to.join(name))?;
        }
    } else if m.is_file() {
        fs::copy(from, to)?;
    }
    Ok(())
}

/// Copy the user's files into `uploads/`, never over one already there: a
/// name taken gets " (2)" and so on. Each goes in under a hidden name and is
/// renamed when whole, so Claude never sees half a file. Returns `{added:
/// [{name, path}], errors: [...]}`.
pub fn add(root: &Path, paths: &[PathBuf]) -> Value {
    let dir = root.join(DIR).join("uploads");
    let (mut added, mut errors) = (vec![], vec![]);
    if let Err(e) = fs::create_dir_all(&dir) {
        return json!({"added": [], "errors": [format!("{}: {}", dir.display(), e)]});
    }
    for p in paths {
        let mut n = 0;
        if let Err(e) = files_in(p, &mut n) {
            errors.push(e);
            continue;
        }
        let name = safe_name(&p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default());
        let tmp = dir.join(format!(".adding-{}-{}", std::process::id(), name));
        let _ = fs::remove_dir_all(&tmp).or_else(|_| fs::remove_file(&tmp));
        match copy_tree(p, &tmp) {
            Ok(()) => {
                let dest = unique_path(&dir, &name);
                match fs::rename(&tmp, &dest) {
                    Ok(()) => {
                        let name = dest.file_name().unwrap().to_string_lossy().to_string();
                        added.push(json!({"name": name, "path": format!("{}/uploads/{}", IN_SESSION, name),
                                          "from": p.to_string_lossy(), "files": n}));
                    }
                    Err(e) => errors.push(format!("{}: {}", p.display(), e)),
                }
            }
            Err(e) => {
                let _ = fs::remove_dir_all(&tmp).or_else(|_| fs::remove_file(&tmp));
                errors.push(format!("{}: {}", p.display(), e));
            }
        }
    }
    json!({"added": added, "errors": errors})
}

/// The user's actions on a listed file: `save` (a copy into Downloads),
/// `remove`, or `reveal` (the folder, in the file manager).
pub fn act(root: &Path, op: &str, kind: &str, name: &str) -> Value {
    let r = (|| -> Result<Value, String> {
        match op {
            "save" => {
                let p = entry(root, kind, name)?;
                let dest = unique_path(&downloads_dir(), &safe_name(name));
                copy_tree(&p, &dest).map_err(|e| e.to_string())?;
                Ok(json!({"saved": dest.to_string_lossy()}))
            }
            "remove" => {
                let p = entry(root, kind, name)?;
                if p.symlink_metadata().map(|m| m.is_dir()).unwrap_or(false) { fs::remove_dir_all(&p) } else { fs::remove_file(&p) }
                    .map_err(|e| e.to_string())?;
                let dir = kind_dir(root, kind)?;
                let mut n = notes(&dir);
                if n.as_object_mut().and_then(|o| o.remove(name)).is_some() {
                    let _ = fs::write(dir.join(".notes.json"), serde_json::to_vec_pretty(&n).unwrap_or_default());
                }
                Ok(json!({"removed": name}))
            }
            "reveal" => {
                let dir = kind_dir(root, kind)?;
                fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
                crate::launcher::reveal(&dir.to_string_lossy());
                Ok(json!({"shown": dir.to_string_lossy()}))
            }
            _ => Err(format!("no such action {}", op)),
        }
    })();
    r.unwrap_or_else(|e| json!({"error": e}))
}

/// Of what was dragged onto a window, the files and folders (a link dragged
/// from a page comes as a URI, not a path).
pub fn real_paths(paths: &[PathBuf]) -> Vec<PathBuf> {
    paths.iter().filter(|p| p.is_absolute() && p.symlink_metadata().is_ok()).cloned().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("adhoc-files-{}-{}", name, std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn files_go_in_without_overwriting_and_are_listed() {
        let (root, src) = (tmp("root"), tmp("src"));
        fs::write(src.join("notes.txt"), "hello").unwrap();
        fs::create_dir_all(src.join("folder/inner")).unwrap();
        fs::write(src.join("folder/a.csv"), "1,2").unwrap();
        fs::write(src.join("folder/inner/b.txt"), "b").unwrap();
        let r = add(&root, &[src.join("notes.txt"), src.join("notes.txt"), src.join("folder")]);
        assert_eq!(r["errors"], json!([]));
        let names: Vec<&str> = r["added"].as_array().unwrap().iter().map(|x| x["name"].as_str().unwrap()).collect();
        assert_eq!(names, ["notes.txt", "notes (2).txt", "folder"]);
        assert_eq!(r["added"][0]["path"], "/matter/workspace/files/uploads/notes.txt");
        assert_eq!(fs::read_to_string(root.join(DIR).join("uploads/folder/inner/b.txt")).unwrap(), "b");
        let l = list(&root);
        let folder = l["uploads"].as_array().unwrap().iter().find(|x| x["name"] == "folder").unwrap();
        assert_eq!((folder["files"].as_u64(), folder["size"].as_u64()), (Some(2), Some(4)));
        assert_eq!(l["uploads"].as_array().unwrap().len(), 3);
        assert_eq!(l["from-claude"], json!([]));
        // nothing half-copied is left behind or listed
        assert!(fs::read_dir(root.join(DIR).join("uploads")).unwrap().flatten().all(|e| !e.file_name().to_string_lossy().starts_with(".adding")));
    }

    #[test]
    fn notes_save_and_remove() {
        let (root, dl) = (tmp("root2"), tmp("dl"));
        let dir = root.join(DIR).join("from-claude");
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("memo.docx"), "memo").unwrap();
        fs::write(dir.join(".notes.json"), r#"{"memo.docx": "The draft you asked for"}"#).unwrap();
        assert_eq!(list(&root)["from-claude"][0]["note"], "The draft you asked for");
        std::env::set_var("ADHOC_DOWNLOAD_DIR", &dl);
        let a = act(&root, "save", "from-claude", "memo.docx");
        let b = act(&root, "save", "from-claude", "memo.docx");
        assert_eq!(fs::read_to_string(a["saved"].as_str().unwrap()).unwrap(), "memo");
        assert!(b["saved"].as_str().unwrap().ends_with("memo (2).docx"));
        assert!(act(&root, "save", "from-claude", "../../matter.toml")["error"].is_string());
        assert!(act(&root, "remove", "store", "x")["error"].is_string());
        assert_eq!(act(&root, "remove", "from-claude", "memo.docx")["removed"], "memo.docx");
        assert_eq!(list(&root)["from-claude"], json!([]));
        assert_eq!(notes(&dir), json!({}));
    }

    #[test]
    fn only_real_paths_count_as_dropped_files() {
        let src = tmp("src3");
        fs::write(src.join("x.pdf"), "x").unwrap();
        let got = real_paths(&[src.join("x.pdf"), PathBuf::from("https://example.com/a"), PathBuf::from("relative.txt")]);
        assert_eq!(got, vec![src.join("x.pdf")]);
    }
}
