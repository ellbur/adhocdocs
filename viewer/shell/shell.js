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
const BUILTIN_KEYS = new Set(["j", "k", "m", "t", "v", "f", "F", "N", "e", "E", "[", "]", " ", "Tab", "Enter", "Escape", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "PageDown", "PageUp", "Home", "End", "W", "H", "?"]);

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
      else if (k === "bytes") sel.bytes = v.split(",").map(Number);
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
  if (sel.bytes) parts.push("bytes=" + sel.bytes.join(","));
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
// A limited copy's originals: the viewer says when a fetch ends (adhoc.fetched, from its fetching
// thread through the window's event loop). whenFetched(content) resolves then, with "here" or
// "failed", or after `ms` regardless, so a pane checks again even if a word went astray.
const FETCH_CHECK_MS = 15000;
const fetchWaiters = new Map();      // content -> Set of resolve functions
function whenFetched(content, ms) {
  let done, timer;
  const p = new Promise((res) => {
    done = (state) => { clearTimeout(timer); fetchWaiters.get(content)?.delete(done); res(state); };
    timer = setTimeout(() => done("unheard"), ms);
    if (!fetchWaiters.has(content)) fetchWaiters.set(content, new Set());
    fetchWaiters.get(content).add(done);
  });
  p.cancel = () => done("cancelled");
  return p;
}
// a limited copy: start fetching the originals of what's likely to be opened next (no waiting)
function prefetch(locs) {
  const docs = [...new Set(locs.map((l) => parseLoc(l).doc).filter(Boolean))];
  if (docs.length) fetch("/api/prefetch?docs=" + docs.map(encodeURIComponent).join(",")).catch(() => {});
}
async function docInfo(doc) { return cache.docs[doc] ??= getJSON("/api/doc/" + encodeURIComponent(doc)); }
async function layerText(id) {
  return cache.text[id] ??= fetch(`/api/layer/${id}/text`).then((r) => r.text()).then((t) => ({ text: t, cps: Array.from(t) }));
}
async function geometry(id) { return cache.geometry[id] ??= getJSON(`/api/layer/${id}/geometry`); }
async function segments(id) { return cache.segments[id] ??= getJSON(`/api/layer/${id}/segments`); }
// a chunk of an original's bytes, for hex mode (the original never changes, so chunks are kept)
const HEX_CHUNK = 4096;
cache.bytes = {};
async function byteChunk(doc, i) {
  const key = doc + ":" + i;
  return cache.bytes[key] ??= getJSON(`/api/bytes/${encodeURIComponent(doc)}?start=${i * HEX_CHUNK}&end=${(i + 1) * HEX_CHUNK}`)
    .then((r) => { const b = new Uint8Array(r.hex.length / 2); for (let j = 0; j < b.length; j++) b[j] = parseInt(r.hex.substr(2 * j, 2), 16); return b; })
    .catch((e) => { delete cache.bytes[key]; throw e; });
}
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
  annotations: { definitions: [], tags: [], notes: [], entities: [], links: [], facets: [], filed: [] }, annLogSize: -1,
  // the issue the user is working on (a facet): what isn't filed under it is dimmed; null for all
  facet: (() => { try { return localStorage.getItem("adhoc.facet") || null; } catch { return null; } })(),
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
// -- menus -----------------------------------------------------------------------------------
// One menu at a time, under the button that opened it. Items: {label, hint,
// checked, disabled, onclick}, {heading}, or "-" for a separator.

function showMenu(anchor, items, align = "right") {
  const m = $("#menu");
  if (!m.hidden && m.anchor === anchor) return hideMenu();
  hideMenu();
  m.replaceChildren(...items.map((it) => {
    if (it === "-") return el("div", { class: "msep" });
    if (it.heading) return el("div", { class: "mh" }, it.heading);
    return el("button", { role: "menuitem", title: it.title, disabled: it.disabled ? "" : null,
                          onclick: () => { hideMenu(); it.onclick?.(); } },
      ...(items.some((x) => x.checked !== undefined) ? [el("span", { class: "ck" }, it.checked ? "✓" : "")] : []),
      el("span", {}, it.label), it.hint ? el("span", { class: "mhint" }, it.hint) : "");
  }));
  m.hidden = false; m.anchor = anchor;
  anchor.classList.add("open");
  const r = anchor.getBoundingClientRect(), w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.max(6, Math.min(innerWidth - w - 6, align === "left" ? r.left : r.right - w)) + "px";
  m.style.top = (r.bottom + 4 + h > innerHeight ? Math.max(6, r.top - h - 4) : r.bottom + 4) + "px";
  m.querySelector("button:not(:disabled)")?.focus();
}
function hideMenu() {
  const m = $("#menu");
  if (m.hidden) return;
  m.hidden = true; m.anchor?.classList.remove("open"); m.anchor = null;
}
document.addEventListener("mousedown", (ev) => { if (!ev.target.closest("#menu, .icon-btn.open")) hideMenu(); }, true);
window.addEventListener("blur", () => hideMenu());
// keys while a menu is open: arrows move, Escape closes (Enter and Space press the button)
function menuKey(ev) {
  const bs = [...$("#menu").querySelectorAll("button:not(:disabled)")];
  const i = bs.indexOf(document.activeElement);
  if (ev.key === "Escape") hideMenu();
  else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") bs[(i + (ev.key === "ArrowDown" ? 1 : -1) + bs.length) % bs.length]?.focus();
  else return;
  ev.preventDefault();
}

// -- light and dark ----------------------------------------------------------------------------
// The interface and documents each have a theme. The interface follows the
// system unless told otherwise; documents follow the interface unless told
// otherwise. Dark documents: page images come inverted from MuPDF (?dark=1),
// presentations and text get dark colors (shell.css), so nothing is filtered
// on screen except a presentation with colors of its own.

const prefGet = (k, d) => { try { return localStorage.getItem(k) || d; } catch { return d; } };
const prefSet = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
adhoc.theme = { ui: prefGet("adhoc.theme.ui", "system"), doc: prefGet("adhoc.theme.doc", "ui") };
const systemDark = matchMedia("(prefers-color-scheme: dark)");
function uiDark() { return adhoc.theme.ui === "dark" || (adhoc.theme.ui === "system" && systemDark.matches); }
function docDark() { return adhoc.theme.doc === "ui" ? uiDark() : adhoc.theme.doc === "dark"; }
function themeFrame(f) {
  const r = f.contentDocument?.documentElement;
  if (!r) return;
  r.dataset.theme = uiDark() ? "dark" : "light";
  r.dataset.doc = docDark() ? "dark" : "light";
}
function applyTheme() {
  const r = document.documentElement, wasDoc = r.dataset.doc;
  r.dataset.theme = uiDark() ? "dark" : "light";
  r.dataset.doc = docDark() ? "dark" : "light";
  for (const f of document.querySelectorAll("iframe")) themeFrame(f);
  if (wasDoc && wasDoc !== r.dataset.doc) for (const p of adhoc.panes) p.docThemeChanged?.();
  window.adhocTerm?.retheme();
}
// cropping pages' blank margins: one setting for every paginated document
function cropMargins() { return prefGet("adhoc.cropMargins", "") === "1"; }
function setCropMargins(on) {
  prefSet("adhoc.cropMargins", on ? "1" : "");
  for (const p of adhoc.panes) if (p.mode === "page" && p.info) p.relayout();
}
function setTheme(which, v) { adhoc.theme[which] = v; prefSet("adhoc.theme." + which, v); applyTheme(); }
systemDark.addEventListener?.("change", () => applyTheme());
applyTheme();
$("#theme-btn").addEventListener("click", (ev) => showMenu(ev.currentTarget, [
  { heading: "Interface" },
  ...[["system", "Match the system"], ["light", "Light"], ["dark", "Dark"]].map(([v, label]) =>
    ({ label, checked: adhoc.theme.ui === v, onclick: () => setTheme("ui", v) })),
  "-", { heading: "Documents" },
  ...[["ui", "Match the interface"], ["light", "Light"], ["dark", "Dark"]].map(([v, label]) =>
    ({ label, checked: adhoc.theme.doc === v, onclick: () => setTheme("doc", v),
       hint: v === "dark" ? "pages inverted" : "" })),
]));

function addHistory(loc, source) {
  const entry = { at: new Date().toISOString(), loc: typeof loc === "string" ? loc : locString(loc), source };
  adhoc.history.push(entry);                 // this window's, for marking visited items
  if (adhoc.history.length > 1000) adhoc.history.shift();
  post({ type: "history", entry });         // the host keeps every window's, for `view history`
}

