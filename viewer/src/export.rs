//! Downloads: a document saved to the user's Downloads folder as the
//! original, a PDF, a PDF with the annotations on it, or its text.
//!
//! Only the shell asks for these (the export menu, a click by the user),
//! over IPC: the API Claude reaches has no way to write a file on the host.
//! The shell sends what only it knows: for paginated documents, the
//! annotations' boxes from the layer geometry; for everything else, the
//! HTML to lay out (the presentation, with the annotations marked in it).
//! MuPDF does the rest: HTML layout, image-to-PDF, and PDF annotations.

use crate::matter::Matter;
use crate::render::magic_for;
use mupdf::color::AnnotationColor;
use mupdf::pdf::{PdfDocument, PdfWriteOptions};
use mupdf::text_page::TextPageFlags;
use mupdf::{Document, Point, Quad, Rect};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// The user's Downloads folder: XDG_DOWNLOAD_DIR, else ~/Downloads, else home.
pub fn downloads_dir() -> PathBuf {
    if let Ok(d) = std::env::var("ADHOC_DOWNLOAD_DIR").or_else(|_| std::env::var("XDG_DOWNLOAD_DIR")) {
        if !d.is_empty() {
            return PathBuf::from(d);
        }
    }
    let home = std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")).map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("."));
    // the folder xdg-user-dirs set up, if it says
    if let Ok(cfg) = std::fs::read_to_string(home.join(".config/user-dirs.dirs")) {
        for line in cfg.lines() {
            if let Some(v) = line.strip_prefix("XDG_DOWNLOAD_DIR=") {
                let v = v.trim_matches('"').replace("$HOME", &home.to_string_lossy());
                if Path::new(&v).is_dir() {
                    return PathBuf::from(v);
                }
            }
        }
    }
    let d = home.join("Downloads");
    if d.is_dir() { d } else { home }
}

/// A file name safe on any system, from a document's name.
pub fn safe_name(name: &str) -> String {
    let s: String = name.chars().map(|c| if c.is_control() || "/\\:*?\"<>|".contains(c) { '_' } else { c }).collect();
    let s = s.trim().trim_start_matches('.').to_string();
    if s.is_empty() { "document".into() } else { s.chars().take(150).collect() }
}

/// `dir/name`, or `dir/name (2)` and so on if that exists: nothing is overwritten.
pub fn unique_path(dir: &Path, name: &str) -> PathBuf {
    let p = dir.join(name);
    if !p.exists() {
        return p;
    }
    let (stem, ext) = match name.rfind('.') {
        Some(i) if i > 0 => (&name[..i], &name[i..]),
        _ => (name, ""),
    };
    (2..).map(|n| dir.join(format!("{} ({}){}", stem, n, ext))).find(|p| !p.exists()).unwrap()
}

/// The name without its extension, plus `ext`.
fn with_ext(name: &str, ext: &str) -> String {
    let stem = match name.rfind('.') {
        Some(i) if i > 0 && name.len() - i <= 6 => &name[..i],
        _ => name,
    };
    format!("{}{}", stem, ext)
}

fn color(hex: &str) -> AnnotationColor {
    let h = hex.trim_start_matches('#');
    let c = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("ff"), 16).unwrap_or(255) as f32 / 255.0;
    if h.len() >= 6 {
        AnnotationColor::Rgb { red: c(0), green: c(2), blue: c(4) }
    } else {
        AnnotationColor::Rgb { red: 1.0, green: 0.85, blue: 0.2 }
    }
}

fn pdf_bytes(pdf: &PdfDocument) -> Result<Vec<u8>, String> {
    let mut opts = PdfWriteOptions::default();
    opts.set_compress(true).set_compress_fonts(true).set_compress_images(true).set_garbage_level(3);
    let mut out = vec![];
    pdf.write_to_with_options(&mut out, opts).map_err(|e| e.to_string())?;
    Ok(out)
}

/// Lay out HTML as US Letter pages, as a PDF document.
pub fn html_to_pdf_doc(html: &str) -> Result<PdfDocument, String> {
    let mut d = Document::from_bytes(html.as_bytes(), "text/html").map_err(|e| e.to_string())?;
    d.layout(612.0, 792.0, 11.0).map_err(|e| e.to_string())?;
    d.convert_to_pdf(0, -1, 0).map_err(|e| e.to_string())
}

