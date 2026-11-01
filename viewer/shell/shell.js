// The viewer's own code. It is the only script that runs in the viewer:
// presentations, pages, lists and Claude's panes are sanitized when they
// arrive and served with `script-src 'none'`, and this code reaches into
// their frames to read selections and carry out their declarative
// attributes. (A sandboxed iframe would block scripts too, but WebKit then
// also refuses to run this code's event listeners on the frame.)
//
// Offsets are Unicode code points into layer text, as everywhere else.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
};
const cpLen = (s) => { let n = 0; for (const _ of s) n++; return n; };
const MAX_GRID_ROWS = 3000;
const colName = (i) => { let s = ""; for (i++; i; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s; return s; };
const colIndex = (letters) => [...letters].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1;
// "B2:D10" -> {r0, c0, r1, c1}, 0-based and inclusive
function parseCells(spec) {
  const ends = String(spec).toUpperCase().split(":").map((x) => /^([A-Z]{1,3})([1-9]\d*)$/.exec(x));
  if (!ends.length || ends.length > 2 || ends.some((m) => !m)) return null;
  const [a, b = a] = ends.map((m) => [Number(m[2]) - 1, colIndex(m[1])]);
  return { r0: Math.min(a[0], b[0]), r1: Math.max(a[0], b[0]), c0: Math.min(a[1], b[1]), c1: Math.max(a[1], b[1]) };
}
const BUILTIN_KEYS = new Set(["j", "k", "m", "t", "v", "f", "N", "e", "E", "[", "]", " ", "Tab", "Enter", "Escape", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "PageDown", "PageUp", "Home", "End", "W", "H", "?"]);

// -- locators -------------------------------------------------------------------------

function parseLoc(s) {
  if (typeof s === "object") return s;
  const m = /^doc:([^@#]+)(?:@([^#]+))?(?:#(.*))?$/.exec(s);
  if (!m) throw new Error("not a locator: " + s);
  const loc = { doc: m[1] };
  if (m[2]) loc.layer = m[2];
  if (m[3]) {
    const sel = {};
    for (const part of m[3].split("&")) {
      const [k, v] = part.split("=");
      if (k === "char") sel.char = v.split(",").map(Number);
      else if (k === "page") sel.page = v.includes("-") ? v.split("-").map(Number) : Number(v);
      else if (k === "xywh") sel.xywh = v.split(",").map(Number);
      else if (k === "sheet") sel.sheet = decodeURIComponent(v);
      else sel[k] = v;
    }
    loc.select = sel;
  }
  return loc;
}
function locString(loc) {
  let s = "doc:" + loc.doc;
  if (loc.layer) s += "@" + loc.layer;
  const sel = loc.select || {};
  const parts = [];
  if (sel.page !== undefined) parts.push("page=" + (Array.isArray(sel.page) ? sel.page.join("-") : sel.page));
  if (sel.char) parts.push("char=" + sel.char.join(","));
  if (sel.xywh) parts.push("xywh=" + sel.xywh.join(","));
  if (sel.sheet !== undefined) parts.push("sheet=" + encodeURIComponent(sel.sheet));
  if (sel.cells) parts.push("cells=" + sel.cells);
  if (parts.length) s += "#" + parts.join("&");
  return s;
}
const short = (h) => (h || "").replace("sha256:", "").slice(0, 10);

// -- data from the viewer -------------------------------------------------------------

const cache = { docs: {}, text: {}, geometry: {}, segments: {}, detected: {}, dates: {}, folded: {} };
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}
async function docInfo(doc) { return cache.docs[doc] ??= getJSON("/api/doc/" + encodeURIComponent(doc)); }
async function layerText(id) {
  return cache.text[id] ??= fetch(`/api/layer/${id}/text`).then((r) => r.text()).then((t) => ({ text: t, cps: Array.from(t) }));
}
async function geometry(id) { return cache.geometry[id] ??= getJSON(`/api/layer/${id}/geometry`); }
async function segments(id) { return cache.segments[id] ??= getJSON(`/api/layer/${id}/segments`); }
// the entities index's detections for a layer (they appear once the index reaches it, so an empty answer isn't kept)
async function detected(id) {
  const d = await (cache.detected[id] ??= getJSON(`/api/layer/${id}/detected`).catch(() => []));
  if (!d.length) delete cache.detected[id];
  return d;
}
// the dates index's date mentions for a layer, the same way
async function layerDates(id) {
  const d = await (cache.dates[id] ??= getJSON(`/api/layer/${id}/dates`).catch(() => []));
  if (!d.length) delete cache.dates[id];
  return d;
}

// -- state -------------------------------------------------------------------------------

const adhoc = {
  panes: [], focused: null, user: "user", history: [], nextId: 1, window: "w1",
  elsewhereMap: {},       // pane ID -> {window, kind}, for every window's panes
  annotations: { definitions: [], tags: [], notes: [], entities: [] }, annLogSize: -1,
  // the entity layer: "all" (known and suggested), "known", or "off"; candidates: detected key -> {docs, …}
  entityMode: (() => { try { return localStorage.getItem("adhoc.entityMode") || "all"; } catch { return "all"; } })(),
  candidates: {}, candidatesAt: 0,
};
window.adhoc = adhoc;

function post(msg) { window.ipc.postMessage(JSON.stringify(msg)); }
function pane(id) { return adhoc.panes.find((p) => p.id === id); }
// Pane IDs are unique across windows: p1, p2, … in the first, w2-p1, … in others.
function newPaneId() { return (adhoc.window === "w1" ? "p" : adhoc.window + "-p") + adhoc.nextId++; }
// A pane in this window or another one.
function paneExists(id) { return !!id && (!!pane(id) || !!adhoc.elsewhereMap[id]); }
function paneKind(id) { return pane(id)?.kind ?? adhoc.elsewhereMap[id]?.kind; }
// Call a method on a pane here or, through the host, in the window that has it.
const RELAYABLE = new Set(["seqOpen", "open", "step", "follow"]);
function callPane(id, method, ...args) {
  const p = pane(id);
  if (p) return p[method]?.(...args);
  if (adhoc.elsewhereMap[id]) post({ type: "relay", pane: id, method, args });
}
function focusedPane() { return pane(adhoc.focused); }
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 3500);
}
function addHistory(loc, source) {
  const entry = { at: new Date().toISOString(), loc: typeof loc === "string" ? loc : locString(loc), source };
  adhoc.history.push(entry);                 // this window's, for marking visited items
  if (adhoc.history.length > 1000) adhoc.history.shift();
  post({ type: "history", entry });         // the host keeps every window's, for `view history`
}

function snapshot() {
  return {
    running: true, focused: adhoc.focused,
    windows: [{ id: adhoc.window, layout: adhoc.tile || "tabs", tree: treeSpec(), panes: leaves() }],
    panes: adhoc.panes.map((p) => p.state()),
  };
}

// -- layout ---------------------------------------------------------------------------------
// A window's panes are arranged in a tree. Its root is a row of tabs; a tab
// holds a pane or a split, and a split puts a view beside the views it led to
// (a page and the documents its references opened), which are themselves
// tabs. Unrelated views get tabs of their own, so columns are spent only on
// views that are read together:
//
//   tabs(split(page, tabs(doc, doc)), split(page, tabs(doc)), list)
//
// Panes are never moved in the DOM (an iframe moved there reloads): they stay
// in #panes, positioned over the slots the tree is drawn with.

const tabsNode = (children = [], active = 0) => ({ type: "tabs", children, active });
const splitNode = (children, dir = "v") => ({ type: "split", dir, children, sizes: children.map(() => 1) });
const leafNode = (id) => ({ type: "pane", id });
adhoc.tree = tabsNode();
adhoc.tile = null;          // null: the root's tabs one at a time; or "split-v", "split-h", "grid" to tile them
adhoc.focusStack = [];      // most recently focused last, for where focus goes when a pane closes

// The nodes from the root down to a pane's leaf, or null.
function pathTo(id, node = adhoc.tree, path = []) {
  if (node.type === "pane") return node.id === id ? [...path, node] : null;
  for (const c of node.children) { const r = pathTo(id, c, [...path, node]); if (r) return r; }
  return null;
}
function leaves(node = adhoc.tree) { return node.type === "pane" ? [node.id] : node.children.flatMap((c) => leaves(c)); }
function addTab(group, node) { group.children.push(node); group.active = group.children.length - 1; }
function replaceChild(parent, old, node) {
  parent.children[parent.children.indexOf(old)] = node;
}

// A new, unrelated view: a tab of its own.
function placeTop(id) { addTab(adhoc.tree, leafNode(id)); }
// A view opened from another (a reference in a page, a list's item): beside it,
// in the group of views it has opened, as a new tab there.
function placeBeside(srcId, id) {
  const path = pathTo(srcId);
  if (!path || path.length < 2) return placeTop(id);
  const leaf = path[path.length - 1], parent = path[path.length - 2];
  if (parent.type === "split") {
    const next = parent.children[parent.children.indexOf(leaf) + 1];
    if (next?.type === "tabs") return addTab(next, leafNode(id));
    if (next) return replaceChild(parent, next, tabsNode([next, leafNode(id)], 1));
  }
  // a view that is itself beside the one that opened it: its neighbors take the
  // new one, so columns don't multiply
  const grand = path[path.length - 3];
  if (parent.type === "tabs" && grand?.type === "split" && grand.children.indexOf(parent) > 0) return addTab(parent, leafNode(id));
  replaceChild(parent, leaf, splitNode([leaf, tabsNode([leafNode(id)])]));
}
// Take a pane's leaf out, folding away groups and splits it leaves empty or alone.
function removeLeaf(id) {
  const path = pathTo(id);
  if (!path) return;
  for (let i = path.length - 1; i > 0; i--) {
    const node = path[i], parent = path[i - 1], k = parent.children.indexOf(node);
    const gone = node.type === "pane" || node.children.length === 0;
    if (gone) {
      parent.children.splice(k, 1);
      if (parent.sizes) parent.sizes.splice(k, 1);
      if (parent.type === "tabs" && (parent.active > k || parent.active === parent.children.length)) parent.active = Math.max(0, parent.active - 1);
      continue;
    }
    if (node.type === "split" && node.children.length === 1) {
      const only = node.children[0];
      // tabs left alone in a tab: its tabs join the outer ones
      if (parent.type === "tabs" && only.type === "tabs") {
        parent.children.splice(k, 1, ...only.children);
        if (parent.active === k) parent.active = k + only.active;
        else if (parent.active > k) parent.active += only.children.length - 1;
      } else parent.children[k] = only;
    }
    break;
  }
}

function tabLabel(node) {
  if (node.type === "pane") {
    const p = pane(node.id);
    if (!p) return node.id;
    return p.kind === "home" ? "⌂ Overview" : `${p.id} · ${p.title()}`;
  }
  const ids = leaves(node);
  const first = pane(ids[0]);
  return (first?.kind === "home" ? "⌂ Overview" : first ? first.title() : ids[0]) + (ids.length > 1 ? `  +${ids.length - 1}` : "");
}
function tabTitle(node) {
  return leaves(node).map((id) => { const p = pane(id); return p ? `${p.id} · ${p.title()}` : id; }).join("\n");
}

function renderTabs() {
  const slots = new Map();
  const focusedPath = adhoc.focused ? pathTo(adhoc.focused) || [] : [];
  const strip = (node, into) => {
    node.active = Math.max(0, Math.min(node.active, node.children.length - 1));
    into.replaceChildren(...node.children.map((c, i) => {
      const home = c.type === "pane" && pane(c.id)?.kind === "home";
      const cls = ["lt-tab", i === node.active ? "on" : "", focusedPath.includes(c) ? "focused" : "", home ? "home-tab" : ""].filter(Boolean).join(" ");
      return el("div", { class: cls, role: "tab", title: home ? "Overview: what this matter holds (H)" : tabTitle(c),
                         onclick: () => focus(adhoc.focusStack.findLast((id) => leaves(c).includes(id)) || leaves(c)[0]) },
        el("span", { class: "label" }, tabLabel(c)),
        home ? "" : el("span", { class: "x", title: c.type === "pane" ? "Close" : "Close these panes",
                                 onclick: (ev) => { ev.stopPropagation(); for (const id of leaves(c)) closePane(id); } }, "×"));
    }));
    return into;
  };
  const build = (node, top = false) => {
    if (node.type === "pane") { const s = el("div", { class: "lt-slot" }); slots.set(node.id, s); return s; }
    if (node.type === "split") {
      const box = el("div", { class: "lt-split " + node.dir });
      node.children.forEach((c, i) => {
        if (i) box.append(el("div", { class: "lt-splitter", onmousedown: (ev) => dragSplitter(ev, node, i, box) }));
        const cell = build(c);
        cell.style.flex = `${node.sizes[i] ?? 1} 1 0`;
        box.append(cell);
      });
      return box;
    }
    const tiled = top && adhoc.tile;
    if (top) strip(node, $("#tabs"));
    const body = el("div", { class: "lt-body" + (tiled ? " " + tiled : "") });
    node.children.forEach((c, i) => { if (tiled || i === node.active) body.append(build(c)); });
    if (top) return body;
    // a group of one shows no tab strip: the pane's own header says what it is
    return el("div", { class: "lt-tabs" }, ...(node.children.length > 1 ? [strip(node, el("div", { class: "lt-strip" }))] : []), body);
  };
  $("#layout").replaceChildren(build(adhoc.tree, true));
  adhoc.slots = slots;
  for (const p of adhoc.panes) p.root.classList.toggle("focused", p.id === adhoc.focused);
  positionPanes();
}
// Each pane over its slot; panes in tabs not showing are hidden.
function positionPanes() {
  const origin = $("#panes").getBoundingClientRect();
  for (const p of adhoc.panes) {
    const s = adhoc.slots?.get(p.id);
    if (!s) { p.root.style.display = "none"; continue; }
    const r = s.getBoundingClientRect();
    Object.assign(p.root.style, { display: "", left: `${r.left - origin.left}px`, top: `${r.top - origin.top}px`,
                                  width: `${r.width}px`, height: `${r.height}px` });
  }
}
new ResizeObserver(() => positionPanes()).observe($("#panes"));
function dragSplitter(ev, node, i, box) {
  ev.preventDefault();
  const cells = [...box.children].filter((c) => !c.classList.contains("lt-splitter"));
  const a = cells[i - 1], b = cells[i], horiz = node.dir === "v";
  const size = (e) => (horiz ? e.getBoundingClientRect().width : e.getBoundingClientRect().height);
  const pairPx = size(a) + size(b), pairW = node.sizes[i - 1] + node.sizes[i], startA = size(a);
  const start = horiz ? ev.clientX : ev.clientY;
  document.body.classList.add("dragging", horiz ? "drag-v" : "drag-h");
  const move = (e) => {
    const aPx = Math.max(120, Math.min(pairPx - 120, startA + (horiz ? e.clientX : e.clientY) - start));
    node.sizes[i - 1] = pairW * aPx / pairPx; node.sizes[i] = pairW - node.sizes[i - 1];
    a.style.flex = `${node.sizes[i - 1]} 1 0`; b.style.flex = `${node.sizes[i]} 1 0`;
    positionPanes();
  };
  const up = () => {
    document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
    document.body.classList.remove("dragging", "drag-v", "drag-h");
    changed();
  };
  document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
}
// The tree for `view state`: a pane ID, {tabs, active} or {split, sizes}.
function treeSpec(node = adhoc.tree) {
  if (node.type === "pane") return node.id;
  if (node.type === "tabs") return { tabs: node.children.map((c) => treeSpec(c)), active: node.active };
  return { split: node.dir, children: node.children.map((c) => treeSpec(c)), sizes: node.sizes.map((s) => Math.round(s / node.sizes.reduce((x, y) => x + y, 0) * 100) / 100) };
}

