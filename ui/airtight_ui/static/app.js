// The airtight floor. Polls /api/snapshot and redraws when anything changed.
"use strict";

const $ = (sel) => document.querySelector(sel);
const store = {
  get(key, fallback) { try { return localStorage.getItem("at-ui:" + key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem("at-ui:" + key, value); } catch { /* private window */ } },
};

const ui = {
  tab: new URLSearchParams(location.search).get("tab") || store.get("tab", "statements"),
  stFilter: store.get("stFilter", "live"),
  stConcernsOnly: store.get("stConcernsOnly", "0") === "1",
  cFilter: store.get("cFilter", "open"),
  cAgent: store.get("cAgent", ""),
  drawer: null,            // {type, id}
  auto: store.get("auto", "1") === "1",
  interval: Number(store.get("interval", "0")) || null,
};
let data = null, lastText = "", lastOk = null, timer = null, logCache = [];

// ------------------------------------------------------------------ helpers

function h(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// Escaped text with S-n and C-n turned into links that open the drawer.
function linked(text) {
  return h(text).replace(/\b([SC])-(\d+)\b/g, (m, p, n) =>
    `<button class="ref" data-open="${p === "S" ? "statement" : "concern"}" data-id="${n}">${m}</button>`);
}
const agentsById = () => Object.fromEntries((data?.agents || []).map((a) => [a.id, a]));
function shortName(a) { return a ? a.name.split(" (")[0] : "—"; }
function title(a) { const m = a?.name.match(/\((.*)\)/); return m ? m[1] : a?.role || ""; }
function initials(a) { return shortName(a).slice(0, 1).toUpperCase(); }
function who(id) { return shortName(agentsById()[id]); }
function plural(n, word, many) { return `${n} ${n === 1 ? word : many || word + "s"}`; }
function clip(text, n = 140) { text = String(text || "").split("\n")[0]; return text.length > n ? text.slice(0, n - 1) + "…" : text; }
function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
function time(iso) { return iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : ""; }

const AGENT_TONE = {
  working: "s-working", needed: "s-critical", "up next": "s-warning", "has work": "s-serious",
  done: "s-good", skipped: "s-muted", benched: "s-muted", "off phase": "s-muted",
};
const STATEMENT_TONE = { agreed: "s-good", pending: "s-warning", superseded: "s-muted", cancelled: "s-muted" };
const STAGE_TONE = { "to answer": "s-serious", "to judge": "s-warning", "awaiting Peter": "s-info", changed: "s-good", kept: "s-muted", closed: "s-muted" };
const OUTCOME_TONE = { ok: "s-good", refused: "s-warning", usage: "s-serious", error: "s-critical" };
const pill = (text, tone) => `<span class="pill ${tone || ""}">${h(text)}</span>`;

function stageText(c) {
  if (c.stage === "to answer") return `with ${who(c.with)} to answer`;
  if (c.stage === "to judge") return `${who(c.with)} to judge`;
  if (c.stage === "awaiting Peter") return "closed · awaiting Peter";
  if (c.stage === "changed") return "closed · changed";
  if (c.stage === "kept") return "closed · kept as is";
  return c.stage;
}

// ------------------------------------------------------------------ polling

async function poll() {
  try {
    const res = await fetch("/api/snapshot", { cache: "no-store" });
    const text = await res.text();
    if (!res.ok) throw new Error(JSON.parse(text).detail || res.statusText);
    lastOk = new Date();
    setLive(true);
    $("#banner").classList.remove("error");
    if (text !== lastText) {
      lastText = text;
      data = JSON.parse(text);
      render();
    }
    if (ui.tab === "log") await loadLog();
  } catch (err) {
    setLive(false, err.message);
  }
}

async function loadLog() {
  try {
    const res = await fetch("/api/log?limit=150", { cache: "no-store" });
    const entries = await res.json();
    if (JSON.stringify(entries) !== JSON.stringify(logCache)) {
      logCache = entries;
      if (ui.tab === "log") renderTab();
    }
  } catch { /* the next poll tries again */ }
}

function setLive(ok, message) {
  const live = $("#live");
  live.classList.toggle("ok", ok);
  live.classList.toggle("err", !ok);
  if (!ok) {
    $("#live-text").textContent = "disconnected";
    const banner = $("#banner");
    banner.hidden = false;
    banner.className = "banner error";
    banner.textContent = message || "Cannot reach the server.";
  }
}

function tickLive() {
  if (lastOk && $("#live").classList.contains("ok")) {
    $("#live-text").textContent = ui.auto ? `live · updated ${ago(lastOk.toISOString())}` : `paused · updated ${ago(lastOk.toISOString())}`;
  }
}

function schedule() {
  clearInterval(timer);
  if (ui.auto) timer = setInterval(poll, (ui.interval || 3) * 1000);
}

// ------------------------------------------------------------------ render

function render() {
  if (!data) return;
  const st = data.state;
  $("#db-path").textContent = st.db;
  $("#db-path").title = st.db;
  $("#phases").innerHTML = data.phases.map((p) => {
    const cls = p.number === st.phase ? "current" : p.number < st.phase ? "done" : "";
    return `<li class="${cls}" title="${h(p.ends_when)}">${p.number} · ${h(p.name)}</li>`;
  }).join("");
  renderBanner();
  renderKpis();
  renderRoundStrip();
  renderFloor();
  renderTab();
  renderDrawer();
  tickLive();
}

function renderBanner() {
  const st = data.state, banner = $("#banner");
  let html = "";
  if (st.status === "paused") {
    const ref = st.paused_reason === "report" ? `report P-${st.paused_ref}` : `C-${st.paused_ref}`;
    html = `<b>Paused — waiting for you.</b> The loop is stopped on ${linked(ref)}. Run <span class="mono">/airtight:continue</span> to answer.`;
    banner.className = "banner paused";
  } else if (st.status === "converged") {
    html = `<b>Converged.</b> ${h(st.ends_when)}`;
    banner.className = "banner";
  } else if (st.report_due) {
    html = `<b>Report due this round.</b> Peter will pause the loop for you to read it.`;
    banner.className = "banner";
  }
  banner.hidden = !html;
  banner.innerHTML = html;
}

function renderKpis() {
  const st = data.state, by = st.statements_by_status || {};
  const live = (by.agreed || 0) + (by.pending || 0);
  const turn = st.current_turn;
  const liveDeliverables = data.deliverables.filter((d) => d.status === "live").length;
  const tiles = [
    ["Round", st.round, `phase ${st.phase} · ${st.phase_name}`],
    ["Turn now", turn ? `T-${turn.id}` : "—", turn ? `${who(turn.agent_id)} is working` : "none running"],
    ["Statements", live, `${by.agreed || 0} agreed · ${by.pending || 0} pending`],
    ["Open concerns", st.open_concerns, st.stuck.length ? `${st.stuck.length} near the stall limit` : `${data.concerns.length} raised in all`],
    ["Awaiting Peter", st.awaiting_peter, "closed, not yet acted on"],
    ["Turns taken", st.turns_total, st.tokens_total ? `${st.tokens_total.toLocaleString()} tokens` : `change mark ${st.change_mark}`],
    ["Deliverables", liveDeliverables, `${data.milestones.length} milestones`],
  ];
  $("#kpis").innerHTML = tiles.map(([label, value, sub]) =>
    `<div class="kpi"><div class="label">${h(label)}</div><div class="value">${h(value)}</div><div class="sub">${h(sub)}</div></div>`).join("");
}

function renderRoundStrip() {
  const st = data.state, byId = agentsById();
  const doneThisRound = {};
  for (const t of data.turns) if (t.round === st.round) doneThisRound[t.agent_id] = t.status;
  const steps = st.rotation.map((id) => {
    const status = doneThisRound[id] || "left";
    const glyph = { done: "✓", skipped: "–", running: "●", left: "○" }[status] || "○";
    const cls = status === "running" ? "working" : status;
    return `<span class="step ${cls}" data-tip="${h(byId[id]?.name)} — ${status === "left" ? "still to go" : status}">${glyph} ${h(shortName(byId[id]))}</span>`;
  });
  $("#round-strip").innerHTML = `<b>Round ${st.round}</b> ${steps.join('<span class="arrow">›</span>')}`;
}

function desk(a) {
  const st = data.state, s = a.stats;
  const working = st.current_turn && st.current_turn.agent_id === a.id;
  const cls = ["desk", a.status.replace(" ", "-"), working ? "working" : ""].join(" ");
  if (a.status === "benched" || a.status === "off phase") {
    const why = a.status === "benched" ? (a.join_trigger ? `Joins when ${a.join_trigger}` : "Not staffed.") : "Not on the floor this phase.";
    return `<button class="${cls} compact" data-open="agent" data-id="${a.id}">
      <div class="desk-top">
        <div class="avatar" aria-hidden="true">${h(initials(a))}</div>
        <div class="who"><div class="name">${h(shortName(a))}</div><div class="role">${h(title(a))}</div></div>
        ${pill(a.status, AGENT_TONE[a.status])}
      </div><div class="trigger">${h(why)}</div></button>`;
  }
  let bubble;
  if (working) bubble = `Taking turn T-${st.current_turn.id} — started ${ago(st.current_turn.started_at)}`;
  else if (a.status === "needed") bubble = "The loop is waiting for you.";
  else if (a.last_turn?.summary) bubble = a.last_turn.summary;
  const queue = Object.entries(a.queue || {}).map(([k, n]) =>
    `<span class="chip">${n} ${h(k.replace("_", " "))}</span>`).join("");
  return `<button class="${cls}" data-open="agent" data-id="${a.id}">
    <div class="desk-top">
      <div class="avatar" aria-hidden="true">${h(initials(a))}</div>
      <div class="who"><div class="name">${h(shortName(a))}</div><div class="role">${h(title(a))}</div></div>
      ${pill(a.status, AGENT_TONE[a.status])}
    </div>
    <div class="bubble ${bubble ? "" : "empty"}"><span>${h(bubble || "No turns yet.")}</span></div>
    ${queue ? `<div class="chips">${queue}</div>` : ""}
    <div class="desk-stats">
      <div data-tip="Concerns ${h(shortName(a))} raised (${s.concerns_raised_open} still open)"><b>${s.concerns_raised}</b><span>raised</span></div>
      <div data-tip="Concerns first addressed to ${h(shortName(a))}"><b>${s.concerns_received}</b><span>received</span></div>
      <div data-tip="Answers given (hand-offs not counted)"><b>${s.answers}</b><span>answers</span></div>
      <div data-tip="${a.id === 1 ? "Turns taken" : "Statements approved"}"><b>${a.id === 1 ? s.turns_done : s.approvals}</b><span>${a.id === 1 ? "turns" : "approved"}</span></div>
    </div>
  </button>`;
}

function renderFloor() {
  // Turn order first, then the human, then everyone not on the floor this phase.
  const away = (a) => a.status === "benched" || a.status === "off phase";
  const bySeq = (a, b) => a.seq - b.seq;
  const onFloor = data.agents.filter((a) => !away(a) && a.id !== 1).sort(bySeq);
  const human = data.agents.filter((a) => a.id === 1);
  const benched = data.agents.filter((a) => away(a) && a.id !== 1).sort(bySeq);
  $("#floor").innerHTML = [...onFloor, ...human].map(desk).join("")
    + (benched.length ? `<div class="bench-title">Not on the floor</div>${benched.map(desk).join("")}` : "");
}

// ------------------------------------------------------------------ tabs

function renderTab() {
  for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === ui.tab));
  const body = $("#tab-body"), scroll = body.scrollTop;
  const fn = { statements: tabStatements, concerns: tabConcerns, agents: tabAgents, timeline: tabTimeline, log: tabLog }[ui.tab];
  body.innerHTML = data ? fn() : "";
  body.scrollTop = scroll;
}

