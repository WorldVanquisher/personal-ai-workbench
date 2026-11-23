"use strict";
// Read-only round board: GET only. Every value goes into the page as text (textContent), never as HTML.
(function () {
  const $ = (id) => document.getElementById(id);
  function show(err) { const e = $("error"); e.hidden = !err; e.textContent = err || ""; }
  async function get(path) {
    const r = await fetch(path, { credentials: "same-origin" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    return d;
  }
  function cell(tr, text, cls) {
    const td = document.createElement("td");
    td.textContent = text === null || text === undefined ? "-" : String(text);
    if (cls) td.className = cls;
    tr.appendChild(td);
    return td;
  }
  function totals(q) {
    if (!q) return "-";
    if (q.error) return q.error;
    return "done " + (q.done || 0) + " / todo " + (q.todo || 0) + (q.next ? " · next " + q.next : "");
  }
  let lanes = [];
  function renderLanes(rows) {
    const by = {};
    (rows || []).forEach((x) => { by[x.lane] = x; });
    const tb = $("lanes").querySelector("tbody");
    tb.textContent = "";
    lanes.forEach((ln) => {
      const x = by[ln.name] || {};
      const tr = document.createElement("tr");
      cell(tr, ln.name, "mono");
      const st = cell(tr, x.state || "未查", x.state ? "state-" + x.state : "");
      if (x.why) st.title = x.why;
      cell(tr, x.window || ln.tmux_target, "mono");
      cell(tr, x.worktree);
      cell(tr, x.checkpoint ? x.checkpoint + (x.restart ? " (RESTART)" : "") : "-");
      cell(tr, totals(x.queue));
      cell(tr, ln.session_name + (x.name_ok === false ? " ≠ " + x.agent_name : ""));
      cell(tr, ln.branch, "mono");
      tb.appendChild(tr);
    });
  }
  async function load() {
    try {
      $("state").textContent = "loading…";
      const b = await get("/api/round/board");
      lanes = b.lanes;
      const dl = $("round");
      dl.textContent = "";
      Object.entries(b.round).forEach(([k, v]) => {
        if (v === null || v === undefined) return;
        const dt = document.createElement("dt"); dt.textContent = k;
        const dd = document.createElement("dd"); dd.textContent = String(v);
        dl.appendChild(dt); dl.appendChild(dd);
      });
      const ul = $("decisions");
      ul.textContent = "";
      b.decisions.forEach((d) => {
        const li = document.createElement("li");
        li.textContent = d.id + " " + d.title + (d.text ? "：" + d.text : "") + "  [sha " + d.sha + ", " + d.sheet + "]" +
          (d.fields.recommendation ? "  推荐：" + d.fields.recommendation : "") + (d.fields.default ? "  默认：" + d.fields.default : "");
        ul.appendChild(li);
      });
      if (!b.decisions.length) { const li = document.createElement("li"); li.textContent = "（没有）"; ul.appendChild(li); }
      const ct = $("collect").querySelector("tbody");
      ct.textContent = "";
      ((b.collect && b.collect.lanes) || []).forEach((x) => {
        const tr = document.createElement("tr");
        cell(tr, x.lane, "mono"); cell(tr, x.checkpoint); cell(tr, x.sums, x.sums === "MISMATCH" ? "disagrees" : "");
        cell(tr, x.items); cell(tr, x.jev_verdict, String(x.jev_verdict).startsWith("DISAGREES") ? "disagrees" : "");
        cell(tr, x.note);
        ct.appendChild(tr);
      });
      renderLanes(null);
      await refresh();
      show(null);
    } catch (e) { show(String(e.message || e)); }
  }
  async function refresh() {
    try {
      $("state").textContent = "reading tmux…";
      const s = await get("/api/round/status");
      renderLanes(s.lanes);
      $("state").textContent = "updated " + new Date().toLocaleTimeString();
    } catch (e) { show(String(e.message || e)); $("state").textContent = ""; }
  }
  $("refresh").addEventListener("click", refresh);
  load();
})();