// Show a pane: every group on the way shows the tab that leads to it.
function reveal(id, render = true) {
  const path = pathTo(id);
  if (!path) return false;
  for (let i = 0; i < path.length - 1; i++) if (path[i].type === "tabs") path[i].active = path[i].children.indexOf(path[i + 1]);
  if (render) renderTabs();
  return true;
}
function focus(id) {
  if (!pane(id) || !reveal(id, false)) return;
  adhoc.focused = id;
  adhoc.focusStack = [...adhoc.focusStack.filter((x) => x !== id), id];
  renderTabs();
  pane(id).refresh?.();
}
// Add a pane: by default as a tab of its own; {beside: id} puts it with the
// views that pane opened.
function addPane(p, focusIt = true, where = {}) {
  adhoc.panes.push(p);
  $("#panes").append(p.root);
  p.root.addEventListener("mousedown", () => focus(p.id), true);
  if (where.beside && pathTo(where.beside)) placeBeside(where.beside, p.id); else placeTop(p.id);
  if (focusIt || !adhoc.focused) focus(p.id); else renderTabs();
  return p;
}
function closePane(id, moving = false) {
  const p = pane(id);
  if (!p) throw new Error("no pane " + id);
  p.root.remove();
  clearInterval(p.timer);
  adhoc.panes = adhoc.panes.filter((x) => x !== p);
  removeLeaf(id);
  adhoc.focusStack = adhoc.focusStack.filter((x) => x !== id);
  if (!moving) for (const q of adhoc.panes) if (q.link === id) q.link = null;
  if (adhoc.focused === id) {
    adhoc.focused = null;
    const next = adhoc.focusStack[adhoc.focusStack.length - 1] || leaves()[0];
    if (next) return focus(next);
  }
  if (adhoc.focused) reveal(adhoc.focused); else renderTabs();
}

// -- selections in any DOM that marks text with offsets -----------------------------------
// Elements with data-s (the viewer's own) or data-o (presentations) carry the
// layer offset of their first character.

function offsetOf(node, off) {
  const e = node.nodeType === 3 ? node.parentElement : node;
  const base = e && e.closest("[data-s],[data-o]");
  if (!base) return null;
  const start = Number(base.dataset.s ?? base.dataset.o);
  const r = base.ownerDocument.createRange();
  r.setStart(base, 0);
  try { r.setEnd(node, off); } catch { return start; }
  return start + cpLen(r.toString());
}
function selectionRange(doc) {
  const sel = doc.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const r = sel.getRangeAt(0);
  let s = offsetOf(r.startContainer, r.startOffset), e = offsetOf(r.endContainer, r.endOffset);
  if (s === null || e === null) {
    // an end outside any marked element: use the marked elements inside the range
    const marked = [...doc.querySelectorAll("[data-s],[data-o]")].filter((x) => r.intersectsNode(x));
    if (!marked.length) return null;
    const first = marked[0], last = marked[marked.length - 1];
    if (s === null) s = Number(first.dataset.s ?? first.dataset.o);
    if (e === null) e = Number(last.dataset.s ?? last.dataset.o) + cpLen(last.textContent);
  }
  return s < e ? [s, e] : null;
}
// The marked elements of a DOM, by offset, for pointAt to search.
function markedIndex(root) {
  const marked = [...root.querySelectorAll("[data-s],[data-o]")].map((m) => [Number(m.dataset.s ?? m.dataset.o), m]);
  marked.sort((a, b) => a[0] - b[0]);
  return marked;
}
// The DOM point for a layer offset.
function pointAt(root, off, index = null) {
  let best = null;
  if (index) {
    let lo = 0, hi = index.length - 1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (index[mid][0] <= off) { best = index[mid][1]; lo = mid + 1; } else hi = mid - 1; }
  } else {
    for (const m of root.querySelectorAll("[data-s],[data-o]")) {
      const b = Number(m.dataset.s ?? m.dataset.o);
      if (b <= off && (!best || b >= Number(best.dataset.s ?? best.dataset.o))) best = m;
    }
  }
  if (!best) return null;
  let left = off - Number(best.dataset.s ?? best.dataset.o);
  const walker = root.ownerDocument.createTreeWalker(best, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const cps = Array.from(n.data);
    if (left <= cps.length) return [n, cps.slice(0, left).join("").length];
    left -= cps.length;
  }
  return [best, best.childNodes.length];
}
function rangeRects(root, s, e, index = null) {
  const a = pointAt(root, s, index), b = pointAt(root, e, index);
  if (!a || !b) return [];
  const r = root.ownerDocument.createRange();
  r.setStart(...a); r.setEnd(...b);
  return [...r.getClientRects()];
}
function drawRangeBoxes(container, scroller, root, s, e, cls, index = null) {
  const origin = container.getBoundingClientRect();
  // a scrolled body's own box moves with it; a scrolled pane's doesn't
  const [dx, dy] = container.tagName === "BODY" ? [0, 0] : [container.scrollLeft, container.scrollTop];
  for (const rc of rangeRects(root, s, e, index)) {
    container.append(el("div", { class: "hlbox " + cls, style:
      `left:${rc.left - origin.left + dx}px;top:${rc.top - origin.top + dy}px;width:${rc.width}px;height:${rc.height}px` }));
  }
}

// -- document panes ---------------------------------------------------------------------------

class DocumentPane {
  constructor(id) {
    this.id = id; this.kind = "document"; this.info = null; this.layer = null; this.mode = null;
    this.selection = null; this.marks = []; this.highlights = []; this.sequence = null; this.visible = null; this.region = null;
    this.root = el("section", { class: "pane", "data-pane": id });
    this.head = el("div", { class: "pane-head" });
    this.links = el("div", { class: "pane-links" });
    this.strip = el("div", { class: "pane-dates" });
    this.body = el("div", { class: "pane-body", tabindex: "0" });
    this.side = el("div", { class: "pane-side" });
    this.root.append(this.head, this.strip, this.links, el("div", { class: "pane-split" }, this.body, this.side));
    this.body.addEventListener("scroll", () => this.onScroll());
    this.watchHover(document, this.body);
    // pages are laid out to the pane's width: lay them out again when it changes
    let lastWidth = 0;
    new ResizeObserver(() => {
      const w = this.body.clientWidth;
      // text and flow modes reflow on their own: only the marks drawn over them move
      if (this.mode !== "page" && this.info) { clearTimeout(this.redrawTimer); this.redrawTimer = setTimeout(() => this.drawBoxes(), 120); return; }
      if (this.mode !== "page" || !this.info || Math.abs(w - lastWidth) < 20) return;
      lastWidth = w;
      clearTimeout(this.resizeTimer);
      this.resizeTimer = setTimeout(() => this.relayout(), 150);
    }).observe(this.body);
  }
  async relayout() {
    this.updateStatus();                      // where it is now, even if the scroll hasn't been reported yet
    const top = this.visible?.page?.[0];
    await this.render();
    if (top && this.pageEls?.[top - 1]) this.body.scrollTop = this.pageEls[top - 1].offsetTop - 8;
  }
  title() { return this.info ? (this.info.meta.name || short(this.info.doc)) : "(empty)"; }
  // the modes this document and layer can be shown in, the default first
  modes() {
    const info = this.info, layer = this.currentLayer();
    if (!info) return [];
    return [info.meta.kind === "spreadsheet" && layer && "grid", info.pages && "page", layer?.presentation && "flow", "text"].filter(Boolean);
  }
  currentLayer() { return this.info?.layers.find((l) => l.id === this.layer); }
  state() {
    return {
      id: this.id, kind: "document", mode: this.mode, doc: this.info?.doc, layer: this.layer, view: this.currentLayer()?.view,
      visible: this.visible, sequence: this.sequence && { position: this.sequence.position + 1, length: this.sequence.items.length, from: this.sequence.from },
      selection: this.selection, marks: this.marks, highlights: this.highlights.map((h) => ({ loc: h.loc, label: h.label })),
      ...(this.selectionEntity() ? { entity: this.selectionEntity() } : {}),
      ...(this.selectionDate() ? { date: this.selectionDate() } : {}),
      dated: this.info?.dated?.date ? { date: this.info.dated.date, source: this.info.dated.source } : null,
      entities: this.ents ? { known: this.ents.known.length, suggested: this.ents.suggested.length, display: adhoc.entityMode } : null,
      open_ms: this.timings || [],
    };
  }

  async open(locIn, opts = {}) {
    this.openedAt = performance.now();        // for open_ms: until the first page (or text) is showing
    const loc = parseLoc(locIn);
    const info = await docInfo(loc.doc);
    const fresh = !this.info || this.info.doc !== info.doc;
    this.info = info;
    let layer = loc.layer && info.layers.find((l) => l.id === loc.layer);
    if (opts.view) layer = info.layers.find((l) => l.view === opts.view) || layer;
    if (!layer && !fresh) layer = this.currentLayer();
    this.layer = (layer || info.layers.find((l) => l.primary) || info.layers[0] || {}).id || null;
    const paged = !!info.pages && this.currentLayer()?.geometry !== false;
    const mode = opts.mode || (fresh ? null : this.mode) || this.modes()[0];
    this.mode = this.modes().includes(mode) ? mode : "text";
    if (fresh) this.grid = null;
    if (fresh) { this.selection = null; this.highlights = []; }
    await this.render();
    const sel = loc.select || {};
    if (sel.char || sel.cells || sel.page !== undefined) {
      this.flash(loc);
      await this.scrollTo(loc);
    }
    if (!opts.quiet) addHistory(loc, opts.source || "user");
    renderTabs();
  }

  async render() {
    const info = this.info, layer = this.currentLayer();
    this.head.replaceChildren(
      el("span", { class: "pane-title", title: info.doc }, this.title()),
      el("span", { class: "seg" }, ...info.layers.map((l) => el("button", { class: l.id === this.layer ? "on" : "", title: "layer " + short(l.id),
        onclick: () => this.open({ doc: info.doc, layer: l.id }, { quiet: true }) }, l.view + (l.primary ? "*" : "")))),
      el("span", { class: "seg" }, ...this.modes()
        .map((m) => el("button", { class: m === this.mode ? "on" : "", onclick: () => this.setMode(m) }, m))),
      el("span", { class: "chips" }),
      el("button", { class: "entstat", title: "The entity layer: hover a name to see what it is, or a date to see what came before and after; click to select it and ask Claude. E shows all, known only, or none.",
        onclick: () => cycleEntityMode() }), el("span", { class: "status" }));
    const links = [];
    if (info.parent) links.push(["parent: " + (info.parent.name || short(info.parent.doc)), info.parent.doc]);
    for (const c of info.children) links.push(["attachment: " + (c.name || short(c.doc)), c.doc]);
    for (const d of info.duplicates) links.push(["duplicate " + short(d), d]);
    this.links.replaceChildren(...links.map(([t, d]) => el("a", { onclick: () => this.open("doc:" + d, { source: "user" }) }, t)));
    this.links.hidden = !links.length;
    this.renderStrip();
    this.body.replaceChildren();
    this.words = null;
    if (!layer) { this.body.append(el("p", { class: "hint", style: "padding:1em" }, "This document has no text layer: nothing here reads its format. Ask Claude what it is; it can look at the bytes, and show you its own reading of them, marked as such.")); }
    if (this.mode === "page") await this.renderPages();
    else if (this.mode === "flow") await this.renderFlow();
    else if (this.mode === "grid") await this.renderGrid();
    else if (layer) await this.renderText();
    this.renderAnnotations();
    this.updateStatus();
    if (this.mode !== "page") this.shown();
    this.loadEntities();
  }
  async setMode(m) { this.mode = m; await this.render(); }

