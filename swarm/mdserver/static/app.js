// PAI Markdown server (experimental). Plain DOM, no external code. Every write sends the
// session's CSRF token, and a save names the exact revision the operator confirmed.
"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "", cur = null, dirty = false, timer = null;

async function api(method, url, body) {
  const opt = { method, headers: { "X-PAI-CSRF": csrf }, credentials: "same-origin" };
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  const data = await r.json().catch(() => ({}));
  return { status: r.status, data };
}
function setDirty(v) {
  dirty = v;
  $("state").textContent = !cur ? "" : (v ? "Unsaved changes" : "Saved");
  $("state").className = "state" + (v ? " dirty" : "");
  $("save").disabled = !cur || !v;
}
function showError(msg) { const e = $("error"); e.textContent = msg || ""; e.hidden = !msg; }
async function preview() {
  const r = await api("POST", "/api/preview", { text: $("text").value });
  if (r.status === 200) $("preview").innerHTML = r.data.html; // server-rendered, every source character escaped
}
async function loadFiles() {
  const r = await api("GET", "/api/files?root=" + $("root").value);
  const ul = $("files"); ul.textContent = "";
  const f = $("filter").value.toLowerCase();
  for (const p of (r.data.files || []).filter((x) => x.toLowerCase().includes(f))) {
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button"; b.textContent = p; b.addEventListener("click", () => openFile(p));
    if (cur && cur.path === p && String(cur.root) === $("root").value) b.setAttribute("aria-current", "true");
    li.appendChild(b); ul.appendChild(li);
  }
}
async function openFile(path) {
  if (dirty && !confirm("Discard unsaved changes to " + cur.path + "?")) return;
  const r = await api("GET", "/api/file?root=" + $("root").value + "&path=" + encodeURIComponent(path));
  if (r.status !== 200) return showError(r.data.error);
  showError(""); $("stale").hidden = true;
  cur = r.data; $("text").value = cur.text; $("text").disabled = false;
  $("path").textContent = $("root").selectedOptions[0].textContent + "/" + cur.path;
  setDirty(false); preview(); loadFiles();
}
async function review() {
  const r = await api("POST", "/api/diff", { root: cur.root, path: cur.path, text: $("text").value, base: cur.base });
  if (r.status !== 200) return showError(r.data.error);
  if (r.data.stale) { $("stale").hidden = false; return; }
  const pre = $("diff"); pre.textContent = "";
  for (const line of r.data.diff.slice(2)) {
    const s = document.createElement("span");
    if (line.startsWith("+")) { s.className = "add"; s.textContent = "+ " + line.slice(1); }
    else if (line.startsWith("-")) { s.className = "del"; s.textContent = "− " + line.slice(1); }
    else if (line.startsWith("@@")) { s.className = "hunk"; s.textContent = line; }
    else { s.textContent = "  " + line.slice(1); }
    pre.appendChild(s); pre.appendChild(document.createTextNode("\n"));
  }
  if (!r.data.diff.length) pre.textContent = "(no changes)";
  $("dialog-path").textContent = $("root").selectedOptions[0].textContent + "/" + cur.path;
  $("confirm").dataset.revision = r.data.proposed;
  $("confirm").textContent = "Save revision " + r.data.proposed.slice(0, 8);
  $("dialog").hidden = false; $("confirm").focus();
}
async function save() {
  const text = $("text").value;
  const r = await api("POST", "/api/save", { root: cur.root, path: cur.path, text, base: cur.base,
    confirm: $("confirm").dataset.revision, crlf: cur.crlf, bom: cur.bom });
  $("dialog").hidden = true;
  if (r.status === 409) { $("stale").hidden = false; return; }
  if (r.status !== 200) return showError(r.data.error);
  cur.base = r.data.base; cur.text = text; setDirty(false); $("save").focus();
}
function view(which) {
  $("show-edit").setAttribute("aria-pressed", String(which === "edit"));
  $("show-preview").setAttribute("aria-pressed", String(which === "preview"));
  document.querySelector(".panes").className = "panes show-" + which;
  if (which === "preview") preview();
}
window.addEventListener("DOMContentLoaded", async () => {
  const r = await api("GET", "/api/session");
  if (r.status !== 200) { location.reload(); return; }
  csrf = r.data.csrf;
  r.data.roots.forEach((name, i) => { const o = document.createElement("option"); o.value = i; o.textContent = name; $("root").appendChild(o); });
  $("root").addEventListener("change", loadFiles);
  $("filter").addEventListener("input", loadFiles);
  $("text").addEventListener("input", () => { setDirty($("text").value !== cur.text); clearTimeout(timer); timer = setTimeout(preview, 400); });
  $("save").addEventListener("click", review);
  $("confirm").addEventListener("click", save);
  $("cancel").addEventListener("click", () => { $("dialog").hidden = true; $("save").focus(); });
  $("dialog").addEventListener("keydown", (e) => { if (e.key === "Escape") { $("dialog").hidden = true; $("save").focus(); } });
  $("copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("text").value); $("copy").textContent = "Copied"; }
    catch (e) { $("text").select(); $("copy").textContent = "Selected: press Ctrl+C"; }
  });
  $("reload").addEventListener("click", () => { dirty = false; $("stale").hidden = true; openFile(cur.path); });
  $("show-edit").addEventListener("click", () => view("edit"));
  $("show-preview").addEventListener("click", () => view("preview"));
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  view("edit"); loadFiles();
});
