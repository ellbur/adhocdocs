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
    // where each page's ink is (see `margins`), kept while the viewer runs: small, and costly to find again
    margins: HashMap<(String, i32), [f32; 4]>,
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
        Renderer { docs: HashMap::new(), order: vec![], pages: HashMap::new(), page_order: vec![], margins: HashMap::new() }
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

    /// Where page `n`'s ink is, as fractions of the page ([x0, y0, x1, y1]),
    /// with a little room around it: what's left when the blank margins are
    /// cropped. Found on a small grayscale rendering (about 1.5 ms a page);
    /// a blank page is all margin, and is kept whole.
    pub fn margins(&mut self, content: &str, n: i32, bytes: impl FnOnce() -> Option<(Vec<u8>, String)>) -> Result<[f32; 4], String> {
        let key = (content.to_string(), n);
        if let Some(m) = self.margins.get(&key) {
            return Ok(*m);
        }
        let d = self.doc(content, bytes)?;
        let page = d.load_page(n - 1).map_err(|e| e.to_string())?;
        let b = page.bounds().map_err(|e| e.to_string())?;
        let scale = 200.0 / (b.x1 - b.x0).max(1.0);
        let pix = page
            .to_pixmap(&Matrix::new_scale(scale, scale), &Colorspace::device_gray(), false, false)
            .map_err(|e| e.to_string())?;
        let m = ink_box(pix.samples(), pix.width() as usize, pix.height() as usize, pix.stride() as usize);
        self.margins.insert(key, m);
        Ok(m)
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
    use super::{darken, ink_box};

    #[test]
    fn ink_box_finds_the_text_and_ignores_specks() {
        let (w, h) = (100, 200);
        let mut px = vec![255u8; w * h];
        for y in 50..60 { for x in 20..70 { px[y * w + x] = 0; } }
        px[5 * w + 95] = 0;                          // a speck in the corner
        let b = ink_box(&px, w, h, w);
        assert!((b[0] - 0.175).abs() < 1e-4 && (b[2] - 0.725).abs() < 1e-4);
        assert!(b[1] > 0.2 && b[1] < 0.25 && b[3] > 0.3 && b[3] < 0.32);
        assert_eq!(ink_box(&vec![255u8; w * h], w, h, w), [0.0, 0.0, 1.0, 1.0]);   // blank: kept whole
    }

    #[test]
    fn dark_pages_invert_lightness_and_keep_hue() {
        let mut px = vec![255, 255, 255, 0, 0, 0, 200, 30, 30];
        darken(&mut px, 3);
        assert_eq!(&px[..3], &[30, 30, 30]);        // paper
        assert_eq!(&px[3..6], &[228, 228, 228]);    // ink
        assert!(px[6] > px[7] && px[6] > px[8]);    // red stays red
    }
}

/// The box around a grayscale image's ink, as fractions, with room around
/// it. Ink is darker than near-white; a row or column needs two such
/// pixels to count, so a speck of scanner dust doesn't hold a margin open.
pub fn ink_box(px: &[u8], w: usize, h: usize, stride: usize) -> [f32; 4] {
    const INK: u8 = 235;
    const PAD: f32 = 0.025;
    if w == 0 || h == 0 {
        return [0.0, 0.0, 1.0, 1.0];
    }
    let mut cols = vec![0u32; w];
    let mut rows = vec![0u32; h];
    for y in 0..h {
        for (x, &v) in px[y * stride..y * stride + w].iter().enumerate() {
            if v < INK {
                cols[x] += 1;
                rows[y] += 1;
            }
        }
    }
    let first = |v: &[u32]| v.iter().position(|&c| c >= 2);
    let last = |v: &[u32]| v.iter().rposition(|&c| c >= 2);
    let (Some(x0), Some(x1), Some(y0), Some(y1)) = (first(&cols), last(&cols), first(&rows), last(&rows)) else {
        return [0.0, 0.0, 1.0, 1.0];
    };
    // the same room on every side, in page widths (pages are taller than wide)
    let pad_y = PAD * w as f32 / h as f32;
    [
        (x0 as f32 / w as f32 - PAD).max(0.0),
        (y0 as f32 / h as f32 - pad_y).max(0.0),
        ((x1 + 1) as f32 / w as f32 + PAD).min(1.0),
        ((y1 + 1) as f32 / h as f32 + pad_y).min(1.0),
    ]
}