  // page mode: MuPDF images plus an invisible text layer from geometry
  async renderPages() {
    const info = this.info;
    const wrap = el("div", { class: "pages" });
    this.body.append(wrap);
    const width = Math.max(300, Math.min(this.body.clientWidth - 30, 1100));
    this.pageEls = info.pages.map(([w, h], i) => {
      const p = el("div", { class: "page", "data-page": i + 1, style: `width:${width}px;height:${(width * h) / w}px` });
      p.append(el("span", { class: "pageno" }, String(i + 1)));
      wrap.append(p);
      return p;
    });
    this.pageWidth = width;
    // Page images first: the text layer over them can arrive later.
    this.observer?.disconnect();
    this.observer = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) this.fillPage(e.target);
    }, { root: this.body, rootMargin: "800px 0px" });
    for (const p of this.pageEls) this.observer.observe(p);
    wrap.addEventListener("mouseup", (ev) => this.onMouseUp(ev, document));
    this.setupRegionSelect(wrap);
    const layer = this.currentLayer();
    if (layer?.geometry) {
      const [geo, t] = await Promise.all([geometry(layer.id), layerText(layer.id)]);
      this.words = { geo, cps: t.cps };
      for (const p of this.pageEls) if (p.dataset.filled) this.fillWords(p);
    }
  }
  pageWidthPx() { return Math.round((this.pageWidth * devicePixelRatio) / 200) * 200; }
  fillPage(p) {
    if (p.dataset.filled) return;
    p.dataset.filled = "1";
    const n = Number(p.dataset.page);
    const img = el("img", { src: `/page/${this.info.doc}/${n}?w=${this.pageWidthPx()}`, draggable: "false", alt: "" });
    img.addEventListener("load", () => this.shown(), { once: true });
    p.prepend(img);
    if (this.words) this.fillWords(p);
  }
  fillWords(p) {
    if (p.querySelector(".words")) return;
    const n = Number(p.dataset.page);
    const words = el("div", { class: "words" });
    const H = p.clientHeight, W = p.clientWidth;
    for (const g of this.words.geo) {
      if (g.page !== n) continue;
      const [x0, y0, x1, y1] = g.box;
      const span = el("span", { class: "w" + (g.conf !== undefined && g.conf < 0.6 ? " lowconf" : ""), "data-s": g.start,
        style: `left:${x0 * 100}%;top:${y0 * 100}%;font-size:${(y1 - y0) * H}px` });
      span.textContent = this.words.cps.slice(g.start, g.end).join("");
      span.dataset.w = (x1 - x0) * W;
      words.append(span);
    }
    p.append(words);
    requestAnimationFrame(() => {
      for (const s of words.children) {
        const natural = s.getBoundingClientRect().width;
        if (natural > 0) s.style.transform = `scaleX(${Number(s.dataset.w) / natural})`;
      }
    });
    this.drawBoxes();
  }
  shown() {
    if (this.openedAt === null) return;
    this.timings = [...(this.timings || []).slice(-49), Math.round(performance.now() - this.openedAt)];
    this.openedAt = null;
    this.prefetch();
  }
  // Warm the viewer's page cache with the next document's first page.
  async prefetch() {
    const s = this.sequence;
    if (!s || s.position + 1 >= s.items.length || !this.pageWidth) return;
    try {
      const info = await docInfo(parseLoc(s.items[s.position + 1]).doc);
      if (info.pages) fetch(`/page/${info.doc}/1?w=${this.pageWidthPx()}`);
    } catch {}
  }
  boxesFor(s, e) {
    if (!this.words) return [];
    return this.words.geo.filter((g) => g.start < e && s < g.end);
  }
  drawBoxes() {
    if (this.mode === "grid") return this.paintCells();
    if (this.mode === "page") {
      for (const p of this.pageEls || []) p.querySelectorAll(".box").forEach((b) => b.remove());
      const draw = (s, e, cls) => {
        for (const g of this.boxesFor(s, e)) {
          const p = this.pageEls[g.page - 1];
          if (!p) continue;
          const [x0, y0, x1, y1] = g.box;
          p.append(el("div", { class: "box " + cls, style: `left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%` }));
        }
      };
      for (const h of this.highlights) if (h.range) draw(...h.range, "hl");
      for (const mk of this.marks) if (mk.select?.char && mk.layer === this.layer) draw(...mk.select.char, "mark");
      for (const h of this.highlights) if (h.region) this.drawRegion(h.region, "hl");
      for (const x of this.shownEntities()) draw(x.start, x.end, markClass(x));
    } else {
      const root = this.textRoot();
      if (!root) return;
      const container = root.ownerDocument === document ? this.body : root.ownerDocument.body;
      container.querySelectorAll(".hlbox").forEach((b) => b.remove());
      if (getComputedStyle(container).position === "static") container.style.position = "relative";
      for (const h of this.highlights) if (h.range) drawRangeBoxes(container, null, root, ...h.range, "hl");
      for (const mk of this.marks) if (mk.select?.char && mk.layer === this.layer) drawRangeBoxes(container, null, root, ...mk.select.char, "mark");
      const index = markedIndex(root);
      for (const x of this.shownEntities()) drawRangeBoxes(container, null, root, x.start, x.end, markClass(x), index);
    }
  }
  drawRegion(r, cls) {
    const p = this.pageEls?.[r.page - 1];
    if (!p) return;
    const [x, y, w, h] = r.xywh;
    p.append(el("div", { class: "region box " + cls, style: `left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${h * 100}%` }));
  }
  setupRegionSelect(wrap) {
    // Alt-drag on a page selects a region: a page plus xywh locator.
    let start = null, box = null, page = null;
    wrap.addEventListener("mousedown", (ev) => {
      if (!ev.altKey) return;
      page = ev.target.closest(".page");
      if (!page) return;
      ev.preventDefault();
      const r = page.getBoundingClientRect();
      start = [(ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height];
      box = el("div", { class: "region" });
      page.append(box);
    });
    wrap.addEventListener("mousemove", (ev) => {
      if (!start) return;
      const r = page.getBoundingClientRect();
      const cur = [(ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height];
      const x = Math.min(start[0], cur[0]), y = Math.min(start[1], cur[1]);
      this.region = { x, y, w: Math.abs(cur[0] - start[0]), h: Math.abs(cur[1] - start[1]) };
      Object.assign(box.style, { left: x * 100 + "%", top: y * 100 + "%", width: this.region.w * 100 + "%", height: this.region.h * 100 + "%" });
    });
    window.addEventListener("mouseup", () => {
      if (!start) return;
      const n = Number(page.dataset.page), g = this.region;
      start = null;
      if (g && g.w > 0.005 && g.h > 0.005) {
        const round = (v) => Math.round(v * 10000) / 10000;
        this.selection = { doc: this.info.doc, select: { page: n, xywh: [g.x, g.y, g.w, g.h].map(round) } };
        changed();
      } else box.remove();
    });
  }

  // flow mode: the layer's presentation, in a frame where no script runs
  async renderFlow() {
    const frame = el("iframe", { class: "web", src: "/presentation/" + this.layer });
    this.body.append(frame);
    this.frame = frame;
    await new Promise((res) => frame.addEventListener("load", res, { once: true }));
    const d = frame.contentDocument;
    d.addEventListener("mousedown", () => focus(this.id), true);   // events in a frame don't reach the pane
    d.addEventListener("mouseup", (ev) => this.onMouseUp(ev, d));
    d.addEventListener("keydown", onKey);
    d.addEventListener("scroll", () => this.onScroll());
    this.watchHover(d, d.body, frame);
    this.drawBoxes();
  }

  // text mode: the layer text, one line per element
  async renderText() {
    const layer = this.currentLayer();
    const [t, segs] = await Promise.all([layerText(layer.id), segments(layer.id)]);
    const noteKinds = ["footnote", "endnote", "comment", "header-footer"];   // a .docx's text outside its body
    const lineKinds = segs.filter((s) => ["header", "quoted", "signature", ...noteKinds].includes(s.kind));
    const titles = new Map(segs.filter((s) => noteKinds.includes(s.kind) && s.title).map((s) => [s.start, s.title]));
    const raised = segs.filter((s) => s.kind === "note-ref" || s.kind === "note-mark");   // footnote numbers
    const labels = new Map(segs.filter((s) => s.kind === "line").map((s) => [s.start, s.label]));
    const box = el("div", { class: "textmode" });
    let pos = 0, page = 1;
    for (const pageText of t.text.split("\f")) {
      if (page > 1) box.append(el("div", { class: "pagebreak", "data-page": page }));
      for (const line of pageText.split("\n")) {
        const n = cpLen(line);
        const kinds = lineKinds.filter((s) => s.start <= pos && pos < s.end).map((s) => s.kind);
        const div = el("div", { class: "ln " + kinds.join(" ") });
        if (titles.has(pos)) div.dataset.title = titles.get(pos);   // shown by CSS, so selections don't take it
        const lbl = labels.get(pos);
        if (lbl) div.append(el("span", { class: "lbl" }, lbl));
        // spans carry their offsets; a footnote number is a span of its own, raised
        const end = pos + n, cps = [...line];
        const cuts = [...new Set([pos, end, ...raised.flatMap((s) => [s.start, s.end]).filter((x) => pos < x && x < end)])].sort((a, b) => a - b);
        for (let i = 0; i + 1 < cuts.length || (i === 0 && n === 0); i++) {
          const a = cuts[i], b = cuts[i + 1] ?? a;
          const span = el("span", { "data-s": a });
          span.textContent = cps.slice(a - pos, b - pos).join("");
          div.append(raised.some((s) => s.start <= a && b <= s.end && a < b) ? el("sup", {}, span) : span);
        }
        box.append(div);
        pos += n + 1;
      }
      page++;
    }
    this.body.append(box);
    box.addEventListener("mouseup", (ev) => this.onMouseUp(ev, document));
    this.drawBoxes();
  }
  // grid mode: one sheet at a time as a table, built from the layer text
  // (sheets are pages, rows lines, cells tab-separated). Cells are selected
  // as rectangles: a sheet plus cells locator, with its text as the quote.
  async renderGrid() {
    const layer = this.currentLayer();
    const [t, segs] = await Promise.all([layerText(layer.id), segments(layer.id)]);
    let sheets = segs.filter((x) => x.kind === "sheet").map((x) => ({ name: x.name, start: x.start, end: x.end }));
    if (!sheets.length) {
      let pos = 0;
      sheets = t.text.split("\f").map((p, i) => { const n = cpLen(p), sh = { name: null, label: `Sheet ${i + 1}`, start: pos, end: pos + n }; pos += n + 1; return sh; });
    }
    const keep = this.grid?.layer === layer.id ? this.grid : null;
    this.grid = { layer: layer.id, cps: t.cps, sheets, sheet: keep ? keep.sheet : 0, anchor: keep?.anchor || null, focus: keep?.focus || null };
    this.drawSheet();
  }
  drawSheet(minRows = 0) {
    const g = this.grid, sh = g.sheets[g.sheet];
    // rows of cells, each with its char range
    const rows = [];
    let row = [], cellStart = sh.start, text = "";
    for (let i = sh.start; i <= sh.end; i++) {
      const c = i < sh.end ? g.cps[i] : "\n";
      if (c === "\t" || c === "\n") {
        row.push({ s: cellStart, e: i, text });
        cellStart = i + 1; text = "";
        if (c === "\n") { rows.push(row); row = []; }
      } else text += c;
    }
    g.rows = rows;
    g.ncols = Math.max(1, ...rows.map((r) => r.length));
    const limit = Math.max(MAX_GRID_ROWS, minRows + 100);
    const table = el("table", { class: "grid" });
    const head = el("tr", {}, el("th", { class: "corner" }));
    for (let c = 0; c < g.ncols; c++) head.append(el("th", { class: "col", "data-c": c }, colName(c)));
    const tbody = el("tbody");
    table.append(el("thead", {}, head), tbody);
    g.cells = [];
    rows.slice(0, limit).forEach((r, ri) => {
      const tr = el("tr", {}, el("th", { class: "row", "data-r": ri }, String(ri + 1)));
      const els = [];
      for (let c = 0; c < g.ncols; c++) {
        const cell = r[c];
        const td = el("td", { "data-r": ri, "data-c": c });
        if (cell?.text) {
          td.append(el("span", { "data-s": cell.s }, cell.text));
          if (cell.text.length > 40) td.title = cell.text;
          if (/^-?[\d,]*\.?\d+(e[-+]?\d+)?%?$/i.test(cell.text.trim())) td.className = "num";
        }
        tr.append(td);
        els.push(td);
      }
      g.cells.push(els);
      tbody.append(tr);
    });
    const tabs = g.sheets.length > 1 ? el("div", { class: "sheet-tabs" }, ...g.sheets.map((x, i) =>
      el("button", { class: i === g.sheet ? "on" : "", onclick: () => { this.setSheet(i); this.drawSheet(); this.updateStatus(); changed(); } }, x.name ?? x.label))) : null;
    const more = rows.length > limit ? el("p", { class: "hint", style: "padding:0 8px" },
      `Showing the first ${limit} of ${rows.length} rows; search or text mode reaches the rest.`) : null;
    const wrap = el("div", { class: "gridwrap" }, table);
    this.body.replaceChildren(...[tabs, wrap, more].filter(Boolean));
    this.setupCellSelect(table);
    this.paintCells();
  }
  // a cell selection belongs to its sheet: switching sheets drops it
  setSheet(i) {
    const g = this.grid;
    g.sheet = i;
    g.anchor = g.focus = null;
    if (this.selection?.select?.cells) this.selection = null;
  }
  setupCellSelect(table) {
    const g = this.grid;
    const at = (ev) => {
      const t = ev.target.closest("td,th");
      if (!t) return null;
      const r = t.dataset.r !== undefined ? Number(t.dataset.r) : null, c = t.dataset.c !== undefined ? Number(t.dataset.c) : null;
      return { r, c, th: t.tagName === "TH" };
    };
    let dragging = false;
    table.addEventListener("mousedown", (ev) => {
      const p = at(ev);
      if (!p || ev.button !== 0) return;
      ev.preventDefault();
      focus(this.id);
      const last = g.rows.length - 1, lastCol = g.ncols - 1;
      if (p.th && p.r === null && p.c === null) { g.anchor = [0, 0]; g.focus = [last, lastCol]; }         // corner: all
      else if (p.th && p.r !== null) { if (!ev.shiftKey || !g.anchor) g.anchor = [p.r, 0]; g.focus = [p.r, lastCol]; }   // a row
      else if (p.th) { if (!ev.shiftKey || !g.anchor) g.anchor = [0, p.c]; g.focus = [last, p.c]; }      // a column
      else { if (!ev.shiftKey || !g.anchor) g.anchor = [p.r, p.c]; g.focus = [p.r, p.c]; dragging = true; }
      this.paintCells();
      if (!dragging) this.cellsSelected();
    });
    table.addEventListener("mouseover", (ev) => {
      if (!dragging) return;
      const p = at(ev);
      if (p && !p.th) { g.focus = [p.r, p.c]; this.paintCells(); }
    });
    window.addEventListener("mouseup", () => { if (dragging) { dragging = false; this.cellsSelected(); } });
  }
  rect() {
    const g = this.grid;
    if (!g?.anchor) return null;
    const [r0, r1] = [g.anchor[0], g.focus[0]].sort((a, b) => a - b), [c0, c1] = [g.anchor[1], g.focus[1]].sort((a, b) => a - b);
    return { r0, r1, c0, c1 };
  }
  rectText(x) {
    const out = [];
    for (let r = x.r0; r <= x.r1; r++) {
      const row = [];
      for (let c = x.c0; c <= x.c1; c++) row.push(this.grid.rows[r]?.[c]?.text ?? "");
      out.push(row.join("\t"));
    }
    return out.join("\n");
  }
  cellsSelected() {
    const x = this.rect(), sh = this.grid.sheets[this.grid.sheet];
    if (!x) return;
    const ref = colName(x.c0) + (x.r0 + 1) + (x.r0 === x.r1 && x.c0 === x.c1 ? "" : ":" + colName(x.c1) + (x.r1 + 1));
    const select = sh.name !== null ? { sheet: sh.name, cells: ref } : { cells: ref };
    this.selection = { doc: this.info.doc, layer: this.layer, select, quote: { exact: this.rectText(x) } };
    this.updateStatus();
    changed();
  }
  moveCell(key, extend) {
    const g = this.grid;
    if (!g?.rows.length) return;
    const d = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[key];
    const from = g.focus || [0, 0];
    const to = [Math.min(Math.max(from[0] + d[0], 0), g.rows.length - 1), Math.min(Math.max(from[1] + d[1], 0), g.ncols - 1)];
    if (!extend || !g.anchor) g.anchor = to;
    g.focus = to;
    this.paintCells();
    g.cells[to[0]]?.[to[1]]?.scrollIntoView({ block: "nearest", inline: "nearest" });
    this.cellsSelected();
  }
  // selection, highlights and marks on the current sheet
  paintCells() {
    const g = this.grid;
    if (!g?.cells) return;
    const sh = g.sheets[g.sheet];
    for (const row of g.cells) for (const td of row) td.classList.remove("sel", "hl", "mark", "cur");
    const paintRect = (x, cls) => {
      for (let r = x.r0; r <= Math.min(x.r1, g.cells.length - 1); r++)
        for (let c = x.c0; c <= x.c1; c++) g.cells[r]?.[c]?.classList.add(cls);
    };
    const paintChars = (s, e, cls) => {
      if (e <= sh.start || s >= sh.end) return;
      g.rows.forEach((row, r) => row.forEach((cell, c) => {
        if (cell.s < e && s < Math.max(cell.e, cell.s + 1)) g.cells[r]?.[c]?.classList.add(cls);
      }));
    };
    const paint = (x, cls) => {
      if (x.cells) {
        if ((x.cells.sheet ?? null) !== null ? x.cells.sheet !== sh.name : g.sheet !== 0) return;
        const r = parseCells(x.cells.cells);
        if (r) paintRect(r, cls);
      } else if (x.range) paintChars(...x.range, cls);
    };
    for (const h of this.highlights) paint(h, "hl");
    for (const mk of this.marks) if (mk.layer === this.layer) paint({ range: mk.select?.char, cells: mk.select?.cells && mk.select }, "mark");
    const x = this.rect();
    if (x) { paintRect(x, "sel"); g.cells[g.focus[0]]?.[g.focus[1]]?.classList.add("cur"); }
  }
  scrollToCells(sel) {
    const g = this.grid;
    if (!g) return;
    let target = null, sheet = g.sheet;
    if (sel.cells) {
      sheet = sel.sheet !== undefined ? g.sheets.findIndex((x) => x.name === sel.sheet) : 0;
      const r = parseCells(sel.cells);
      if (sheet < 0 || !r) return;
      target = [r.r0, r.c0];
    } else if (sel.char) {
      sheet = g.sheets.findIndex((x) => x.start <= sel.char[0] && sel.char[0] <= x.end);
      if (sheet < 0) return;
    } else if (sel.page) {
      sheet = Math.min((Array.isArray(sel.page) ? sel.page[0] : sel.page) - 1, g.sheets.length - 1);
    }
    if (sheet !== g.sheet) this.setSheet(sheet);
    if (sel.char) {
      this.drawSheet();
      g.rows.forEach((row, r) => row.forEach((cell, c) => { if (!target && cell.s <= sel.char[0] && sel.char[0] <= cell.e) target = [r, c]; }));
    }
    this.drawSheet(target ? target[0] : 0);
    if (target) g.cells[target[0]]?.[target[1]]?.scrollIntoView({ block: "center", inline: "center" });
    this.updateStatus();
  }
  gridStatus() {
    const g = this.grid, sh = g.sheets[g.sheet];
    const tr = this.body.querySelectorAll("tbody tr");
    const top = this.body.getBoundingClientRect().top, bottom = top + this.body.clientHeight;
    const vis = [...tr].filter((r) => { const b = r.getBoundingClientRect(); return b.bottom > top && b.top < bottom; });
    const name = sh.name ?? sh.label;
    if (vis.length) {
      const a = Number(vis[0].firstChild.dataset.r) + 1, b = Number(vis[vis.length - 1].firstChild.dataset.r) + 1;
      this.visible = { sheet: name, rows: [a, b] };
    }
    const sel = this.selection?.select?.cells;
    return `${name} · ${g.rows.length} rows` + (sel ? ` · ${sel} selected` : "");
  }
  textRoot() {
    if (this.mode === "flow") return this.frame?.contentDocument?.body;
    if (this.mode === "text") return $(".textmode", this.body);
    return null;
  }

  onMouseUp(ev, doc) {
    if (ev.altKey) return;
    const click = this.downAt && Math.hypot(ev.clientX - this.downAt[0], ev.clientY - this.downAt[1]) < 4;
    const hit = click && this.mentionAtPoint(ev, doc);
    setTimeout(() => {
      const r = selectionRange(doc);
      if (r) {
        this.selection = { doc: this.info.doc, layer: this.layer, select: { char: r } };
        changed();
      } else if (hit) this.selectMention(hit, doc);
    }, 0);
  }
  clearSelection() {
    this.selection = null;
    (this.frame?.contentDocument || document).getSelection()?.removeAllRanges();
    document.getSelection().removeAllRanges();
    this.body.querySelectorAll(".region").forEach((r) => r.remove());
    if (this.grid) { this.grid.anchor = this.grid.focus = null; this.paintCells(); }
    changed();
  }

  // where the locator is, scrolled into view
  async scrollTo(loc) {
    const sel = loc.select || {};
    if (this.mode === "grid") return this.scrollToCells(sel);
    if (this.mode === "page") {
      let n = Array.isArray(sel.page) ? sel.page[0] : sel.page;
      if (sel.char) {
        const g = this.boxesFor(...sel.char)[0];
        if (g) {
          const p = this.pageEls[g.page - 1];
          this.body.scrollTop = p.offsetTop + g.box[1] * p.clientHeight - this.body.clientHeight / 3;
          return;
        }
        const t = await layerText(this.layer);
        n = t.cps.slice(0, sel.char[0]).filter((c) => c === "\f").length + 1;
      }
      if (n && this.pageEls[n - 1]) this.body.scrollTop = this.pageEls[n - 1].offsetTop - 8;
    } else if (sel.char) {
      const root = this.textRoot();
      const rect = root && rangeRects(root, sel.char[0], sel.char[0] + 1)[0];
      if (!rect) return;
      if (this.mode === "flow") this.frame.contentWindow.scrollBy(0, rect.top - 120);
      else this.body.scrollTop += rect.top - this.body.getBoundingClientRect().top - 120;
    }
  }
  flash(loc) {
    const sel = parseLoc(loc).select || {};
    const h = { loc: locString(loc), label: "opened", range: sel.char, cells: sel.cells && { sheet: sel.sheet, cells: sel.cells }, temp: true };
    this.highlights.push(h);
    this.drawBoxes();
    setTimeout(() => { this.highlights = this.highlights.filter((x) => x !== h); this.drawBoxes(); }, 1800);
  }
  async highlight(loc, label) {
    const l = parseLoc(loc);
    if (l.doc !== this.info?.doc || (l.layer && l.layer !== this.layer)) await this.open(l, { quiet: true });
    const sel = l.select || {};
    this.highlights.push({ loc: locString(l), label, range: sel.char, region: sel.xywh && { page: sel.page, xywh: sel.xywh },
                           cells: sel.cells && { sheet: sel.sheet, cells: sel.cells } });
    this.drawBoxes();
    await this.scrollTo(l);
  }

  onScroll() {
    if (entityCard.pane === this) entityCard.hide();
    clearTimeout(this.scrollTimer);
    this.scrollTimer = setTimeout(() => { this.updateStatus(); changed(); }, 150);
  }
  updateStatus() {
    const st = $(".status", this.head);
    if (!st) return;
    let text = "";
    if (this.mode === "page" && this.pageEls) {
      const top = this.body.scrollTop, bottom = top + this.body.clientHeight;
      const vis = this.pageEls.filter((p) => p.offsetTop < bottom && p.offsetTop + p.clientHeight > top).map((p) => Number(p.dataset.page));
      if (vis.length) {
        this.visible = { page: [vis[0], vis[vis.length - 1]] };
        const n = vis[0], label = this.info.page_labels?.[n];
        text = `page ${n} of ${this.pageEls.length}` + (label && label !== String(n) ? ` (p. ${label})` : "");
        const bates = this.batesFor(n);
        if (bates) text += " · " + bates;
        const ex = this.exhibitFor(n);
        if (ex) text += " · " + ex;
      }
    } else if (this.mode === "grid" && this.grid) {
      text = this.gridStatus();
    } else if (this.mode === "text") {
      const spans = [...this.body.querySelectorAll("[data-s]")];
      const top = this.body.getBoundingClientRect().top, bottom = top + this.body.clientHeight;
      const vis = spans.filter((s) => { const r = s.getBoundingClientRect(); return r.bottom > top && r.top < bottom; });
      if (vis.length) this.visible = { char: [Number(vis[0].dataset.s), Number(vis[vis.length - 1].dataset.s) + cpLen(vis[vis.length - 1].textContent)] };
    }
    const custodian = (this.info?.custodian || "").replace("cust:", "");
    if (custodian) text = custodian + (text ? " · " + text : "");
    if (this.sequence) text += ` · ${this.sequence.position + 1}/${this.sequence.items.length}`;
    st.textContent = text;
  }

  batesFor(page) {
    for (const r of [...(this.info?.bates || [])].reverse()) {
      if (!r.page_map) return page === 1 ? r.begin : null;
      const i = r.page_map.indexOf(page);
      if (i >= 0) {
        const m = /^(.*?)(\d+)$/.exec(r.begin);
        return m[1] + String(Number(m[2]) + i).padStart(m[2].length, "0");
      }
    }
    return null;
  }
  // the latest exhibit with this page in it, and the page's number in the exhibit
  exhibitFor(page) {
    for (const x of [...(this.info?.exhibits || [])].reverse()) {
      if (!x.page_map) return x.label;
      const i = x.page_map.indexOf(page);
      if (i >= 0) return `${x.label}, p. ${i + 1}`;
    }
    return null;
  }
  async step(delta) {
    if (!this.sequence) return toast("No sequence in this pane");
    const s = this.sequence;
    const next = s.position + delta;
    if (next < 0 || next >= s.items.length) return toast(delta > 0 ? "End of the sequence" : "Start of the sequence");
    s.position = next;
    await this.open(s.items[next], { source: "user" });
    if (s.from) callPane(s.from, "follow", s.items[next]);
    for (const d of s.items.slice(next + 1, next + 4)) docInfo(parseLoc(d).doc).catch(() => {});
    changed();
  }
  // a linked list pane's choice: its sequence, at this item
  seqOpen(sequence, loc) {
    this.sequence = sequence;
    return this.open(loc, { source: "pane" });
  }
  page(delta) {
    if (this.mode === "flow") this.frame.contentWindow.scrollBy(0, delta * this.frame.clientHeight * 0.9);
    else this.body.scrollBy(0, delta * this.body.clientHeight * 0.9);
  }

  renderAnnotations() {
    if (!this.info) return;
    const doc = this.info.doc, A = adhoc.annotations;
    const color = (t) => A.definitions.find((d) => d.tag === t)?.color || "var(--muted)";
    const docTags = A.tags.filter((t) => t.target.doc === doc && !t.target.select);
    $(".chips", this.head)?.replaceChildren(...docTags.map((t) =>
      el("span", { class: "chip " + (t.by === "claude" ? "by-claude" : ""), style: `color:${color(t.tag)}`, title: `${t.by}, ${t.at}` }, t.tag)));
    const ranged = A.tags.filter((t) => t.target.doc === doc && t.target.select);
    const notes = A.notes.filter((n) => n.target?.doc === doc);
    const item = (cls, text, who, target, extra = []) => el("div", { class: "ann " + cls + (who === "claude" ? " by-claude" : ""),
      onclick: () => target?.select && this.highlight(target, "annotation") }, el("div", {}, text), el("div", { class: "who" }, who), ...extra);
    const kids = [];
    for (const t of ranged) kids.push(item("", `▍${t.tag}: “${(t.target.quote?.exact || "").slice(0, 60)}”`, t.by, t.target));
    const top = notes.filter((n) => !n.parent || !notes.some((p) => p.note === n.parent));
    const thread = (n, depth) => {
      const mine = n.by === adhoc.user;
      kids.push(item(depth ? "reply" : "", n.body, n.by + (n.edited_at ? " (edited)" : ""), n.target, [
        el("a", { onclick: (e) => { e.stopPropagation(); noteEditor({ op: "reply", parent: n.note }, "Reply"); } }, "reply "),
        ...(mine || true ? [el("a", { onclick: (e) => { e.stopPropagation(); noteEditor({ op: "edit-note", note: n.note }, "Edit note", n.body); } }, "edit "),
          el("a", { onclick: (e) => { e.stopPropagation(); if (confirm("Delete this note?")) annotate({ op: "delete-note", note: n.note }); } }, "delete")] : []),
      ]));
      for (const r of notes.filter((x) => x.parent === n.note)) thread(r, depth + 1);
    };
    top.forEach((n) => thread(n, 0));
    this.side.replaceChildren(...kids);
  }

  // -- the entity layer: known entities' mentions, and suggestions from the entities index --

  async loadEntities() {
    const layer = this.currentLayer();
    if (!layer || this.mode === "grid") { this.ents = null; this.updateEntityStat(); return; }
    const id = layer.id;
    const [t, det, dts] = await Promise.all([layerText(id), detected(id), layerDates(id)]);
    if (this.layer !== id) return;
    refreshCandidates();
    const folded = cache.folded[id] ??= entityFoldText(t.cps);
    const ents = adhoc.annotations.entities || [];
    const known = findEntityMentions(t.cps, ents, folded).map((x) => ({ ...x, known: true }));
    const suggested = det.filter((d) => !known.some((k) => d.start < k.end && k.start < d.end));
    // dates: placed by the document's own date; a known entity's name wins over a date inside it
    const anchor = this.info?.dated?.date;
    const dates = dts.map((d) => ({ ...d, isDate: true, resolved: resolveDate(d, anchor) }))
      .filter((d) => d.resolved && !known.some((k) => d.start < k.end && k.start < d.end));
    this.ents = { layer: id, cps: t.cps, known, suggested, dates };
    this.updateEntityStat();
    this.drawBoxes();
    changed();
  }
  shownEntities() {
    if (!this.ents || this.ents.layer !== this.layer || adhoc.entityMode === "off") return [];
    return [...(adhoc.entityMode === "all" ? [...this.ents.known, ...this.ents.suggested] : this.ents.known), ...this.ents.dates];
  }
  updateEntityStat() {
    const b = $(".entstat", this.head);
    if (!b) return;
    b.hidden = !this.ents || (!this.ents.known.length && !this.ents.suggested.length && !this.ents.dates.length);
    if (!this.ents) return;
    const k = new Set(this.ents.known.flatMap((x) => x.entities)).size, s = new Set(this.ents.suggested.map((x) => x.key)).size;
    const d = this.ents.dates.length;
    b.textContent = adhoc.entityMode === "off" ? "entities and dates hidden" : `${k} known` + (s ? ` · ${s} suggested` + (adhoc.entityMode === "known" ? " (hidden)" : "") : "")
      + (d ? ` · ${plural(d, "date")}` : "");
    b.className = "entstat " + adhoc.entityMode;
  }
  // the mention under a mouse event, if the pointer is on its words
  mentionAtPoint(ev, doc) {
    const shown = this.shownEntities();
    if (!shown.length) return null;
    let s = null;
    const w = ev.target?.closest?.(".w");
    if (w) s = Number(w.dataset.s);
    else {
      const r = doc.caretRangeFromPoint?.(ev.clientX, ev.clientY);
      if (r) s = offsetOf(r.startContainer, r.startOffset);
    }
    if (s === null || Number.isNaN(s)) return null;
    const cands = shown.filter((x) => x.start <= s + (w ? cpLen(w.textContent) - 1 : 0) && s <= x.end);
    for (const x of cands) {
      if (w) { if (x.start < s + cpLen(w.textContent) && s < x.end) return x; continue; }
      const root = this.textRoot();
      if (root && rangeRects(root, x.start, x.end).some((rc) => ev.clientX >= rc.left - 1 && ev.clientX <= rc.right + 1 && ev.clientY >= rc.top - 1 && ev.clientY <= rc.bottom + 1)) return x;
    }
    return null;
  }
  watchHover(doc, target, frame = null) {
    target.addEventListener("mousedown", (ev) => { this.downAt = [ev.clientX, ev.clientY]; });
    let pending = false;
    target.addEventListener("mousemove", (ev) => {
      if (pending || ev.buttons) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        const x = this.mentionAtPoint(ev, doc);
        if (x) entityCard.hoverOn(this, x, ev, frame);
        else entityCard.hoverOff();
      });
    });
    target.addEventListener("mouseleave", () => entityCard.hoverOff());
  }
  selectMention(x, doc) {
    const root = this.mode === "page" ? this.body : this.textRoot();
    const a = root && pointAt(root, x.start), b = root && pointAt(root, x.end);
    if (a && b) {
      const r = (root.ownerDocument || doc).createRange();
      r.setStart(...a); r.setEnd(...b);
      const sel = (root.ownerDocument || doc).getSelection();
      sel.removeAllRanges(); sel.addRange(r);
    }
    this.selection = { doc: this.info.doc, layer: this.layer, select: { char: [x.start, x.end] } };
    entityCard.hide();
    const name = x.known ? entityById(x.entities[0])?.name : x.form.replace(/\s+/g, " ");
    toast(`Selected ${name}: ask Claude about it` + (x.known ? "" : x.isDate ? " (\"what else happened then?\")" : ", or press e to add it to the entities"));
    changed();
  }
  // the entity the selection is (or is inside of): what Claude sees as "this"
  selectionMention() {
    const c = this.selection?.select?.char;
    if (!c || !this.ents || this.selection.layer !== this.ents.layer) return null;
    const all = [...this.ents.known, ...this.ents.suggested, ...this.ents.dates];
    return all.find((x) => x.start === c[0] && x.end === c[1]) || all.find((x) => x.start <= c[0] && c[1] <= x.end) || null;
  }
  // the date the selection is (or is inside of), resolved
  selectionDate() {
    const c = this.selection?.select?.char;
    if (!c || !this.ents || this.selection.layer !== this.ents.layer) return null;
    const x = this.ents.dates.find((d) => d.start <= c[0] && c[1] <= d.end);
    if (!x) return null;
    return { form: x.form.replace(/\s+/g, " "), date: x.resolved, kind: x.kind,
             ...(x.ambiguous ? { ambiguous: true, alt: x.alt } : {}), ...(x.header ? { quoted_from: x.header.who } : {}) };
  }
  selectionEntity() {
    const x = this.selectionMention();
    if (!x || x.isDate) return null;
    if (x.known) return { known: x.entities.map((id) => { const e = entityById(id); return e && { entity: id, name: e.name, kind: e.kind, summary: e.summary }; }).filter(Boolean) };
    const { start, end, ...d } = x;
    const c = adhoc.candidates[d.key];
    return { suggested: { ...d, ...(c && d.kind !== "term" ? { docs: c.docs } : {}) } };
  }

  // the date strip: when the document was sent (or attached, created,
  // modified), and where that date came from; hover the date for its card
  renderStrip() {
    const d = this.info?.dated || {};
    const parts = [];
    if (d.date) {
      this.stripMention = { isDate: true, strip: true, kind: "absolute", form: d.date, resolved: d.date.slice(0, 10), source: d.source };
      const date = el("span", { class: "pd-date" }, fmtDateTime(d.date, d.source !== "sent"));
      date.addEventListener("mousemove", (ev) => entityCard.hoverOn(this, this.stripMention, ev, null));
      date.addEventListener("mouseleave", () => entityCard.hoverOff());
      parts.push(el("span", { class: "pd-label" }, DATE_SOURCE[d.source] || "Dated"), " ", date);
      if (d.source === "attached" && d.parent)
        parts.push(" ", el("a", { onclick: () => openFromPane(this, "doc:" + d.parent.doc) }, "(the email)"));
    }
    // a file's own created and modified dates, when they say something the date above doesn't
    const shown = (d.date || "").slice(0, 10);
    for (const [k, label] of [["created", "Created"], ["modified", "Modified"]]) {
      const v = d[k];
      if (!v || v.slice(0, 10) === shown) continue;
      if (parts.length) parts.push(el("span", { class: "pd-sep" }, " · "));
      parts.push(el("span", { class: "pd-label" }, label), " ", el("span", { class: "pd-other" }, fmtDateTime(v, true)));
    }
    if (d.date && d.source !== "sent" && d.source !== "attached")
      parts.push(el("span", { class: "pd-note", title: "Metadata dates are often wrong: a template's creation date, or when the file was copied." }, " (from the file's metadata)"));
    this.strip.replaceChildren(...parts);
    this.strip.hidden = !parts.length;
  }

  // everything needed to show this pane again in another window
  spec() {
    return { kind: "document", id: this.id, doc: this.info?.doc ?? null, layer: this.layer, mode: this.mode, sequence: this.sequence,
             marks: this.marks, highlights: this.highlights.filter((h) => !h.temp), selection: this.selection, visible: this.visible };
  }

  // what tags and notes act on: the selection, else the document
  target() { return this.selection || { doc: this.info?.doc }; }
  targets() { return this.info ? [this.target()] : []; }
}

