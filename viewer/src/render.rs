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
    pages: HashMap<(String, i32, u32, bool), Vec<u8>>,
    page_order: Vec<(String, i32, u32, bool)>,
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

    /// Page `n` (from 1) as PNG, `width` pixels wide; `dark` for the dark
    /// document theme (see `darken`).
    pub fn png(&mut self, content: &str, n: i32, width: u32, dark: bool, bytes: impl FnOnce() -> Option<(Vec<u8>, String)>) -> Result<Vec<u8>, String> {
        let key = (content.to_string(), n, width, dark);
        if let Some(png) = self.pages.get(&key) {
            return Ok(png.clone());
        }
        let png = self.render(content, n, width, dark, bytes)?;
        self.pages.insert(key.clone(), png.clone());
        self.page_order.push(key);
        if self.page_order.len() > CACHE_PAGES {
            let old = self.page_order.remove(0);
            self.pages.remove(&old);
        }
        Ok(png)
    }

    fn render(&mut self, content: &str, n: i32, width: u32, dark: bool, bytes: impl FnOnce() -> Option<(Vec<u8>, String)>) -> Result<Vec<u8>, String> {
        let d = self.doc(content, bytes)?;
        let page = d.load_page(n - 1).map_err(|e| e.to_string())?;
        let b = page.bounds().map_err(|e| e.to_string())?;
        let scale = width as f32 / (b.x1 - b.x0).max(1.0);
        let mut pix = page
            .to_pixmap(&Matrix::new_scale(scale, scale), &Colorspace::device_rgb(), false, false)
            .map_err(|e| e.to_string())?;
        if dark {
            let (stride, n, w) = (pix.stride() as usize, pix.n() as usize, pix.width() as usize);
            for row in pix.samples_mut().chunks_mut(stride) {
                darken(&mut row[..w * n], n);
            }
        }
        let mut out = vec![];
        pix.write_to(&mut out, ImageFormat::PNG).map_err(|e| e.to_string())?;
        Ok(out)
    }
}

/// Dark pages: each pixel's lightness inverted and its hue kept (what CSS
/// `invert(1) hue-rotate(180deg)` approximates), squeezed between the dark
/// theme's page color and a soft white so it sits with the rest of the
/// window. Done here, once per rendered page, so the page costs the web
/// view nothing to show (a CSS filter is redone on every repaint).
pub fn darken(px: &mut [u8], n: usize) {
    const LO: i32 = 30;    // white paper becomes the dark page color (#1e1e1e)
    const HI: i32 = 228;   // black ink becomes a soft white
    for p in px.chunks_exact_mut(n) {
        let (r, g, b) = (p[0] as i32, p[1] as i32, p[2] as i32);
        let y = (r * 77 + g * 150 + b * 29) >> 8;
        let target = LO + ((255 - y) * (HI - LO)) / 255;
        let d = target - y;
        p[0] = (r + d).clamp(0, 255) as u8;
        p[1] = (g + d).clamp(0, 255) as u8;
        p[2] = (b + d).clamp(0, 255) as u8;
    }
}

#[cfg(test)]
mod tests {
    use super::darken;

    #[test]
    fn dark_pages_invert_lightness_and_keep_hue() {
        let mut px = vec![255, 255, 255, 0, 0, 0, 200, 30, 30];
        darken(&mut px, 3);
        assert_eq!(&px[..3], &[30, 30, 30]);        // paper
        assert_eq!(&px[3..6], &[228, 228, 228]);    // ink
        assert!(px[6] > px[7] && px[6] > px[8]);    // red stays red
    }
}