function seg(name, options, current) {
  return `<span class="seg">${options.map(([v, label]) =>
    `<button data-filter="${name}" data-value="${h(v)}" class="${v === current ? "on" : ""}">${h(label)}</button>`).join("")}</span>`;
}

function statementRow(s, child) {
  const st = data.state;
  const approverCount = s.approved_by.length + s.approvals_missing.length;
  const c = s.concerns;
  const retired = s.status === "superseded" || s.status === "cancelled";
  const supersedes = s.links_out.filter((l) => l.relation === "supersedes").map((l) => `S-${l.id}`);
  const supersededBy = s.links_in.filter((l) => l.relation === "supersedes").map((l) => `S-${l.id}`);
  const meta = [
    `<span class="kind">${h(s.kind_label)}</span>`,
    `round ${s.created_round}`,
    s.status === "pending" ? `${s.approved_by.length}/${approverCount} approved` : "",
    supersedes.length ? `replaces ${linked(supersedes.join(", "))}` : "",
    supersededBy.length ? `replaced by ${linked(supersededBy.join(", "))}` : "",
    s.created_round === st.round && !retired ? `<span class="new">new</span>` : "",
  ].filter(Boolean).join(" · ");
  let concernPill = "";
  if (c.open) concernPill = pill(plural(c.open, "open concern"), "s-serious");
  else if (c.awaiting_peter) concernPill = pill(`${c.awaiting_peter} awaiting Peter`, "s-info");
  else if (c.total) concernPill = `<span class="chip">${plural(c.total, "concern")}</span>`;
  return `<div class="row ${retired ? "retired" : ""} ${child ? "child" : ""}" data-open="statement" data-id="${s.id}">
    <span class="id">S-${s.id}</span>
    <div class="text"><div>${h(s.text)}</div><div class="meta">${meta}</div></div>
    <div class="right">${pill(s.status, STATEMENT_TONE[s.status])}${concernPill}</div>
  </div>`;
}