// -- web panes (lists, pages, Claude's panes) --------------------------------------------------

class WebPane {
  constructor(id, kind, content, title) {
    this.id = id; this.kind = kind; this.content = content; this._title = title; this.link = null;
    this.selected = []; this.form = {}; this.sequence = null; this.cursor = -1;
    this.root = el("section", { class: "pane", "data-pane": id });
    this.head = el("div", { class: "pane-head" }, el("span", { class: "pane-title" }, title || kind), el("span", { class: "status" }));
    this.frame = el("iframe", { class: "web" });
    this.root.append(this.head, el("div", { class: "pane-body" }, this.frame));
    this.frame.addEventListener("load", () => this.wire());
    this.load(content);
  }
  title() { return this._title || this.kind; }
  load(content) { this.content = content; this.frame.src = `/pane/${content}/index.html`; }
  state() {
    return { id: this.id, kind: this.kind, title: this._title, link: this.link,
      selection: this.selected.map((s) => parseLoc(s)), position: this.cursor >= 0 ? this.cursor + 1 : null,
      length: this.items().length, form: this.form, sequence: this.sequence ? { length: this.sequence.length } : null };
  }
  doc() { return this.frame.contentDocument; }
  items() { return this.doc() ? [...this.doc().querySelectorAll("[data-locator]")] : []; }