function snapshot() {
  return {
    running: true, focused: adhoc.focused, facet: adhoc.facet,
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
  dockLayout();
  for (const p of adhoc.panes) p.root.classList.toggle("focused", p.id === adhoc.focused);
  positionPanes();
}
// A window holding only the terminal shows only the terminal.
function dockLayout() {
  document.body.classList.toggle("dock-only", !adhoc.panes.length && document.body.classList.contains("has-dock"));
}
// Each pane over its slot; panes in tabs not showing are hidden.
function positionPanes() {
  const origin = $("#panes").getBoundingClientRect();
  for (const p of adhoc.panes) {
    const s = adhoc.slots?.get(p.id);
    if (!s) { p.root.style.display = "none"; continue; }
    const r = s.getBoundingClientRect();
    // whole pixels, so page images inside land on the screen's pixels and stay sharp
    const L = Math.round(r.left - origin.left), T = Math.round(r.top - origin.top);
    Object.assign(p.root.style, { display: "", left: `${L}px`, top: `${T}px`,
                                  width: `${Math.round(r.right - origin.left) - L}px`, height: `${Math.round(r.bottom - origin.top) - T}px` });
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

// -- downloads ---------------------------------------------------------------------------------

// The modes' names in the header, and what each shows.
const MODES = {
  page: { label: "Pages", title: "The pages as they print" },
  flow: { label: "Formatted", title: "The document as it reads, with its formatting" },
  text: { label: "Extracted text", title: "The extracted text, line by line: exactly what search, quotes and Claude read" },
  grid: { label: "Cells", title: "The sheet's cells, one sheet at a time" },
  hex: { label: "Bytes", title: "The original's bytes, exactly as collected" },
};
// a tag's color, made light enough to highlight with: text under it stays readable
function lighten(hex, k = 0.5) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(hex || "");
  if (!m) return "#ffe066";
  return "#" + [m[1], m[2], m[3]].map((x) => Math.round(parseInt(x, 16) + (255 - parseInt(x, 16)) * k).toString(16).padStart(2, "0")).join("");
}
function fmtSize(n) {
  if (n == null) return "";
  return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB" : (n / 1048576).toFixed(1) + " MB";
}
// Wrap the text between two layer offsets in elements from `make`, one per
// text node it crosses; the text itself is unchanged, so later offsets still
// hold. Returns the last wrapper, or null if the range isn't there.
function wrapRange(root, s, e, make) {
  const a = pointAt(root, s), b = pointAt(root, e);
  if (!a || !b) return null;
  const d = root.ownerDocument, r = d.createRange();
  try { r.setStart(...a); r.setEnd(...b); } catch { return null; }
  const top = r.commonAncestorContainer.nodeType === 3 ? r.commonAncestorContainer.parentNode : r.commonAncestorContainer;
  const texts = [], w = d.createTreeWalker(top, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) if (r.intersectsNode(n)) texts.push(n);
  let last = null;
  for (const t of texts) {
    const from = t === r.startContainer ? r.startOffset : 0, to = t === r.endContainer ? r.endOffset : t.data.length;
    if (to <= from) continue;
    const mid = from > 0 ? t.splitText(from) : t;
    if (to - from < mid.data.length) mid.splitText(to - from);
    const m = make();
    mid.replaceWith(m); m.append(mid);
    last = m;
  }
  return last;
}
// How MuPDF lays out a document for a PDF: print styles, not the screen's.
// Helvetica and Courier are MuPDF's own, so the PDF carries a small font, not a system one.
const PRINT_CSS = `
@page { margin: 54pt 60pt; }
body { font-family: Helvetica, sans-serif; font-size: 10.5pt; line-height: 1.4; color: #111; margin: 0; }
header.doc-head { border-bottom: 1pt solid #999; padding-bottom: 4pt; margin-bottom: 10pt; }
header.doc-head .t { font-weight: bold; font-size: 13pt; }
header.doc-head .m { color: #666; font-size: 9pt; }
.adhoc-flow p { margin: 0 0 2pt; }
.adhoc-flow p.header { font-weight: bold; }
.adhoc-flow p.quoted { color: #555; border-left: 2pt solid #ccc; padding-left: 6pt; }
.adhoc-flow p.footnote, .adhoc-flow p.endnote, .adhoc-flow p.comment, .adhoc-flow p.header-footer { font-size: 9pt; color: #444; }
.adhoc-flow.plain p { font-family: Courier, monospace; font-size: 9pt; white-space: pre-wrap; margin: 0; }
.adhoc-flow .pagebreak { border-top: 1pt dashed #bbb; margin: 6pt 0; }
.adhoc-flow img { max-width: 100%; }
.adhoc-inline-image.missing, .adhoc-remote { color: #8a5a00; font-size: 9pt; }
sup.nref { color: #1a4fb0; font-size: 7.5pt; font-weight: bold; }
section.notes { margin-top: 18pt; border-top: 1pt solid #999; padding-top: 6pt; font-size: 9.5pt; }
section.notes h2 { font-size: 11pt; margin: 0 0 6pt; }
section.notes .by { color: #666; }
`;
const exportWaiting = new Map();
let exportSeq = 0;
function exportRequest(request) {
  const id = ++exportSeq;
  return new Promise((res) => { exportWaiting.set(id, res); post({ type: "export", id, request }); });
}
adhoc.exported = (id, result) => { const res = exportWaiting.get(id); exportWaiting.delete(id); res?.(result); };

// -- document panes ---------------------------------------------------------------------------

class DocumentPane {
  constructor(id) {
    this.id = id; this.kind = "document"; this.info = null; this.layer = null; this.mode = null;
    this.selection = null; this.marks = []; this.highlights = []; this.sequence = null; this.visible = null; this.region = null;
    this.hlCur = null;                        // the highlight the user stepped to, if any
    this.root = el("section", { class: "pane", "data-pane": id });
    this.head = el("div", { class: "pane-head" });
    this.links = el("div", { class: "pane-links" });
    this.strip = el("div", { class: "pane-dates" });
    this.nav = el("div", { class: "pane-nav", hidden: "" });
    this.body = el("div", { class: "pane-body", tabindex: "0" });
    this.side = el("div", { class: "pane-side" });
    this.root.append(this.head, this.strip, this.links, this.nav, el("div", { class: "pane-split" }, this.body, this.side));
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
    if (top && this.pageEls?.[top - 1]) this.body.scrollTop = this.pageTop(this.pageEls[top - 1]) - 8;
  }
  title() { return this.info ? (this.info.meta.name || short(this.info.doc)) : "(empty)"; }
  // the modes this document and layer can be shown in, the default first
  modes() {
    const info = this.info, layer = this.currentLayer();
    if (!info) return [];
    // hex shows the original's bytes: for any document, and the only mode for one nothing reads
    return [info.meta.kind === "spreadsheet" && layer && "grid", info.pages && "page", layer?.presentation && "flow",
            (layer || info.size == null) && "text", info.size != null && "hex"].filter(Boolean);
  }
  // the modes offered in the header: hex (the original's bytes) is in the ⋯ menu,
  // unless it's the only one or showing now
  shownModes() { return this.modes().filter((m) => m !== "hex" || this.mode === "hex" || this.modes().length === 1); }
  currentLayer() { return this.info?.layers.find((l) => l.id === this.layer); }
  state() {
    return {
      id: this.id, kind: "document", mode: this.mode, doc: this.info?.doc, layer: this.layer, view: this.currentLayer()?.view,
      visible: this.visible, sequence: this.sequence && { position: this.sequence.position + 1, length: this.sequence.items.length, from: this.sequence.from },
      selection: this.selection, marks: this.marks,
      highlights: this.highlights.map((h) => ({ loc: h.loc, label: h.label, ...(h === this.hlCur ? { current: true } : {}) })),
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
    if (fresh) { this.fetchToken = null; this.fetchNote = null; }
    this.info = info;
    let layer = loc.layer && info.layers.find((l) => l.id === loc.layer);
    if (opts.view) layer = info.layers.find((l) => l.view === opts.view) || layer;
    if (!layer && !fresh) layer = this.currentLayer();
    this.layer = (layer || info.layers.find((l) => l.primary) || info.layers[0] || {}).id || null;
    const paged = !!info.pages && this.currentLayer()?.geometry !== false;
    const sel = loc.select || {};
    const mode = opts.mode || (sel.bytes && "hex") || (fresh ? null : this.mode) || this.modes()[0];
    this.mode = this.modes().includes(mode) ? mode : (this.modes()[0] || "text");
    if (fresh) { this.grid = null; this.hex = null; }
    if (fresh) { this.selection = null; this.highlights = []; this.hlCur = null; }
    await this.render();
    if (sel.char || sel.cells || sel.bytes || sel.page !== undefined) {
      this.flash(loc);
      await this.scrollTo(loc);
    }
    if (!opts.quiet) addHistory(loc, opts.source || "user");
    renderTabs();
    // a limited copy without this original: the text shows now, and the original once it's fetched
    if (info.fetchable && !this.fetchToken) this.awaitOriginal(info.doc, locIn);
  }

  // Ask the viewer to fetch this document's original (it answers at once), then wait for it to say
  // the fetch ended (adhoc.fetched): then show it, unless the user has started working in the text
  // meanwhile, when a link offers it. Opening another document ends the wait.
  async awaitOriginal(doc, loc, retry = false) {
    const token = (this.fetchToken = {});
    const mine = () => this.fetchToken === token && this.info?.doc === doc;
    const content = this.info.content;
    this.setFetchNote({ text: "Fetching the original… its text is here meanwhile" });
    for (;;) {
      const ended = whenFetched(content, FETCH_CHECK_MS);       // listening before asking: it can't be missed
      const r = await getJSON("/api/fetch/" + encodeURIComponent(doc) + (retry ? "?retry=1" : "")).catch(() => ({ state: "failed" }));
      retry = false;
      if (!mine()) return ended.cancel();
      if (r.state !== "fetching") ended.cancel();
      if (r.state === "here") {
        delete cache.docs[doc];
        const show = async () => {
          if (this.info?.doc !== doc) return;
          this.fetchToken = null; this.fetchNote = null; this.info = null;     // as if opened afresh: pages, hex
          await this.open(loc, { quiet: true, source: "fetch" });
        };
        if (this.selection || (this.highlights || []).length) {
          this.fetchToken = null;
          return this.setFetchNote({ text: "The original is here:", action: ["show it", show] });
        }
        return show();
      }
      if (r.state !== "fetching") {
        this.fetchToken = null;
        return this.setFetchNote(r.state === "failed"
          ? { text: "The original couldn't be fetched (is the hub reachable?). Its text is here.", failed: true,
              action: ["try again", () => { this.fetchToken = null; this.awaitOriginal(doc, loc, true); }] }
          : null);
      }
      await ended;              // told it ended (or, if that was somehow missed, a while passed): ask again
      if (!mine()) return;
    }
  }
  setFetchNote(note) {
    this.fetchNote = note;
    const span = $(".fetchnote", this.head);
    if (!span) return;
    span.hidden = !note;
    span.classList.toggle("failed", !!note?.failed);
    span.replaceChildren(...(note ? [note.text, ...(note.action ? [" ", el("a", { onclick: note.action[1] }, note.action[0])] : [])] : []));
  }

  async render() {
    const info = this.info, layer = this.currentLayer();
    const modes = this.shownModes();
    this.head.replaceChildren(
      info.meta.kind ? el("span", { class: "pane-kind", title: info.meta.content_type || info.meta.kind }, info.meta.kind) : "",
      el("span", { class: "pane-title", title: info.doc }, this.title()),
      // a received document: its Bates range and who produced it
      info.meta.produced ? el("span", { class: "produced", title: `Produced to us by ${info.meta.produced.party}, volume ${info.meta.produced.volume}` +
          (info.meta.produced.control !== info.meta.produced.begin ? `; control number ${info.meta.produced.control}` : "") +
          (info.meta.produced.as === "native" ? "; produced natively" : "") },
        (info.meta.produced.begin === info.meta.produced.end ? info.meta.produced.begin : `${info.meta.produced.begin}–${info.meta.produced.end}`) +
        " · " + info.meta.produced.party) : "",
      // layers: only when there's a choice (the primary one, the one search reads first, is starred)
      info.layers.length > 1 ? el("span", { class: "seg layers", title: "Layers: readings of the document (v)" }, ...info.layers.map((l) => el("button", {
        class: l.id === this.layer ? "on" : "", title: `layer ${short(l.id)}${l.primary ? " (primary: what search reads first)" : ""}`,
        onclick: () => this.open({ doc: info.doc, layer: l.id }, { quiet: true }) }, l.view + (l.primary ? "*" : "")))) : "",
      modes.length > 1 || this.mode === "hex" ? el("span", { class: "seg modes" }, ...modes.map((m) => el("button", { class: m === this.mode ? "on" : "",
        title: (MODES[m]?.title || m) + " (f)", onclick: () => this.setMode(m) }, MODES[m]?.label || m))) : "",
      el("span", { class: "chips" }),
      el("span", { class: "fetchnote", hidden: true }),
      el("button", { class: "entstat", title: "The entity layer: hover a name to see what it is, or a date to see what came before and after; click to select it and ask Claude. E shows all, known only, or none.",
        onclick: () => cycleEntityMode() }), el("span", { class: "status" }),
      el("button", { class: "icon-btn", "aria-label": "More: download, the original's bytes", "aria-haspopup": "menu",
        onclick: (ev) => showMenu(ev.currentTarget, this.menuItems()) }, "⋯"));
    const open = (d) => this.open("doc:" + d, { source: "user" });
    const groups = [];
    if (info.parent) groups.push(["In", [[info.parent.name || short(info.parent.doc), info.parent.doc]]]);
    if (info.children.length) groups.push([info.children.length > 1 ? "Attachments" : "Attachment", info.children.map((c) => [c.name || short(c.doc), c.doc])]);
    if (info.duplicates.length) groups.push([info.duplicates.length > 1 ? "Duplicates" : "Duplicate", info.duplicates.map((d) => [short(d), d])]);
    this.links.replaceChildren(...groups.map(([label, ds]) => el("span", { class: "grp" }, label + ":",
      ...ds.map(([t, d]) => el("a", { title: d, onclick: () => open(d) }, t)))));
    this.links.hidden = !groups.length;
    this.setFetchNote(this.fetchNote);          // the head was just rebuilt
    this.renderStrip();
    this.body.replaceChildren();
    this.body.className = "pane-body docview" + (this.mode === "page" ? " paged" : "");
    this.words = null;
    if (!layer) { this.body.append(el("p", { class: "hint", style: "padding:1em" }, "This document has no text layer: nothing here reads its format. " + (this.mode === "hex" ? "These are its bytes, exactly as given. " : "") + "Ask Claude what it is; it can look at the bytes, and show you its own reading of them, marked as such.")); }
    if (this.mode === "page") await this.renderPages();
    else if (this.mode === "flow") await this.renderFlow();
    else if (this.mode === "grid") await this.renderGrid();
    else if (this.mode === "hex") await this.renderHex();
    else if (layer) await this.renderText();
    this.renderAnnotations();
    this.renderNav();
    this.updateStatus();
    if (this.mode !== "page") this.shown();
    this.loadEntities();
  }
  async setMode(m) { this.mode = m; await this.render(); }
  // the document theme changed: pages are drawn again (MuPDF inverts them),
  // only those in view at once; a presentation is restyled where it is
  docThemeChanged() {
    if (this.mode === "page" && this.pageEls) {
      this.observer?.disconnect();
      for (const p of this.pageEls) { p.querySelector("img")?.remove(); delete p.dataset.filled; }
      for (const p of this.pageEls) this.observer.observe(p);
    } else if (this.mode === "flow" && this.frame) this.frame.classList.toggle("own-colors", this.ownColors && docDark());
    this.drawBoxes();
  }
  pageURL(doc, n, px = this.pagePx) { return `/page/${doc}/${n}?w=${px}${docDark() ? "&dark=1" : ""}`; }

  // -- downloads, and the rest of the ⋯ menu ------------------------------------------------
  menuItems() {
    const info = this.info, layer = this.currentLayer(), modes = this.modes();
    const ext = (info.meta.name || "").match(/\.([A-Za-z0-9]{1,6})$/)?.[1]?.toLowerCase();
    const n = this.exportAnnotations().count;
    const items = [{ heading: "Download" },
      { label: "Original", hint: (ext ? "." + ext + " · " : "") + fmtSize(info.size), disabled: info.size == null,
        title: "The file exactly as it was collected", onclick: () => this.exportAs("original") },
      { label: "PDF", hint: info.meta.kind === "pdf" ? "the original" : info.pages ? "the pages" : "as formatted",
        disabled: !info.pages && !layer, title: info.pages ? "The pages as a PDF" : "The formatted view, laid out as pages", onclick: () => this.exportAs("pdf") },
      { label: "PDF with annotations", hint: n ? `${n} marked` : "none yet", disabled: !n || (!info.pages && !layer),
        title: "Tags, notes and highlights marked on the pages, notes as PDF comments (or numbered, at the end)", onclick: () => this.exportAs("annotated") },
      { label: "Text", hint: ".txt", disabled: !layer, title: "The extracted text of this layer: what search and Claude read", onclick: () => this.exportAs("text") }];
    if (info.pages) items.push("-", { label: "Crop blank margins", checked: cropMargins(), title: "Show only the part of each page with something on it, larger (for every paginated document)",
      onclick: () => setCropMargins(!cropMargins()) });
    if (modes.includes("hex") && modes.length > 1) items.push("-", this.mode === "hex"
      ? { label: "Back from the bytes", onclick: () => this.setMode(modes[0]) }
      : { label: "Show the original's bytes", hint: "hex", title: "The file's bytes, exactly as collected, sixteen to a row", onclick: () => this.setMode("hex") });
    return items;
  }
  // What a PDF with annotations shows: tags and notes on this layer's text
  // (or on regions of pages), and Claude's highlights in this pane.
  // Annotations on another layer can't be placed on these pages, so they're counted apart.
  exportAnnotations() {
    const doc = this.info.doc, A = adhoc.annotations, out = [], docLevel = [];
    let elsewhere = 0;
    const color = (t) => A.definitions.find((d) => d.tag === t)?.color;
    const place = (target, x) => {
      const sel = target?.select;
      if (!sel) return docLevel.push(x.tag ? "Tag on the document: " + x.text : "Note on the document, " + x.text);
      if (target.layer && target.layer !== this.layer && sel.char) return elsewhere++;
      if (sel.char) out.push({ ...x, range: sel.char });
      else if (sel.xywh) out.push({ ...x, region: { page: sel.page, xywh: sel.xywh } });
      else if (sel.page !== undefined) docLevel.push(`p. ${sel.page}: ${x.text}`);
      else elsewhere++;
    };
    for (const t of A.tags.filter((t) => t.target.doc === doc)) place(t.target, { text: t.tag, author: t.by, color: lighten(color(t.tag) || "#ffcc00"), tag: true });
    const notes = A.notes.filter((n) => n.target?.doc === doc);
    for (const n of notes.filter((n) => !n.parent || !notes.some((p) => p.note === n.parent))) {
      const replies = [], walk = (id) => notes.filter((r) => r.parent === id).forEach((r) => { replies.push(`${r.by}: ${r.body}`); walk(r.note); });
      walk(n.note);
      place(n.target, { text: [`${n.by}: ${n.body}`, ...replies].join("\n\n"), author: n.by, color: "#9cc2ff", note: true });
    }
    for (const h of this.highlights.filter((h) => !h.temp && h.label !== "annotation")) {
      if (h.range) out.push({ range: h.range, text: h.label || "highlight", author: "claude", color: "#ffc34d" });
      else if (h.region) out.push({ region: h.region, text: h.label || "highlight", author: "claude", color: "#ffc34d" });
    }
    // in document order, so notes are numbered as they're read
    const at = (a) => a.range ? a.range[0] : a.region.page * 1e9 + a.region.xywh[1] * 1e8;
    out.sort((a, b) => at(a) - at(b));
    return { list: out, docLevel, elsewhere, count: out.length + docLevel.length };
  }
  async exportAs(format) {
    const info = this.info, req = { doc: info.doc, format, layer: this.layer };
    const ann = format === "annotated" ? this.exportAnnotations() : null;
    toast(format === "original" ? "Saving the original…" : "Making the " + (format === "text" ? "text" : "PDF") + "…");
    try {
      if ((format === "pdf" || format === "annotated") && !info.pages) {
        req.html = await this.printHTML(ann);
        if (ann) {
          req.annots = ann.list.filter((a) => a.mark).map((a) => ({ mark: a.mark, color: a.color, text: a.text, author: a.author }));
          req.doc_note = ann.docLevel.join("\n\n");
        }
      } else if (ann) {
        // boxes on the pages, from the layer's geometry, as fractions of each page
        const geo = this.words?.geo || (this.currentLayer()?.geometry ? await geometry(this.layer) : []);
        req.annots = [];
        for (const a of ann.list) {
          if (a.region) {
            const [x, y, w, h] = a.region.xywh;
            req.annots.push({ page: a.region.page, kind: "region", boxes: [[x, y, x + w, y + h]], color: a.color, text: a.text, author: a.author });
            continue;
          }
          const byPage = new Map();
          for (const g of geo) if (g.start < a.range[1] && a.range[0] < g.end) (byPage.get(g.page) || byPage.set(g.page, []).get(g.page)).push(g.box);
          for (const [page, boxes] of byPage) req.annots.push({ page, boxes, color: a.color, text: a.text, author: a.author });
        }
        req.doc_note = ann.docLevel.join("\n\n");
      }
    } catch (e) { return toast("Couldn't make it: " + e.message); }
    const r = await exportRequest(req);
    if (r.error) return toast("Couldn't save: " + r.error);
    toast("Saved to " + r.saved + (ann?.elsewhere ? ` (${ann.elsewhere} on another layer left out)` : ""));
  }
  // The document as HTML for MuPDF to lay out as pages: its presentation (or
  // its text, line by line), with annotations marked and notes numbered at the end.
  async printHTML(ann) {
    const info = this.info, layer = this.currentLayer();
    let d;
    if (layer?.presentation) {
      d = new DOMParser().parseFromString(await (await fetch("/presentation/" + layer.id)).text(), "text/html");
      d.querySelectorAll("link, script").forEach((x) => x.remove());
      // inline images are fetched here: MuPDF reaches nothing
      for (const img of d.querySelectorAll("img[src^='/']")) {
        try {
          const b = await (await fetch(img.getAttribute("src"))).blob();
          img.src = await new Promise((res) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(b); });
        } catch { img.remove(); }
      }
    } else {
      const t = await layerText(layer.id);
      d = document.implementation.createHTMLDocument("");
      const art = d.createElement("article"); art.className = "adhoc-flow plain";
      let at = 0;
      for (const line of t.cps.join("").split("\n")) {
        const parts = line.split("\f");
        parts.forEach((part, i) => {
          if (i) { art.append(Object.assign(d.createElement("div"), { className: "pagebreak" })); at++; }
          const pEl = d.createElement("p"); pEl.dataset.o = at; pEl.textContent = part || "\u00a0";
          if (!part) pEl.dataset.empty = "";
          art.append(pEl); at += cpLen(part);
        });
        at++;
      }
      d.body.append(art);
    }
    const root = d.body, notes = [];
    if (ann) {
      const marked = [];
      // MuPDF's layout paints no inline backgrounds: each range's text gets a
      // color that stands for it (#01xxyy, all but black), and the host puts a
      // PDF highlight wherever that color landed (export.rs)
      ann.list.filter((a) => a.range).forEach((a, i) => {
        a.mark = i + 1;
        const last = wrapRange(root, ...a.range, () => {
          const m = d.createElement("span");
          m.style.color = "#01" + a.mark.toString(16).padStart(4, "0");
          return m;
        });
        if (last) marked.push([last, a]);
      });
      // numbers go in after every range is marked, so the offsets hold while marking
      for (const [last, a] of marked) {
        notes.push(a);
        const sup = d.createElement("sup"); sup.className = "nref"; sup.textContent = `[${notes.length}]`;
        last.after(sup);
      }
      for (const a of ann.list.filter((a) => a.region)) notes.push({ ...a, text: `p. ${a.region.page}, a region: ${a.text}` });
    }
    const esc = (t) => t.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    const meta = [info.meta.kind, info.dated?.date && fmtDate(info.dated.date)].filter(Boolean).join(" · ");
    const end = ann && (notes.length || ann.docLevel.length) ? `<section class="notes"><h2>Annotations</h2>` +
      (ann.docLevel.length ? `<p class="doclevel">${ann.docLevel.map(esc).join("<br>")}</p>` : "") +
      `<ol>${notes.map((a) => `<li><b>${esc(a.tag ? "Tag: " + a.text : a.note ? "Note" : "Highlight")}</b>${a.tag ? "" : " " + esc(a.text).replace(/\n/g, "<br>")}` +
        `<span class="by"> (${esc(a.author || "")})</span></li>`).join("")}</ol></section>` : "";
    return `<!doctype html><html><head><meta charset="utf-8"><style>${PRINT_CSS}</style></head><body>` +
      `<header class="doc-head"><div class="t">${esc(this.title())}</div><div class="m">${esc(meta)}</div></header>` +
      root.innerHTML + end + `</body></html>`;
  }

  // page mode: MuPDF images plus an invisible text layer from geometry
  async renderPages() {
    const info = this.info;
    const wrap = el("div", { class: "pages" });
    this.body.append(wrap);
    // the image is drawn at its own size, pixel for pixel: a page image the
    // web view has to scale (up or down) loses its antialiasing
    const dpr = devicePixelRatio || 1, avail = Math.max(300, Math.min(this.body.clientWidth - 30, 1100));
    const gen = this.pagesGen = (this.pagesGen || 0) + 1;
    // Cropping blank margins: each page is shown through a window onto its
    // ink (the host finds it, api/margins). Every page keeps one scale, so
    // type is the same size throughout: the widest ink known fits the pane.
    // The first pages' margins come before layout; the rest are guessed
    // from them and set as they arrive (fillMargins).
    this.crops = null;
    if (cropMargins()) {
      this.crops = new Array(info.pages.length).fill(null);
      await this.fetchMargins(1, Math.min(info.pages.length, 4));
    }
    const known = (this.crops || []).filter(Boolean);
    this.cropGuess = known.length ? [0, 1, 2, 3].map((k) => (k < 2 ? Math.min : Math.max)(...known.map((c) => c[k]))) : [0, 0, 1, 1];
    if (this.crops) {
      const inkW = Math.max(...info.pages.map(([w], i) => { const c = this.crops[i] || this.cropGuess; return w * (c[2] - c[0]); }));
      this.scale = avail / inkW;              // CSS pixels per point
    }
    this.pagePx = Math.round(avail * dpr);
    this.bodyWidth = this.body.clientWidth;
    this.pageEls = info.pages.map(([w, h], i) => {
      // uncropped, every page is the pane's width, as before
      const W = this.crops ? Math.round(this.scale * w * dpr) / dpr : this.pagePx / dpr;
      const p = el("div", { class: "page", "data-page": i + 1, "data-px": Math.round(W * dpr), style: `width:${W}px;height:${(W * h) / w}px` });
      const slot = el("div", { class: "pageslot" }, el("div", { class: "pageclip" }, p), el("span", { class: "pageno" }, String(i + 1)));
      wrap.append(slot);
      this.placePage(p);
      return p;
    });
    this.pageWidth = this.pagePx / dpr;
    if (this.crops) this.fillMargins(gen);
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

  // the window onto a page: its crop (or all of it), on whole device pixels, centered
  placePage(p) {
    const dpr = devicePixelRatio || 1, n = Number(p.dataset.page);
    const c = this.crops ? this.crops[n - 1] || this.cropGuess : [0, 0, 1, 1];
    const W = parseFloat(p.style.width), H = parseFloat(p.style.height);     // no layout read: this runs per page
    const snap = (v) => Math.round(v * dpr) / dpr;
    const x0 = snap(c[0] * W), y0 = snap(c[1] * H), cw = snap(c[2] * W) - x0, ch = snap(c[3] * H) - y0;
    const clip = p.parentElement;
    Object.assign(clip.style, { width: cw + "px", height: ch + "px" });
    Object.assign(p.style, { marginLeft: -x0 + "px", marginTop: -y0 + "px" });
    clip.parentElement.style.marginLeft = Math.max(0, snap((this.bodyWidth - cw) / 2)) + "px";
  }
  async fetchMargins(from, to) {
    const r = await getJSON(`/api/margins/${this.info.doc}?from=${from}&to=${to}`).catch(() => null);
    r?.boxes.forEach((b, i) => { if (this.crops) this.crops[from - 1 + i] = b; });
  }
  // The rest of the pages' margins, a batch at a time; a page above the view
  // that changes height moves the scroll with it, so what's shown stays put.
  async fillMargins(gen) {
    const n = this.info.pages.length;
    for (let from = 5; from <= n; from += 24) {
      const to = Math.min(n, from + 23);
      await this.fetchMargins(from, to);
      if (gen !== this.pagesGen || !this.crops) return;
      // all reads, then all writes, then one read: a layout per batch, not per page
      const ps = this.pageEls.slice(from - 1, to), st = this.body.scrollTop;
      const before = ps.map((p) => [p.parentElement.offsetTop, p.parentElement.offsetHeight]);
      ps.forEach((p) => this.placePage(p));
      const shift = ps.reduce((d, p, i) => d + (before[i][0] + before[i][1] <= st ? p.parentElement.offsetHeight - before[i][1] : 0), 0);
      if (shift) this.body.scrollTop = st + shift;
    }
  }
  // where a page's window is, in the pane's scrolled content
  pageTop(p) { return p.parentElement.offsetTop; }

  fillPage(p) {
    if (p.dataset.filled) return;
    p.dataset.filled = "1";
    const n = Number(p.dataset.page);
    const img = el("img", { src: this.pageURL(this.info.doc, n, p.dataset.px), draggable: "false", alt: "" });
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
      if (info.pages) fetch(this.pageURL(info.doc, 1));
    } catch {}
  }
  boxesFor(s, e) {
    if (!this.words) return [];
    return this.words.geo.filter((g) => g.start < e && s < g.end);
  }
  drawBoxes() {
    if (this.mode === "grid") return this.paintCells();
    if (this.mode === "hex") return this.paintHex();
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
      for (const h of this.highlights) if (h.range) draw(...h.range, this.hlClass(h));
      for (const mk of this.marks) if (mk.select?.char && mk.layer === this.layer) draw(...mk.select.char, "mark");
      for (const h of this.highlights) if (h.region) this.drawRegion(h.region, this.hlClass(h));
      for (const x of this.shownEntities()) draw(x.start, x.end, markClass(x));
    } else {
      const root = this.textRoot();
      if (!root) return;
      const container = root.ownerDocument === document ? this.body : root.ownerDocument.body;
      container.querySelectorAll(".hlbox").forEach((b) => b.remove());
      if (getComputedStyle(container).position === "static") container.style.position = "relative";
      for (const h of this.highlights) if (h.range) drawRangeBoxes(container, null, root, ...h.range, this.hlClass(h));
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
    const frame = el("iframe", { class: "web", src: "/presentation/" + this.layer + (docDark() ? "?doc=dark" : "") });
    this.body.append(frame);
    this.frame = frame;
    await new Promise((res) => frame.addEventListener("load", res, { once: true }));
    const d = frame.contentDocument;
    themeFrame(frame);
    // colors of its own (an HTML body) can't be given dark ones: then the frame is inverted
    this.ownColors = !!d.querySelector("[style*='color'], [bgcolor], [color], [style*='background']");
    frame.classList.toggle("own-colors", this.ownColors && docDark());
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
    for (const row of g.cells) for (const td of row) td.classList.remove("sel", "hl", "hlcur", "mark", "cur");
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
    for (const h of this.highlights) { paint(h, "hl"); if (h === this.hlCur) paint(h, "hlcur"); }
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

  // hex mode: the original's bytes, sixteen to a row: offset, hex, and the
  // printable ASCII among them (the display rule for bytes, DATA.md). Rows
  // are drawn only around what's in view, from chunks fetched as needed, so
  // a large file is never read whole. A drag selects bytes: a bytes=a,b
  // locator, fingerprinted by whoever resolves it.
  async renderHex() {
    const size = this.info.size || 0, rows = Math.ceil(size / 16);
    const ROW = 18, MAXH = 8e6;              // past MAXH px, scroll position maps to rows proportionally
    const spacer = el("div", { class: "hex-spacer", style: `height:${Math.min(rows * ROW, MAXH) + 8}px` });
    const win = el("div", { class: "hex-rows" });
    spacer.append(win);
    const box = el("div", { class: "hexmode" }, spacer);
    const keep = this.hex?.doc === this.info.doc ? this.hex : null;
    this.hex = { doc: this.info.doc, size, rows, ROW, H: Math.min(rows * ROW, MAXH), scaled: rows * ROW > MAXH, win, spacer, first: -1, count: 0,
                 anchor: keep?.anchor ?? null, focus: keep?.focus ?? null, chunks: new Map() };
    this.body.append(box);
    if (!size) { win.append(el("p", { class: "hint" }, "The original is empty.")); return; }
    this.setupByteSelect(win);
    await this.drawHex();
  }
  // where the rows start in the pane's scrolling (below the hint, if any)
  hexOrigin() { return this.hex.spacer.getBoundingClientRect().top - this.body.getBoundingClientRect().top + this.body.scrollTop; }
  hexView() {
    const x = this.hex, viewRows = Math.ceil(this.body.clientHeight / x.ROW) + 1;
    const st = Math.max(0, this.body.scrollTop - this.hexOrigin());
    let top;
    if (!x.scaled) top = Math.floor(st / x.ROW);
    else top = Math.round(st / Math.max(1, x.H - this.body.clientHeight) * Math.max(0, x.rows - viewRows + 1));
    return { top: Math.max(0, Math.min(top, x.rows - 1)), viewRows, st };
  }
  async drawHex() {
    const x = this.hex;
    if (!x || !x.size) return;
    const { top, viewRows, st } = this.hexView();
    const pad = x.scaled ? 0 : 32;
    const first = Math.max(0, top - pad), count = Math.min(x.rows - first, viewRows + 2 * pad);
    // in a scaled view the rows ride along with the scroll position; otherwise each row has its place
    x.win.style.top = (x.scaled ? st - (top - first) * x.ROW : first * x.ROW) + "px";
    if (!x.scaled && first === x.first && count === x.count) return;
    if (x.scaled && first === x.first && count === x.count && x.win.dataset.st === String(st)) return;
    x.first = first; x.count = count; x.win.dataset.st = String(st);
    const a = first * 16, b = Math.min(x.size, (first + count) * 16);
    const need = [];
    for (let c = Math.floor(a / HEX_CHUNK); c * HEX_CHUNK < b; c++) need.push(c);
    let got;
    try { got = await Promise.all(need.map((c) => byteChunk(x.doc, c))); }
    catch (e) { x.win.replaceChildren(el("p", { class: "hint" }, "Can't read the bytes: " + e.message)); return; }
    if (this.hex !== x || x.first !== first || x.count !== count) return;      // scrolled on meanwhile
    const byteAt = (o) => got[Math.floor(o / HEX_CHUNK) - need[0]]?.[o % HEX_CHUNK];
    const frag = document.createDocumentFragment();
    const width = Math.max(8, (x.size - 1).toString(16).length);
    for (let r = first; r < first + count; r++) {
      const row = el("div", { class: "hx-row" }, el("span", { class: "hx-off" }, (r * 16).toString(16).padStart(width, "0")));
      const hex = el("span", { class: "hx-hex" }), asc = el("span", { class: "hx-asc" });
      for (let i = 0; i < 16; i++) {
        const o = r * 16 + i;
        if (o >= x.size) break;
        const v = byteAt(o);
        hex.append(el("span", { "data-b": o, class: i === 8 ? "gap" : "" }, v.toString(16).padStart(2, "0")));
        asc.append(el("span", { "data-b": o }, v >= 0x20 && v < 0x7f ? String.fromCharCode(v) : "."));
      }
      row.append(hex, asc);
      frag.append(row);
    }
    x.win.replaceChildren(frag);
    this.paintHex();
    this.updateStatus();
  }
  // the selected range, [a, b), or null
  byteSel() {
    const x = this.hex;
    if (!x || x.anchor === null || x.focus === null) return null;
    return [Math.min(x.anchor, x.focus), Math.max(x.anchor, x.focus) + 1];
  }
  paintHex() {
    const x = this.hex;
    if (!x) return;
    const ranges = [...this.highlights.filter((h) => h.bytes).flatMap((h) => h === this.hlCur ? [[h.bytes, "hl"], [h.bytes, "hlcur"]] : [[h.bytes, "hl"]]),
                    ...this.marks.filter((m) => m.doc === x.doc && m.select?.bytes).map((m) => [m.select.bytes, "mark"])];
    const sel = this.byteSel();
    if (sel) ranges.push([sel, "sel"]);
    for (const c of x.win.querySelectorAll("[data-b]")) {
      const o = Number(c.dataset.b);
      c.classList.remove("hl", "hlcur", "mark", "sel");
      for (const [[a, b], cls] of ranges) if (a <= o && o < b) c.classList.add(cls);
    }
  }
  setupByteSelect(win) {
    const x = this.hex;
    const at = (ev) => { const t = ev.target.closest("[data-b]"); return t ? Number(t.dataset.b) : null; };
    let dragging = false;
    win.addEventListener("mousedown", (ev) => {
      const o = at(ev);
      if (o === null || ev.button !== 0) return;
      ev.preventDefault();
      focus(this.id);
      if (ev.shiftKey && x.anchor !== null) x.focus = o;
      else x.anchor = x.focus = o;
      dragging = true;
      this.paintHex();
    });
    win.addEventListener("mouseover", (ev) => {
      if (!dragging) return;
      const o = at(ev);
      if (o !== null && o !== x.focus) { x.focus = o; this.paintHex(); }
    });
    const up = () => {
      if (!dragging) return;
      dragging = false;
      document.removeEventListener("mouseup", up);
      if (this.hex !== x) return;
      const r = this.byteSel();
      this.selection = r && { doc: this.info.doc, select: { bytes: r } };
      this.updateStatus();
      changed();
    };
    win.addEventListener("mousedown", (ev) => { if (at(ev) !== null && ev.button === 0) document.addEventListener("mouseup", up); });
  }
  scrollToByte(o) {
    const x = this.hex;
    if (!x) return;
    const r = Math.floor(o / 16), viewH = this.body.clientHeight, origin = this.hexOrigin();
    if (!x.scaled) this.body.scrollTop = Math.max(0, origin + r * x.ROW - viewH / 3);
    else {
      const viewRows = Math.ceil(viewH / x.ROW) + 1;
      this.body.scrollTop = origin + Math.max(0, r - 2) / Math.max(1, x.rows - viewRows + 1) * Math.max(1, x.H - viewH);
    }
    return this.drawHex();
  }
  hexStatus() {
    const x = this.hex;
    if (!x.size) return "0 bytes";
    const { top, viewRows } = this.hexView();
    const a = top * 16, b = Math.min(x.size, (top + viewRows) * 16);
    this.visible = { bytes: [a, b] };
    const sel = this.byteSel();
    return `0x${a.toString(16)} · ${x.size.toLocaleString()} bytes` + (sel ? ` · ${sel[1] - sel[0]} selected (0x${sel[0].toString(16)}–0x${sel[1].toString(16)})` : "");
  }
  // the selected bytes as hex, for copying
  async byteSelText() {
    const r = this.byteSel();
    if (!r || r[1] - r[0] > 1 << 20) return null;
    const out = [];
    for (let c = Math.floor(r[0] / HEX_CHUNK); c * HEX_CHUNK < r[1]; c++) {
      const b = await byteChunk(this.hex.doc, c);
      for (let i = 0; i < b.length; i++) { const o = c * HEX_CHUNK + i; if (r[0] <= o && o < r[1]) out.push(b[i].toString(16).padStart(2, "0")); }
    }
    return out.join(" ");
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
      else if (click) { const h = this.highlightAt(ev, doc); if (h && h !== this.hlCur) this.goHighlight(h, false); }
    }, 0);
  }
  clearSelection() {
    this.selection = null;
    (this.frame?.contentDocument || document).getSelection()?.removeAllRanges();
    document.getSelection().removeAllRanges();
    this.body.querySelectorAll(".region").forEach((r) => r.remove());
    if (this.grid) { this.grid.anchor = this.grid.focus = null; this.paintCells(); }
    if (this.hex) { this.hex.anchor = this.hex.focus = null; this.paintHex(); }
    changed();
  }

  // where the locator is, scrolled into view
  async scrollTo(loc) {
    const sel = loc.select || {};
    if (this.mode === "grid") return this.scrollToCells(sel);
    if (this.mode === "hex") return sel.bytes && this.scrollToByte(sel.bytes[0]);
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
      if (n && this.pageEls[n - 1]) this.body.scrollTop = this.pageTop(this.pageEls[n - 1]) - 8;
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
    const h = { loc: locString(loc), label: "opened", range: sel.char, bytes: sel.bytes, cells: sel.cells && { sheet: sel.sheet, cells: sel.cells }, temp: true };
    this.highlights.push(h);
    this.drawBoxes();
    setTimeout(() => { this.highlights = this.highlights.filter((x) => x !== h); this.drawBoxes(); }, 1800);
  }
  async highlight(loc, label) {
    const l = parseLoc(loc);
    if (l.doc !== this.info?.doc || (l.layer && l.layer !== this.layer)) await this.open(l, { quiet: true });
    const sel = l.select || {}, at = locString(l);
    // the same place again (say, a note clicked twice) takes the newer label, not a second highlight
    const same = this.highlights.find((h) => !h.temp && h.loc === at);
    if (same) { if (label) same.label = label; }
    else this.highlights.push({ loc: at, label, range: sel.char, bytes: sel.bytes, region: sel.xywh && { page: sel.page, xywh: sel.xywh },
                                cells: sel.cells && { sheet: sel.sheet, cells: sel.cells } });
    this.drawBoxes();
    this.renderHighlights();
    await this.scrollTo(l);
  }
  clearHighlights() {
    this.highlights = []; this.hlCur = null;
    this.drawBoxes();
    this.renderHighlights();
  }
  hlClass(h) { return h === this.hlCur ? "hl hlcur" : "hl"; }
  // the highlights (not an open's brief flash) in the order they come in the document
  hlList() {
    const key = (h) => {
      if (h.range) return h.range[0];
      if (h.bytes) return h.bytes[0];
      if (h.region) return h.region.page * 1e6 + h.region.xywh[1] * 1e5;
      const c = h.cells && parseCells(h.cells.cells);
      return c ? c.r0 * 1e4 + c.c0 : 0;
    };
    return this.highlights.filter((h) => !h.temp).map((h, i) => [key(h), i, h])
      .sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
  }
  // ]/[ with no sequence, and the bar's buttons: the next or previous highlight
  async stepHighlight(delta) {
    const hs = this.hlList();
    if (!hs.length) return toast("Nothing to step through here: no sequence and no highlights");
    const i = hs.indexOf(this.hlCur);
    const next = i < 0 ? (delta > 0 ? 0 : hs.length - 1) : i + delta;
    if (next < 0 || next >= hs.length) return toast(delta > 0 ? "That was the last highlight" : "That was the first highlight");
    await this.goHighlight(hs[next]);
  }
  async goHighlight(h, scroll = true) {
    this.hlCur = h;
    const l = parseLoc(h.loc);
    if (l.layer && l.layer !== this.layer) await this.open({ doc: l.doc, layer: l.layer }, { quiet: true });
    this.drawBoxes();
    this.renderHighlights();
    if (scroll) await this.scrollTo(l);
    changed();
  }
  // the highlight at a mouse event, if the pointer is on one (text in any mode but grid and hex)
  highlightAt(ev, doc) {
    const hs = this.hlList().filter((h) => h.range);
    if (!hs.length) return null;
    const w = ev.target?.closest?.(".w");
    let s = null;
    if (w) s = Number(w.dataset.s);
    else {
      const r = doc.caretRangeFromPoint?.(ev.clientX, ev.clientY);
      if (r) s = offsetOf(r.startContainer, r.startOffset);
    }
    if (s === null || Number.isNaN(s)) return null;
    const under = hs.filter((h) => h.range[0] <= s && s < h.range[1]);
    // the innermost, when highlights nest
    return under.sort((a, b) => (a.range[1] - a.range[0]) - (b.range[1] - b.range[0]))[0] || null;
  }
  renderHighlights() { this.renderNav(); this.renderAnnotations(); }
  // the bar under the head: buttons to step through the sequence and the highlights, and the
  // current highlight's label, so neither depends on knowing the ] and [ keys
  renderNav() {
    const s = this.sequence, hs = this.hlList(), parts = [];
    const btn = (text, title, fn) => el("button", { title, onclick: (ev) => { ev.stopPropagation(); fn(); } }, text);
    if (s) parts.push(el("span", { class: "nav-group" },
      el("span", { class: "nav-kind" }, "Sequence"),
      btn("‹ Prev", "Previous in the sequence · shortcut: [", () => this.step(-1)),
      el("span", { class: "nav-pos" }, `${s.position + 1} of ${s.items.length}`),
      btn("Next ›", "Next in the sequence · shortcut: ]", () => this.step(1))));
    if (hs.length) {
      const i = hs.indexOf(this.hlCur), key = (k) => s ? "" : ` · shortcut: ${k}`;
      const label = i < 0 ? "" : hs[i].label || "";
      parts.push(el("span", { class: "nav-group" },
        el("span", { class: "nav-kind" }, hs.length === 1 ? "Highlight" : "Highlights"),
        btn("‹ Prev", "Previous highlight" + key("["), () => this.stepHighlight(-1)),
        el("span", { class: "nav-pos" }, `${i < 0 ? "–" : i + 1} of ${hs.length}`),
        btn("Next ›", "Next highlight" + key("]"), () => this.stepHighlight(1)),
        i < 0 ? el("span", { class: "nav-hint" }, `Next goes to the first`)
              : el("span", { class: "nav-label", title: label }, label)));
    }
    this.nav.replaceChildren(...parts);
    this.nav.hidden = !parts.length;
  }

  onScroll() {
    if (this.mode === "hex" && this.hex) requestAnimationFrame(() => this.drawHex());
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
      const vis = this.pageEls.filter((p) => { const c = p.parentElement; return c.offsetTop < bottom && c.offsetTop + c.offsetHeight > top; })
        .map((p) => Number(p.dataset.page));
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
    } else if (this.mode === "hex" && this.hex) {
      text = this.hexStatus();
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
    // else the number it was produced to us under (a native has one, for its first page)
    const p = this.info?.meta?.produced;
    if (p) return p.pages ? p.pages[page - 1] ?? null : page === 1 ? p.begin : null;
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
    if (!this.sequence) return this.stepHighlight(delta);
    const s = this.sequence;
    const next = s.position + delta;
    if (next < 0 || next >= s.items.length) return toast(delta > 0 ? "End of the sequence" : "Start of the sequence");
    s.position = next;
    await this.open(s.items[next], { source: "user" });
    if (s.from) callPane(s.from, "follow", s.items[next]);
    for (const d of s.items.slice(next + 1, next + 4)) docInfo(parseLoc(d).doc).catch(() => {});
    prefetch(s.items.slice(next + 1, next + 4));
    changed();
  }
  // a linked list pane's choice: its sequence, at this item
  seqOpen(sequence, loc) {
    this.sequence = sequence;
    const i = (sequence?.items || []).indexOf(loc);
    if (i >= 0) prefetch(sequence.items.slice(i + 1, i + 4));
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
    $(".chips", this.head)?.replaceChildren(...docTags.map((t) => t.carried
      ? el("span", { class: "chip carried " + (t.by === "claude" ? "by-claude" : ""), style: `color:${color(t.tag)}`,
          title: carriedNote(t.carried) + "\nClick to confirm it still holds.",
          onclick: () => confirmCarried({ tag: t.tag, target: t.target }, `“${t.tag}”`) }, "↪ " + t.tag)
      : el("span", { class: "chip " + (t.by === "claude" ? "by-claude" : ""), style: `color:${color(t.tag)}`, title: `${t.by}, ${t.at}` }, t.tag)));
    const allRanged = A.tags.filter((t) => t.target.doc === doc && t.target.select);
    const ranged = allRanged.filter((t) => inFacet("tag", t.tag));
    const allNotes = A.notes.filter((n) => n.target?.doc === doc);
    const notes = allNotes.filter((n) => inFacet("note", n.note));
    const item = (cls, text, who, target, extra = []) => el("div", { class: "ann " + cls + (who === "claude" ? " by-claude" : ""),
      onclick: () => target?.select && this.highlight(target, "annotation") }, el("div", {}, text), el("div", { class: "who" }, who), ...extra);
    // a carried-over tag or note: marked, with where it came from and a way to confirm it
    const carriedBits = (c, what, req) => c ? [el("div", { class: "carried-note", title: carriedNote(c) },
      "↪ carried over" + (c.unplaced ? " (its passage isn't in this version)" : "") + ": may not reflect what changed. ",
      el("a", { onclick: (e) => { e.stopPropagation(); confirmCarried(req, what); } }, "confirm"))] : [];
    const kids = [];
    // the highlights, labelled, in document order: click one to go to it
    const hs = this.hlList();
    if (hs.length) kids.push(el("div", { class: "side-h" }, "Highlights"));
    hs.forEach((h, i) => kids.push(el("div", { class: "ann hl-item" + (h === this.hlCur ? " current" : ""), title: "Go to this highlight",
      onclick: () => this.goHighlight(h) }, el("div", {}, h.label || "(no label)"), el("div", { class: "who" }, `highlight ${i + 1} of ${hs.length}`))));
    if (ranged.length) kids.push(el("div", { class: "side-h" }, "Tags"));
    for (const t of ranged) kids.push(item(t.carried ? "carried" : "", `▍${t.tag}: “${(t.target.quote?.exact || "").slice(0, 60)}”`, t.by, t.target,
      carriedBits(t.carried, `“${t.tag}”`, { tag: t.tag, target: t.target })));
    const top = notes.filter((n) => !n.parent || !notes.some((p) => p.note === n.parent));
    const thread = (n, depth) => {
      const mine = n.by === adhoc.user;
      kids.push(item((depth ? "reply" : "") + (n.carried ? " carried" : ""), n.body, n.by + (n.edited_at ? " (edited)" : ""), n.target, [
        ...carriedBits(n.carried, "this note", { note: n.note }),
        el("a", { onclick: (e) => { e.stopPropagation(); noteEditor({ op: "reply", parent: n.note }, "Reply"); } }, "reply "),
        ...(mine || true ? [el("a", { onclick: (e) => { e.stopPropagation(); noteEditor({ op: "edit-note", note: n.note }, "Edit note", n.body); } }, "edit "),
          el("a", { onclick: (e) => { e.stopPropagation(); if (confirm("Delete this note?")) annotate({ op: "delete-note", note: n.note }); } }, "delete")] : []),
      ]));
      for (const r of notes.filter((x) => x.parent === n.note)) thread(r, depth + 1);
    };
    if (top.length) kids.push(el("div", { class: "side-h" }, "Notes"));
    top.forEach((n) => thread(n, 0));
    // the facts that rest on this document: links and entities whose evidence is here
    const here = (see) => (see || []).filter((t) => t.doc === doc);
    const facts = [];
    for (const l of A.links || []) if (here(l.see).length && inFacet("link", l.link)) facts.push({ text: linkSentence(l), by: l.by, at: here(l.see)[0], link: l });
    for (const e of A.entities || []) if (here(e.see).length && inFacet("entity", e.entity)) facts.push({ text: `${e.name} (${ENTITY_KIND[e.kind] || e.kind})`, by: e.by, at: here(e.see)[0], entity: e });
    if (facts.length) kids.push(el("div", { class: "side-h", title: "Links and entities whose evidence is in this document" }, "Evidence for"));
    for (const f of facts) kids.push(el("div", { class: "ann fact" + (f.by === "claude" ? " by-claude" : ""), title: f.at.select ? "Go to the evidence" : "",
      onclick: () => f.at.select && this.highlight(f.at, f.text) }, el("div", {}, f.text), el("div", { class: "who" }, f.link ? "link" + (f.link.date ? " · " + f.link.date : "") : "entity")));
    const hidden = (allRanged.length - ranged.length) + (allNotes.length - notes.length);
    if (adhoc.facet && hidden) kids.push(el("div", { class: "side-more" }, `${plural(hidden, "tag or note", "tags and notes")} on other issues · `,
      el("a", { onclick: () => setFacet(null) }, "show all")));
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
    const dates = dts.map((d) => ({ ...d, isDate: true, resolved: resolveDate(d, anchor), until: resolveUntil(d, anchor) }))
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
        const h = !x && this.highlightAt(ev, doc);
        const tip = h ? (h.label || "Highlighted") : "";
        if (target.title !== tip) target.title = tip;
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
    return { form: x.form.replace(/\s+/g, " "), date: x.resolved, kind: x.kind, ...(x.until ? { until: x.until } : {}),
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
             marks: this.marks, highlights: this.highlights.filter((h) => !h.temp), selection: this.selection, visible: this.visible,
             current_highlight: this.highlights.filter((h) => !h.temp).indexOf(this.hlCur) };
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
    themeFrame(this.frame);
    const d = this.doc();
    if (!d || !d.body) return;
    d.addEventListener("keydown", onKey);
    d.addEventListener("mousedown", () => focus(this.id), true);
    d.addEventListener("click", (ev) => {
      const a = ev.target.closest("a[href]");
      if (a && a.getAttribute("href").startsWith("loc:")) {
        ev.preventDefault();
        openFromPane(this, a.getAttribute("href").slice(4), true, false, a.dataset.mode);
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

// -- loose files ----------------------------------------------------------------------------
// Files the user hands Claude, and files Claude hands back, that aren't
// discovery documents: plain files in the workspace (files.rs). The user
// drops them on any window (on the terminal, their paths are typed at the
// prompt too) or chooses them here; Claude hands files back with `view
// deliver`. The host copies in and saves out; this pane lists both folders.

const filesWaiting = new Map();
let filesSeq = 0;
function filesRequest(msg) {
  const id = ++filesSeq;
  return new Promise((res) => { filesWaiting.set(id, res); post({ ...msg, id }); });
}
adhoc.filesDone = (id, result) => { const res = filesWaiting.get(id); filesWaiting.delete(id); res?.(result); };

const FILE_KINDS = {
  uploads: { title: "For Claude", from: "you",
             empty: "Nothing yet. Drop files anywhere in the viewer, or on Claude's terminal, to hand them to Claude." },
  "from-claude": { title: "From Claude", from: "Claude",
                   empty: "Nothing yet. When Claude makes a file for you (a draft, a spreadsheet), it appears here to save." },
};
// how a path is typed at a shell-like prompt: quoted if it needs to be
const quotePath = (p) => /^[\w@%+=:,./-]+$/.test(p) ? p : "'" + p.replace(/'/g, "'\\''") + "'";

class FilesPane {
  constructor(id) {
    this.id = id; this.kind = "files"; this.link = null; this.selected = []; this.cursor = -1;
    this.data = null; this.raw = ""; this.fresh = new Set();
    this.root = el("section", { class: "pane", "data-pane": id });
    this.head = el("div", { class: "pane-head" }, el("span", { class: "pane-title" }, "Files"),
      el("span", { class: "status" }, "Loose files: not discovery documents"));
    this.body = el("div", { class: "pane-body files" });
    this.root.append(this.head, this.body);
    this.refresh();
    this.timer = setInterval(() => { if (this.root.isConnected && this.root.offsetParent) this.refresh(); }, 2000);
  }
  title() { return "Files"; }
  state() {
    const names = (k) => (this.data?.[k] || []).map((x) => x.name);
    return { id: this.id, kind: "files", selection: [], uploads: names("uploads"), from_claude: names("from-claude") };
  }
  spec() { return { kind: "files", id: this.id }; }
  items() { return []; }
  move() {}
  targets() { return []; }
  // just added: shown marked for a while
  flash(names) {
    for (const n of names) this.fresh.add(n);
    this.raw = "";
    this.refresh();
    setTimeout(() => { for (const n of names) this.fresh.delete(n); this.raw = ""; this.refresh(); }, 6000);
  }
  async refresh() {
    let raw;
    try { raw = await fetch("/api/files").then((r) => r.text()); } catch { return; }
    if (raw === this.raw) return;
    this.raw = raw;
    try { this.data = JSON.parse(raw); } catch { return; }
    const top = this.body.scrollTop;
    this.render();
    this.body.scrollTop = top;
    changed();
  }
  async act(op, kind, name) {
    const r = await filesRequest({ type: "files-act", op, kind, name });
    if (r.error) return toast(r.error);
    if (op === "save") toast("Saved to " + r.saved);
    this.raw = ""; this.refresh();
  }
  async pick(folders = false) {
    const r = await filesRequest({ type: "files-pick", folders });
    filesAdded(r, null, this);
  }
  render() {
    const o = this.data;
    const warn = el("div", { class: "files-warn", role: "note" },
      el("strong", {}, "These are not discovery documents. "),
      "They aren't ingested or numbered, have no document IDs, aren't searched, synced or tracked, and Claude can read, change or delete them. " +
      "To make one part of the record, ingest it on the host (adhoc ingest).");
    const add = el("div", { class: "files-add" },
      el("button", { class: "primary", onclick: () => this.pick(false) }, "Add files…"),
      el("button", { onclick: () => this.pick(true) }, "Add a folder…"),
      el("span", { class: "hint" }, "or drop them anywhere in the viewer. Dropped on Claude's terminal, their paths are typed at its prompt."));
    const section = (kind) => {
      const k = FILE_KINDS[kind], rows = o[kind] || [];
      const head = el("h2", {}, k.title, el("span", { class: "count" }, String(rows.length)),
        el("button", { class: "link", title: "Open this folder in the file manager", onclick: () => this.act("reveal", kind, "") }, "Show folder"));
      if (!rows.length) return el("section", { class: "card" }, head, el("p", { class: "empty" }, k.empty));
      return el("section", { class: "card" }, head, el("ul", { class: "rows files-rows" }, ...rows.map((x) => {
        const meta = [x.files != null ? plural(x.files, "file") + " · " : "", fmtSize(x.size), " · ", fmtAgo(x.modified)].join("");
        return el("li", { class: "row" + (this.fresh.has(x.name) ? " fresh" : "") },
          el("span", { class: "main" },
            el("span", { class: "fname" }, (x.files != null ? "📁 " : "") + x.name),
            x.note ? el("span", { class: "fnote" }, x.note) : "",
            el("code", { class: "fpath", title: "Where Claude finds it" }, x.path)),
          el("span", { class: "side" }, meta),
          el("span", { class: "acts" },
            el("button", { title: "Copy the path, to paste into Claude", onclick: async () => toast((await copyText(x.path)) ? "Copied " + x.path : x.path) }, "Copy path"),
            el("button", { title: "Save a copy to your Downloads folder", onclick: () => this.act("save", kind, x.name) }, "Save to Downloads"),
            el("button", { class: "danger", title: "Delete it from the workspace (click twice)", onclick: (ev) => {
              // a second click within a few seconds deletes: no dialog to get in the way
              const b = ev.currentTarget;
              if (b.dataset.armed) return this.act("remove", kind, x.name);
              b.dataset.armed = "1"; b.textContent = "Delete it?";
              setTimeout(() => { delete b.dataset.armed; b.textContent = "Remove"; }, 3000);
            } }, "Remove")));
      })));
    };
    this.body.replaceChildren(warn, add, el("div", { class: "cards" }, section("uploads"), section("from-claude")));
  }
}
function openFiles(focusIt = true) {
  const f = adhoc.panes.find((p) => p.kind === "files") || addPane(new FilesPane(newPaneId()), focusIt);
  if (focusIt) focus(f.id);
  return f;
}
// what came of copying files in: say so, mark them in the Files pane
function filesAdded(r, intoTerminal, filesPane) {
  for (const e of r.errors || []) toast("Not added: " + e);
  const added = r.added || [];
  if (!added.length) return;
  const what = added.length === 1 ? added[0].name : plural(added.length, "item");
  if (intoTerminal) {
    window.adhocTerm.paste(added.map((x) => quotePath(x.path)).join(" ") + " ");
    window.adhocTerm.focus();
    toast(`Handed Claude ${what} (in /matter/workspace/files/uploads). Not a discovery document.`);
    adhoc.panes.find((p) => p.kind === "files")?.flash(added.map((x) => x.name));
  } else {
    (filesPane || openFiles()).flash(added.map((x) => x.name));
    toast(`Handed Claude ${what}. Tell Claude what to do with ${added.length === 1 ? "it" : "them"}.`);
  }
}
// Files dragged over the window, from outside (the host sees them over any frame)
// (the veil ignores the pointer, so what's under it is what the files land on)
const overTerminal = (x, y) => !!window.adhocTerm?.term && !!document.elementFromPoint(x, y)?.closest("#dock");
adhoc.dragging = (d) => {
  $("#drop-veil").hidden = !d.over;
  $("#drop-veil").classList.toggle("term", d.over && overTerminal(d.x, d.y));
};
adhoc.dropped = (at, r) => {
  $("#drop-veil").hidden = true;
  filesAdded(r, overTerminal(at.x, at.y), null);
};
$("#menu-btn").addEventListener("click", (ev) => showMenu(ev.currentTarget, [
  { label: "⌂ Overview", hint: "H", onclick: () => openHome() },
  { label: "Files: hand Claude files", hint: "or drop them", onclick: () => openFiles() },
  "-",
  { label: "Move this pane to a new window", hint: "W", disabled: !focusedPane(), onclick: () => focusedPane() && post({ type: "tearoff", pane: focusedPane().id }) },
  { label: "Keys", hint: "?", onclick: () => onKey({ key: "?", target: document.body, preventDefault() {} }) },
], "left"));

// Open a locator from a pane. A list's preview follows it (its linked pane);
// anything else opens beside the pane, as a new tab among the documents it has
// opened, or in the tab already showing that document.
// Tags and notes copied from a document a replacement volume superseded (RECEIVED.md)
function carriedNote(c) {
  const whose = c.by && c.by !== adhoc.user ? ` (${c.by}'s)` : "";
  return `Carried over${whose} from the version this document replaced` + (c.reason ? ` (${c.reason}). ` : ". ") +
    "It was about that version, and may not reflect what changed in this one" +
    (c.unplaced ? "; the passage it was on isn't in this version, so it is on the whole document." : ".");
}
async function confirmCarried(req, what) {
  if (!confirm(`Keep ${what}? It was carried over from the version this document replaced. Confirm once you've checked it still holds here.`)) return;
  await annotate({ op: "confirm-carried", ...req });
}

// mode: a link's data-mode (a page's "check against the page image" marker asks for "page")
function openFromPane(p, loc, newIfUnlinked, forceNew = false, mode = undefined) {
  const opts = { source: "pane", ...(mode ? { mode } : {}) };
  if (!forceNew && paneExists(p.link) && !pane(p.link)) return callPane(p.link, "open", loc, opts);
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
  target.open(loc, opts);
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
    const graph = (a) => JSON.stringify([a.links || [], a.facets || [], a.filed || []]);
    const graphBefore = graph(adhoc.annotations);
    adhoc.annotations = await getJSON("/api/annotations");
    adhoc.annotations.entities ||= [];
    for (const k of ["links", "facets", "filed"]) adhoc.annotations[k] ||= [];
    const entitiesChanged = JSON.stringify(adhoc.annotations.entities) !== before;
    const graphChanged = graph(adhoc.annotations) !== graphBefore;
    for (const p of adhoc.panes) {
      p.renderAnnotations?.();
      if (entitiesChanged) p.loadEntities?.();
    }
    facetButton();
    if (graphChanged) facetRefresh();
    else if (entitiesChanged) entityCard.refresh();
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

// -- links and facets --------------------------------------------------------------------------
// Links join two entities (a relation, its evidence); facets are issues that
// entities, links, notes and tags are filed under (annotations.py). With a
// facet chosen (F, or the ◇ button), what isn't filed under it is dimmed.

function linkById(id) { return (adhoc.annotations.links || []).find((l) => l.link === id); }
function linkSentence(l) { return `${entityById(l.from)?.name || "?"} ${l.rel} ${entityById(l.to)?.name || "?"}`; }
function itemKey(i) { const [k, v] = Object.entries(i || {})[0] || []; return k ? `${k}:${v}` : "?"; }
function facetsOf(kind, id) {
  const A = adhoc.annotations;
  const keys = [`${kind}:${id}`];
  if (kind === "note") {          // a reply is filed where its thread is
    let n = A.notes.find((x) => x.note === id); const seen = new Set();
    while (n?.parent && !seen.has(n.note)) { seen.add(n.note); n = A.notes.find((x) => x.note === n.parent) || null; if (n) keys.push(`note:${n.note}`); }
  }
  return [...new Set((A.filed || []).filter((f) => keys.includes(itemKey(f.item))).map((f) => f.facet))];
}
function inFacet(kind, id) { return !adhoc.facet || facetsOf(kind, id).includes(adhoc.facet); }
function setFacet(f, quiet = false) {
  adhoc.facet = f || null;
  try { f ? localStorage.setItem("adhoc.facet", f) : localStorage.removeItem("adhoc.facet"); } catch {}
  facetRefresh();
  if (!quiet) toast(f ? `Showing the issue “${f}”: the rest is dimmed (F to change)` : "Showing all issues");
  changed();
}
function facetButton() {
  const b = $("#facet-btn");
  if (!b) return;
  b.hidden = !(adhoc.annotations.facets || []).length && !adhoc.facet;
  b.textContent = adhoc.facet ? "◆ " + adhoc.facet : "◇";
  b.classList.toggle("on", !!adhoc.facet);
}
function facetRefresh() {
  facetButton();
  for (const p of adhoc.panes) if (p.kind === "document") { p.drawBoxes?.(); p.renderAnnotations?.(); }
  entityCard.refresh();
}
function facetMenu(anchor) {
  if (!anchor) return;
  const A = adhoc.annotations, fs = A.facets || [];
  const count = (f) => (A.filed || []).filter((x) => x.facet === f).length;
  showMenu(anchor, [{ heading: "Issue: show what is filed under it (F)" },
    { label: "All issues", checked: !adhoc.facet, onclick: () => setFacet(null) },
    ...fs.map((f) => ({ label: f.facet, hint: String(count(f.facet)), title: f.description || "", checked: adhoc.facet === f.facet,
                        onclick: () => setFacet(f.facet) })),
    ...(fs.length ? [] : [{ label: "No issues yet: ask Claude to define some", disabled: true }]),
    "-", { label: "Ask Claude to file things under an issue…", onclick: () => askClaude(adhoc.facet
      ? `File the entities, links and notes that bear on “${adhoc.facet}” under that facet.`
      : "Define facets for the main issues in this matter, and file the key entities, links and notes under them.") }]);
}
window.addEventListener("storage", (ev) => { if (ev.key === "adhoc.facet") { adhoc.facet = ev.newValue || null; facetRefresh(); changed(); } });
(() => {
  const b = el("button", { id: "facet-btn", class: "icon-btn", "aria-label": "Issue: show what is filed under one (F)", "aria-haspopup": "menu",
    title: "Issue (facet): show what is filed under one, dim the rest (F)", onclick: (ev) => facetMenu(ev.currentTarget) }, "◇");
  $("#top-tools")?.prepend(b);
  facetButton();
})();

const entityCard = {
  el: null, pane: null, mention: null, showTimer: null, hideTimer: null, stack: [],
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
  hide() { clearTimeout(this.showTimer); this.el.hidden = true; this.mention = null; this.stack = []; },
  refresh() { if (!this.el.hidden && this.pane && this.mention) this.show(this.pane, this.mention, this.at); },
  show(pane, x, at) {
    if (this.mention !== x) this.stack = [];
    this.pane = pane; this.mention = x; this.at = at;
    if (this.stack.length) return this.showEntity(this.stack[this.stack.length - 1], false);
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
  // from one entity to another, inside the card: the names in a summary, and
  // the entities a link joins, are links themselves; ← goes back
  showEntity(id, push = true) {
    if (push) this.stack.push(id);
    const e = entityById(id);
    if (!e) { this.stack.pop(); return; }
    const back = el("a", { class: "ec-back", onclick: (ev) => { ev.stopPropagation(); this.stack.pop(); this.show(this.pane, this.mention, this.at); } },
      "← " + (this.stack.length > 1 ? entityById(this.stack[this.stack.length - 2])?.name || "back" : "back"));
    this.el.replaceChildren(el("div", { class: "ec-nav" }, back), this.knownBlock(this.pane, e),
      el("div", { class: "ec-foot" }, "Not selected: hover a name in the document to select it"));
    this.place(this.at);
  },
  // text with the known entities it names made into links to their cards
  linkify(text, self) {
    const cps = Array.from(text);
    const ents = (adhoc.annotations.entities || []).filter((x) => x.entity !== self);
    const found = findEntityMentions(cps, ents);
    if (!found.length) return [text];
    const out = [];
    let at = 0;
    for (const m of found) {
      if (m.start > at) out.push(cps.slice(at, m.start).join(""));
      const id = m.entities[0];
      out.push(el("a", { class: "ec-ent", title: entityById(id)?.summary || "", onclick: (ev) => { ev.stopPropagation(); this.showEntity(id); } },
        cps.slice(m.start, m.end).join("")));
      at = m.end;
    }
    if (at < cps.length) out.push(cps.slice(at).join(""));
    return out;
  },
  connections(pane, e) {
    const links = (adhoc.annotations.links || []).filter((l) => l.from === e.entity || l.to === e.entity);
    if (!links.length) return [];
    links.sort((a, b) => (inFacet("link", b.link) - inFacet("link", a.link)) || String(a.date || "").localeCompare(String(b.date || "")));
    const shown = links.slice(0, 8);
    const rows = shown.map((l) => {
      const out = l.from === e.entity, other = entityById(out ? l.to : l.from);
      const ev = (l.see || [])[0];
      const name = other ? el("a", { class: "ec-ent", onclick: (x) => { x.stopPropagation(); this.showEntity(other.entity); } }, other.name) : "?";
      // read from this entity: "signed for Dock 4 delivery", or "Tri-County Freight Lines carried" (it)
      return el("div", { class: "ec-link" + (inFacet("link", l.link) ? "" : " muted") + (ev ? "" : " bare"), title: linkSentence(l) + (l.summary ? "\n" + l.summary : "") },
        ...(out ? [el("span", { class: "ec-rel" }, l.rel), " ", name] : [name, " ", el("span", { class: "ec-rel" }, l.rel)]),
        l.date ? el("span", { class: "ec-date" }, " " + l.date) : "",
        ev ? el("a", { class: "ec-ev", title: "Open the evidence", onclick: (x) => { x.stopPropagation(); this.hide(); pane?.highlight(ev, linkSentence(l)); } }, " evidence")
           : el("span", { class: "ec-ev none", title: "No document recorded for this link" }, " no evidence"));
    });
    const more = links.length - shown.length;
    return [el("div", { class: "ec-links" }, el("div", { class: "ec-label" }, "Connections"), ...rows,
      ...(more ? [el("div", { class: "ec-why" }, `${more} more: ask Claude to draw them (view graph)`)] : []))];
  },
  knownBlock(pane, e) {
    if (!e) return null;
    const see = (e.see || []).slice(0, 3).map((t) => el("a", { class: "ec-see", title: "open",
      onclick: () => { this.hide(); pane.highlight(t, "entity"); } }, "“" + ((t.quote?.exact || "the document").replace(/\s+/g, " ").slice(0, 60)) + "”"));
    const facets = facetsOf("entity", e.entity);
    return el("div", { class: "ec-entity" },
      el("div", { class: "ec-head" }, el("span", { class: "ec-name" }, e.name), el("span", { class: "ec-kind" }, (ENTITY_KIND[e.kind] || e.kind) + (e.date ? " · " + e.date : ""))),
      e.summary ? el("div", { class: "ec-summary" }, ...this.linkify(e.summary, e.entity)) : el("div", { class: "ec-summary ec-empty" }, "Not described yet: select it and ask Claude."),
      ...(e.recap ? [el("div", { class: "ec-recap" }, el("span", { class: "ec-label" }, "Last time" + (e.recap_at ? ` (${shortDate(e.recap_at)})` : "") + ": "), ...this.linkify(e.recap, e.entity))] : []),
      ...this.connections(pane, e),
      ...(see.length ? [el("div", { class: "ec-sees" }, "See ", ...see.flatMap((a, i) => i ? [" · ", a] : [a]))] : []),
      ...(facets.length ? [el("div", { class: "ec-facets" }, ...facets.map((f) => el("span", { class: "ec-facet" + (f === adhoc.facet ? " on" : ""), title: "An issue it is filed under" }, f)))] : []),
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
function markClass(x) {
  if (x.isDate) return "ent date";
  if (!x.known) return "ent sug";
  return adhoc.facet && !x.entities.some((id) => inFacet("entity", id)) ? "ent muted" : "ent";
}
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
    // a range or a week shows both ends; the card is about its first day
    const name = x.until ? (x.precision === "week" ? "Week of " : "") + `${fmtDay(date, false)} – ${fmtDay(x.until, false)}` : fmtDay(date);
    const head = el("div", { class: "ec-head" }, el("span", { class: "ec-name" }, name),
      el("span", { class: "ec-kind" }, x.strip ? (DATE_SOURCE[x.source] || "date").toLowerCase() : "date"));
    const lines = [];
    if (!x.strip) {
      const read = x.kind === "relative" || x.kind === "noyear";
      if (read) lines.push(el("div", { class: "ec-why" }, `“${x.form.replace(/\s+/g, " ")}”, counted from the document's date`));
      if (x.rel?.bare === "near") lines.push(el("div", { class: "ec-why" }, "Read as the nearer one; the text doesn't say whether it's before or after"));
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
  if (ev.target.closest?.("#dock")) return;          // the terminal's keys are the program's
  if (!$("#menu").hidden) return menuKey(ev);
  if (ev.target.closest?.("input,textarea,select") && ev.key !== "Escape") return;
  if (!$("#palette").hidden || !$("#note-editor").hidden || !$("#entity-editor").hidden) return;
  const p = focusedPane();
  const k = ev.key;
  const doc = p?.kind === "document" ? p : null, web = p && !["document", "home", "files"].includes(p.kind) ? p : null;
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
  else if (k === "F") facetMenu($("#facet-btn"));
  else if (k === "v" && doc?.info?.layers.length > 1) { const ls = doc.info.layers; const i = ls.findIndex((l) => l.id === doc.layer); doc.open({ doc: doc.info.doc, layer: ls[(i + 1) % ls.length].id }, { quiet: true }); }
  else if (k === "f" && doc?.info) { const modes = doc.modes().filter((m) => m !== "hex" || doc.modes().length === 1); doc.setMode(modes[(modes.indexOf(doc.mode) + 1) % modes.length]); }
  else if (k.startsWith("Arrow") && doc?.mode === "grid") doc.moveCell(k, ev.shiftKey);
  else if (k === "Escape" && doc) { entityCard.hide(); doc.clearSelection(); }
  else if (k === "?") toast("j/k move · Enter open · ]/[ next/prev in the sequence, or highlight if none · Space page · m mark · t tag · N note · e entity · E show entities · F issue (facet) · v layer · f mode · Tab pane · H overview · W pane to a new window · Alt-drag region · grid: click, drag or shift-arrows to select cells");
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
  // in hex mode, the selected bytes' hex (the copy itself has to be made later: the bytes may need fetching)
  if (p?.mode === "hex" && p.byteSel()) {
    ev.preventDefault();
    p.byteSelText().then((t) => t ? navigator.clipboard.writeText(t).then(() => toast("Copied the bytes as hex"), () => toast("Couldn't copy"))
                                  : toast("Too many bytes to copy; ask Claude for raw hex"));
    return;
  }
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
    for (const p of c.pane ? [pane(c.pane)] : adhoc.panes) if (p?.kind === "document") p.clearHighlights();
    return {};
  },
  async sequence(c) {
    const p = docPaneFor(c);
    p.sequence = { items: c.locs, position: 0, from: null };
    prefetch(c.locs.slice(1, 4));
    if (c.locs.length) await p.open(c.locs[0], { source: "claude" });
    return { pane: p.id };
  },
  async pane(c) {
    if (c.update) {
      const p = pane(c.update);
      if (!p || ["document", "home", "files"].includes(p.kind)) throw new Error("no web pane " + c.update);
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
  // the Files pane; with names, those just handed over are marked
  async files(c) {
    const f = openFiles();
    if (c.names?.length) f.flash(c.names);
    return { pane: f.id };
  },
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
        p.hlCur = p.highlights[s.current_highlight] || null;
        p.drawBoxes();
        p.renderHighlights();
        const v = s.visible || {};
        const at = v.page ? { page: v.page[0] } : v.char ? { char: v.char } : v.rows ? { sheet: v.sheet, cells: "A" + v.rows[0] } : null;
        if (at) await p.scrollTo({ select: at });
        p.updateStatus();
      }
    } else if (s.kind === "home") {
      p = addPane(new HomePane(s.id), true);
    } else if (s.kind === "files") {
      p = addPane(new FilesPane(s.id), true);
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
             ...(entity ? { entity } : {}), ...(date ? { date } : {}), marks: p?.marks || adhoc.listMarks || [],
             ...(adhoc.facet ? { facet: adhoc.facet } : {}) };
  },
  // the issue to show (a facet), or none: what isn't filed under it is dimmed
  async facet(c) {
    const f = c.facet || null;
    if (f && !(adhoc.annotations.facets || []).some((x) => x.facet === f)) throw new Error(`no facet ${f}; ann facets lists them`);
    setFacet(f, true);
    return { facet: f };
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
// a limited copy's original finished fetching: whoever waits for it looks again
adhoc.fetched = (msg) => {
  for (const done of [...(fetchWaiters.get(msg.content) || [])]) done(msg.state);
};
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
// The session's terminal, when the viewer runs Claude Code itself (adhoc open
// --terminal window): beside the documents, or in a window of its own.
adhocTerm.init({
  title: "Claude Code",
  buttons: (info) => [info.alone
    ? { label: "⇲", title: "Put Claude Code back beside the documents", onclick: () => post({ type: "term-move", to: "back" }) }
    : { label: "⇱", title: "Move Claude Code to a window of its own", onclick: () => post({ type: "term-move", to: "window" }) }],
  onLayout: () => { dockLayout(); positionPanes(); },
});
renderTabs();
post({ type: "ready" });