function tabStatements() {
  const live = (s) => s.status === "pending" || s.status === "agreed";
  let list = data.statements.filter((s) => ui.stFilter === "all" || live(s));
  if (ui.stConcernsOnly) list = list.filter((s) => s.concerns.total);
  const filters = `<div class="filters">${seg("stFilter", [["live", "In force"], ["all", "All, with history"]], ui.stFilter)}
    ${seg("stConcernsOnly", [["0", "Any"], ["1", "With concerns"]], ui.stConcernsOnly ? "1" : "0")}
    <span class="muted">${plural(list.length, "statement")}</span></div>`;
  if (!list.length) return filters + `<div class="empty-state">No statements match.</div>`;

  const shown = new Set(list.map((s) => s.id));
  const project = list.filter((s) => s.level === "project" || !s.deliverable_id);
  const out = [];
  // Goals with the criteria that measure them nested underneath.
  const measures = {};
  for (const s of list) for (const l of s.links_out) if (l.relation === "measures") (measures[l.id] ||= []).push(s);
  const nested = new Set(Object.values(measures).flat().map((s) => s.id));
  if (project.length) {
    out.push(`<div class="group-title">Project-wide <span class="muted">goals, criteria, invariants</span></div>`);
    for (const s of project) {
      if (nested.has(s.id) && [...s.links_out].some((l) => l.relation === "measures" && shown.has(l.id))) continue;
      out.push(statementRow(s));
      for (const m of measures[s.id] || []) out.push(statementRow(m, true));
    }
  }
  for (const d of data.deliverables) {
    const rows = list.filter((s) => s.deliverable_id === d.id);
    if (!rows.length) continue;
    const tag = d.status === "live" ? "" : ` <span class="muted">(${h(d.status)})</span>`;
    const agreed = rows.filter((s) => s.status === "agreed").length;
    out.push(`<div class="group-title">D-${d.id} · ${h(d.name)}${tag} <span class="muted">${agreed}/${rows.filter(live).length} agreed</span></div>`);
    out.push(...rows.map((s) => statementRow(s)));
  }
  return filters + out.join("");
}