  wire() {
    const d = this.doc();
    if (!d || !d.body) return;
    d.addEventListener("keydown", onKey);
    d.addEventListener("mousedown", () => focus(this.id), true);
    d.addEventListener("click", (ev) => {
      const a = ev.target.closest("a[href]");
      if (a && a.getAttribute("href").startsWith("loc:")) {
        ev.preventDefault();
        openFromPane(this, a.getAttribute("href").slice(4), true);
        return;
      }
      if (a && /^[a-z]+:/i.test(a.getAttribute("href")) && !a.getAttribute("href").startsWith("adhoc:")) { ev.preventDefault(); return; }
      const item = ev.target.closest("[data-locator]");
      if (item && !ev.target.closest("input,select,textarea,button,label")) this.select(item, ev.shiftKey, ev.ctrlKey || ev.metaKey);
    });
    d.addEventListener("dblclick", (ev) => {
      const item = ev.target.closest("[data-locator]");
      if (item) openFromPane(this, item.dataset.locator, true, true);
    });
    d.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const action = ev.target.getAttribute("action") || "";
      if (!action.startsWith("svc:")) return toast("Form values are recorded for Claude; this form has no service.");
      const values = {};
      for (const [k, v] of new FormData(ev.target, ev.submitter)) (values[k] ??= []).push(v);
      for (const k in values) if (values[k].length === 1) values[k] = values[k][0];
      serve(this, action.slice(4), { form: values, selection: this.selected, pane: this.id });
    });
    const record = (ev) => {
      const f = ev.target;
      if (!f.name) return;
      if (f.type === "checkbox") {
        const all = [...d.querySelectorAll(`input[type=checkbox][name="${CSS.escape(f.name)}"]`)].filter((x) => x.checked).map((x) => x.value);
        this.form[f.name] = all;
      } else if (f.type === "radio") { if (f.checked) this.form[f.name] = f.value; }
      else this.form[f.name] = f.value;
      changed();
    };
    d.addEventListener("change", record);
    d.addEventListener("input", record);
    const seq = d.querySelector("[data-sequence]");
    if (seq && !this.sequence) this.sequence = [...seq.querySelectorAll("[data-locator]")].map((x) => x.dataset.locator);
    fillAttributes(d);
    this.markVisited();
    changed();
  }
  markVisited() {
    const seen = new Set(adhoc.history.map((h) => parseLoc(h.loc).doc));
    for (const it of this.items()) {
      try { it.classList.toggle("adhoc-visited", seen.has(parseLoc(it.dataset.locator).doc)); } catch {}
    }
  }
  select(item, extend = false, toggle = false) {
    const items = this.items();
    const idx = items.indexOf(item);
    if (toggle) item.classList.toggle("adhoc-selected");
    else if (extend && this.cursor >= 0) {
      const [a, b] = [Math.min(this.cursor, idx), Math.max(this.cursor, idx)];
      items.forEach((x, i) => x.classList.toggle("adhoc-selected", i >= a && i <= b));
    } else items.forEach((x) => x.classList.toggle("adhoc-selected", x === item));
    items.forEach((x) => x.classList.toggle("adhoc-current", x === item));
    this.cursor = idx;
    this.selected = items.filter((x) => x.classList.contains("adhoc-selected")).map((x) => x.dataset.locator);
    item.scrollIntoView({ block: "nearest" });
    if (paneExists(this.link)) {
      if (pane(this.link)) reveal(this.link);
      const loc = item.dataset.locator;
      const seq = this.sequence || items.map((x) => x.dataset.locator);
      const pos = seq.indexOf(loc);
      Promise.resolve(callPane(this.link, "seqOpen", { items: seq, position: pos >= 0 ? pos : 0, from: this.id }, loc))
        .then(() => this.markVisited());
    }
    changed();
  }
  move(delta) {
    const items = this.items();
    if (!items.length) return;
    const next = Math.max(0, Math.min(items.length - 1, (this.cursor < 0 ? -1 : this.cursor) + delta));
    this.select(items[next]);
  }
  // keep in step when the linked document pane moves through the sequence
  follow(loc) {
    const items = this.items();
    const it = items.find((x) => x.dataset.locator === loc);
    if (it) {
      items.forEach((x) => x.classList.toggle("adhoc-selected", x === it));
      items.forEach((x) => x.classList.toggle("adhoc-current", x === it));
      this.cursor = items.indexOf(it);
      this.selected = [loc];
      it.scrollIntoView({ block: "nearest" });
    }
    this.markVisited();
  }
  targets() { return this.selected.map((s) => parseLoc(s)); }
  renderAnnotations() { fillTags(this.doc()); }
  spec() {
    return { kind: this.kind, id: this.id, content: this.content, title: this._title, link: this.link, sequence: this.sequence, form: this.form };
  }
}

// -- the landing page ----------------------------------------------------------------------
// What this matter holds and what state it's in, so the user sees what they
// can ask for. Work isn't started here: suggestions are copied for the user
// to paste into Claude, and the only thing that opens is a document.

async function copyText(t) {
  try { await navigator.clipboard.writeText(t); return true; } catch {}
  const ta = el("textarea", { style: "position:fixed;opacity:0" });
  ta.value = t; document.body.append(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  ta.remove();
  return ok;
}
async function askClaude(t) {
  toast((await copyText(t)) ? `Copied. Paste it into Claude: “${t}”` : `Ask Claude: “${t}”`);
}
const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;
function fmtDate(d) {
  if (!d) return "";
  const t = new Date(d.length === 10 ? d + "T00:00:00Z" : d);
  return isNaN(t) ? d : t.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: d.length === 10 ? "UTC" : undefined });
}
function fmtAgo(secs) {
  if (!secs) return "";
  const s = Date.now() / 1000 - secs;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return fmtDate(new Date(secs * 1000).toISOString());
}

class HomePane {
  constructor(id) {
    this.id = id; this.kind = "home"; this.link = null;
    this.selected = []; this.cursor = -1; this.data = null; this.raw = "";
    this.root = el("section", { class: "pane", "data-pane": id });
    this.head = el("div", { class: "pane-head" }, el("span", { class: "pane-title" }, "Overview"),
      el("span", { class: "status" }, "What this matter holds. Ask Claude to work on it."));
    this.body = el("div", { class: "pane-body home" });
    this.root.append(this.head, this.body);
    this.refresh();
    this.timer = setInterval(() => { if (this.root.isConnected && this.root.offsetParent) this.refresh(); }, 3000);
  }
  title() { return "Overview"; }
  state() { return { id: this.id, kind: "home", selection: [] }; }
  spec() { return { kind: "home", id: this.id }; }
  items() { return []; }
  move() {}
  targets() { return []; }
  async refresh() {
    let raw;
    try { raw = await fetch("/api/overview").then((r) => r.text()); } catch { return; }
    if (raw === this.raw) return;
    this.raw = raw;
    try { this.data = JSON.parse(raw); } catch { return; }
    const top = this.body.scrollTop;
    this.render();
    this.body.scrollTop = top;
  }

  // a suggestion the user can paste into Claude
  ask(text, cls = "") { return el("button", { class: "ask " + cls, title: "Copy, to paste into Claude", onclick: () => askClaude(text) }, text); }
  // a row: opens a document, or copies a request about it
  row(main, side, action, title) {
    return el("li", { class: "row", tabindex: "0", title, onclick: action, onkeydown: (ev) => { if (ev.key === "Enter") { ev.stopPropagation(); action(); } } },
      el("span", { class: "main" }, main), el("span", { class: "side" }, side));
  }
  section(name, count, ...kids) {
    return el("section", { class: "card" }, el("h2", {}, name, count !== null ? el("span", { class: "count" }, count.toLocaleString()) : ""), ...kids);
  }
  empty(text, ask) { return el("div", { class: "empty" }, el("p", {}, text), ask ? this.ask(ask) : ""); }
  more(n, shown, what, ask, whats = what + "s") {
    return n > shown ? el("p", { class: "more" }, `${plural(n - shown, "more " + what, "more " + whats)} · `, el("a", { onclick: () => askClaude(ask) }, "ask Claude for them")) : "";
  }

  render() {
    const o = this.data, d = o.documents, ann = o.annotations;
    const lede = d.total
      ? `${plural(d.total, "document")}` +
        (d.custodians.length ? ` from ${plural(d.custodians.filter((c) => c.name !== "(none)").length || d.custodians.length, "custodian")}` : "") +
        (d.first_date ? `, ${fmtDate(d.first_date)} to ${fmtDate(d.last_date)}` : "") + "."
      : "No documents yet.";
    const intro = el("header", { class: "home-intro" },
      el("h1", {}, o.matter || "This matter"),
      el("p", { class: "lede" }, lede),
      el("p", {}, "This window shows the documents and what has been made from them. To search, read, summarize, " +
        "tag or write about them, ask Claude in its prompt. Claude can open things here for you."),
      d.total ? el("div", { class: "asks" }, el("span", { class: "label" }, "Try asking:"),
        this.ask("What is this matter about?"),
        this.ask("What are the key events, in order?"),
        this.ask("Which documents matter most, and why?"),
        this.ask("Find anything about …")) : "");

    const cards = [this.documents(d), this.entities(o.entities, d), this.people(o.people, d), this.pages(o.pages), this.lists(o.lists), this.published(o.published),
                   this.tags(ann), this.productions(o.productions, o.exhibits)];
    this.body.replaceChildren(intro, el("div", { class: "cards" }, ...cards),
      el("p", { class: "home-foot" }, "Click a suggestion to copy it, then paste it into Claude. Documents open in a new pane. Press ? for keys."));
  }

  documents(d) {
    if (!d.total) return this.section("Documents", 0,
      this.empty("Documents are added on your computer with adhoc ingest (Claude can't add or remove them). Once they're in, they show up here."));
    const state = [];
    if (d.attachments) state.push(`${plural(d.attachments, "attachment")} among them`);
    if (d.no_text) state.push(`${plural(d.no_text, "document")} without text yet, so searches don't reach ${d.no_text === 1 ? "it" : "them"}`);
    if (d.withdrawn) state.push(`${plural(d.withdrawn, "document")} withdrawn`);
    const breakdown = (label, rows, ask) => rows.length ? el("div", { class: "breakdown" }, el("span", { class: "label" }, label),
      ...rows.slice(0, 8).map((r) => el("button", { class: "tally", title: "Copy a request for these", onclick: () => askClaude(ask(r.name)) }, r.name, el("b", {}, r.n.toLocaleString()))),
      rows.length > 8 ? el("span", { class: "muted" }, `+${rows.length - 8}`) : "") : "";
    return this.section("Documents", d.total,
      breakdown("By kind", d.kinds, (k) => `List the ${k} documents`),
      breakdown("By custodian", d.custodians.filter((c) => c.name !== "(none)"), (c) => `Summarize ${c}'s documents`),
      state.length ? el("p", { class: "muted" }, state.join(" · ") + ".") : "",
      el("h3", {}, "Added most recently"),
      el("ul", { class: "rows" }, ...d.recent.map((r) => this.row(r.subject || r.name || short(r.doc),
        [r.kind, r.custodian, fmtDate(r.date)].filter(Boolean).join(" · "),
        () => openFromPane(this, "doc:" + r.doc, true), r.name || ""))),
      this.more(d.total - d.attachments, d.recent.length, "document", "List all the documents by date"));
  }

