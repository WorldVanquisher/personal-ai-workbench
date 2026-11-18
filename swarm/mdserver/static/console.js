// PAI Web console. Plain DOM, no external code. The screen is built with text nodes only (never
// innerHTML), every write sends the session's CSRF token, and the browser names a lane, never a
// tmux pane. Live typing goes through a hidden textarea so input methods (IME) work.
"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "", info = null, lane = null, target = "agent", scrollback = false, live = false;
let queue = [], flushing = false, lastKey = "", polling = null, quick = null;

async function api(method, url, body) {
  const opt = { method, headers: { "X-PAI-CSRF": csrf }, credentials: "same-origin" };
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  const data = await r.json().catch(() => ({}));
  return { status: r.status, data };
}
function showError(msg) { $("error").textContent = msg || ""; $("error").hidden = !msg; }
function result(msg) { $("result").textContent = msg || ""; }
function setState() {
  $("state").textContent = live && lane ? "LIVE → lane " + lane + (target === "shell" ? " (shell)" : "") : "";
  $("state").className = "state" + (live ? " dirty" : "");
}

// ---------------------------------------------------------------- screen (ANSI colours)
const BASE16 = ["#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
  "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"];
const RESET = { fg: null, bg: null, b: false, d: false, i: false, u: false, r: false, s: false };
function color256(n) {
  if (n < 16) return BASE16[n];
  if (n < 232) { n -= 16; const c = [0, 95, 135, 175, 215, 255]; return `rgb(${c[Math.floor(n / 36)]},${c[Math.floor(n / 6) % 6]},${c[n % 6]})`; }
  const g = 8 + (n - 232) * 10; return `rgb(${g},${g},${g})`;
}
function extColor(p) { // [5, n] or [2, r, g, b] (also the colon form with a colour-space id)
  if (p[0] === 5) return color256(p[1] || 0);
  if (p[0] === 2) { const v = p.length > 4 ? p.slice(-3) : p.slice(1, 4); return `rgb(${v[0] || 0},${v[1] || 0},${v[2] || 0})`; }
  return null;
}
function sgr(st, params) {
  const groups = params === "" ? ["0"] : params.split(";");
  for (let i = 0; i < groups.length; i++) {
    if (groups[i].includes(":")) { // 38:5:n, 38:2::r:g:b
      const sub = groups[i].split(":").map((x) => parseInt(x || "0", 10));
      if (sub[0] === 38) st.fg = extColor(sub.slice(1)); else if (sub[0] === 48) st.bg = extColor(sub.slice(1));
      else if (sub[0] === 4) st.u = sub[1] !== 0;
      continue;
    }
    const v = parseInt(groups[i] || "0", 10);
    if (v === 0) Object.assign(st, RESET);
    else if (v === 1) st.b = true; else if (v === 2) st.d = true; else if (v === 3) st.i = true;
    else if (v === 4) st.u = true; else if (v === 7) st.r = true; else if (v === 9) st.s = true;
    else if (v === 22) { st.b = false; st.d = false; } else if (v === 23) st.i = false; else if (v === 24) st.u = false;
    else if (v === 27) st.r = false; else if (v === 29) st.s = false;
    else if (v >= 30 && v <= 37) st.fg = BASE16[v - 30]; else if (v === 39) st.fg = null;
    else if (v >= 40 && v <= 47) st.bg = BASE16[v - 40]; else if (v === 49) st.bg = null;
    else if (v >= 90 && v <= 97) st.fg = BASE16[v - 82]; else if (v >= 100 && v <= 107) st.bg = BASE16[v - 92];
    else if (v === 38 || v === 48) {
      const n = parseInt(groups[i + 1], 10), take = n === 5 ? 2 : n === 2 ? 4 : 0;
      const c = extColor(groups.slice(i + 1, i + 1 + take).map((x) => parseInt(x || "0", 10)));
      i += take;
      if (v === 38) st.fg = c; else st.bg = c;
    }
  }
}
function cells(ch) { // display width of one character: 2 for East Asian wide and most emoji
  const c = ch.codePointAt(0);
  return (c >= 0x1100 && (c <= 0x115f || (c >= 0x2e80 && c <= 0xa4cf && c !== 0x303f) || (c >= 0xac00 && c <= 0xd7a3) ||
    (c >= 0xf900 && c <= 0xfaff) || (c >= 0xfe30 && c <= 0xfe6f) || (c >= 0xff00 && c <= 0xff60) ||
    (c >= 0xffe0 && c <= 0xffe6) || (c >= 0x1f300 && c <= 0x1faff) || (c >= 0x20000 && c <= 0x3fffd))) ? 2 : 1;
}
function emit(frag, text, st, extra) {
  if (!text) return;
  const plain = !st.fg && !st.bg && !st.b && !st.d && !st.i && !st.u && !st.r && !st.s && !extra;
  if (plain) { frag.appendChild(document.createTextNode(text)); return; }
  const s = document.createElement("span");
  s.textContent = text;
  for (const k of ["b", "d", "i", "u", "s"]) if (st[k]) s.classList.add(k);
  if (extra) s.classList.add(extra);
  const fg = st.r ? (st.bg || "#161616") : st.fg, bg = st.r ? (st.fg || "#e8e8e3") : st.bg;
  if (fg) s.style.color = fg;
  if (bg) s.style.backgroundColor = bg;
  frag.appendChild(s);
}
const ESC = /\x1b\[([0-9;:]*)m|\x1b\[[0-9;:?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]/g;
function render(text, cursor, showCursor) {
  const frag = document.createDocumentFragment(), st = Object.assign({}, RESET);
  const lines = text.replace(/\n$/, "").split("\n");
  lines.forEach((line, y) => {
    const curX = showCursor && cursor && y === cursor[1] ? cursor[0] : -1;
    let col = 0, last = 0, m, done = curX < 0;
    const out = (chunk) => {
      if (done) { emit(frag, chunk, st); col += Array.from(chunk).reduce((n, ch) => n + cells(ch), 0); return; }
      let before = "";
      for (const ch of Array.from(chunk)) {
        if (!done && col === curX) { emit(frag, before, st); before = ""; emit(frag, ch, st, "cur"); done = true; col += cells(ch); continue; }
        before += ch; col += cells(ch);
      }
      emit(frag, before, st);
    };
    ESC.lastIndex = 0;
    while ((m = ESC.exec(line))) { out(line.slice(last, m.index)); if (m[1] !== undefined) sgr(st, m[1]); last = ESC.lastIndex; }
    out(line.slice(last));
    if (!done && curX >= col) { emit(frag, " ".repeat(curX - col), st); emit(frag, " ", RESET, "cur"); }
    if (y < lines.length - 1) frag.appendChild(document.createTextNode("\n"));
  });
  $("screen").replaceChildren(frag);
}
async function refresh() {
  if (!lane) return;
  const url = "/api/screen?lane=" + encodeURIComponent(lane) + "&target=" + target + (scrollback ? "&history=1" : "");
  const r = await api("GET", url);
  if (r.status === 404 && target === "shell") { shellShown(false); return; }
  if (r.status !== 200) { showError(r.data.error || "the lane is not available"); return; }
  showError("");
  if (target === "shell") shellShown(true);
  $("trust").hidden = !r.data.trust;
  const key = JSON.stringify([lane, target, scrollback, r.data.text, r.data.cursor, live]);
  if (key === lastKey) return;
  const pre = $("screen"), atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4, first = !lastKey.includes(lane);
  lastKey = key;
  render(r.data.text, r.data.cursor, !scrollback);
  $("screen").querySelectorAll(".cur").forEach((c) => c.classList.toggle("idle", !live));
  if (scrollback && (atBottom || first)) pre.scrollTop = pre.scrollHeight;
}
function poll() {
  clearTimeout(polling);
  polling = setTimeout(async () => { if (!document.hidden) { try { await refresh(); } catch (e) { /* shown below */ } } poll(); }, 800);
}
function soon() { clearTimeout(quick); quick = setTimeout(() => refresh().catch(() => {}), 120); }

// ---------------------------------------------------------------- lanes
function badge(text, cls) { const b = document.createElement("span"); b.className = "badge " + (cls || ""); b.textContent = text; return b; }
async function loadLanes() {
  const r = await api("GET", "/api/lanes");
  if (r.status !== 200) return showError(r.data.error || "could not read the run");
  info = r.data;
  $("run").textContent = info.project + (info.run ? " · run " + info.run : "");
  $("notes").hidden = !info.roots.length;
  $("hint").hidden = !info.hint; $("hint").textContent = info.hint || "";
  const ul = $("lanes"); ul.textContent = "";
  for (const l of info.lanes) {
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button";
    const title = document.createElement("span"); title.textContent = "lane " + l.lane + " · " + l.agent;
    title.appendChild(badge(l.trust ? "TRUST?" : l.phase, l.trust ? "warn" : (l.process === "running" ? "" : "off")));
    const meta = document.createElement("span"); meta.className = "meta";
    meta.textContent = (l.note ? l.note + " · " : "") + "dispatch " + l.dispatch + " · " + l.branch;
    b.append(title, meta);
    if (l.lane === lane) b.setAttribute("aria-current", "true");
    b.addEventListener("click", () => select(l.lane));
    li.appendChild(b); ul.appendChild(li);
  }
  if (!lane && info.lanes.length) select(info.lanes[0].lane);
  else if (lane) laneHead();
}
function current() { return info && info.lanes.find((l) => l.lane === lane); }
function laneHead() {
  const l = current(); if (!l) return;
  $("lane-title").textContent = "LANE " + l.lane + " · " + l.agent + " · " + l.worktree;
  const n = $("note-link");
  n.hidden = !l.note_link;
  if (l.note_link) n.href = "/notes#open=" + l.note_link.root + ":" + encodeURIComponent(l.note_link.path);
}
function select(name) {
  lane = name; lastKey = ""; $("lane").hidden = false;
  document.querySelectorAll("#lanes button").forEach((b) => b.removeAttribute("aria-current"));
  loadLanes(); laneHead(); tab(target);
}
function tab(which) {
  target = which; lastKey = "";
  $("tab-agent").setAttribute("aria-pressed", String(which === "agent"));
  $("tab-shell").setAttribute("aria-pressed", String(which === "shell"));
  if (which === "agent") shellShown(true);
  $("close-shell").hidden = which !== "shell";
  setState(); refresh().catch(() => {});
}
function shellShown(yes) { $("no-shell").hidden = yes; $("term").hidden = !yes; if (!yes) setLive(false); }

// ---------------------------------------------------------------- typing and pasting
function setLive(on) {
  live = on && !$("term").hidden;
  $("live").setAttribute("aria-pressed", String(live));
  $("screen").classList.toggle("live", live);
  if (live && document.activeElement !== $("kbd")) $("kbd").focus({ preventScroll: true });
  setState(); lastKey = ""; soon();
}
function push(item) {
  const prev = queue[queue.length - 1];
  if (item.text && prev && prev.text !== undefined) prev.text += item.text; else queue.push(item);
  if (!flushing) setTimeout(flush, 10);
}
async function flush() {
  if (flushing || !queue.length || !lane) return;
  flushing = true;
  const items = queue.splice(0, 200);
  let r;
  try { r = await api("POST", "/api/keys", { lane, target, keys: items }); } finally { flushing = false; }
  if (r.status !== 200) result(r.data.error);
  if (queue.length) flush(); else soon();
}
async function sendPaste(text, enter, anyway) {
  if (!lane || !text) return false;
  const r = await api("POST", "/api/paste", { lane, target, text, enter, allow_unbracketed: !!anyway });
  const why = /^(not-bracketed|bracketed-unknown):/.exec(r.data.error || "");
  if (r.status === 409 && why && !anyway && confirm((why[1] === "not-bracketed"
      ? "The program in this pane did not ask for bracketed paste: each of these lines may be entered (submitted) on its own."
      : "This tmux cannot tell whether the program in this pane takes a bracketed paste. Claude Code and Codex normally do; " +
        "if it does not, each of these lines may be entered (submitted) on its own.") + "\n\nPaste these lines anyway?")) {
    return sendPaste(text, enter, true);
  }
  if (r.status !== 200) { result(r.data.error || "not sent"); return false; }
  const d = r.data;
  result("Pasted " + d.pasted + " characters, " + d.lines + " line(s)" + (d.enter ? "; Enter pressed." : "; nothing submitted.") +
    (d.notes.length ? " (" + d.notes.join("; ") + ")" : ""));
  soon();
  return true;
}
const KEYMAP = { Enter: "Enter", Backspace: "BSpace", Tab: "Tab", Escape: "Escape", ArrowUp: "Up", ArrowDown: "Down",
  ArrowLeft: "Left", ArrowRight: "Right", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown",
  Delete: "Delete", Insert: "Insert" };
function onKey(e) {
  if (e.isComposing || e.keyCode === 229 || e.metaKey) return; // input methods, and Cmd shortcuts stay the browser's
  let k = null;
  if (KEYMAP[e.key]) k = e.key === "Tab" && e.shiftKey ? "BTab" : KEYMAP[e.key];
  else if (/^F([1-9]|1[0-2])$/.test(e.key)) k = e.key;
  else if (e.ctrlKey && !e.altKey && e.key.length === 1) {
    const c = e.key.toLowerCase();
    if (c === "v") return; // Ctrl-V: the browser pastes, and the paste goes in one piece (below)
    if (/[a-z@[\]\\^_]/.test(c)) k = "C-" + c;
  } else if (e.altKey && !e.ctrlKey && e.key.length === 1 && e.key !== ";") k = "M-" + e.key;
  if (k) { e.preventDefault(); push({ key: k }); }
}
function takeTyped() { const v = $("kbd").value; $("kbd").value = ""; if (v) push({ text: v }); }

window.addEventListener("unhandledrejection", () =>
  showError("pai web could not be reached (stopped?). Start it again with `pai web`; the run itself is not affected."));
window.addEventListener("DOMContentLoaded", async () => {
  const r = await api("GET", "/api/session");
  if (r.status !== 200) { location.reload(); return; }
  csrf = r.data.csrf;
  const kbd = $("kbd");
  kbd.addEventListener("keydown", onKey);
  kbd.addEventListener("input", (e) => { if (!e.isComposing) takeTyped(); });
  kbd.addEventListener("compositionend", takeTyped);
  kbd.addEventListener("paste", (e) => { e.preventDefault(); sendPaste(e.clipboardData.getData("text/plain"), false); });
  kbd.addEventListener("blur", () => { if (live) setLive(false); });
  $("screen").addEventListener("click", () => { if (!String(window.getSelection())) setLive(true); });
  $("live").addEventListener("click", () => setLive(!live));
  document.querySelectorAll("[data-key]").forEach((b) => {
    b.addEventListener("mousedown", (e) => e.preventDefault()); // keep live typing focused
    b.addEventListener("click", () => push({ key: b.dataset.key }));
  });
  $("tab-agent").addEventListener("click", () => tab("agent"));
  $("tab-shell").addEventListener("click", () => tab("shell"));
  $("history").addEventListener("click", () => {
    scrollback = !scrollback; $("history").setAttribute("aria-pressed", String(scrollback)); lastKey = ""; soon();
  });
  $("copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("screen").textContent); result("Screen copied."); }
    catch (e) { result("Select the text on the screen and copy it (Ctrl+C / Cmd+C)."); }
  });
  $("open-shell").addEventListener("click", async () => {
    const s = await api("POST", "/api/shell/open", { lane });
    if (s.status !== 200) return result(s.data.error);
    result(s.data.created ? "Shell opened in " + s.data.cwd + "." : ""); lastKey = ""; tab("shell"); loadLanes();
  });
  $("close-shell").addEventListener("click", async () => {
    if (!confirm("Close lane " + lane + "'s shell? Whatever runs in it stops.")) return;
    const s = await api("POST", "/api/shell/close", { lane });
    result(s.status === 200 && s.data.closed ? "Shell closed." : (s.data.error || "No shell to close.")); tab("shell");
  });
  $("send").addEventListener("click", async () => { if (await sendPaste($("compose").value, false)) $("compose").value = ""; });
  $("send-enter").addEventListener("click", async () => { if (await sendPaste($("compose").value, true)) $("compose").value = ""; });
  await loadLanes();
  setInterval(() => { if (!document.hidden) loadLanes().catch(() => {}); }, 4000);
  poll();
});