function concernRow(c) {
  const route = c.raised_to !== c.addressed_to && c.status === "open"
    ? `${who(c.raised_by)} → ${who(c.raised_to)} → ${who(c.addressed_to)}`
    : `${who(c.raised_by)} → ${who(c.raised_to)}`;
  const meta = [
    `<span class="kind">${h(c.kind)}</span>`,
    `on ${linked(c.on)}`,
    h(route),
    `round ${c.round}`,
    c.answer_count ? plural(c.answer_count, "answer") : "no answer yet",
    c.replies_total ? plural(c.replies_total, "reply", "replies") : "",
  ].filter(Boolean).join(" · ");
  return `<div class="row" data-open="concern" data-id="${c.id}">
    <span class="id">C-${c.id}</span>
    <div class="text"><div>${h(clip(c.body, 220))}</div><div class="meta">${meta}</div></div>
    <div class="right">${pill(stageText(c), STAGE_TONE[c.stage])}</div>
  </div>`;
}

function tabConcerns() {
  const match = {
    open: (c) => c.status === "open",
    peter: (c) => c.stage === "awaiting Peter",
    closed: (c) => c.status === "closed",
    all: () => true,
  }[ui.cFilter] || (() => true);
  const agent = Number(ui.cAgent) || 0;
  const list = data.concerns.filter(match).filter((c) => !agent || [c.raised_by, c.raised_to, c.addressed_to].includes(agent)).reverse();
  const options = [`<option value="">everyone</option>`, ...data.agents.filter((a) => a.stats.concerns_raised || a.stats.concerns_received)
    .map((a) => `<option value="${a.id}" ${a.id === agent ? "selected" : ""}>${h(shortName(a))}</option>`)].join("");
  const counts = { open: data.concerns.filter((c) => c.status === "open").length, peter: data.state.awaiting_peter };
  const filters = `<div class="filters">${seg("cFilter", [["open", `Open (${counts.open})`], ["peter", `Awaiting Peter (${counts.peter})`], ["closed", "Closed"], ["all", "All"]], ui.cFilter)}
    <label class="muted">involving <select id="c-agent">${options}</select></label></div>`;
  if (!list.length) return filters + `<div class="empty-state">No concerns here.</div>`;
  return filters + list.map(concernRow).join("");
}

function tabAgents() {
  const rows = [...data.agents].sort((a, b) => a.seq - b.seq);
  const table = `<div class="table-scroll"><table>
    <thead><tr><th>Agent</th><th>Status</th><th>Turns</th><th>Skipped</th><th>Raised</th><th>Still open</th>
      <th>Received</th><th>Holding</th><th>Answers</th><th>Hand-offs</th><th>Approvals</th><th>Written</th><th>Tokens</th></tr></thead>
    <tbody>${rows.map((a) => {
      const s = a.stats;
      return `<tr class="clickable" data-open="agent" data-id="${a.id}"><td>${h(shortName(a))} <span class="muted">${h(a.role)}</span></td>
        <td>${pill(a.status, AGENT_TONE[a.status])}</td><td>${s.turns_done}</td><td>${s.turns_skipped}</td>
        <td>${s.concerns_raised}</td><td>${s.concerns_raised_open}</td><td>${s.concerns_received}</td><td>${s.concerns_holding}</td>
        <td>${s.answers}</td><td>${s.handoffs}</td><td>${s.approvals}</td><td>${s.statements_written}</td><td>${s.tokens ? s.tokens.toLocaleString() : "—"}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  return `<h3 style="margin-top:0">Per agent</h3>${table}<h3>Who raises concerns to whom</h3>${matrix()}`;
}