pub fn html_to_pdf(html: &str) -> Result<Vec<u8>, String> {
    pdf_bytes(&html_to_pdf_doc(html)?)
}

/// Where marked text landed once HTML is laid out. MuPDF's layout can't
/// paint an inline background, so the shell marks each annotation's text
/// with a color that stands for it, #01xxyy for mark xxyy (from 1), all but
/// black; this finds those characters and returns, per page and mark, a
/// box per run of them on a line, as fractions of the page.
fn marked_boxes(pdf: &PdfDocument) -> Result<Vec<(i32, u32, Vec<[f32; 4]>)>, String> {
    let mut out: Vec<(i32, u32, Vec<[f32; 4]>)> = vec![];
    for i in 0..pdf.page_count().map_err(|e| e.to_string())? {
        let page = pdf.load_page(i).map_err(|e| e.to_string())?;
        let b = page.bounds().map_err(|e| e.to_string())?;
        let (w, h) = ((b.x1 - b.x0).max(1.0), (b.y1 - b.y0).max(1.0));
        let tp = page.to_text_page(TextPageFlags::empty()).map_err(|e| e.to_string())?;
        for block in tp.blocks() {
            for line in block.lines() {
                let mut run: Option<(u32, [f32; 4])> = None;
                let mut flush = |run: &mut Option<(u32, [f32; 4])>| {
                    if let Some((mark, r)) = run.take() {
                        let bx = [(r[0] - b.x0) / w, (r[1] - b.y0) / h, (r[2] - b.x0) / w, (r[3] - b.y0) / h];
                        match out.iter_mut().find(|(p, m, _)| *p == i + 1 && *m == mark) {
                            Some((_, _, boxes)) => boxes.push(bx),
                            None => out.push((i + 1, mark, vec![bx])),
                        }
                    }
                };
                for c in line.chars() {
                    let rgb = c.argb() & 0xff_ffff;
                    let mark = if rgb >> 16 == 0x01 && rgb & 0xffff != 0 { rgb & 0xffff } else { 0 };
                    let q = c.quad();
                    let r = [q.ul.x.min(q.ll.x), q.ul.y.min(q.ur.y), q.ur.x.max(q.lr.x), q.ll.y.max(q.lr.y)];
                    match &mut run {
                        Some((m, rr)) if *m == mark && mark != 0 => {
                            *rr = [rr[0].min(r[0]), rr[1].min(r[1]), rr[2].max(r[2]), rr[3].max(r[3])];
                        }
                        _ => {
                            flush(&mut run);
                            if mark != 0 {
                                run = Some((mark, r));
                            }
                        }
                    }
                }
                flush(&mut run);
            }
        }
    }
    Ok(out)
}