  entities(e, d) {
    const sugg = e.suggested ? el("div", {},
      el("p", { class: "muted" }, `${plural(e.suggested, "name")} found in the documents ${e.suggested === 1 ? "isn't" : "aren't"} on the list yet` +
        " (dotted underlines in a document). Most mentioned:"),
      el("ul", { class: "rows" }, ...e.top_suggested.map((x) => this.row(x.name, [x.kind, plural(x.docs, "document")].filter(Boolean).join(" · "),
        () => askClaude(`Who or what is ${x.name}? Add it to the entity list.`))))) : "";
    if (!e.total) return this.section("Entities", 0,
      this.empty("The entity list holds the people, companies, defined terms and numbers that matter here, with what each is. " +
        "Names on it are underlined in documents: hover one to see what it is, or select it and ask Claude.",
        d.total ? "Who are the main people and companies? Add them to the entity list." : null),
      sugg);
    const kinds = e.kinds.map((k) => `${k.n} ${k.name}`).join(" · ");
    return this.section("Entities", e.total,
      kinds ? el("p", { class: "muted" }, kinds) : "",
      el("ul", { class: "rows" }, ...e.known.map((x) => this.row(x.name, x.summary || x.kind || "",
        () => askClaude(`Tell me about ${x.name}`), [x.kind, x.summary].filter(Boolean).join(": ")))),
      this.more(e.total, e.known.length, "entity", "Show me the whole entity list", "entities"),
      sugg);
  }

  people(p, d) {
    if (!p.total) return this.section("People", null,
      this.empty(d.total ? "No email headers to take names from. Claude can find people named in the documents' text." :
        "People appear here from the documents' email headers.", d.total ? "Who are the people in these documents, and what are their roles?" : null));
    return this.section("Email correspondents", p.total,
      el("ul", { class: "rows" }, ...p.top.map((x) => this.row(x.name || x.address, plural(x.emails, "email"),
        () => askClaude(`Who is ${x.name || x.address}, and what is their role in this matter?`), x.address))),
      this.more(p.total, p.top.length, "person", "List everyone in the email headers", "people"),
      el("p", { class: "muted" }, "From the From, To and Cc lines. For people named only in the text, with their roles:"),
      this.ask("Make a cast of characters", "small"));
  }

  pages(pages) {
    if (!pages.length) return this.section("Pages", 0,
      this.empty("Pages are memos, chronologies and draft sections that Claude writes for you. Every quotation in them comes from a document and is checked against it.",
        "Write a chronology of the key events, quoting the documents"));
    return this.section("Pages", pages.length,
      el("ul", { class: "rows" }, ...pages.slice(0, RECENT_ROWS).map((x) => this.row(x.title || x.file,
        [x.refs ? plural(x.refs, "quotation") : "", fmtAgo(x.modified)].filter(Boolean).join(" · "),
        () => askClaude(`Show me the page ${x.file}`), x.file))),
      this.more(pages.length, RECENT_ROWS, "page", "List the pages in the workspace"),
      this.ask("Draft a memo on …", "small"));
  }

  lists(lists) {
    if (!lists.length) return this.section("Lists", 0,
      this.empty("Lists are sets of documents Claude found for a question, to step through here. A key set keeps the few that explain a point and says why the rest add nothing.",
        "Which documents show what happened, and in what order? Give me a key set."));
    return this.section("Lists", lists.length,
      el("ul", { class: "rows" }, ...lists.slice(0, RECENT_ROWS).map((x) => this.row(x.title || x.question || x.file,
        [x.key_set ? "key set" : "", plural(x.items, "item"), fmtAgo(x.modified)].filter(Boolean).join(" · "),
        () => askClaude(`Show me the list ${x.file}`), x.question || x.query || x.file))),
      this.more(lists.length, RECENT_ROWS, "list", "List the saved lists and key sets"));
  }

  // work product published from any installation of this matter (SYNC.md)
  published(p) {
    if (!p) return "";
    const items = [...p.pages.map((x) => ({ ...x, what: "page" })), ...p.lists.map((x) => ({ ...x, what: "list" }))]
      .sort((a, b) => b.modified - a.modified);
    if (!items.length) return this.section("Published", 0,
      this.empty("Pages and lists published to share with this matter's other installations appear here. Your workspace stays yours until you publish.",
        "Publish the chronology so the rest of the team has it"));
    return this.section("Published", items.length,
      el("ul", { class: "rows" }, ...items.slice(0, RECENT_ROWS).map((x) => this.row(x.title || x.question || x.file,
        [x.what === "page" && x.refs ? plural(x.refs, "quotation") : x.what === "list" ? plural(x.items, "item") : "", fmtAgo(x.modified)]
          .filter(Boolean).join(" · "),
        () => askClaude(`Show me the published ${x.what} work/${x.file}`), "work/" + x.file))),
      p.forks ? el("p", { class: "muted" }, `${plural(p.forks, "item")} revised on two installations at once. `,
        el("a", { onclick: () => askClaude("Which published work has forks? Show me the differences.") }, "Ask Claude to compare them")) : "",
      this.more(items.length, RECENT_ROWS, "item", "List the published work"));
  }

  tags(a) {
    const notes = a.notes ? el("p", { class: "muted" }, `${plural(a.threads, "note")}${a.notes > a.threads ? ` (${plural(a.notes - a.threads, "reply", "replies")})` : ""}.`) : "";
    if (!a.tags.length) return this.section("Tags", 0,
      this.empty("Tags record review calls: hot, responsive, privileged, and so on. Once defined, press t on a document (or its hotkey) to tag it.",
        "Set up review tags: hot, responsive, not responsive"), notes);
    return this.section("Tags", a.tags.length,
      el("ul", { class: "rows" }, ...a.tags.map((t) => this.row(
        el("span", {}, el("span", { class: "swatch", style: t.color ? `background:${t.color}` : "" }), t.tag, t.hotkey ? el("kbd", {}, t.hotkey) : ""),
        t.by_user + t.by_claude ? `on ${t.by_user + t.by_claude}` + (t.by_claude ? ` (${t.by_claude} by Claude)` : "") : "not used yet",
        () => askClaude(`Show me what's tagged ${t.tag}`), t.description || ""))),
      notes,
      ...a.recent_notes.slice(0, 3).map((n) => el("blockquote", { class: "note", title: "Open the document", onclick: () => openFromPane(this, "doc:" + n.doc, true) },
        el("span", { class: "who" }, `${n.by === adhoc.user ? "You" : String(n.by || "").startsWith("user:") ? n.by.slice(5) : "Claude"} · ${fmtDate(n.at)}`), String(n.body || "").slice(0, 200))));
  }

  productions(prods, exhibits) {
    const n = prods.length + exhibits.length;
    if (!n) return this.section("Productions and exhibits", 0,
      this.empty("Claude can Bates-stamp a production from your tags, with withholding, redactions and a check for possible privilege, and sticker exhibits for a deposition.",
        "Prepare a production of the documents tagged responsive"));
    const rows = [...prods.map((x) => this.row(x.name, `production · ${plural(x.documents, "document")}`, () => askClaude(`Summarize the production ${x.name}`))),
                  ...exhibits.map((x) => this.row(x.name, `exhibits · ${plural(x.documents, "exhibit")}`, () => askClaude(`Show me the exhibit list ${x.name}`)))];
    return this.section("Productions and exhibits", n, el("ul", { class: "rows" }, ...rows));
  }
}
const RECENT_ROWS = 8;
function openHome() {
  const h = adhoc.panes.find((p) => p.kind === "home") || addPane(new HomePane(newPaneId()), true);
  focus(h.id);
  return h;
}

// Open a locator from a pane. A list's preview follows it (its linked pane);
// anything else opens beside the pane, as a new tab among the documents it has
// opened, or in the tab already showing that document.
function openFromPane(p, loc, newIfUnlinked, forceNew = false) {
  if (!forceNew && paneExists(p.link) && !pane(p.link)) return callPane(p.link, "open", loc, { source: "pane" });
  let target = !forceNew && p.link && pane(p.link);
  if (!target && !forceNew) {
    let doc = null; try { doc = parseLoc(loc).doc; } catch {}
    const path = pathTo(p.id) || [];
    const parent = path[path.length - 2];
    const beside = parent?.type === "split" ? parent.children[parent.children.indexOf(path[path.length - 1]) + 1] : null;
    target = beside ? leaves(beside).map(pane).find((x) => x?.kind === "document" && x.info?.doc === doc) : null;
  }
  if (!target) target = addPane(new DocumentPane(newPaneId()), true, { beside: p.id });
  reveal(target.id);
  target.open(loc, { source: "pane" });
}