function matrix() {
  if (!data.matrix.length) return `<div class="empty-state">No concerns raised yet.</div>`;
  const byId = agentsById();
  const from = [...new Set(data.matrix.map((m) => m.from))].sort((a, b) => byId[a].seq - byId[b].seq);
  const to = [...new Set(data.matrix.map((m) => m.to))].sort((a, b) => byId[a].seq - byId[b].seq);
  const n = Object.fromEntries(data.matrix.map((m) => [`${m.from}:${m.to}`, m.n]));
  const max = Math.max(...data.matrix.map((m) => m.n));
  const step = (v) => (v ? Math.max(1, Math.ceil((v / max) * 6)) : 0);
  const body = from.map((f) => `<tr><th scope="row" style="text-align:left">${h(shortName(byId[f]))}</th>${to.map((t) => {
    const v = n[`${f}:${t}`] || 0, k = step(v);
    const ink = k >= 4 ? "color:#fff" : "";
    return `<td class="cell" style="background:var(--seq-${k});${ink}" data-tip="${h(shortName(byId[f]))} raised ${plural(v, "concern")} to ${h(shortName(byId[t]))}">${v || ""}</td>`;
  }).join("")}<td><b>${from.length && to.reduce((sum, t) => sum + (n[`${f}:${t}`] || 0), 0)}</b></td></tr>`).join("");
  const scale = [1, 2, 3, 4, 5, 6].map((k) => `<i style="background:var(--seq-${k})"></i>`).join("");
  return `<div class="table-scroll"><table class="matrix">
    <thead><tr><th>raised by ↓ · to →</th>${to.map((t) => `<th class="col">${h(shortName(byId[t]))}</th>`).join("")}<th class="col">total</th></tr></thead>
    <tbody>${body}</tbody></table></div>
    <div class="legend-scale">fewer ${scale} more <span style="margin-left:8px">· counted against whom each concern was first addressed to</span></div>`;
}

function barChart(titleText, key) {
  const rows = data.per_round;
  const max = Math.max(1, ...rows.map((r) => r[key]));
  const w = 300, hgt = 80, gap = 2, bw = Math.min(24, Math.max(2, w / rows.length - gap));
  const bars = rows.map((r, i) => {
    const bh = (r[key] / max) * (hgt - 4);
    const x = i * (bw + gap), y = hgt - bh;
    // A 4px rounded top anchored to the baseline: a path, not a fully rounded rect.
    const rad = Math.min(4, bw / 2, bh);
    const d = bh ? `M${x},${hgt} V${y + rad} Q${x},${y} ${x + rad},${y} H${x + bw - rad} Q${x + bw},${y} ${x + bw},${y + rad} V${hgt} Z` : "";
    return `<g data-tip="Round ${r.round}: ${r[key]}"><rect x="${x}" y="0" width="${bw + gap}" height="${hgt}" fill="transparent"></rect>${d ? `<path class="bar" d="${d}"></path>` : ""}</g>`;
  }).join("");
  const total = rows.reduce((s, r) => s + r[key], 0);
  return `<div class="chart"><div class="title">${h(titleText)} <b>${total}</b></div>
    <svg viewBox="0 0 ${w} ${hgt + 14}" preserveAspectRatio="none" role="img" aria-label="${h(titleText)} per round">
      ${bars}<line class="axis" x1="0" x2="${w}" y1="${hgt}" y2="${hgt}"></line>
      <text x="0" y="${hgt + 12}">round 1</text><text x="${w}" y="${hgt + 12}" text-anchor="end">round ${rows.length}</text>
    </svg></div>`;
}

function tabTimeline() {
  const charts = `<div class="charts">${barChart("Statements written per round", "statements")}${barChart("Concerns raised per round", "concerns")}
    ${barChart("Answers given per round", "answers")}${barChart("Statements agreed per round", "agreed")}</div>`;
  let out = "", round = null;
  for (const t of data.turns) {
    if (t.round !== round) { round = t.round; out += `<div class="round-head">Round ${round}</div>`; }
    const status = t.status === "running" ? pill("working", "s-working") : t.status === "skipped" ? pill("skipped", "s-muted") : "";
    out += `<div class="turn ${t.status}"><div class="when">T-${t.id}<br>${time(t.started_at)}</div>
      <div><b>${h(who(t.agent_id))}</b> ${status} <span class="muted">phase ${t.phase}${t.tokens ? ` · ${t.tokens.toLocaleString()} tokens` : ""}</span>
      <div>${t.summary ? linked(t.summary) : t.status === "skipped" ? '<span class="muted">Nothing in the queue.</span>' : ""}</div></div></div>`;
  }
  return charts + (out || `<div class="empty-state">No turns yet.</div>`);
}

