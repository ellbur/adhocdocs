// The launcher (launcher.rs): matters, adding documents, opening sessions and
// setting up, without a command line. Everything it does is an `adhoc`
// command, run by the host: launcher.run(["init", path]) is `adhoc init path`.

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...kids.flat().filter((k) => k != null && k !== false));
  return e;
};
const post = (msg) => window.ipc.postMessage(JSON.stringify(msg));
const prefGet = (k, d) => { try { return localStorage.getItem(k) || d; } catch { return d; } };
const prefSet = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;
function ago(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} minutes ago`;
  if (s < 86400 * 1.5) return `${Math.round(s / 3600)} hours ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} days ago`;
  return new Date(t * 1000).toLocaleDateString();
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 4500);
}
const errText = (r) => r?.err?.error || r?.err?.refused || (r?.code ? `failed (exit ${r.code})` : "");

const L = {
  next: 1, waiting: new Map(), streams: new Map(),
  data: { matters: [] }, setup: null, sessions: new Map(), adding: null, busy: new Set(),

  request(msg, onLine) {
    const id = L.next++;
    if (onLine) L.streams.set(id, onLine);
    return new Promise((resolve) => { L.waiting.set(id, resolve); post({ ...msg, id }); });
  },
  // `adhoc args…`: {code, out (its JSON), err}
  run: (args) => L.request({ type: "run", args }),
  stream: (args, onLine) => L.request({ type: "stream", args }, onLine),
  spawn: (args) => L.request({ type: "spawn", args }),
  pick: (opts) => L.request({ type: "pick", ...opts }).then((r) => r.paths || []),
  done(id, result) { L.streams.delete(id); L.waiting.get(id)?.(result); L.waiting.delete(id); },
  progress(id, line) { L.streams.get(id)?.(line); },
  termDone(code) { L.checkSetup(); if (!code) toast("Set up."); },

  async refresh() {
    const r = await L.run(["matters"]);
    if (r.out) L.data = r.out;
    render();
  },
  async checkSetup() {
    const r = await L.run(["setup", "--check"]);
    const info = r.code ? r.err || {} : r.out || {};
    L.setup = { missing: info.missing || [], image: info.image ?? null, error: r.code && !info.missing ? errText(r) : "" };
    renderSetup();
  },
};
window.launcher = L;

// Sessions need podman and the session image; adding documents works without them.
function whyNoSession() {
  const s = L.setup;
  if (!s) return "";
  if (s.missing.some((c) => c.name === "podman")) return "Podman is missing (see above)";
  if (!s.image) return "Set up first (see above)";
  return "";
}

// -- what the user does ----------------------------------------------------------------------

function terminalMode() {
  const m = prefGet("adhoc.terminal", "beside");
  return m === "app" && !L.data.terminal_app ? "beside" : m;
}

async function newMatter(sample = false) {
  const [path] = await L.pick({ kind: "save", title: sample ? "Where to put the sample matter" : "New matter: where it goes, and its name",
                                name: sample ? "demo" : "", start: L.data.home });
  if (!path) return;
  const r = await L.run(["init", path]);
  if (r.code) return toast("Couldn't make the matter: " + errText(r));
  await L.refresh();
  if (sample && L.data.sample) return ingest(path, { manifest: L.data.sample });
  L.adding = { matter: path, paths: [] };
  render();
}

async function addExisting() {
  const [path] = await L.pick({ kind: "folder", title: "A matter folder (one with store/ in it)", start: L.data.home });
  if (!path) return;
  const r = await L.run(["matters", "add", path]);
  if (r.code) return toast(errText(r));
  L.data = r.out; render();
}

async function openSession(m) {
  const mode = terminalMode();
  L.sessions.set(m.path, { mode, started: Date.now() });
  render();
  const r = await L.spawn(["open", m.path, "--terminal", mode]);
  L.sessions.delete(m.path);
  // in a terminal program, the session runs on after `adhoc open` returns
  if (r.code) toast(`The session for ${m.name} ended with an error: ${errText(r)}`);
  L.refresh();
}

async function viewOnly(m) {
  const r = await L.run(["--matter", m.path, "view"]);
  if (r.code) toast(errText(r));
}

async function forget(m) {
  const r = await L.run(["matters", "forget", m.path]);
  if (r.out) L.data = r.out;
  render();
  toast(`${m.name} is off the list. Its folder is still there: ${m.path}`);
}

async function choose(kind) {
  const titles = { files: "Documents to add", folders: "Folders of documents to add", manifest: "A manifest (.toml) listing collections" };
  const paths = await L.pick({ kind, title: titles[kind], start: L.data.home });
  if (!paths.length) return;
  const a = L.adding;
  if (kind === "manifest") { a.manifest = paths[0]; a.paths = []; }
  else { a.manifest = null; a.paths = [...new Set([...a.paths, ...paths])]; }
  if (!a.collection && a.paths.length) {
    // a collection's name, guessed from where the files came from, and this month
    const parts = a.paths[0].split(/[\\/]/).filter(Boolean);
    const base = (kind === "folders" ? parts[parts.length - 1] : parts[parts.length - 2]) || "documents";
    a.collection = base.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + "-" + new Date().toISOString().slice(0, 7);
  }
  render();
}

async function ingest(matter, a) {
  const args = ["--matter", matter, "ingest"];
  if (a.manifest) args.push("--manifest", a.manifest);
  else {
    args.push("--collection", a.collection);
    if (a.custodian) args.push("--custodian", a.custodian);
    args.push(...a.paths);
  }
  const job = { matter, count: 0, last: "" };
  L.adding = { ...a, matter, job };
  render();
  const r = await L.stream(args, (line) => {
    if (line.doc) { job.count++; job.last = line.name || ""; renderJob(job); }
  });
  L.adding = null;
  await L.refresh();
  if (r.code) toast(`Stopped after ${plural(job.count, "document")}: ${errText(r)}`);
  else toast(`Added ${plural(job.count, "document")}.`);
}

function runSetup() {
  post({ type: "term-run", args: ["setup"] });
  adhocTerm.attach("", { title: "Setting up" });
}

// -- drawing -----------------------------------------------------------------------------------

function render() {
  const ms = L.data.matters || [];
  const list = $("#list");
  if (!ms.length) {
    list.replaceChildren(el("section", { class: "l-empty" },
      el("h2", {}, "Start with a matter"),
      el("p", {}, "A matter is a folder that holds one case: its documents, your tags and notes, and Claude's work. ",
        "Claude works on one matter at a time, in a sealed session that sees only that matter's documents."),
      el("div", { class: "row" },
        el("button", { class: "primary", onclick: () => newMatter() }, "New matter…"),
        L.data.sample ? el("button", { onclick: () => newMatter(true) }, "Try the sample matter") : null),
      L.data.sample ? el("p", { class: "hint" }, "The sample is a fictional supply dispute buried in several hundred ordinary documents. Adding it takes a minute or two.") : null));
    return renderTerminalChoice();
  }
  list.replaceChildren(el("h2", {}, "Matters"), el("ul", { class: "l-matters" }, ms.map(row)));
  renderTerminalChoice();
}

function row(m) {
  const session = L.sessions.get(m.path), adding = L.adding?.matter === m.path ? L.adding : null;
  const facts = [m.exists ? (m.documents == null ? "" : plural(m.documents, "document")) : "folder not found",
                 m.opened ? `opened ${ago(m.opened)}` : m.added ? `added ${ago(m.added)}` : ""].filter(Boolean).join(" · ");
  const more = el("button", { class: "icon-btn", "aria-label": "More for " + m.name, onclick: (ev) => menu(ev.currentTarget, [
    { label: "View documents only", hint: "no Claude", onclick: () => viewOnly(m), disabled: !m.exists },
    { label: "Show the folder", onclick: () => post({ type: "reveal", path: m.path }), disabled: !m.exists },
    { label: "Show Claude's work", hint: "workspace", onclick: () => post({ type: "reveal", path: m.path + "/workspace" }), disabled: !m.exists },
    "-",
    { label: "Remove from this list", hint: "keeps the folder", onclick: () => forget(m) },
  ]) }, "⋯");
  return el("li", { class: "l-matter" + (m.exists ? "" : " gone") },
    el("div", { class: "l-row" },
      el("div", { class: "l-name" }, el("strong", {}, m.name), el("span", { class: "l-path" }, m.path), el("span", { class: "l-facts" }, facts)),
      el("div", { class: "l-buttons" },
        session ? el("span", { class: "l-badge" }, session.mode === "app" ? "Session starting…" : "Session open")
                : el("button", { class: "primary", disabled: !m.exists || !!whyNoSession(), title: whyNoSession() || null,
                                 onclick: () => openSession(m) }, "Open with Claude"),
        el("button", { disabled: !m.exists || !!adding, onclick: () => { L.adding = { matter: m.path, paths: [] }; render(); } }, "Add documents…"),
        more)),
    adding ? addPanel(m, adding) : null);
}

function addPanel(m, a) {
  if (a.job) {
    return el("div", { class: "l-add" }, el("div", { class: "l-job" }, el("span", { class: "spinner" }),
      el("span", { class: "l-job-text" }, jobText(a.job))));
  }
  const chosen = a.manifest ? [a.manifest] : a.paths;
  const ok = a.manifest || (a.paths.length && a.collection);
  return el("div", { class: "l-add" },
    el("div", { class: "row" }, el("span", { class: "l-label" }, "Add"),
      el("button", { onclick: () => choose("files") }, "Files…"),
      el("button", { onclick: () => choose("folders") }, "Folders…"),
      el("button", { onclick: () => choose("manifest"), title: "A .toml file listing several collections (see corpus/manifest.toml)" }, "A manifest…"),
      el("span", { class: "hint" }, "PDFs, scans, email and mailboxes, Office files, zip archives, anything.")),
    chosen.length ? el("ul", { class: "l-chosen" }, chosen.slice(0, 6).map((p) => el("li", {}, p)),
                       chosen.length > 6 ? el("li", { class: "hint" }, `and ${chosen.length - 6} more`) : null) : null,
    a.paths.length ? el("div", { class: "l-fields" },
      el("label", {}, "Collection", el("input", { value: a.collection || "", placeholder: "e.g. smith-laptop-2026-03",
        oninput: (ev) => { a.collection = ev.target.value.trim(); $("#add-go").disabled = !(a.paths.length && a.collection); } })),
      el("label", {}, "Custodian ", el("span", { class: "hint" }, "(optional)"), el("input", { value: a.custodian || "", placeholder: "whose files, e.g. smith-j",
        oninput: (ev) => { a.custodian = ev.target.value.trim(); } })),
      el("p", { class: "hint" }, "The collection says where these came from; the custodian, whose they were. Both stay with each document.")) : null,
    el("div", { class: "row" },
      el("button", { id: "add-go", class: "primary", disabled: !ok, onclick: () => ingest(m.path, a) },
        chosen.length ? `Add ${a.manifest ? "the manifest's collections" : plural(chosen.length, "item")}` : "Add"),
      el("button", { onclick: () => { L.adding = null; render(); } }, "Cancel")));
}

function jobText(j) { return `Adding… ${plural(j.count, "document")} so far` + (j.last ? ` · ${j.last}` : ""); }
function renderJob(j) { const t = $(".l-job-text"); if (t) t.textContent = jobText(j); }

function renderSetup() {
  const box = $("#setup"), s = L.setup;
  if (!s || (!s.missing.length && !s.error && s.image)) { box.hidden = true; render(); return; }
  box.hidden = false;
  if (s.missing.length) {
    box.replaceChildren(el("h2", {}, "Some things are missing"),
      el("p", {}, s.image ? "Sessions work; what's missing is for the parts listed." : "Install these, then set up: it builds the session container."),
      el("ul", {}, s.missing.map((c) => el("li", {}, el("strong", {}, c.name), ` for ${c.needed_for}: ${c.fix}`, c.detail ? el("span", { class: "hint" }, ` (${c.detail})`) : null))),
      el("div", { class: "row" }, el("button", { onclick: () => L.checkSetup() }, "Check again")));
  } else if (s.error) {
    box.replaceChildren(el("h2", {}, "Couldn't check the setup"), el("pre", {}, s.error),
      el("div", { class: "row" }, el("button", { onclick: () => L.checkSetup() }, "Check again")));
  } else {
    box.replaceChildren(el("h2", {}, "One more step"),
      el("p", {}, "The session container isn't built yet. Setting up builds it: a few minutes the first time."),
      el("div", { class: "row" }, el("button", { class: "primary", onclick: runSetup }, "Set up")));
  }
  render();
}

function renderTerminalChoice() {
  const mode = terminalMode();
  const opt = (value, label, hint, disabled = false) => el("label", { class: "l-radio" + (disabled ? " disabled" : "") },
    el("input", { type: "radio", name: "term", value, checked: mode === value, disabled,
                  onchange: () => { prefSet("adhoc.terminal", value); renderTerminalChoice(); } }),
    el("span", {}, el("strong", {}, label), el("span", { class: "hint" }, hint)));
  const app = L.data.terminal_app;
  $("#terminal-choice").replaceChildren(
    opt("beside", "Beside the documents", "in the viewer's window, on the left"),
    opt("window", "In a window of its own", "next to the viewer's; it can move back while it runs"),
    opt("app", app ? `In ${app}` : "In a terminal program", app ? "your terminal program, in a window of its own" : "none found: set ADHOC_TERMINAL", !app));
}

// a small menu under a button (the viewer's #menu styles)
function menu(anchor, items) {
  const m = $("#menu");
  if (!m.hidden && m.anchor === anchor) { m.hidden = true; return; }
  m.replaceChildren(...items.map((it) => it === "-" ? el("div", { class: "msep" }) :
    el("button", { role: "menuitem", disabled: it.disabled, onclick: () => { m.hidden = true; it.onclick(); } },
      el("span", {}, it.label), it.hint ? el("span", { class: "mhint" }, it.hint) : null)));
  m.hidden = false; m.anchor = anchor;
  const r = anchor.getBoundingClientRect();
  m.style.left = Math.max(6, r.right - m.offsetWidth) + "px";
  m.style.top = (r.bottom + 4) + "px";
}
document.addEventListener("mousedown", (ev) => { if (!ev.target.closest("#menu, .icon-btn")) $("#menu").hidden = true; }, true);
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !ev.target.closest("#dock")) $("#menu").hidden = true; });

// the interface's light or dark, as the viewer's (which follows the system unless told otherwise)
const systemDark = matchMedia("(prefers-color-scheme: dark)");
function applyTheme() {
  const ui = prefGet("adhoc.theme.ui", "system");
  document.documentElement.dataset.theme = ui === "dark" || (ui === "system" && systemDark.matches) ? "dark" : "light";
  adhocTerm.retheme();
}
systemDark.addEventListener?.("change", applyTheme);

adhocTerm.init({
  title: "Terminal",
  buttons: () => [{ label: "×", title: "Close the terminal", onclick: () => {
    if (adhocTerm.running) return toast("Still running: wait for it to finish");
    post({ type: "term-hide" });
  } }],
});
$("#new-matter").onclick = () => newMatter();
$("#add-existing").onclick = addExisting;
applyTheme();
render();
L.refresh();
L.checkSetup();
setInterval(() => { if (!L.adding && document.hasFocus()) L.refresh(); }, 15000);
