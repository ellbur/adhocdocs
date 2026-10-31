//! Paginated documents through MuPDF: page sizes and page images.
//! MuPDF objects are not Send, so one worker thread owns this cache.

use mupdf::{Colorspace, Document, ImageFormat, Matrix};
use std::collections::HashMap;

const CACHE_DOCS: usize = 8;

const CACHE_PAGES: usize = 64;

pub struct Renderer {
    docs: HashMap<String, Document>,
    order: Vec<String>,
    // recently rendered pages, so a prefetched page is served at once
    pages: HashMap<(String, i32, u32), Vec<u8>>,
    page_order: Vec<(String, i32, u32)>,
}

pub fn magic_for(kind: &str, name: &str) -> String {
    if kind == "pdf" {
        "application/pdf".into()
    } else {
        // MuPDF picks image handlers from the extension
        name.rsplit('.').next().map(|e| format!("x.{}", e.to_lowercase())).unwrap_or_else(|| "x.png".into())
    }
}

impl Renderer {
    pub fn new() -> Self {
        Renderer { docs: HashMap::new(), order: vec![], pages: HashMap::new(), page_order: vec![] }
    }

    fn doc(&mut self, content: &str, bytes: impl FnOnce() -> Option<(Vec<u8>, String)>) -> Result<&Document, String> {
        if !self.docs.contains_key(content) {
            let (data, magic) = bytes().ok_or("content not found")?;
            let d = Document::from_bytes(&data, &magic).map_err(|e| e.to_string())?;
            self.docs.insert(content.to_string(), d);
            self.order.push(content.to_string());
            if self.order.len() > CACHE_DOCS {
                let old = self.order.remove(0);
                self.docs.remove(&old);
            }
        }
        Ok(&self.docs[content])
    }

    /// Width and height in points of each page.
    pub fn sizes(&mut self, content: &str, bytes: impl FnOnce() -> Option<(Vec<u8>, String)>) -> Result<Vec<(f32, f32)>, String> {
        let d = self.doc(content, bytes)?;
        let n = d.page_count().map_err(|e| e.to_string())?;
        (0..n)
            .map(|i| {
                let b = d.load_page(i).and_then(|p| p.bounds()).map_err(|e| e.to_string())?;
                Ok((b.x1 - b.x0, b.y1 - b.y0))
            })
            .collect()
    }

    /// Page `n` (from 1) as PNG, `width` pixels wide.
    pub fn png(&mut self, content: &str, n: i32, width: u32, bytes: impl FnOnce() -> Option<(Vec<u8>, String)>) -> Result<Vec<u8>, String> {
        let key = (content.to_string(), n, width);
        if let Some(png) = self.pages.get(&key) {
            return Ok(png.clone());
        }
        let png = self.render(content, n, width, bytes)?;
        self.pages.insert(key.clone(), png.clone());
        self.page_order.push(key);
        if self.page_order.len() > CACHE_PAGES {
            let old = self.page_order.remove(0);
            self.pages.remove(&old);
        }
        Ok(png)
    }

    fn render(&mut self, content: &str, n: i32, width: u32, bytes: impl FnOnce() -> Option<(Vec<u8>, String)>) -> Result<Vec<u8>, String> {
        let d = self.doc(content, bytes)?;
        let page = d.load_page(n - 1).map_err(|e| e.to_string())?;
        let b = page.bounds().map_err(|e| e.to_string())?;
        let scale = width as f32 / (b.x1 - b.x0).max(1.0);
        let pix = page
            .to_pixmap(&Matrix::new_scale(scale, scale), &Colorspace::device_rgb(), false, false)
            .map_err(|e| e.to_string())?;
        let mut out = vec![];
        pix.write_to(&mut out, ImageFormat::PNG).map_err(|e| e.to_string())?;
        Ok(out)
    }
}