function tabLog() {
  if (!logCache.length) return `<div class="empty-state">No command log yet (looked for log.jsonl next to the database).</div>`;
  return logCache.map((e) => {
    const out = [e.stdout, e.stderr].filter(Boolean).join("\n");
    return `<details class="log-entry"><summary>
      <span class="mono muted">${time(e.at)}</span> ${pill(e.outcome, OUTCOME_TONE[e.outcome])}
      <b class="mono">${h(e.tool)}</b> <span class="mono">${h(clip((e.args || []).join(" "), 120))}</span>
      <span class="muted">${e.agent ? h(e.agent) + " · " : ""}round ${h(e.round)}${e.turn ? ` · T-${h(e.turn)}` : ""}</span>
    </summary><pre>${h(out || "(no output)")}</pre></details>`;
  }).join("");
}

// ------------------------------------------------------------------ drawer

// The open drawer lives in the URL hash (#concern/7), so it survives a reload and can be linked.
function openDrawer(type, id) { location.hash = `${type}/${id}`; }
function closeDrawer() { if (location.hash) history.pushState("", "", location.pathname + location.search); readHash(); }
function readHash() {
  const m = location.hash.match(/^#(statement|concern|agent)\/(\d+)$/);
  ui.drawer = m ? { type: m[1], id: Number(m[2]) } : null;
  renderDrawer();
}
addEventListener("hashchange", readHash);

function renderDrawer() {
  const open = !!(ui.drawer && data);
  $("#drawer").hidden = !open;
  $("#scrim").hidden = !open;
  if (!open) return;
  const { type, id } = ui.drawer;
  const fn = { statement: drawerStatement, concern: drawerConcern, agent: drawerAgent }[type];
  const [titleHtml, bodyHtml] = fn(id);
  const body = $("#drawer-body"), scroll = body.scrollTop;
  $("#drawer-title").innerHTML = titleHtml;
  body.innerHTML = bodyHtml;
  body.scrollTop = scroll;
}

function thread(c, expanded = true) {
  const msgs = [`<div class="msg raise${c.kind === "staffing" ? " mono" : ""}"><div class="by"><b>${h(who(c.raised_by))}</b> raised it to ${h(who(c.raised_to))} · round ${c.round}</div>${linked(c.body)}</div>`];
  for (const a of c.answers) {
    if (a.kind === "reassign") {
      msgs.push(`<div class="msg answer"><div class="by"><b>${h(who(a.answered_by))}</b> handed it to ${h(who(a.reassigned_to))} · round ${a.round}</div>${linked(a.body)}</div>`);
      continue;
    }
    const verdict = a.verdict ? pill(a.verdict, a.verdict === "replied" ? "s-warning" : "s-good")
      : c.status === "open" ? pill(`${who(c.raised_by)} to judge`, "s-warning") : "";
    msgs.push(`<div class="msg answer"><div class="by"><b>${h(who(a.answered_by))}</b> answered · round ${a.round} ${verdict}</div>${linked(a.body)}</div>`);
    if (a.reply) msgs.push(`<div class="msg reply"><div class="by"><b>${h(who(c.raised_by))}</b> replied · round ${a.verdict_round ?? ""}</div>${linked(a.reply)}</div>`);
  }
  if (c.stage === "to answer" && c.answers.every((a) => a.kind === "reassign" || a.verdict)) {
    msgs.push(`<div class="msg answer muted">Waiting for ${h(who(c.addressed_to))} to answer.</div>`);
  }
  return `<div class="thread">
    <div class="thread-head"><button class="ref" data-open="concern" data-id="${c.id}">C-${c.id}</button>
      <span class="kind">${h(c.kind)}</span> ${pill(stageText(c), STAGE_TONE[c.stage])}</div>
    ${expanded ? msgs.join("") : `<div class="msg raise">${linked(clip(c.body, 200))}</div>`}
  </div>`;
}

function drawerStatement(id) {
  const s = data.statements.find((x) => x.id === id);
  if (!s) return [`S-${id}`, `<div class="empty-state">Not found.</div>`];
  const concerns = data.concerns.filter((c) => c.statement_id === id);
  const d = data.deliverables.find((x) => x.id === s.deliverable_id);
  const links = [
    ...s.links_out.map((l) => `${l.relation} S-${l.id}`),
    ...s.links_in.map((l) => `S-${l.id} ${l.relation} this`),
  ];
  const body = `
    <div class="statement-text">${h(s.text)}</div>
    ${s.rationale ? `<div class="muted">${linked(s.rationale)}</div>` : ""}
    <dl class="kv">
      <dt>Status</dt><dd>${pill(s.status, STATEMENT_TONE[s.status])}</dd>
      <dt>Kind</dt><dd>${h(s.kind_label)} <span class="muted">(${h(s.kind)})</span></dd>
      <dt>Deliverable</dt><dd>${d ? `D-${d.id} · ${h(d.name)} <span class="muted">${h(d.status)}</span>` : "project-wide"}</dd>
      <dt>Written</dt><dd>round ${s.created_round}${s.retired_round ? ` · retired round ${s.retired_round}` : ""} · T-${s.turn_id}</dd>
      <dt>Approved by</dt><dd>${s.approved_by.map(who).map(h).join(", ") || "nobody yet"}</dd>
      ${s.approvals_missing.length ? `<dt>Still to approve</dt><dd>${s.approvals_missing.map(who).map(h).join(", ")}</dd>` : ""}
      ${links.length ? `<dt>Links</dt><dd>${linked(links.join(" · "))}</dd>` : ""}
      ${s.milestone_id ? `<dt>Milestone</dt><dd>M-${s.milestone_id}</dd>` : ""}
    </dl>
    <h3>Concerns (${concerns.length})</h3>
    ${concerns.length ? concerns.map((c) => thread(c)).join("") : `<div class="muted">None raised on this statement.</div>`}`;
  return [h(s.label), body];
}

function drawerConcern(id) {
  const c = data.concerns.find((x) => x.id === id);
  if (!c) return [`C-${id}`, `<div class="empty-state">Not found.</div>`];
  const s = c.statement_id && data.statements.find((x) => x.id === c.statement_id);
  const body = `
    ${s ? `<dl class="kv"><dt>On</dt><dd>${linked(`S-${s.id}`)} ${h(s.text)}</dd></dl>` : `<dl class="kv"><dt>On</dt><dd>milestone M-${c.milestone_id}</dd></dl>`}
    <dl class="kv">
      <dt>Raised by</dt><dd>${h(who(c.raised_by))}, round ${c.round}</dd>
      <dt>Raised to</dt><dd>${h(who(c.raised_to))}${c.addressed_to !== c.raised_to ? ` · now with ${h(who(c.addressed_to))}` : ""}</dd>
      <dt>Replies</dt><dd>${c.replies_total} in all · ${c.replies_since_reassign} since the last hand-off</dd>
      ${c.closed_round ? `<dt>Closed</dt><dd>round ${c.closed_round}${c.acted_on ? ` · Peter ${c.acted_on === "kept" ? "kept the statement as is" : "changed statements"} in round ${c.acted_round}` : ""}</dd>` : ""}
    </dl>
    ${thread(c)}`;
  return [`<span class="id">C-${c.id}</span> ${h(c.kind)}`, body];
}

function bars(entries) {
  if (!entries.length) return `<div class="muted">None.</div>`;
  const max = Math.max(...entries.map(([, n]) => n));
  return `<div class="bars-inline">${entries.map(([label, n]) =>
    `<span>${h(label)}</span><span class="track"><span class="fill" style="width:${(n / max) * 100}%;display:block"></span></span><span class="n">${n}</span>`).join("")}</div>`;
}

function drawerAgent(id) {
  const a = data.agents.find((x) => x.id === id);
  if (!a) return [`agent ${id}`, ""];
  const s = a.stats;
  const byRole = Object.fromEntries(data.agents.map((x) => [x.role, x]));
  const raisedTo = Object.entries(s.raised_to).map(([role, n]) => [shortName(byRole[role]), n]).sort((x, y) => y[1] - x[1]);
  const receivedFrom = data.matrix.filter((m) => m.to === id).map((m) => [who(m.from), m.n]).sort((x, y) => y[1] - x[1]);
  const holding = data.concerns.filter((c) => c.with === id);
  const raised = data.concerns.filter((c) => c.raised_by === id).reverse();
  const turns = data.turns.filter((t) => t.agent_id === id).slice(0, 12);
  const queue = Object.entries(a.queue || {}).map(([k, n]) => `<span class="chip">${n} ${h(k.replace("_", " "))}</span>`).join(" ");
  const body = `
    <p class="muted">${h(a.motivation)}</p>
    <dl class="kv">
      <dt>Status</dt><dd>${pill(a.status, AGENT_TONE[a.status])} ${a.approver ? '<span class="chip">approver</span>' : ""}</dd>
      <dt>Joined</dt><dd>${a.active ? (a.joined_round ? `round ${a.joined_round}` : "from the start") : `not staffed${a.join_trigger ? ` · joins when ${h(a.join_trigger)}` : ""}`}</dd>
      ${a.duties.length ? `<dt>Duties</dt><dd>${a.duties.map((d) => `${h(d.relation)} ${h(d.duty)}`).join(", ")}</dd>` : ""}
      ${queue ? `<dt>Queue</dt><dd>${queue}</dd>` : ""}
      <dt>Turns</dt><dd>${s.turns_done} taken · ${s.turns_skipped} skipped${s.tokens ? ` · ${s.tokens.toLocaleString()} tokens` : ""}</dd>
      <dt>Concerns</dt><dd>${s.concerns_raised} raised (${s.concerns_raised_open} open) · ${s.concerns_received} received · ${s.concerns_holding} waiting on them</dd>
      <dt>Answers</dt><dd>${s.answers} given · ${s.handoffs} handed off</dd>
      <dt>Approvals</dt><dd>${s.approvals}</dd>
      ${s.statements_written ? `<dt>Statements written</dt><dd>${s.statements_written}</dd>` : ""}
      ${Object.keys(s.verdicts_given).length ? `<dt>Verdicts given</dt><dd>${Object.entries(s.verdicts_given).map(([k, n]) => `${n} ${h(k)}`).join(" · ")}</dd>` : ""}
    </dl>
    <h3>Raises concerns to</h3>${bars(raisedTo)}
    <h3>Receives concerns from</h3>${bars(receivedFrom)}
    <h3>Kinds raised</h3>${bars(Object.entries(s.raised_by_kind).sort((x, y) => y[1] - x[1]))}
    <h3>Waiting on ${h(shortName(a))} (${holding.length})</h3>
    ${holding.length ? holding.map(concernRow).join("") : `<div class="muted">Nothing.</div>`}
    <h3>Raised by ${h(shortName(a))} (${raised.length})</h3>
    ${raised.length ? raised.map(concernRow).join("") : `<div class="muted">Nothing yet.</div>`}
    <h3>Recent turns</h3>
    ${turns.map((t) => `<div class="turn ${t.status}"><div class="when">T-${t.id}<br>round ${t.round}</div><div>${t.summary ? linked(t.summary) : `<span class="muted">${h(t.status)}</span>`}</div></div>`).join("") || `<div class="muted">None.</div>`}`;
  return [`<span class="avatar" style="width:28px;height:28px;font-size:13px;display:inline-grid;vertical-align:middle">${h(initials(a))}</span> ${h(a.name)}`, body];
}

// ------------------------------------------------------------------ events

document.addEventListener("click", (e) => {
  const opener = e.target.closest("[data-open]");
  if (opener) { e.stopPropagation(); openDrawer(opener.dataset.open, opener.dataset.id); return; }
  const tab = e.target.closest("#tabs button");
  if (tab) {
    ui.tab = tab.dataset.tab; store.set("tab", ui.tab);
    $("#tab-body").scrollTop = 0;
    renderTab();
    if (ui.tab === "log") loadLog();
    return;
  }
  const filter = e.target.closest("[data-filter]");
  if (filter) {
    const { filter: key, value } = filter.dataset;
    if (key === "stConcernsOnly") ui[key] = value === "1"; else ui[key] = value;
    store.set(key, value);
    renderTab();
  }
});
document.addEventListener("change", (e) => {
  if (e.target.id === "c-agent") { ui.cAgent = e.target.value; store.set("cAgent", ui.cAgent); renderTab(); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
$("#drawer-close").onclick = closeDrawer;
$("#scrim").onclick = closeDrawer;
$("#refresh-now").onclick = () => poll();
$("#auto").onchange = (e) => { ui.auto = e.target.checked; store.set("auto", ui.auto ? "1" : "0"); schedule(); tickLive(); };
$("#interval").onchange = (e) => { ui.interval = Number(e.target.value); store.set("interval", e.target.value); schedule(); };
$("#theme").onclick = () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  store.set("theme", document.documentElement.dataset.theme);
};

const tip = $("#tooltip");
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest("[data-tip]");
  if (!el) { tip.hidden = true; return; }
  tip.textContent = el.dataset.tip;
  tip.hidden = false;
});
document.addEventListener("mousemove", (e) => {
  if (tip.hidden) return;
  const x = Math.min(e.clientX + 12, innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + "px";
  tip.style.top = e.clientY + 14 + "px";
});

(async function start() {
  const theme = store.get("theme", "");
  if (theme) document.documentElement.dataset.theme = theme;
  $("#auto").checked = ui.auto;
  try {
    const config = await (await fetch("/api/config")).json();
    ui.interval ||= config.refresh_seconds;
  } catch { ui.interval ||= 3; }
  const select = $("#interval");
  if (![...select.options].some((o) => Number(o.value) === ui.interval)) {
    select.add(new Option(`${ui.interval}s`, String(ui.interval)));
  }
  select.value = String(ui.interval);
  readHash();
  await poll();
  schedule();
  setInterval(tickLive, 1000);
})();