/// Annotations on a paginated document: each has a page (from 1), boxes as
/// fractions of the page ([x0, y0, x1, y1]), a color and the text a reader
/// sees on it. A document-level note goes on the first page's corner.
fn annotate(pdf: &mut PdfDocument, annots: &[Value], doc_note: &str) -> Result<usize, String> {
    let mut n = 0;
    let pages = pdf.page_count().map_err(|e| e.to_string())?;
    for a in annots {
        let p = a["page"].as_i64().unwrap_or(0) as i32;
        if p < 1 || p > pages {
            continue;
        }
        let mut page = pdf.load_pdf_page(p - 1).map_err(|e| e.to_string())?;
        let b = page.bounds().map_err(|e| e.to_string())?;
        let (w, h) = (b.x1 - b.x0, b.y1 - b.y0);
        let quads: Vec<Quad> = a["boxes"].as_array().into_iter().flatten().filter_map(|r| {
            let r: Vec<f32> = r.as_array()?.iter().filter_map(|v| v.as_f64().map(|v| v as f32)).collect();
            if r.len() != 4 { return None; }
            let (x0, y0, x1, y1) = (b.x0 + r[0] * w, b.y0 + r[1] * h, b.x0 + r[2] * w, b.y0 + r[3] * h);
            Some(Quad::new(Point::new(x0, y0), Point::new(x1, y0), Point::new(x0, y1), Point::new(x1, y1)))
        }).collect();
        if quads.is_empty() {
            continue;
        }
        let region = a["kind"] == "region";
        let mut annot = if region {
            let q = &quads[0];
            page.add_square_annotation(Rect::new(q.ul.x, q.ul.y, q.lr.x, q.lr.y)).map_err(|e| e.to_string())?
        } else {
            page.add_highlight_annotation(quads).map_err(|e| e.to_string())?
        };
        annot.set_color(color(a["color"].as_str().unwrap_or(""))).map_err(|e| e.to_string())?;
        if let Some(t) = a["text"].as_str().filter(|t| !t.is_empty()) {
            annot.set_contents(t).map_err(|e| e.to_string())?;
        }
        if let Some(t) = a["author"].as_str().filter(|t| !t.is_empty()) {
            annot.set_author(t).map_err(|e| e.to_string())?;
        }
        annot.update().map_err(|e| e.to_string())?;
        page.update().map_err(|e| e.to_string())?;
        n += 1;
    }
    if !doc_note.is_empty() && pages > 0 {
        let mut page = pdf.load_pdf_page(0).map_err(|e| e.to_string())?;
        let b = page.bounds().map_err(|e| e.to_string())?;
        let mut annot = page.add_text_annotation(Rect::new(b.x0 + 8.0, b.y0 + 8.0, b.x0 + 28.0, b.y0 + 28.0), doc_note)
            .map_err(|e| e.to_string())?;
        annot.set_color(color("#f5c518")).map_err(|e| e.to_string())?;
        annot.update().map_err(|e| e.to_string())?;
        page.update().map_err(|e| e.to_string())?;
        n += 1;
    }
    Ok(n)
}

/// What a request asks for, written to the Downloads folder: `{saved: path}`
/// or `{error}`. Formats: `original`, `pdf`, `annotated`, `text`.
pub fn export(m: &Matter, req: &Value) -> Value {
    match build(m, req) {
        Ok((name, bytes)) => {
            let dir = downloads_dir();
            let path = unique_path(&dir, &safe_name(&name));
            match std::fs::write(&path, bytes) {
                Ok(()) => json!({"saved": path}),
                Err(e) => json!({"error": format!("couldn't write {}: {}", path.display(), e)}),
            }
        }
        Err(e) => json!({"error": e}),
    }
}

