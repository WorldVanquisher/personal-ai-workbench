// PAI Web console. Plain DOM, no external code. The screen is built with text nodes only (never
// innerHTML), every write sends the session's CSRF token, and the browser names a lane, never a
// tmux pane. Live typing goes through a hidden textarea so input methods (IME) work.
"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "", info = null, lane = null, target = "agent", scrollback = false, live = false;
let queue = [], flushing = false, lastKey = "", polling = null, quick = null;
let lastStatus = "", staged = null, opener = null, pendingPaste = null;
const AUTO = ["restart", "deliver", "record-delivered"];

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
  if (r.status === 404) { // a closed pane or a stale lane address: say so, with the next action
    $("gone-text").textContent = r.data.error || "the lane is not available";
    $("gone").hidden = false; $("term").hidden = true; setLive(false); return;
  }
  if (r.status !== 200) { showError(r.data.error || "the lane is not available"); return; }
  showError("");
  if (!$("gone").hidden) { $("gone").hidden = true; $("term").hidden = false; }
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
  if (info.summary_line !== undefined && $("summary").textContent !== info.summary_line) $("summary").textContent = info.summary_line;
  $("recover").hidden = !info.lanes.some((l) => l.lifecycle && (l.lifecycle.attention || AUTO.includes(l.recovery.action)));
  const ul = $("lanes"); ul.textContent = "";
  for (const l of info.lanes) {
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button";
    const lc = l.lifecycle || { state: l.phase, why: "" };
    const title = document.createElement("span"); title.textContent = "lane " + l.lane + " · " + l.agent;
    title.appendChild(badge(lc.attention ? lc.state.toUpperCase() : lc.state, lc.state === "waiting-for-trust" ? "warn" :
      lc.attention ? "attn" : (l.process === "running" ? "" : "off")));
    const meta = document.createElement("span"); meta.className = "meta";
    meta.textContent = (l.note ? l.note + " · " : "") + "dispatch " + l.dispatch + " · " + l.branch;
    b.append(title, meta);
    b.setAttribute("aria-label", "lane " + l.lane + ", " + l.agent + ", " + lc.state + ": " + lc.why);
    if (l.lane === lane) b.setAttribute("aria-current", "true");
    b.addEventListener("click", () => select(l.lane, true));
    li.appendChild(b); ul.appendChild(li);
  }
  if (!lane && info.lanes.length) select(info.lanes[0].lane);
  else if (lane) laneHead();
}
function laneStatus(l) {
  const lc = l.lifecycle; if (!lc) return;
  const text = lc.state + "|" + lc.why + "|" + (lc.next || []).join("|");
  if (text === lastStatus) return; // announce a change once, not every poll
  lastStatus = text;
  const box = $("lane-status"); box.textContent = "";
  const name = document.createElement("span"); name.className = "name"; name.textContent = lc.state.toUpperCase() + ": ";
  box.append(name, document.createTextNode(lc.why));
  if (lc.next && lc.next.length) {
    const nxt = document.createElement("div"); nxt.append(document.createTextNode("Next: "));
    const c = document.createElement("code"); c.textContent = lc.next[0]; nxt.appendChild(c); box.appendChild(nxt);
  }
}
async function loadInspect() {
  const panel = $("inspect-panel");
  if (panel.hidden || !lane) return;
  const r = await api("GET", "/api/inspect?lane=" + encodeURIComponent(lane));
  panel.textContent = "";
  if (r.status !== 200) { panel.textContent = r.data.error || "not available"; return; }
  for (const e of r.data.evidence) {
    const dt = document.createElement("dt"), dd = document.createElement("dd"), src = document.createElement("div");
    dt.textContent = e.kind; dd.textContent = (e.state == null ? "" : e.state) + ": " + (e.detail || "");
    src.className = "src"; src.textContent = "from " + e.source; dd.appendChild(src); panel.append(dt, dd);
  }
}
function openPanel(id, focusId, from) { opener = from || document.activeElement; $(id).hidden = false; $(focusId).focus(); }
function closePanel(id) { $(id).hidden = true; if (opener && opener.focus) opener.focus(); opener = null; }
async function recoverPreview(from) {
  const r = await api("GET", "/api/recover");
  if (r.status !== 200) return showError(r.data.error || "no recovery preview");
  $("recover-cli").textContent = r.data.cli;
  const ul = $("recover-lanes"); ul.textContent = "";
  for (const e of r.data.interrupted) {
    const li = document.createElement("li");
    li.textContent = "INTERRUPTED: an earlier recovery of lane " + e.lane + " (" + e.action + ", " + e.ts + ") did not finish; before it: " +
      e.before.lifecycle; ul.appendChild(li);
  }
  for (const p of r.data.lanes) {
    const li = document.createElement("li"), tag = AUTO.includes(p.action) ? "AUTO" : p.human ? "HUMAN" :
      p.action === "choose" ? "CHOOSE" : "";
    li.textContent = "lane " + p.lane + " · task " + p.task + " · now " + p.lifecycle + " → " + p.action + (tag ? " [" + tag + "]" : "") +
      ": " + p.would;
    for (const c of p.choices || []) { const code = document.createElement("code"); code.textContent = " pai recover " + c.split("   ")[0]; li.append(document.createElement("br"), code); }
    ul.appendChild(li);
  }
  $("recover-apply").disabled = !r.data.apply.length;
  $("recover-apply").textContent = r.data.apply.length ? "Apply recovery: " + r.data.apply.length + " automatic action(s)" : "Nothing to apply automatically";
  $("recover-report").hidden = true;
  openPanel("recover-panel", "recover-title", from);
}
async function recoverApply() {
  if (!confirm("Apply the automatic recovery actions now? Restarted workers get their tasks; nothing is resent that was delivered.")) return;
  $("recover-apply").disabled = true; $("recover-report").hidden = false; $("recover-report").textContent = "Working…";
  const r = await api("POST", "/api/recover", { confirm: true, wait: 10 });
  $("recover-report").textContent = r.status === 200 ? r.data.report : (r.data.error || "not applied");
  lastKey = ""; loadLanes(); soon();
}
function current() { return info && info.lanes.find((l) => l.lane === lane); }
function laneHead() {
  const l = current(); if (!l) return;
  laneStatus(l);
  $("lane-title").textContent = "LANE " + l.lane + " · " + l.agent + " · " + l.worktree;
  const n = $("note-link");
  n.hidden = !l.note_link;
  if (l.note_link) n.href = "/notes#open=" + l.note_link.root + ":" + encodeURIComponent(l.note_link.path);
}
function select(name, byUser) {
  if (name !== lane) { lastStatus = ""; setStaged(null); $("inspect-panel").hidden = true; $("inspect").setAttribute("aria-pressed", "false"); }
  lane = name; lastKey = ""; $("lane").hidden = false;
  document.querySelectorAll("#lanes button").forEach((b) => b.removeAttribute("aria-current"));
  loadLanes(); laneHead(); tab(target);
  if (byUser) $("lane-title").focus(); // keyboard and screen-reader users land on the lane they chose
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
function setStaged(state) { // the input box holds pasted text (staged) or it was submitted
  staged = state;
  const box = $("inbox");
  box.hidden = !state; box.className = "inbox" + (state ? " " + state.kind : "");
  $("inbox-text").textContent = !state ? "" : state.kind === "staged"
    ? " in lane " + state.lane + "'s input box (" + state.chars + " characters): not submitted. Read it on the screen, then submit it."
    : " to lane " + state.lane + " (" + state.chars + " characters).";
  $("inbox-submit").hidden = !state || state.kind !== "staged";
}
function sizeText(n) { return n < 1024 ? n + " bytes" : (n / 1024).toFixed(1) + " KB"; }
function composeSize() {
  const t = $("compose").value, bytes = new TextEncoder().encode(t).length, lines = t ? t.split("\n").length : 0;
  $("compose-size").textContent = t ? t.length + " characters, " + lines + " line(s), " + sizeText(bytes) : "";
}
async function stagePaste(enter) { // preview where the paste goes and how big it is, before sending it
  const text = $("compose").value;
  if (!lane || !text) return;
  const r = await api("POST", "/api/paste/preview", { lane, target, text, enter });
  if (r.status !== 200) { result(r.data.error || "cannot paste here"); return; }
  const d = r.data;
  pendingPaste = { text, enter, anyway: false };
  let msg = "Paste into lane " + d.lane + " · " + d.program + " (" + d.target + " pane " + d.pane + "): " + d.chars +
    " characters, " + d.lines + " line(s), " + sizeText(d.bytes) + " of at most " + sizeText(d.limit) + ". " +
    (enter ? "Enter is pressed after it (submitted)." : "Nothing is submitted (it stays in the input box).");
  if (!d.ok && (d.code === "not-bracketed" || d.code === "bracketed-unknown")) { msg += " Warning: " + d.why; pendingPaste.anyway = true; }
  else if (!d.ok) { result("Not pasted: " + d.why); pendingPaste = null; return; }
  $("staging-text").textContent = msg;
  openPanel("staging", "staging-confirm");
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
  setStaged({ kind: d.enter ? "submitted" : "staged", lane, chars: d.pasted });
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
  if (k) { e.preventDefault(); push({ key: k }); if (k === "Enter" && staged && staged.kind === "staged") setStaged(Object.assign({}, staged, { kind: "submitted" })); }
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
    b.addEventListener("click", () => {
      push({ key: b.dataset.key });
      if (b.dataset.key === "Enter" && staged && staged.kind === "staged") setStaged(Object.assign({}, staged, { kind: "submitted" }));
    });
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
  $("send").addEventListener("click", () => stagePaste(false));
  $("send-enter").addEventListener("click", () => stagePaste(true));
  $("compose").addEventListener("input", composeSize);
  $("staging-confirm").addEventListener("click", async () => {
    const p = pendingPaste; closePanel("staging"); pendingPaste = null;
    if (p && await sendPaste(p.text, p.enter, p.anyway)) { $("compose").value = ""; composeSize(); }
  });
  $("staging-cancel").addEventListener("click", () => { pendingPaste = null; closePanel("staging"); result("Nothing was pasted."); });
  $("staging").addEventListener("keydown", (e) => { if (e.key === "Escape") { pendingPaste = null; closePanel("staging"); } });
  $("inbox-submit").addEventListener("click", () => { push({ key: "Enter" }); setStaged(Object.assign({}, staged, { kind: "submitted" })); });
  $("inspect").addEventListener("click", () => {
    const on = $("inspect-panel").hidden; $("inspect-panel").hidden = !on; $("inspect").setAttribute("aria-pressed", String(on));
    loadInspect();
  });
  $("recover").addEventListener("click", (e) => recoverPreview(e.currentTarget));
  $("gone-recover").addEventListener("click", (e) => recoverPreview(e.currentTarget));
  $("recover-apply").addEventListener("click", recoverApply);
  $("recover-close").addEventListener("click", () => closePanel("recover-panel"));
  $("recover-panel").addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel("recover-panel"); });
  await loadLanes();
  setInterval(() => { if (!document.hidden) { loadLanes().catch(() => {}); loadInspect().catch(() => {}); } }, 4000);
  poll();
});