// Declarative attributes in a pane's static HTML, carried out here.
async function fillAttributes(d) {
  for (const e of d.querySelectorAll("[data-text]")) {
    try {
      const r = await getJSON("/api/text?loc=" + encodeURIComponent(e.dataset.text));
      if (r.status === "broken") { e.textContent = "[broken: " + r.error + "]"; e.classList.add("adhoc-status-broken"); continue; }
      const mode = e.dataset.normalize;
      let t = r.text;
      if (mode !== "lines") t = t.replace(/\s+/g, " ");
      else e.style.whiteSpace = "pre-line";
      if (mode === "inline") t = t.replace(/[“”"]/g, (c) => (c === "“" ? "‘" : c === "”" ? "’" : "'"));
      e.textContent = t;           // as text, never as HTML
    } catch (err) { e.textContent = "[error]"; }
  }
  for (const e of d.querySelectorAll("[data-snippet]")) {
    try {
      const ctx = Number(e.dataset.context || 80);
      const r = await getJSON(`/api/text?context=${ctx}&loc=` + encodeURIComponent(e.dataset.snippet));
      if (r.status === "broken") { e.textContent = "[broken]"; continue; }
      const sq = (s) => s.replace(/\s+/g, " ");
      const mark = d.createElement("mark");
      mark.textContent = sq(r.text);
      e.classList.add("adhoc-snippet");
      e.replaceChildren(d.createTextNode("…" + sq(r.pre || "")), mark, d.createTextNode(sq(r.post || "") + "…"));
    } catch {}
  }
  for (const e of d.querySelectorAll("[data-status]")) {
    try {
      const r = await getJSON("/api/text?loc=" + encodeURIComponent(e.dataset.status));
      e.classList.toggle("adhoc-status-broken", r.status === "broken");
    } catch {}
  }
  fillTags(d);
}
function fillTags(d) {
  if (!d) return;
  const A = adhoc.annotations;
  const color = (t) => A.definitions.find((x) => x.tag === t)?.color || "#666";
  for (const e of d.querySelectorAll("[data-tags]")) {
    let doc; try { doc = parseLoc(e.dataset.tags).doc; } catch { continue; }
    const tags = [...new Set(A.tags.filter((t) => t.target.doc === doc).map((t) => t.tag))];
    e.replaceChildren(...tags.map((t) => { const s = d.createElement("span"); s.className = "adhoc-tagchip"; s.style.color = color(t); s.textContent = t; return s; }));
  }
  for (const e of d.querySelectorAll("[data-notes]")) {
    let doc; try { doc = parseLoc(e.dataset.notes).doc; } catch { continue; }
    const n = A.notes.filter((x) => x.target?.doc === doc).length;
    e.textContent = n ? String(n) : "";
  }
}

// -- services: a pane's form, answered by code Claude runs in the container ------------------

const serving = new Map();
let serveSeq = 0;
function serve(p, name, values) {
  const id = ++serveSeq;
  serving.set(id, p);
  $(".status", p.head).textContent = "asking " + name + "…";
  post({ type: "svc", id, name, values });
}
adhoc.served = (id, result) => {
  const p = serving.get(id);
  serving.delete(id);
  if (!p) return;
  $(".status", p.head).textContent = "";
  if (result.error) return toast(result.error);
  p.sequence = null;
  p.load(result.content);
  changed();
};

// -- annotations ----------------------------------------------------------------------------

async function refreshAnnotations() {
  try {
    const before = JSON.stringify(adhoc.annotations.entities || []);
    adhoc.annotations = await getJSON("/api/annotations");
    adhoc.annotations.entities ||= [];
    const entitiesChanged = JSON.stringify(adhoc.annotations.entities) !== before;
    for (const p of adhoc.panes) {
      p.renderAnnotations?.();
      if (entitiesChanged) p.loadEntities?.();
    }
    if (entitiesChanged) entityCard.refresh();
  } catch {}
}
const annotateWaiting = new Map();
let annotateSeq = 0;
function annotate(request) {
  const id = ++annotateSeq;
  return new Promise((res) => { annotateWaiting.set(id, res); post({ type: "annotate", id, request }); });
}
adhoc.annotated = (id, result) => {
  const res = annotateWaiting.get(id);
  annotateWaiting.delete(id);
  if (result.error || result.refused) toast(result.error || result.refused);
  refreshAnnotations();
  res?.(result);
};
async function familyOf(doc) {
  let top = await docInfo(doc);
  while (top.parent) top = await docInfo(top.parent.doc);
  const out = [], todo = [top];
  while (todo.length) {
    const d = todo.shift();
    out.push({ doc: d.doc });
    for (const c of d.children) todo.push(await docInfo(c.doc));
  }
  return out;
}
async function applyTag(tag, scope = "selection") {
  const p = focusedPane();
  if (!p) return;
  let targets = p.targets();
  if (scope !== "selection") {
    const docs = [...new Set(targets.map((t) => t.doc))];
    targets = [];
    for (const d of docs) {
      if (scope === "family") targets.push(...await familyOf(d));
      else targets.push({ doc: d }, ...(await docInfo(d)).duplicates.map((x) => ({ doc: x })));
    }
  }
  if (!targets.length) return toast("Nothing selected to tag");
  const has = (t) => adhoc.annotations.tags.some((x) => x.tag === tag && JSON.stringify([x.target.doc, x.target.layer || null, x.target.select || null]) ===
    JSON.stringify([t.doc, t.layer || null, t.select || null]));
  const op = targets.every(has) ? "untag" : "tag";
  for (const t of targets) await annotate({ op, tag, target: t });
  toast(`${op === "tag" ? "Tagged" : "Untagged"} ${tag} on ${targets.length} item${targets.length > 1 ? "s" : ""}`);
}

function openPalette() {
  const box = $("#palette"), input = $("#palette-input"), list = $("#palette-list");
  let on = 0;
  const defs = () => adhoc.annotations.definitions;
  const matches = () => defs().filter((d) => d.tag.toLowerCase().includes(input.value.toLowerCase()));
  const draw = () => {
    const ms = matches();
    on = Math.min(on, Math.max(0, ms.length - 1));
    list.replaceChildren(...ms.map((d, i) => el("li", { class: i === on ? "on" : "" },
      el("span", { style: `color:${d.color || "inherit"}` }, d.tag), el("span", { class: "k" }, (d.group ? d.group + " · " : "") + (d.hotkey || "")))));
    if (!ms.length && input.value.trim()) list.replaceChildren(el("li", { class: "on" }, `Define new tag “${input.value.trim()}”`));
  };
  box.hidden = false; input.value = ""; draw(); input.focus();
  input.oninput = draw;
  input.onkeydown = async (ev) => {
    ev.stopPropagation();
    const ms = matches();
    if (ev.key === "Escape") { box.hidden = true; }
    else if (ev.key === "ArrowDown") { on = Math.min(on + 1, ms.length - 1); draw(); }
    else if (ev.key === "ArrowUp") { on = Math.max(on - 1, 0); draw(); }
    else if (ev.key === "Enter") {
      // Enter: the selection (or document); Shift+Enter: its whole family; Ctrl+Enter: it and its exact duplicates
      box.hidden = true;
      let tag = ms[on]?.tag;
      if (!tag && input.value.trim()) {
        tag = input.value.trim();
        const r = await annotate({ op: "define-tag", tag });
        if (r.error || r.refused) return;
      }
      if (tag) applyTag(tag, ev.shiftKey ? "family" : ev.ctrlKey ? "dupes" : "selection");
    }
  };
}
function noteEditor(request, title, body = "") {
  const box = $("#note-editor"), ta = $("#note-text");
  $("#note-title").textContent = title;
  box.hidden = false; ta.value = body; ta.focus();
  ta.onkeydown = (ev) => {
    ev.stopPropagation();
    if (ev.key === "Escape") box.hidden = true;
    else if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      box.hidden = true;
      if (ta.value.trim()) annotate({ ...request, body: ta.value });
    }
  };
}
function newNote() {
  const p = focusedPane();
  if (!p || p.kind !== "document" || !p.info) return toast("Notes attach to a document or a selection in one");
  const t = p.target();
  noteEditor({ op: "note", target: t }, t.select ? "Note on the selection" : "Note on the document");
}

// -- the entity layer ---------------------------------------------------------------------------
// Known entities come from the annotation log; suggestions from the entities
// index (detected when documents are indexed, so nothing waits on them).
// Hovering a mention shows a card; clicking selects it, so "who's this?"
// reaches Claude through `view selection`.

const ENTITY_KIND = { person: "person", organization: "organization", term: "defined term", identifier: "number",
                      contract: "contract", event: "event", place: "place", thing: "thing", other: "entity" };
function entityById(id) { return (adhoc.annotations.entities || []).find((e) => e.entity === id); }
async function refreshCandidates(force = false) {
  if (!force && Date.now() - adhoc.candidatesAt < 60000) return;
  adhoc.candidatesAt = Date.now();
  try { adhoc.candidates = await getJSON("/api/entity-candidates"); } catch {}
}
function cycleEntityMode() {
  const order = ["all", "known", "off"];
  adhoc.entityMode = order[(order.indexOf(adhoc.entityMode) + 1) % order.length];
  try { localStorage.setItem("adhoc.entityMode", adhoc.entityMode); } catch {}
  for (const p of adhoc.panes) if (p.kind === "document") { p.updateEntityStat(); p.drawBoxes(); }
  entityCard.hide();
  toast({ all: "Entities: known and suggested", known: "Entities: known only", off: "Entities hidden" }[adhoc.entityMode] + " (E)");
  changed();
}
const shortDate = (at) => { try { return new Date(at).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return ""; } };
const who = (by) => by === "claude" ? "Claude" : by === adhoc.user ? "you" : (by || "").replace("user:", "");

const entityCard = {
  el: null, pane: null, mention: null, showTimer: null, hideTimer: null,
  init() {
    this.el = $("#entity-card");
    this.el.addEventListener("mouseenter", () => clearTimeout(this.hideTimer));
    this.el.addEventListener("mouseleave", () => this.hoverOff());
  },
  hoverOn(pane, x, ev, frame) {
    clearTimeout(this.hideTimer);
    if (this.mention === x && !this.el.hidden) return;
    clearTimeout(this.showTimer);
    const off = frame ? frame.getBoundingClientRect() : { left: 0, top: 0 };
    const at = [ev.clientX + off.left, ev.clientY + off.top];
    this.showTimer = setTimeout(() => this.show(pane, x, at), this.el.hidden ? 280 : 60);
  },
  hoverOff() {
    clearTimeout(this.showTimer);
    clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => this.hide(), 250);
  },
  hide() { clearTimeout(this.showTimer); this.el.hidden = true; this.mention = null; },
  refresh() { if (!this.el.hidden && this.pane && this.mention) this.show(this.pane, this.mention, this.at); },
  show(pane, x, at) {
    this.pane = pane; this.mention = x; this.at = at;
    if (x.isDate) return dateCard.show(this, pane, x, at);
    const body = x.known ? x.entities.map((id) => this.knownBlock(pane, entityById(id))).filter(Boolean) : [this.suggestedBlock(pane, x)];
    this.el.replaceChildren(...body, el("div", { class: "ec-foot" }, x.known
      ? "Click to select, then ask Claude · e edits"
      : "Not in the entity list · click to select and ask Claude · e adds it"));
    this.place(at);
  },
  place(at) {
    this.el.hidden = false;
    const w = this.el.offsetWidth, h = this.el.offsetHeight;
    let left = Math.min(at[0] + 12, innerWidth - w - 8), top = at[1] + 18;
    if (top + h > innerHeight - 8) top = Math.max(8, at[1] - h - 12);
    Object.assign(this.el.style, { left: Math.max(8, left) + "px", top: top + "px" });
  },
  knownBlock(pane, e) {
    if (!e) return null;
    const see = (e.see || []).slice(0, 3).map((t) => el("a", { class: "ec-see", title: "open",
      onclick: () => { this.hide(); pane.highlight(t, "entity"); } }, "“" + ((t.quote?.exact || "the document").replace(/\s+/g, " ").slice(0, 60)) + "”"));
    return el("div", { class: "ec-entity" },
      el("div", { class: "ec-head" }, el("span", { class: "ec-name" }, e.name), el("span", { class: "ec-kind" }, ENTITY_KIND[e.kind] || e.kind)),
      e.summary ? el("div", { class: "ec-summary" }, e.summary) : el("div", { class: "ec-summary ec-empty" }, "Not described yet: select it and ask Claude."),
      ...(e.recap ? [el("div", { class: "ec-recap" }, el("span", { class: "ec-label" }, "Last time" + (e.recap_at ? ` (${shortDate(e.recap_at)})` : "") + ": "), e.recap)] : []),
      ...(see.length ? [el("div", { class: "ec-sees" }, "See ", ...see.flatMap((a, i) => i ? [" · ", a] : [a]))] : []),
      el("div", { class: "ec-by" }, `added by ${who(e.by)}` + (e.edited_by && e.edited_by !== e.by ? `, edited by ${who(e.edited_by)}` : "")));
  },
  suggestedBlock(pane, x) {
    const c = adhoc.candidates[x.key] || {};
    const kind = { person: "possible person", organization: "organization", term: "defined term", identifier: "number" }[x.kind] || x.kind;
    const lines = [];
    if (x.kind === "term") {
      if (x.means) lines.push(el("div", { class: "ec-summary" }, el("span", { class: "ec-label" }, "Means: "), el("span", { class: "ec-literal" }, x.means)));
      else if (x.defined) {
        const t = pane.ents?.cps.slice(...x.defined).join("").replace(/\s+/g, " ") || "";
        lines.push(el("div", { class: "ec-summary" }, el("span", { class: "ec-label" }, "Defined: "), el("span", { class: "ec-literal" }, t.slice(0, 220))));
      }
      if (x.defined && !(x.basis || []).includes("definition"))
        lines.push(el("div", { class: "ec-sees" }, el("a", { onclick: () => { this.hide(); pane.highlight({ doc: pane.info.doc, layer: pane.layer, select: { char: x.defined } }, "definition"); } }, "Go to the definition")));
    }
    if (x.kind === "person") {
      const roles = c.roles || (x.role ? [x.role] : []);
      if (roles.length) lines.push(el("div", { class: "ec-summary" }, roles.join(", ")));
      const why = { title: "a title", initial: "an initial", address: "an email address", by: "a signature line", self: "\"I, …\"",
                    signature: "a sign-off", role: "a role beside it", esq: "Esq." };
      lines.push(el("div", { class: "ec-why" }, "Taken as a person for " + (x.basis || []).map((b) => why[b] || b).join(", ")));
    }
    if (x.kind === "identifier" && (c.labels || x.label)) lines.push(el("div", { class: "ec-summary" }, "Called " + (c.labels || [x.label]).join(", ")));
    if (x.kind !== "term" && c.docs) lines.push(el("div", { class: "ec-why" }, c.docs === 1 ? "Only in this document" : `In ${c.docs} documents`));
    return el("div", { class: "ec-entity suggested" },
      el("div", { class: "ec-head" }, el("span", { class: "ec-name" }, x.form), el("span", { class: "ec-kind" }, kind)), ...lines);
  },
};

// -- date cards ----------------------------------------------------------------------------------
// Hovering a date (in the text, or the document's own in the date strip)
// shows where it sits in the matter's story: the document's place in its
// thread, how many documents are dated near it, the email a quoted header
// names, and the nearest events someone already marked as mattering
// (chronology entries, dated events in the entity list, key sets, pages'
// citations, documents tagged hot or key). DATES.md has why.

const DATE_SOURCE = { sent: "Sent", attached: "Attached to an email sent", created: "Created", modified: "Last modified", metadata: "Dated" };
function markClass(x) { return x.isDate ? "ent date" : x.known ? "ent" : "ent sug"; }
// "2026-03-09T10:14:00-05:00" -> "Mon, Mar 9, 2026, 10:14", in the time as written (no time-zone shift)
function fmtDay(s, weekday = true) {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(s || "");
  if (!m) return s || "";
  const t = new Date(Date.UTC(+m[1], +m[2] - 1, +(m[3] || 1)));
  return t.toLocaleDateString(undefined, m[3] ? { timeZone: "UTC", year: "numeric", month: "short", day: "numeric", ...(weekday ? { weekday: "short" } : {}) }
                                                 : { timeZone: "UTC", year: "numeric", month: "long" });
}
function fmtDateTime(s, dayOnly) {
  const time = !dayOnly && /T(\d{2}:\d{2})/.exec(s || "");
  return fmtDay(s) + (time && time[1] !== "00:00" ? ", " + time[1] : "");
}
// "9 days before", "the same day", "3 months after"
function distance(from, to) {
  const a = dayNumber(from), b = dayNumber(to);
  if (a === null || b === null) return "";
  const n = b - a, abs = Math.abs(n), side = n < 0 ? "before" : "after";
  if (n === 0) return "same day";
  if (abs < 14) return `${abs} day${abs === 1 ? "" : "s"} ${side}`;
  if (abs < 60) return `${Math.round(abs / 7)} weeks ${side}`;
  if (abs < 730) return `${Math.round(abs / 30.44)} months ${side}`;
  return `${Math.round(abs / 365.25)} years ${side}`;
}

const dateCard = {
  seq: 0,
  async show(card, pane, x, at) {
    const seq = ++this.seq;
    const date = x.resolved, doc = pane.info?.doc;
    const head = el("div", { class: "ec-head" }, el("span", { class: "ec-name" }, fmtDay(date)),
      el("span", { class: "ec-kind" }, x.strip ? (DATE_SOURCE[x.source] || "date").toLowerCase() : "date"));
    const lines = [];
    if (!x.strip) {
      const read = x.kind === "relative" || x.kind === "noyear";
      if (read) lines.push(el("div", { class: "ec-why" }, `“${x.form.replace(/\s+/g, " ")}”, counted from the document's date`));
      if (x.ambiguous) lines.push(el("div", { class: "ec-why" }, `Or ${fmtDay(x.alt, false)}, if the day is written first`));
    }
    card.el.replaceChildren(el("div", { class: "ec-entity dc" }, head, ...lines, el("div", { class: "ec-empty" }, "…")));
    card.place(at);
    let r;
    try {
      const q = new URLSearchParams({ date, ...(doc ? { doc } : {}), ...(x.header?.who ? { who: x.header.who } : {}) });
      r = await getJSON("/api/around?" + q);
    } catch { r = null; }
    if (seq !== this.seq || card.mention !== x || card.el.hidden) return;
    const body = r ? this.body(pane, x, r) : [el("div", { class: "ec-empty" }, "The matter's dates couldn't be read.")];
    card.el.replaceChildren(el("div", { class: "ec-entity dc" }, head, ...lines, ...body),
      el("div", { class: "ec-foot" }, x.strip ? "Click an entry to open it" : "Click the date to select it, then ask Claude · click an entry to open it"));
    card.place(at);
  },
  open(pane, loc) { entityCard.hide(); openFromPane(pane, loc, true); },
  body(pane, x, r) {
    const out = [], date = r.date;
    // in-text: where this document stands relative to the date
    if (!x.strip && r.here?.date) {
      const d = distance(date, r.here.date.slice(0, 10));
      out.push(el("div", { class: "ec-summary" }, d === "same day" ? "This document is dated the same day."
        : `This document is dated ${fmtDay(r.here.date, false)}, ${d.replace("before", "earlier").replace("after", "later")}.`));
    }
    for (const q of r.quoted || [])
      out.push(el("div", { class: "ec-sees" }, "The email quoted here: ", el("a", { onclick: () => this.open(pane, "doc:" + q.doc) }, q.label)));
    if (r.quoted_missing)
      out.push(el("div", { class: "ec-why" }, `The email quoted here (${x.header.who}, ${fmtDay(date, false)}) isn't in the matter.`));
    // its thread
    // its thread, when the date is the document's own day (the thread places the document, not the date)
    const t = r.thread;
    if (t && t.size > 1 && (x.strip || r.here?.date?.slice(0, 10) === date)) {
      const nav = [];
      if (t.prev) nav.push(el("a", { title: t.prev.label, onclick: () => this.open(pane, "doc:" + t.prev.doc) }, `← ${fmtDay(t.prev.date, false)}`));
      if (t.next) nav.push(el("a", { title: t.next.label, onclick: () => this.open(pane, "doc:" + t.next.doc) }, `${fmtDay(t.next.date, false)} →`));
      out.push(el("div", { class: "ec-sees" }, `Message ${t.position} of ${t.size} in its thread`, ...(nav.length ? [": ", ...nav.flatMap((a, i) => i ? [" · ", a] : [a])] : [])));
    }
    // events: before, on, after
    const short = (d) => d.slice(0, 4) === date.slice(0, 4) && d.length >= 10
      ? new Date(Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10))).toLocaleDateString(undefined, { timeZone: "UTC", month: "short", day: "numeric" })
      : fmtDay(d, false);
    const row = (e, rel) => el("div", { class: "dc-row" + (e.here ? " here" : ""), title: (e.sources || []).join("; ") },
      el("span", { class: "dc-when" }, rel), el("span", { class: "dc-date" }, short(e.date)),
      e.doc || (e.loc && typeof e.loc === "object" && e.loc.doc)
        ? el("a", { class: "dc-label", onclick: () => this.open(pane, e.loc && (typeof e.loc === "string" || e.loc.doc) ? e.loc : "doc:" + e.doc) }, e.label)
        : el("span", { class: "dc-label" }, e.label),
      el("span", { class: "dc-src" }, (e.sources || [])[0] || ""));
    if (r.events) {
      const rows = [];
      if (r.more_before) rows.push(el("div", { class: "dc-more" }, `${r.more_before} earlier`));
      for (const e of r.before) rows.push(row(e, distance(date, e.date.slice(0, 10)).replace(" before", "")));
      rows.push(el("div", { class: "dc-row now" }, el("span", { class: "dc-when" }, "▸"), el("span", { class: "dc-date" }, short(date)),
        el("span", { class: "dc-label" }, r.on.length ? "" : x.strip ? "this document" : "this date")));
      for (const e of r.on) rows.push(row(e, ""));
      for (const e of r.after) rows.push(row(e, distance(date, e.date.slice(0, 10)).replace(" after", "")));
      if (r.more_after) rows.push(el("div", { class: "dc-more" }, `${r.more_after} later`));
      out.push(el("div", { class: "dc-line" }, ...rows));
    } else {
      out.push(el("div", { class: "ec-why" }, "No events marked yet. They come from chronologies, pages' citations, key sets, dated events in the entity list, and documents tagged hot or key. ",
        el("a", { onclick: () => askClaude("Write a chronology of the key events, quoting the documents, and add the turning points to the entity list as dated events.") }, "Ask Claude for a chronology")));
    }
    // documents near it
    const n = r.near;
    if (n && n.count > (x.strip ? 1 : 0)) {
      const what = n.days ? `${plural(n.count, "document")} dated within ${n.days} days` : `${plural(n.count, "document")} dated in ${fmtDay(date)}`;
      out.push(el("div", { class: "ec-sees" }, el("a", { title: "Step through them in date order with ] and [", onclick: () => this.stepThrough(pane, n.docs, date) }, what),
        n.days && n.same_day > (x.strip ? 1 : 0) ? ` (${n.same_day} that day)` : ""));
    }
    return out;
  },
  // open the near documents beside the pane, as a sequence starting at the date
  stepThrough(pane, docs, date) {
    entityCard.hide();
    if (!docs.length) return;
    const items = docs.map((d) => "doc:" + d);
    const target = addPane(new DocumentPane(newPaneId()), true, { beside: pane.id });
    reveal(target.id);
    target.seqOpen({ items, position: 0, from: pane.id }, items[0]);
    toast(`${plural(items.length, "document")} near ${fmtDay(date, false)}: ] and [ step through them`);
  },
};