fn build(m: &Matter, req: &Value) -> Result<(String, Vec<u8>), String> {
    let doc = m.resolve_doc(req["doc"].as_str().unwrap_or("")).ok_or("document not in this matter")?;
    let content = m.state.documents[&doc].clone();
    let meta = m.metadata(&doc);
    let kind = meta["kind"].as_str().unwrap_or("").to_string();
    let short = doc.trim_start_matches("sha256:").chars().take(12).collect::<String>();
    let name = meta["name"].as_str().filter(|n| !n.is_empty()).map(String::from).unwrap_or_else(|| short.clone());
    let original = || m.bytes(&content).ok_or_else(|| "the original isn't in the store".to_string());
    match req["format"].as_str().unwrap_or("") {
        "original" => Ok((name.clone(), original()?)),
        "text" => {
            let layer = req["layer"].as_str().and_then(|l| m.layer(l)).or_else(|| m.primary_layer(&content)).ok_or("this document has no text")?;
            if m.state.layers.get(&content).map_or(true, |ls| !ls.iter().any(|l| l.id == layer.id)) {
                return Err("that layer isn't this document's".into());
            }
            // pages are form feeds in layer text, as in any plain-text page break
            let t = m.text(&layer.text).unwrap_or_default();
            Ok((with_ext(&name, ".txt"), t.into_bytes()))
        }
        f @ ("pdf" | "annotated") => {
            let annots = req["annots"].as_array().cloned().unwrap_or_default();
            let doc_note = req["doc_note"].as_str().unwrap_or("");
            let out_name = if f == "annotated" { with_ext(&name, " (annotated).pdf") } else { with_ext(&name, ".pdf") };
            if kind == "pdf" || kind == "image" {
                let bytes = original()?;
                if f == "pdf" && kind == "pdf" {
                    return Ok((out_name, bytes));
                }
                let mut pdf = if kind == "pdf" {
                    PdfDocument::from_bytes(&bytes).map_err(|e| e.to_string())?
                } else {
                    Document::from_bytes(&bytes, &magic_for(&kind, &name)).and_then(|d| d.convert_to_pdf(0, -1, 0)).map_err(|e| e.to_string())?
                };
                if f == "annotated" {
                    annotate(&mut pdf, &annots, doc_note)?;
                }
                return Ok((out_name, pdf_bytes(&pdf)?));
            }
            let html = req["html"].as_str().ok_or("nothing to lay out")?;
            let mut pdf = html_to_pdf_doc(html)?;
            if f == "annotated" {
                // the shell's annotations name their marks; their boxes are where those marks landed
                let placed: Vec<Value> = marked_boxes(&pdf)?.into_iter().filter_map(|(page, mark, boxes)| {
                    let a = annots.iter().find(|a| a["mark"].as_u64() == Some(mark as u64))?;
                    let mut a = a.clone();
                    a["page"] = json!(page);
                    a["boxes"] = json!(boxes);
                    Some(a)
                }).collect();
                annotate(&mut pdf, &placed, doc_note)?;
            }
            Ok((out_name, pdf_bytes(&pdf)?))
        }
        _ => Err("no such format".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names() {
        assert_eq!(with_ext("memo.pdf", ".txt"), "memo.txt");
        assert_eq!(with_ext("Lease amendment call", ".pdf"), "Lease amendment call.pdf");
        assert_eq!(with_ext("a.eml", " (annotated).pdf"), "a (annotated).pdf");
        assert_eq!(safe_name("../RE: a/b?"), "_RE_ a_b_");
        assert_eq!(safe_name(""), "document");
    }

    #[test]
    fn nothing_is_overwritten() {
        let d = std::env::temp_dir().join(format!("adhoc-export-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("x.pdf"), b"").unwrap();
        assert_eq!(unique_path(&d, "x.pdf"), d.join("x (2).pdf"));
        assert_eq!(unique_path(&d, "y.pdf"), d.join("y.pdf"));
        std::fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn html_lays_out_as_pdf() {
        let pdf = html_to_pdf("<html><body><p>Hello <mark>there</mark></p></body></html>").unwrap();
        assert!(pdf.starts_with(b"%PDF"));
        let d = PdfDocument::from_bytes(&pdf).unwrap();
        assert_eq!(d.page_count().unwrap(), 1);
    }

    #[test]
    fn marked_text_is_found_after_layout() {
        let pdf = html_to_pdf_doc("<p>Hello <span style=\"color:#010002\">there</span> and <span style=\"color:#010001\">you</span></p>").unwrap();
        let found = marked_boxes(&pdf).unwrap();
        let mut marks: Vec<u32> = found.iter().map(|(_, m, _)| *m).collect();
        marks.sort();
        assert_eq!(marks, vec![1, 2]);
        let (_, _, boxes) = found.iter().find(|(_, m, _)| *m == 2).unwrap();
        assert_eq!(boxes.len(), 1);
        assert!(boxes[0][0] > 0.0 && boxes[0][2] > boxes[0][0] && boxes[0][2] < 1.0);
    }

    #[test]
    fn highlights_go_on_the_page() {
        let mut d = PdfDocument::from_bytes(&html_to_pdf("<p>Hello there</p>").unwrap()).unwrap();
        let n = annotate(&mut d, &[json!({"page": 1, "boxes": [[0.1, 0.1, 0.3, 0.12]], "color": "#ffcc00", "text": "hot: a note"}),
                                     json!({"page": 9, "boxes": [[0.1, 0.1, 0.3, 0.12]]})], "Tags: hot").unwrap();
        assert_eq!(n, 2);   // the page-9 one is dropped, the document note added
        let d = PdfDocument::from_bytes(&pdf_bytes(&d).unwrap()).unwrap();
        let page = d.load_pdf_page(0).unwrap();
        assert_eq!(page.annotations().count(), 2);
    }
}
