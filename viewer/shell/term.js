// The terminal dock: a command the host runs on a pseudo-terminal (term.rs),
// shown with xterm.js. Used for the session's Claude Code when it was started
// without a terminal of its own, beside the documents or in a window of its
// own, and by the launcher for `adhoc setup`. It is a plain terminal: keys go
// to the program, output comes back, nothing else reads either.
//
// The page provides #dock (and optionally #dock-split, a splitter beside it)
// and calls adhocTerm.init({...}). The host calls adhocTerm.attach / out /
// exited / detach / confirmClose.

(() => {
  const post = (msg) => window.ipc.postMessage(JSON.stringify(msg));
  const pref = (k, d) => { try { return localStorage.getItem(k) || d; } catch { return d; } };
  const setPref = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
  const mk = (tag, attrs = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
    }
    e.append(...kids.filter((k) => k != null));
    return e;
  };
  const fromB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

  function themeColors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    const dark = document.documentElement.dataset.theme === "dark";
    return {
      background: v("--term-bg"), foreground: v("--term-ink"), cursor: v("--accent"), cursorAccent: v("--term-bg"),
      selectionBackground: v("--sel"),
      // the ANSI colors, readable on either background
      ...(dark ? {
        black: "#2b2b2b", red: "#ff7b72", green: "#7ee787", yellow: "#e3b341", blue: "#79c0ff", magenta: "#d2a8ff", cyan: "#76e3ea", white: "#d0d0d0",
        brightBlack: "#6e7681", brightRed: "#ffa198", brightGreen: "#a6f0b0", brightYellow: "#f2cc60", brightBlue: "#a5d6ff", brightMagenta: "#e2c5ff", brightCyan: "#b3f0ff", brightWhite: "#ffffff",
      } : {
        black: "#24292f", red: "#cf222e", green: "#116329", yellow: "#7d4e00", blue: "#0550ae", magenta: "#8250df", cyan: "#1b7c83", white: "#6e7781",
        brightBlack: "#57606a", brightRed: "#a40e26", brightGreen: "#1a7f37", brightYellow: "#633c01", brightBlue: "#218bff", brightMagenta: "#a475f9", brightCyan: "#3192aa", brightWhite: "#8c959f",
      }),
    };
  }

  const T = {
    term: null, fit: null, opts: {}, running: false, sized: "",

    // opts: {title, buttons(info) -> [{label, title, onclick}], onLayout, onAttach(info)}
    init(opts) { T.opts = opts || {}; },

    // Show the terminal in this window, redrawn from what it has shown so far.
    attach(replayB64, info = {}) {
      const dock = document.getElementById("dock");
      T.detach();
      T.running = !info.exited;
      const status = mk("span", { class: "dock-status" });
      const tools = mk("span", { class: "dock-tools" }, ...(T.opts.buttons?.(info) || []).map((b) =>
        mk("button", { class: "icon-btn", "aria-label": b.title, onclick: b.onclick }, b.label)));
      const screen = mk("div", { class: "dock-screen" });
      dock.replaceChildren(mk("div", { class: "dock-head" }, mk("span", { class: "dock-title" }, info.title || T.opts.title || "Terminal"), status, tools),
                           screen, mk("div", { class: "dock-dialog", hidden: true }));
      dock.hidden = false;
      document.body.classList.add("has-dock");
      const split = document.getElementById("dock-split");
      if (split) split.hidden = false;
      const w = pref("adhoc.dockWidth", "");
      if (w) dock.style.width = w;
      T.term = new Terminal({
        fontFamily: '"DejaVu Sans Mono", "Cascadia Mono", Menlo, Consolas, "Liberation Mono", monospace', fontSize: 13,
        lineHeight: 1.1, scrollback: 5000, cursorBlink: true, allowProposedApi: true, macOptionIsMeta: true,
        theme: themeColors(),
      });
      T.fit = new FitAddon.FitAddon();
      T.term.loadAddon(T.fit);
      T.term.open(screen);
      // Ctrl+Shift+C / Ctrl+Shift+V copy and paste, as in other terminals; Ctrl+C stays an interrupt
      T.term.attachCustomKeyEventHandler((ev) => {
        if (ev.type !== "keydown" || !ev.ctrlKey || !ev.shiftKey) return true;
        if (ev.code === "KeyC") { const s = T.term.getSelection(); if (s) navigator.clipboard?.writeText(s); return false; }
        if (ev.code === "KeyV") return false;          // the web view pastes (a paste event, which xterm.js takes)
        return true;
      });
      T.term.onData((d) => post({ type: "term-in", data: d }));
      T.term.onBinary((d) => post({ type: "term-in", data: d, binary: true }));
      if (replayB64) T.term.write(fromB64(replayB64));
      if (info.exited) T.exited(info.exited.code, info.exited.message);
      T.sized = "";
      new ResizeObserver(() => T.resize()).observe(screen);
      requestAnimationFrame(() => { T.resize(); T.term?.focus(); });
      T.opts.onLayout?.();
      T.opts.onAttach?.(info);
    },
    detach() {
      const dock = document.getElementById("dock");
      T.term?.dispose();
      T.term = null; T.fit = null;
      dock.replaceChildren();
      dock.hidden = true;
      document.body.classList.remove("has-dock");
      const split = document.getElementById("dock-split");
      if (split) split.hidden = true;
      T.opts.onLayout?.();
    },
    resize() {
      if (!T.term || !T.fit) return;
      const d = T.fit.proposeDimensions();
      if (!d || !d.cols || !d.rows) return;
      T.fit.fit();
      const key = `${T.term.cols}x${T.term.rows}`;
      if (key !== T.sized) { T.sized = key; post({ type: "term-size", cols: T.term.cols, rows: T.term.rows }); }
    },
    out(b64) { T.term?.write(fromB64(b64)); },
    paste(text) { if (T.term && text) T.term.paste(text); },
    focus() { T.term?.focus(); },
    retheme() { if (T.term) T.term.options.theme = themeColors(); },
    exited(code, message) {
      T.running = false;
      const s = document.querySelector("#dock .dock-status");
      if (s) { s.textContent = code ? `ended (exit ${code})` : "ended"; s.classList.toggle("bad", !!code); }
      if (message && T.term) T.term.write("\r\n\x1b[2m" + message.replace(/\n/g, "\r\n") + "\x1b[0m\r\n");
      T.term?.write("\x1b[?25l");          // no cursor: nothing more can be typed
      T.term?.options && (T.term.options.cursorBlink = false);
    },
    // The window is being closed while the program runs: ask first.
    // choices: [{label, action, primary}]
    confirmClose(text, choices) {
      const box = document.querySelector("#dock .dock-dialog");
      if (!box) return post({ type: "term-close", action: "end" });
      box.replaceChildren(mk("p", {}, text), mk("div", { class: "row" }, ...choices.map((c) =>
        mk("button", { class: c.primary ? "primary" : "", onclick: () => { box.hidden = true; if (c.action !== "cancel") post({ type: "term-close", action: c.action }); else T.focus(); } }, c.label))));
      box.hidden = false;
      box.querySelector("button")?.focus();
    },
  };

  // dragging the splitter between the dock and the documents
  document.addEventListener("mousedown", (ev) => {
    if (ev.target.id !== "dock-split") return;
    ev.preventDefault();
    const dock = document.getElementById("dock");
    const startX = ev.clientX, startW = dock.getBoundingClientRect().width;
    // the dock is on the left of its splitter, or (the launcher) on the right
    const sign = ev.target.compareDocumentPosition(dock) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    document.body.classList.add("dragging", "drag-v");
    const move = (e) => {
      const w = Math.max(320, Math.min(window.innerWidth - 320, startW + sign * (e.clientX - startX)));
      dock.style.width = w + "px";
      T.opts.onLayout?.();
    };
    const up = () => {
      document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
      document.body.classList.remove("dragging", "drag-v");
      setPref("adhoc.dockWidth", dock.style.width);
    };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  });

  window.adhocTerm = T;
})();