// e: add the selection (or the selected suggestion) to the entity list, or edit the entity it is
function entityEditor() {
  const p = focusedPane();
  if (!p || p.kind !== "document" || !p.info) return toast("Select a name in a document first");
  const x = p.selectionMention();
  const c = p.selection?.select?.char;
  const selected = c && p.ents && p.selection.layer === p.ents.layer ? p.ents.cps.slice(...c).join("").replace(/\s+/g, " ").trim() : "";
  const known = x?.known ? entityById(x.entities[0]) : null;
  if (!known && !selected) return toast("Select a name, or click one, then press e");
  const box = $("#entity-editor"), name = $("#ee-name"), kind = $("#ee-kind"), summary = $("#ee-summary");
  $("#ee-title").textContent = known ? `Edit ${known.name}` : "Add to the entities";
  name.value = known ? known.name : (x?.kind === "person" ? x.form.replace(/^(?:Mr|Mrs|Ms|Dr|Judge|Justice)\.?\s+/, "") : x?.form || selected);
  kind.value = known ? (ENTITY_KIND[known.kind] ? known.kind : "other") : (x?.kind || "other");
  summary.value = known ? known.summary || "" : (x?.kind === "term" && x.means ? x.means : "");
  summary.placeholder = known ? "What it is (shown when you hover over it)" : "What it is (optional: leave it for Claude)";
  box.hidden = false; name.focus(); name.select();
  const close = () => { box.hidden = true; p.body.focus(); };
  box.onkeydown = async (ev) => {
    ev.stopPropagation();
    if (ev.key === "Escape") return close();
    if (ev.key !== "Enter" || (ev.target === summary && !(ev.ctrlKey || ev.metaKey))) return;
    ev.preventDefault();
    const n = name.value.trim();
    if (!n) return;
    close();
    const alias = selected && selected !== n ? [selected] : [];
    let r;
    if (known) {
      const req = { op: "edit-entity", entity: known.entity };
      if (n !== known.name) req.name = n;
      if (kind.value !== known.kind) req.kind = kind.value;
      if (summary.value.trim() !== (known.summary || "")) req.summary = summary.value.trim();
      if (alias.length && !known.aliases.includes(alias[0])) req.add_aliases = alias;
      if (Object.keys(req).length === 2) return;
      r = await annotate(req);
    } else {
      r = await annotate({ op: "define-entity", name: n, kind: kind.value, aliases: alias, ...(summary.value.trim() ? { summary: summary.value.trim() } : {}) });
    }
    if (!r.error && !r.refused) toast(known ? `Updated ${n}` : `Added ${n}` + (summary.value.trim() ? "" : ": ask Claude to describe it"));
  };
}

// -- keyboard ---------------------------------------------------------------------------------

function onKey(ev) {
  if (ev.target.closest?.("input,textarea,select") && ev.key !== "Escape") return;
  if (!$("#palette").hidden || !$("#note-editor").hidden || !$("#entity-editor").hidden) return;
  const p = focusedPane();
  const k = ev.key;
  const doc = p?.kind === "document" ? p : null, web = p && p.kind !== "document" && p.kind !== "home" ? p : null;
  let handled = true;
  if (k === "Tab") { const ids = leaves(), i = ids.indexOf(p?.id); const n = ids[(i + (ev.shiftKey ? -1 : 1) + ids.length) % ids.length]; if (n) focus(n); }
  else if ((k === "j" || k === "ArrowDown") && web) web.move(1);
  else if ((k === "k" || k === "ArrowUp") && web) web.move(-1);
  else if (k === "Enter" && web && web.cursor >= 0) openFromPane(web, web.items()[web.cursor].dataset.locator, true, true);
  else if ((k === "PageDown" || (k === " " && !ev.shiftKey)) && doc) doc.page(1);
  else if ((k === "PageUp" || (k === " " && ev.shiftKey)) && doc) doc.page(-1);
  else if (k === "]" && doc) doc.step(1);
  else if (k === "[" && doc) doc.step(-1);
  else if (k === "]" && web) { if (paneExists(web.link)) callPane(web.link, "step", 1); else web.move(1); }
  else if (k === "[" && web) { if (paneExists(web.link)) callPane(web.link, "step", -1); else web.move(-1); }
  else if (k === "W" && p) post({ type: "tearoff", pane: p.id });
  else if (k === "H") openHome();
  else if (k === "m") mark();
  else if (k === "t") openPalette();
  else if (k === "N") newNote();
  else if (k === "e") entityEditor();
  else if (k === "E") cycleEntityMode();
  else if (k === "v" && doc?.info?.layers.length > 1) { const ls = doc.info.layers; const i = ls.findIndex((l) => l.id === doc.layer); doc.open({ doc: doc.info.doc, layer: ls[(i + 1) % ls.length].id }, { quiet: true }); }
  else if (k === "f" && doc?.info) { const modes = doc.modes(); doc.setMode(modes[(modes.indexOf(doc.mode) + 1) % modes.length]); }
  else if (k.startsWith("Arrow") && doc?.mode === "grid") doc.moveCell(k, ev.shiftKey);
  else if (k === "Escape" && doc) { entityCard.hide(); doc.clearSelection(); }
  else if (k === "?") toast("j/k move · Enter open · ]/[ next/prev document · Space page · m mark · t tag · N note · e entity · E show entities · v layer · f mode · Tab pane · H overview · W pane to a new window · Alt-drag region · grid: click, drag or shift-arrows to select cells");
  else if (!ev.ctrlKey && !ev.metaKey && !ev.altKey && !BUILTIN_KEYS.has(k)) {
    const def = adhoc.annotations.definitions.find((d) => d.hotkey === k);
    if (def) applyTag(def.tag); else handled = false;
  } else handled = false;
  if (handled) ev.preventDefault();
}
document.addEventListener("keydown", onKey);
// Copying cells in grid mode gives their text, tab-separated, as a spreadsheet would.
document.addEventListener("copy", (ev) => {
  const p = focusedPane();
  const x = p?.mode === "grid" && p.rect();
  if (!x) return;
  ev.clipboardData.setData("text/plain", p.rectText(x));
  ev.preventDefault();
});

function mark() {
  const p = focusedPane();
  if (!p) return;
  const sel = p.kind === "document" ? p.selection : null;
  if (p.kind === "document") {
    if (!sel) return toast("Select something to mark");
    p.marks.push(sel); p.drawBoxes(); toast(`Marked (${p.marks.length} in this pane)`);
  } else {
    if (!p.selected.length) return toast("Select rows to mark");
    adhoc.listMarks = [...(adhoc.listMarks || []), ...p.selected];
    toast(`Marked ${p.selected.length}`);
  }
  changed();
}

// -- talking to the host ------------------------------------------------------------------------

let changeTimer = null;
function changed() {
  clearTimeout(changeTimer);
  changeTimer = setTimeout(() => post({ type: "state", state: snapshot() }), 100);
}

function docPaneFor(cmd) {
  if (cmd.pane) {
    const p = pane(cmd.pane);
    if (!p) throw new Error("no pane " + cmd.pane);
    if (p.kind !== "document") throw new Error(`pane ${cmd.pane} is a ${p.kind} pane`);
    return p;
  }
  if (!cmd.new_pane) {
    const f = focusedPane();
    if (f?.kind === "document") return f;
    const any = adhoc.panes.find((x) => x.kind === "document");
    if (any) return any;
  }
  return addPane(new DocumentPane(newPaneId()), true);
}

const commands = {
  async open(c) {
    const p = docPaneFor(c);
    await p.open(c.loc, { view: c.view, mode: c.mode, source: "claude" });
    focus(p.id);
    return { pane: p.id };
  },
  async highlight(c) {
    const p = docPaneFor(c);
    await p.highlight(c.loc, c.label);
    return { pane: p.id };
  },
  async "clear-highlights"(c) {
    for (const p of c.pane ? [pane(c.pane)] : adhoc.panes) if (p?.kind === "document") { p.highlights = []; p.drawBoxes(); }
    return {};
  },
  async sequence(c) {
    const p = docPaneFor(c);
    p.sequence = { items: c.locs, position: 0, from: null };
    if (c.locs.length) await p.open(c.locs[0], { source: "claude" });
    return { pane: p.id };
  },
  async pane(c) {
    if (c.update) {
      const p = pane(c.update);
      if (!p || p.kind === "document" || p.kind === "home") throw new Error("no web pane " + c.update);
      p.sequence = c.sequence || null;
      if (c.title) { p._title = c.title; $(".pane-title", p.head).textContent = c.title; }
      p.load(c.content);
      return { pane: p.id };
    }
    const p = addPane(new WebPane(newPaneId(), c.kind || "custom", c.content, c.title), true);
    p.sequence = c.sequence || null;
    if (c.preview_new) {
      const d = addPane(new DocumentPane(newPaneId()), false, { beside: p.id });
      p.link = d.id;
    } else if (c.link) {
      if (paneKind(c.link) !== "document") throw new Error("can only link to a document pane");
      p.link = c.link;
    }
    renderTabs();
    return { pane: p.id, preview: p.link };
  },
  async close(c) { closePane(c.pane); return {}; },
  async home() { return { pane: openHome().id }; },
  // moving a pane to another window: taken out of this one, put into that one
  async detach(c) {
    const p = pane(c.pane);
    if (!p) throw new Error("no pane " + c.pane);
    const spec = p.spec();
    closePane(p.id, true);
    return spec;
  },
  async adopt(c) {
    const s = c.spec;
    let p;
    if (s.kind === "document") {
      p = addPane(new DocumentPane(s.id), true);
      if (s.doc) {
        await p.open({ doc: s.doc, layer: s.layer }, { mode: s.mode, quiet: true });
        Object.assign(p, { sequence: s.sequence, marks: s.marks || [], highlights: s.highlights || [], selection: s.selection });
        p.drawBoxes();
        const v = s.visible || {};
        const at = v.page ? { page: v.page[0] } : v.char ? { char: v.char } : v.rows ? { sheet: v.sheet, cells: "A" + v.rows[0] } : null;
        if (at) await p.scrollTo({ select: at });
        p.updateStatus();
      }
    } else if (s.kind === "home") {
      p = addPane(new HomePane(s.id), true);
    } else {
      p = addPane(new WebPane(s.id, s.kind, s.content, s.title), true);
      Object.assign(p, { link: s.link, sequence: s.sequence, form: s.form || {} });
    }
    renderTabs();
    return { window: adhoc.window, pane: p.id };
  },
  // how the window's tabs are shown: one at a time ("tabs"; "single" is the same), or all tiled
  async layout(c) {
    if (!["tabs", "single", "split-v", "split-h", "grid"].includes(c.layout)) throw new Error("layout is tabs, split-v, split-h or grid");
    adhoc.tile = c.layout === "tabs" || c.layout === "single" ? null : c.layout; renderTabs(); return {};
  },
  async state() { return snapshot(); },
  async selection() {
    const p = focusedPane();
    const entity = p?.kind === "document" ? p.selectionEntity() : null;
    const date = p?.kind === "document" ? p.selectionDate() : null;
    return { pane: p?.id ?? null, selection: p ? (p.kind === "document" ? p.selection : p.state().selection) : null,
             ...(entity ? { entity } : {}), ...(date ? { date } : {}), marks: p?.marks || adhoc.listMarks || [] };
  },
  async "clear-marks"(c) {
    for (const p of adhoc.panes) if (!c.pane || p.id === c.pane) { if (p.marks) { p.marks = []; p.drawBoxes(); } }
    adhoc.listMarks = [];
    return {};
  },
};

adhoc.command = async (seq, cmd) => {
  let result;
  try {
    const fn = commands[cmd.cmd];
    if (!fn) throw new Error("unknown command " + cmd.cmd);
    result = await fn(cmd);
  } catch (e) {
    result = { error: String(e.message || e) };
  }
  // the host learns this window's panes before the result, so the next command finds them
  post({ type: "state", state: snapshot() });
  post({ type: "result", seq, result });
  changed();
};
adhoc.config = (c) => {
  adhoc.user = c.user; adhoc.window = c.window || "w1";
  // the first window opens on the overview; Claude's commands then open panes beside it
  if (adhoc.window === "w1" && !adhoc.panes.length) openHome();
};
adhoc.elsewhere = (map) => { adhoc.elsewhereMap = map; };
adhoc.visited = (entry) => {
  adhoc.history.push(entry);
  if (adhoc.history.length > 1000) adhoc.history.shift();
  for (const p of adhoc.panes) p.markVisited?.();
};
adhoc.relayed = (id, method, args) => { if (RELAYABLE.has(method)) Promise.resolve(pane(id)?.[method]?.(...args)).then(changed); };

// The annotation log changes from outside (Claude, through its API), so look again now and then.
setInterval(refreshAnnotations, 2000);
entityCard.init();
refreshAnnotations();
refreshCandidates(true);
renderTabs();
post({ type: "ready" });
