"use strict";
/* ProspectScout UI. Vanilla JS, no build step. All dynamic text goes through h()/textContent. */

const $ = (sel, root = document) => root.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";
const ICONS = {
  copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  ext: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  mail: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
  chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  linkedin: '<path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/><rect width="4" height="12" x="2" y="9"/><circle cx="4" cy="4" r="2"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
};

function icon(name) {
  const s = document.createElementNS(SVGNS, "svg");
  s.setAttribute("class", "i");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/* Only http(s)/mailto links may become hrefs (blocks javascript: from imports or page content). */
function safeUrl(url) {
  const u = String(url || "").trim();
  return /^(https?:\/\/|mailto:)/i.test(u) ? u : null;
}

function svg(tag, attrs) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

async function api(path, body, method) {
  const opts = { method: method || (body !== undefined ? "POST" : "GET"), headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}

let toastTimer;
function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), bad ? 6000 : 2600);
}

async function copyText(text, what) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = h("textarea", { style: "position:fixed;opacity:0" }); ta.value = text; document.body.append(ta);
    ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast(`${what || "Text"} copied. Paste it and send it yourself.`);
}

/* ---------------------------------------------------------------- state */
const S = {
  view: "dashboard", state: null, stats: null, settings: null,
  filter: "all", outreach: "", search: "", detailId: null, detail: null, tab: "overview",
};
const FIT_LABEL = { strong: "Strong fit", possible: "Possible", skip: "Skip", manual: "Check manually" };
const OUTREACH = { new: "New", contacted: "Contacted", replied: "Replied", call_booked: "Call booked", won: "Won", lost: "Lost", skip: "Not pursuing" };
const FINDING = { broken: "Broken", missing: "Missing", competitor_gap: "Competitor gap", growth: "Growth idea", ai: "AI idea" };
const SIGNAL = { hiring_tech_roles: "Hiring tech roles", funding_news: "Funding / news", expansion_launch: "Expansion / launch",
  talks_tech_growth: "Talks tech / growth", changed_job_recently: "Changed job recently", active_on_linkedin: "Active on LinkedIn" };

/* ---------------------------------------------------------------- shell */
function renderShell() {
  const st = S.state; if (!st) return;
  const c = st.counts;
  $("#navCount").textContent = c.contacts;
  const cp = $("#claudePill");
  cp.replaceChildren(h("span", { class: "dot " + (st.claude_available ? "ok" : "bad") }),
    h("span", { text: st.claude_available ? "Claude ready" : "Claude not found" }));
  const w = st.worker, busy = c.queued + c.in_progress > 0;
  const wp = $("#workerPill");
  wp.replaceChildren(h("span", { class: "dot " + (w.paused ? "warn" : busy ? "busy" : "ok") }),
    h("span", { text: w.paused ? "Research paused" : busy ? `Researching (${c.queued + c.in_progress} left)` : "Research up to date" }));

  // step guide
  const researched = c.done, ready = (S.stats && S.stats.totals.messages_ready) || 0;
  const steps = [
    ["Add prospects", c.contacts > 0, c.contacts === 0],
    ["We research them", researched > 0 && !busy, busy],
    ["Copy your messages", false, ready > 0 && !busy],
  ];
  $("#steps").replaceChildren(...steps.flatMap(([label, done, now], i) => [
    i ? h("span", { class: "step-sep", "aria-hidden": "true", text: "→" }) : null,
    h("span", { class: "step" + (done ? " done" : "") + (now ? " now" : "") }, h("b", { text: done ? "✓" : String(i + 1) }), label),
  ]).filter(Boolean));

  // banners
  const banners = [];
  if (!st.claude_available) banners.push(h("div", { class: "banner bad" }, icon("alert"),
    h("span", { class: "grow", text: "Claude Code isn't installed or signed in on this computer, so research can't run. Install it and sign in, then refresh." })));
  if (w.paused && w.pause_reason) banners.push(h("div", { class: "banner" }, icon("pause"),
    h("span", { class: "grow", text: `Research is paused: ${w.pause_reason}` }),
    h("button", { class: "btn small", type: "button", onclick: () => workerCtl("resume") }, icon("play"), "Resume")));
  if (!st.settings_ready) banners.push(h("div", { class: "banner" }, icon("alert"),
    h("span", { class: "grow", text: "Add your name and title in Settings so messages are signed by you." }),
    h("a", { class: "btn small", href: "#/settings" }, "Open Settings")));
  $("#banner").replaceChildren(...banners);

  // queue bar
  const qb = $("#queueBar");
  qb.classList.toggle("hidden", !(busy || w.paused));
  if (busy || w.paused) {
    const total = c.total || 1;
    const cur = w.current;
    $("#queueText").replaceChildren(
      cur ? h("strong", { text: `${cur.domain}: ${cur.stage || "working…"}` }) : h("strong", { text: w.paused ? "Paused" : "Starting…" }),
      ` · ${c.done} of ${c.total} companies researched`);
    $("#queueFill").style.width = `${Math.round((c.done / total) * 100)}%`;
    const tg = $("#queueToggle");
    tg.textContent = w.paused ? "Resume" : "Pause";
    tg.onclick = () => workerCtl(w.paused ? "resume" : "pause");
  }
}

async function workerCtl(action) {
  try { const r = await api(action === "pause" ? "/api/worker/pause" : "/api/worker/resume", {}); S.state.worker = r.worker; renderShell(); }
  catch (e) { toast(e.message, true); }
}

/* ---------------------------------------------------------------- charts */
const tip = () => $("#tooltip");
function showTip(e, text) { const t = tip(); t.textContent = text; t.classList.remove("hidden"); t.style.left = e.clientX + 12 + "px"; t.style.top = e.clientY + 12 + "px"; }
function hideTip() { tip().classList.add("hidden"); }

function roundedEnd(x, y, w, hgt, r, vertical) {
  if (w <= 0) return "";
  r = Math.min(r, w / 2, hgt / 2);
  if (!vertical) return `M${x} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + hgt - r}Q${x + w} ${y + hgt} ${x + w - r} ${y + hgt}H${x}Z`;
  // vertical: x,y is the baseline-left corner, w is height, hgt is bar width
  const top = y - w;
  return `M${x} ${y}V${top + r}Q${x} ${top} ${x + r} ${top}H${x + hgt - r}Q${x + hgt} ${top} ${x + hgt} ${top + r}V${y}Z`;
}

function hbarChart(items, colors) {
  const W = 520, rowH = 34, barH = 16, labelW = 130, valW = 40, H = items.length * rowH;
  const max = Math.max(1, ...items.map(d => d.value));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": items.map(d => `${d.label} ${d.value}`).join(", ") });
  root.append(svg("line", { x1: labelW, x2: labelW, y1: 0, y2: H, class: "axis" }));
  items.forEach((d, i) => {
    const y = i * rowH + (rowH - barH) / 2, w = ((W - labelW - valW) * d.value) / max;
    const label = svg("text", { x: labelW - 10, y: y + barH / 2 + 4, "text-anchor": "end", class: "lbl" }); label.textContent = d.label;
    const val = svg("text", { x: labelW + w + 8, y: y + barH / 2 + 4, class: "val" }); val.textContent = d.value;
    const hit = svg("rect", { x: 0, y: i * rowH, width: W, height: rowH, class: "hit" });
    hit.addEventListener("mousemove", e => showTip(e, `${d.label}: ${d.value}`)); hit.addEventListener("mouseleave", hideTip);
    root.append(label, val);
    if (w > 0) root.append(svg("path", { d: roundedEnd(labelW + 1, y, w, barH, 4), fill: colors[i % colors.length], class: "mark" }));
    root.append(hit);
  });
  return root;
}

function columnChart(items, color) {
  const W = 520, H = 190, base = 160, top = 14, gap = 18, n = items.length;
  const bw = (W - gap * (n + 1)) / n, max = Math.max(1, ...items.map(d => d.value));
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": items.map(d => `${d.label}: ${d.value}`).join(", ") });
  root.append(svg("line", { x1: 0, x2: W, y1: base, y2: base, class: "axis" }));
  items.forEach((d, i) => {
    const x = gap + i * (bw + gap), hgt = ((base - top - 16) * d.value) / max;
    if (hgt > 0) root.append(svg("path", { d: roundedEnd(x, base, hgt, bw, 4, true), fill: color, class: "mark" }));
    const v = svg("text", { x: x + bw / 2, y: base - hgt - 6, "text-anchor": "middle", class: "val" }); v.textContent = d.value;
    const l = svg("text", { x: x + bw / 2, y: base + 18, "text-anchor": "middle", class: "lbl" }); l.textContent = d.label;
    const hit = svg("rect", { x: x - gap / 2, y: 0, width: bw + gap, height: H, class: "hit" });
    hit.addEventListener("mousemove", e => showTip(e, `Score ${d.label}: ${d.value} compan${d.value === 1 ? "y" : "ies"}`)); hit.addEventListener("mouseleave", hideTip);
    root.append(v, l, hit);
  });
  return root;
}

function chartCard(title, sub, chartEl, items) {
  const tableId = "t" + Math.random().toString(36).slice(2, 8);
  const table = h("table", { class: "chart-table hidden", id: tableId },
    h("thead", {}, h("tr", {}, h("th", { text: "Category" }), h("th", { text: "Count" }))),
    h("tbody", {}, items.map(d => h("tr", {}, h("td", { text: d.label }), h("td", { text: d.value })))));
  const toggle = h("button", { class: "linkish", type: "button", "aria-controls": tableId, "aria-expanded": "false",
    onclick: (e) => { const hid = table.classList.toggle("hidden"); e.target.setAttribute("aria-expanded", String(!hid)); e.target.textContent = hid ? "View as table" : "Hide table"; } }, "View as table");
  return h("div", { class: "card chart-card" }, h("div", { class: "head" }, h("h2", { text: title }), toggle),
    h("p", { class: "sub", text: sub }), h("div", { class: "chart" }, chartEl), table);
}

/* ---------------------------------------------------------------- dashboard */
function renderDashboard() {
  const root = $("#view-dashboard");
  const st = S.state, stats = S.stats;
  if (!st || !stats) { root.replaceChildren(h("div", { class: "card empty", text: "Loading…" })); return; }
  const t = stats.totals;
  if (!t.contacts) {
    root.replaceChildren(h("div", { class: "card empty" },
      h("h3", { text: "Welcome to ProspectScout" }),
      h("p", { text: "Add a prospect's website. We'll study their site, find real problems and opportunities, and write short, specific messages for LinkedIn, email and WhatsApp. You copy them and send them yourself." }),
      h("a", { class: "btn primary", href: "#/add" }, icon("plus"), "Add your first prospect")));
    return;
  }
  const f = stats.by_fit, fun = stats.funnel;
  const researched = st.counts.done;
  const kpi = (v, l, s) => h("div", { class: "card kpi" }, h("div", { class: "v", text: v }), h("div", { class: "l", text: l }), h("div", { class: "s", text: s }));
  const kpis = h("div", { class: "kpis" },
    kpi(t.contacts, "Prospects", `${t.companies} compan${t.companies === 1 ? "y" : "ies"}`),
    kpi(researched, "Researched", `${st.counts.queued + st.counts.in_progress} waiting`),
    kpi(f.strong, "Strong fits", `${f.possible} possible`),
    kpi(t.messages_ready, "Messages ready", "to copy and send"),
    kpi(fun.call_booked + fun.won, "Calls booked", `${fun.replied} replied`));

  const fitItems = [{ label: "Strong fit", value: f.strong }, { label: "Possible", value: f.possible }, { label: "Skip", value: f.skip }, { label: "Check manually", value: f.manual }];
  const funnelItems = [["new", "New"], ["contacted", "Contacted"], ["replied", "Replied"], ["call_booked", "Call booked"], ["won", "Won"]].map(([k, l]) => ({ label: l, value: fun[k] }));
  const histItems = ["0–19", "20–39", "40–59", "60–79", "80–100"].map((l, i) => ({ label: l, value: stats.score_hist[i] }));
  const ft = stats.finding_types;
  const findItems = [["broken", "Broken things"], ["missing", "Missing features"], ["competitor_gap", "Competitor gaps"], ["growth", "Growth ideas"], ["ai", "AI ideas"]].map(([k, l]) => ({ label: l, value: ft[k] }));
  const css = getComputedStyle(document.documentElement);
  const blue = css.getPropertyValue("--series-1").trim();
  const ramp = [1, 2, 3, 4, 5].map(i => css.getPropertyValue(`--ramp-${i}`).trim());
  const charts = h("div", { class: "charts" },
    chartCard("Fit of researched companies", "How many companies are worth contacting", hbarChart(fitItems, [blue]), fitItems),
    chartCard("Outreach pipeline", "Where your prospects are right now", hbarChart(funnelItems, ramp), funnelItems),
    chartCard("Opportunity score", "Companies worth contacting, by score", columnChart(histItems, blue), histItems),
    chartCard("What we find most", "Types of opportunities found on their websites", hbarChart(findItems, [blue]), findItems));

  const best = (st.rows || []).filter(r => ["strong", "possible"].includes(r.fit) && r.outreach_status === "new" && ["done", "needs_review"].includes(r.messages_status))
    .sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 6);
  const top = h("div", { class: "card" }, h("h2", { text: "Best prospects to contact next" }),
    h("p", { class: "sub", text: "Messages are written and waiting. Open one, copy, and send at the suggested time." }),
    best.length ? h("div", { class: "toplist" }, best.map(r => h("button", { class: "item", type: "button", onclick: () => openDrawer(r.contact_id) },
      scoreBadge(r.score), h("span", {}, h("div", { class: "cell-main", text: `${r.full_name || "Unknown"} · ${r.company_name || r.domain}` }),
        h("div", { class: "cell-sub", text: r.job_title || r.domain })),
      h("span", { class: "cell-sub", title: r.next_email || "", text: yourTime(r.next_email) }))))
      : h("p", { class: "muted", text: "Nothing ready yet. Research runs in the background; this list fills up as companies are done." }));
  root.replaceChildren(kpis, charts, top);
}

function yourTime(label) {
  if (!label) return "";
  const [their, yours] = label.split(" · ");
  const day = their.split(" ")[0];
  return yours ? (/^[A-Z][a-z]{2} /.test(yours) ? yours : `${day} ${yours}`) : their;
}

function scoreBadge(score) {
  if (score === null || score === undefined) return h("span", { class: "score", text: "–" });
  return h("span", { class: "score " + (score >= 70 ? "hi" : score >= 40 ? "mid" : ""), text: score, title: "Opportunity score (0–100)" });
}

/* ---------------------------------------------------------------- prospects */
function statusCell(r) {
  if (r.contact_status === "needs_website") return h("span", { class: "chip bad", text: "Needs website" });
  const s = r.company_status;
  if (s === "queued") return h("span", { class: "stage" }, h("span", { class: "dot" }), "Waiting in queue");
  if (s === "crawling" || s === "analyzing") return h("span", { class: "stage" }, h("span", { class: "dot busy" }), r.stage || "Researching…");
  if (s === "failed") return h("span", { class: "chip bad", text: "Research failed" });
  if (s === "unreachable") return h("span", { class: "chip bad", text: "Site unreachable" });
  if (s === "skipped") return h("span", { class: "chip skip", text: "Skipped" });
  if (["pending", "writing"].includes(r.messages_status)) return h("span", { class: "stage" }, h("span", { class: "dot busy" }), "Writing messages…");
  if (r.messages_status === "failed") return h("span", { class: "chip bad", text: "Messages failed" });
  if (r.messages_status === "needs_review") return h("span", { class: "chip possible", text: "Check messages" });
  return h("span", { class: "chip " + (r.outreach_status === "new" ? "info" : ""), text: r.outreach_status === "new" ? "Ready to send" : OUTREACH[r.outreach_status] || r.outreach_status });
}

function filteredRows() {
  const q = S.search.trim().toLowerCase();
  return (S.state.rows || []).filter(r => {
    if (S.filter === "strong" || S.filter === "possible" || S.filter === "skip") { if (r.fit !== S.filter) return false; }
    if (S.filter === "needs_website" && r.contact_status !== "needs_website") return false;
    if (S.filter === "researching" && !["queued", "crawling", "analyzing"].includes(r.company_status)) return false;
    if (S.filter === "problems" && !["failed", "unreachable"].includes(r.company_status) && r.messages_status !== "needs_review") return false;
    if (S.outreach && r.outreach_status !== S.outreach) return false;
    if (q && ![r.full_name, r.company_name, r.domain, r.email, r.job_title].some(v => (v || "").toLowerCase().includes(q))) return false;
    return true;
  });
}

function renderProspectsTable() {
  const body = $("tbody", $("#view-prospects"));
  if (!body) return;
  const rows = filteredRows();
  const holder = $(".table-holder", $("#view-prospects"));
  if (!S.state.rows.length) {
    holder.replaceChildren(h("div", { class: "card empty" }, h("h3", { text: "No prospects yet" }),
      h("p", { text: "Add one website or import a sheet to get started." }), h("a", { class: "btn primary", href: "#/add" }, icon("plus"), "Add prospects")));
    return;
  }
  if (!holder.contains(body.closest("table"))) holder.replaceChildren(body.closest(".table-wrap"));
  body.replaceChildren(...(rows.length ? rows.map(r => h("tr", {
    tabindex: "0", onclick: () => openDrawer(r.contact_id),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDrawer(r.contact_id); } },
  },
    h("td", {}, scoreBadge(r.score)),
    h("td", {}, h("div", { class: "cell-main", text: r.company_name || r.domain || "—" }), h("div", { class: "cell-sub", text: r.domain || "" })),
    h("td", {}, h("div", { class: "cell-main", text: r.full_name || "—" }), h("div", { class: "cell-sub", text: r.job_title || "" })),
    h("td", {}, r.fit ? h("span", { class: "chip " + r.fit, text: FIT_LABEL[r.fit] || r.fit }) : h("span", { class: "muted", text: "—" })),
    h("td", {}, statusCell(r)),
    h("td", { class: "cell-sub", title: r.next_email || "", text: yourTime(r.next_email) }),
  )) : [h("tr", {}, h("td", { colspan: "6", class: "empty", text: "No prospects match these filters." }))]));
}

function renderProspects() {
  const root = $("#view-prospects");
  const filters = [["all", "All"], ["strong", "Strong fit"], ["possible", "Possible"], ["skip", "Skipped"], ["researching", "In research"], ["needs_website", "Needs website"], ["problems", "Needs attention"]];
  const exportMenu = h("div", { class: "menu-list hidden" },
    h("a", { href: "/api/export.csv" }, "Prospects + messages (CSV)"),
    h("a", { href: "/api/export_prospectiq.csv" }, "ProspectIQ import file (CSV)"),
    h("a", { href: "/api/export.json" }, "Full backup (JSON)"));
  const toolbar = h("div", { class: "toolbar" },
    h("div", { class: "search" }, icon("search"), h("input", { type: "search", placeholder: "Search name, company, email…", "aria-label": "Search prospects", value: S.search,
      oninput: (e) => { S.search = e.target.value; renderProspectsTable(); } })),
    h("div", { class: "filters", role: "group", "aria-label": "Filter by fit" }, filters.map(([k, l]) => h("button", {
      class: "filter", type: "button", "aria-pressed": String(S.filter === k),
      onclick: (e) => { S.filter = k; root.querySelectorAll(".filter").forEach(b => b.setAttribute("aria-pressed", String(b === e.target))); renderProspectsTable(); },
    }, l))),
    h("select", { "aria-label": "Outreach status", style: "width:auto", onchange: (e) => { S.outreach = e.target.value; renderProspectsTable(); } },
      h("option", { value: "", text: "Any outreach status" }), Object.entries(OUTREACH).map(([k, v]) => h("option", { value: k, text: v, selected: S.outreach === k }))),
    h("span", { class: "spacer" }),
    h("div", { class: "menu" }, h("button", { class: "btn", type: "button", "aria-haspopup": "true",
      onclick: (e) => { e.stopPropagation(); exportMenu.classList.toggle("hidden"); } }, icon("download"), "Export"), exportMenu),
    h("a", { class: "btn primary", href: "#/add" }, icon("plus"), "Add prospects"));
  document.addEventListener("click", () => exportMenu.classList.add("hidden"), { once: true });
  const table = h("div", { class: "table-wrap" }, h("table", { class: "data" },
    h("thead", {}, h("tr", {}, ["Score", "Company", "Contact", "Fit", "Status", "Email them (your time)"].map(t => h("th", { scope: "col", text: t })))),
    h("tbody")));
  root.replaceChildren(toolbar, h("div", { class: "table-holder" }, table));
  renderProspectsTable();
}

/* ---------------------------------------------------------------- drawer */
const MSG_GROUPS = [
  { title: "First message (pick one channel)", items: [
    { key: "linkedin_note", label: "LinkedIn connection note", ch: "linkedin", chars: 300 },
    { key: "email", label: "Email", ch: "email", words: 90 },
    { key: "whatsapp", label: "WhatsApp message", ch: "whatsapp", words: 60 } ] },
  { title: "When they reply", items: [
    { key: "linkedin_after_accept", label: "LinkedIn message after they accept", ch: "linkedin", words: 90 },
    { key: "report_delivery", label: "Send the free review and suggest a call", ch: "email", words: 110 } ] },
  { title: "Follow-ups (only if there's no reply)", items: [
    { key: "followup_1", label: "Follow-up 1", ch: "email", words: 90, fu: 0 },
    { key: "followup_2", label: "Follow-up 2", ch: "email", words: 90, fu: 1 },
    { key: "followup_3", label: "Follow-up 3", ch: "email", words: 60, fu: 2 } ] },
];
const CH_ICON = { linkedin: "linkedin", email: "mail", whatsapp: "chat" };

function signature() {
  const s = S.settings || {}, snd = s.sender || {}, co = s.company || {};
  const short = co.short_name || co.name || "";
  let sig = snd.name ? `${snd.name}\n${snd.title ? `${snd.title}, ${short}` : short}` : `Best regards,\nThe ${short} team`;
  if (snd.booking_link) sig += `\n${snd.booking_link}`;
  return sig;
}
function bodyWords(text) {
  let t = (text || "").trimEnd(); const sig = signature();
  if (t.endsWith(sig)) t = t.slice(0, -sig.length);
  return t.split(/\s+/).filter(Boolean).length;
}

async function openDrawer(contactId) {
  S.detailId = contactId; S.tab = S.tab || "overview";
  if (!location.hash.startsWith(`#/prospect/${contactId}`)) history.replaceState(null, "", `#/prospect/${contactId}`);
  $("#overlay").classList.remove("hidden"); $("#drawer").classList.remove("hidden");
  $("#drawerHead").replaceChildren(h("p", { class: "muted", text: "Loading…" })); $("#drawerBody").replaceChildren();
  await loadDetail(true);
  const first = $("#drawer").querySelector("button, a, select"); if (first) first.focus();
}

function closeDrawer() {
  S.detailId = null; S.detail = null;
  $("#overlay").classList.add("hidden"); $("#drawer").classList.add("hidden"); hideTip();
  if (location.hash.startsWith("#/prospect/")) history.replaceState(null, "", "#/prospects");
  if (S.view !== "prospects" && S.view !== "dashboard") route();
}

async function loadDetail(full) {
  if (!S.detailId) return;
  try {
    const d = await api(`/api/prospect/${S.detailId}`);
    const before = S.detail;
    S.detail = d;
    const changed = !before || before.company.status !== d.company.status || before.contact.messages_status !== d.contact.messages_status;
    if (full || changed) renderDrawer();
  } catch (e) { toast(e.message, true); closeDrawer(); }
}

function renderDrawer() {
  const d = S.detail; if (!d) return;
  const c = d.contact, co = d.company || {}, a = d.analysis || {};
  const title = co.company_name || co.domain || c.company_name || "Prospect";
  const statusSel = h("select", { "aria-label": "Outreach status", style: "width:auto", onchange: async (e) => {
    try { await api(`/api/contact/${c.id}/status`, { outreach_status: e.target.value }); toast("Status updated"); await loadDetail(true); refresh(); } catch (err) { toast(err.message, true); } } },
    Object.entries(OUTREACH).map(([k, v]) => h("option", { value: k, text: v, selected: c.outreach_status === k })));
  const actions = h("div", { class: "row" },
    c.outreach_status === "new" && d.messages && d.messages.email_body ? h("button", { class: "btn small navy", type: "button", onclick: async () => {
      await api(`/api/contact/${c.id}/status`, { outreach_status: "contacted" }); toast("Marked as contacted. Follow-up dates are on the Overview tab."); await loadDetail(true); refresh(); } }, icon("check"), "Mark contacted") : null,
    statusSel,
    co.id ? h("button", { class: "btn small", type: "button", title: "Read the website again and redo the analysis", onclick: async () => {
      if (!window.confirm("Re-run research? We'll read the website again and rewrite messages for people you haven't contacted yet. Messages already sent are kept.")) return;
      await api(`/api/company/${co.id}/rerun`, {}); toast("Queued for fresh research"); await loadDetail(true); refresh(); } }, icon("refresh"), "Re-run research") : null,
    ["done", "needs_review", "failed"].includes(c.messages_status) ? h("button", { class: "btn small", type: "button", onclick: async () => {
      await api(`/api/contact/${c.id}/regenerate`, {}); toast("Writing new messages…"); await loadDetail(true); refresh(); } }, icon("refresh"), "Rewrite messages") : null,
    h("button", { class: "btn small ghost", type: "button", title: "Remove this prospect from the list", onclick: async () => {
      if (!window.confirm(`Remove ${c.full_name || "this prospect"} from your list? This can't be undone.`)) return;
      try { await api(`/api/contact/${c.id}/delete`, {}); toast("Prospect removed"); closeDrawer(); refresh(); } catch (err) { toast(err.message, true); } } }, icon("x"), "Remove"));
  const tabs = [["overview", "Overview"], ["messages", "Messages"], ["report", "Free report"], ["callprep", "Call prep"]];
  $("#drawerHead").replaceChildren(
    h("div", { class: "row" },
      h("div", { style: "min-width:0;flex:1" },
        h("h2", { id: "drawerTitle", text: title }),
        h("div", { class: "row muted", style: "gap:8px;margin-top:2px" },
          c.full_name ? h("span", { text: c.full_name + (c.job_title ? ` · ${c.job_title}` : "") }) : null,
          co.website ? h("a", { href: safeUrl(co.website), target: "_blank", rel: "noopener" }, co.domain, " ", icon("ext")) : null,
          c.linkedin_url ? h("a", { href: safeUrl(c.linkedin_url), target: "_blank", rel: "noopener" }, "LinkedIn ", icon("ext")) : null)),
      h("button", { class: "btn ghost", type: "button", "aria-label": "Close", onclick: closeDrawer }, icon("x"))),
    h("div", { class: "row", style: "margin-top:10px" },
      co.fit ? h("span", { class: "chip " + co.fit, text: FIT_LABEL[co.fit] || co.fit }) : null,
      co.score !== undefined && co.score !== null ? h("span", { class: "chip", text: `Score ${co.score}/100` }) : null,
      d.sop ? h("span", { class: "chip", title: "Company SOP guide", text: `SOP: ${d.sop.label.toUpperCase()}` }) : null,
      h("span", { class: "spacer" }), actions),
    h("div", { class: "tabs", role: "tablist" }, tabs.map(([k, l]) => h("button", { class: "tab", role: "tab", type: "button", "aria-selected": String(S.tab === k),
      onclick: () => { S.tab = k; renderDrawer(); } }, l))));
  const body = $("#drawerBody");
  const render = { overview: drawerOverview, messages: drawerMessages, report: drawerReport, callprep: drawerCallPrep }[S.tab] || drawerOverview;
  body.replaceChildren(...[].concat(render(d)));
  body.scrollTop = 0;
}

function statusNotice(d) {
  const c = d.contact, co = d.company || {}, a = d.analysis || {};
  if (c.status === "needs_website") {
    const inp = h("input", { type: "url", placeholder: "e.g. acmelogistics.com", "aria-label": "Company website" });
    return h("div", { class: "card" }, h("h3", { text: "Add their company website to start research" }),
      h("p", { class: "muted", style: "margin-top:0", text: "We research the company's own website (not a Facebook or LinkedIn page)." }),
      h("div", { class: "row" }, h("div", { style: "flex:1" }, inp), h("button", { class: "btn primary", type: "button", onclick: async () => {
        try { await api(`/api/contact/${c.id}/website`, { website: inp.value }); toast("Website saved. Research has started."); await loadDetail(true); refresh(); }
        catch (err) { toast(err.message, true); } } }, icon("check"), "Save & research")));
  }
  if (["queued", "crawling", "analyzing"].includes(co.status)) return h("div", { class: "card" }, h("div", { class: "stage" }, h("span", { class: "dot busy" }), h("strong", { text: co.stage || "Waiting in the research queue…" })),
    h("p", { class: "muted", text: "This usually takes 2 to 4 minutes per company. This page updates by itself." }));
  if (co.status === "failed") return h("div", { class: "banner bad" }, icon("alert"), h("span", { class: "grow", text: `Research failed: ${co.error || "unknown error"}` }));
  if (co.status === "unreachable") return h("div", { class: "banner bad" }, icon("alert"), h("span", { class: "grow", text: `We couldn't open this website (${co.error || "no response"}). Check the address, then use Re-run research.` }));
  if (co.fit === "skip") return h("div", { class: "banner" }, icon("alert"), h("span", { class: "grow", text: `Skipped: ${a.skip_reason || "not a good fit"}` }));
  if (["pending", "writing"].includes(c.messages_status)) return h("div", { class: "card" }, h("div", { class: "stage" }, h("span", { class: "dot busy" }), h("strong", { text: "Writing messages for this person…" })));
  if ((a._problems || []).length) return h("div", { class: "banner" }, icon("alert"), h("span", { class: "grow" },
    h("strong", { text: "Please check the messages before sending. " }), `Our checker flagged: ${a._problems.slice(0, 3).join("; ")}`));
  return null;
}

function drawerOverview(d) {
  const c = d.contact, a = d.analysis || {}, out = [];
  const notice = statusNotice(d); if (notice) out.push(h("div", { class: "section" }, notice));
  if (a.summary) out.push(h("div", { class: "section card" }, h("h3", { text: "In short" }), h("p", { style: "margin:0", text: a.summary })));
  const findings = a.findings || [];
  if (findings.length) {
    const tag = (f) => f.id === a.opener_finding_id ? ["info", "Used in first message"] : (a.followup_finding_ids || []).includes(f.id) ? ["possible", "Held for follow-ups"] : ["", "For the call"];
    out.push(h("div", { class: "section" }, h("h3", { text: "What we found" }), findings.map(f => {
      const [cls, label] = tag(f);
      return h("div", { class: "finding" }, h("div", { class: "row" }, h("span", { class: "chip " + (f.type === "broken" ? "bad" : f.type === "missing" ? "possible" : "info"), text: FINDING[f.type] || f.type }),
        h("span", { class: "t", text: f.title }), h("span", { class: "spacer" }), h("span", { class: "chip " + cls, text: label })),
        f.evidence ? h("div", { class: "cell-sub", style: "margin-top:4px", text: `Evidence: ${f.evidence}` }) : null,
        f.business_impact ? h("div", { style: "margin-top:4px", text: f.business_impact }) : null);
    })));
  }
  const bi = a.buying_intent;
  if (bi) out.push(h("div", { class: "section card" }, h("h3", {}, "Buying intent ", h("span", { class: "chip " + (bi.level === "high" ? "strong" : bi.level === "medium" ? "possible" : ""), text: bi.level || "low" })),
    (bi.signals || []).length ? h("ul", { style: "margin:0;padding-left:18px" }, bi.signals.map(s => h("li", {}, h("strong", { text: s.signal }), s.evidence ? ` (${s.evidence})` : "",
      s.source_url ? h("span", {}, " ", h("a", { href: safeUrl(s.source_url), target: "_blank", rel: "noopener", text: "source" })) : null))) : h("p", { class: "muted", style: "margin:0", text: "No strong signals found." })));
  out.push(timingCard(d));
  if ((a.competitors || []).length) out.push(h("div", { class: "section card" }, h("h3", { text: "Competitors we looked at" }),
    h("ul", { style: "margin:0;padding-left:18px" }, a.competitors.map(cp => h("li", {}, cp.url ? h("a", { href: safeUrl(cp.url), target: "_blank", rel: "noopener", text: cp.name }) : cp.name,
      ": ", (cp.features || []).filter(x => x.has).map(x => x.feature).join(", ") || "no standout features")))));
  if (d.sop) out.push(sopCard(d));
  if ((d.facts || []).length) out.push(h("details", { class: "section card" }, h("summary", { text: `Website checks (${d.facts.length})` }),
    h("ul", { class: "facts" }, d.facts.map(f => h("li", {}, h("span", { class: "chip " + (f.kind === "issue" ? "bad" : f.kind === "missing" ? "possible" : f.kind === "ok" ? "strong" : ""), text: f.kind }), " ",
      h("strong", { text: f.label }), f.evidence ? h("span", { class: "muted", text: ` (${f.evidence})` }) : null, f.confidence === "low" ? h("span", { class: "muted", text: " · low confidence" }) : null)))));
  return out;
}

function timingCard(d) {
  const sl = d.slots || {}, c = d.contact;
  const rows = [["email", "Email"], ["linkedin", "LinkedIn"], ["whatsapp", "WhatsApp"]].filter(([k]) => sl[k]).map(([k, l]) =>
    h("div", { class: "row", style: "justify-content:space-between" }, h("span", {}, icon(CH_ICON[k]), " ", h("strong", { text: l })), h("span", { text: sl[k].label })));
  const fu = (d.followups || []).map((r, i) => h("li", { text: `Follow-up ${i + 1}: ${fmtDate(r[0])} – ${fmtDate(r[1])}` }));
  return h("div", { class: "section card" }, h("h3", {}, "Best time to send ", d.local_now ? h("span", { class: "chip", text: d.local_now }) : null),
    rows.length ? h("div", { class: "grid", style: "gap:8px" }, rows) : h("p", { class: "muted", style: "margin:0", text: "We couldn't work out their timezone. Add a phone number with country code to get send times." }),
    fu.length ? h("div", { style: "margin-top:10px" }, h("strong", { text: "Follow-up dates" }), h("ul", { style: "margin:4px 0 0;padding-left:18px" }, fu)) :
      (c.outreach_status === "new" ? h("p", { class: "cell-sub", style: "margin:8px 0 0", text: "Mark as contacted after sending to get follow-up dates." }) : null));
}
function fmtDate(iso) { return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); }

function sopCard(d) {
  const g = d.sop, c = d.contact;
  const yn = (v, label) => h("span", { class: "chip " + (v === true ? "strong" : v === false ? "bad" : ""), text: `${label}: ${v === true ? "yes" : v === false ? "no" : "unknown"}` });
  const manual = new Set(c.manual_signals_json || []);
  const box = (key) => h("label", {}, h("input", { type: "checkbox", checked: manual.has(key), onchange: async (e) => {
    e.target.checked ? manual.add(key) : manual.delete(key);
    await api(`/api/contact/${c.id}/manual_signals`, { signals: [...manual] }); await loadDetail(true); } }), SIGNAL[key]);
  return h("div", { class: "section card" }, h("h3", {}, "Company SOP guide ", h("span", { class: "chip", text: `${g.label.toUpperCase()} · ${g.score} pts` })),
    h("p", { class: "cell-sub", style: "margin:0 0 8px", text: "For information only. It never changes the fit decision." }),
    h("div", { class: "row" }, yn(g.industry_match, "Target industry"), yn(g.country_match, "Target country"), yn(g.size_match, "11–1,000 staff")),
    g.signals.length ? h("ul", { style: "margin:8px 0 0;padding-left:18px" }, g.signals.map(s => h("li", { text: `${SIGNAL[s.type] || s.type} (+${s.weight})${s.evidence ? ": " + s.evidence : ""}` }))) : null,
    h("div", { class: "check-list", style: "margin-top:10px" }, h("span", { class: "cell-sub", text: "Seen on their LinkedIn profile:" }), box("changed_job_recently"), box("active_on_linkedin")));
}

function drawerMessages(d) {
  const c = d.contact, m = d.messages || {};
  const notice = statusNotice(d);
  if (!m.email_body && !m.linkedin_note) return [notice || h("div", { class: "card empty", text: "Messages will appear here once research is finished." })];
  let saveTimer;
  const save = (key, value) => { m[key] = value; clearTimeout(saveTimer); saveTimer = setTimeout(async () => {
    try { await api(`/api/contact/${c.id}/messages`, { messages: { [key]: value } }); } catch (e) { toast(e.message, true); } }, 700); };
  const counter = (it, text) => {
    const n = it.chars ? (text || "").length : bodyWords(text), lim = it.chars || it.words;
    return h("span", { class: n > lim ? "over" : "", text: `${n} / ${lim} ${it.chars ? "characters" : "words"}${it.chars ? "" : " (signature not counted)"}` });
  };
  const sl = d.slots || {};
  const card = (it) => {
    const when = sl[it.ch] ? h("span", { class: "when" }, icon("clock"), sl[it.ch].label) : null;
    const fuDates = it.fu !== undefined && d.followups && d.followups[it.fu] ? h("span", { class: "chip", text: `Send ${fmtDate(d.followups[it.fu][0])} – ${fmtDate(d.followups[it.fu][1])}` }) : null;
    if (it.key === "email") {
      const subj = h("input", { type: "text", class: "subject", "aria-label": "Email subject", value: m.email_subject || "" });
      const ta = h("textarea", { "aria-label": "Email body", rows: "9" }); ta.value = m.email_body || "";
      const cnt = h("span"); const upd = () => cnt.replaceChildren(counter(it, ta.value));
      subj.addEventListener("input", () => save("email_subject", subj.value)); ta.addEventListener("input", () => { save("email_body", ta.value); upd(); }); upd();
      const mailto = c.email ? `mailto:${encodeURIComponent(c.email)}?subject=${encodeURIComponent(subj.value)}&body=${encodeURIComponent(ta.value)}` : null;
      return h("div", { class: "msg" }, h("div", { class: "msg-head" }, icon("mail"), h("span", { class: "ttl", text: it.label }), when),
        h("label", { class: "field" }, "Subject", subj), h("label", { class: "field", style: "margin-top:8px" }, "Message", ta),
        h("div", { class: "msg-foot" }, cnt, h("span", { class: "spacer" }),
          h("button", { class: "btn small", type: "button", onclick: () => copyText(subj.value, "Subject") }, icon("copy"), "Copy subject"),
          mailto ? h("a", { class: "btn small", href: mailto }, icon("mail"), "Open in email") : null,
          h("button", { class: "btn small primary", type: "button", onclick: () => copyText(ta.value, "Email") }, icon("copy"), "Copy email")));
    }
    const ta = h("textarea", { "aria-label": it.label, rows: it.key.startsWith("followup") ? "5" : "6" }); ta.value = m[it.key] || "";
    const cnt = h("span"); const upd = () => cnt.replaceChildren(counter(it, ta.value));
    ta.addEventListener("input", () => { save(it.key, ta.value); upd(); }); upd();
    const digits = (c.phone || "").replace(/[^\d]/g, "");
    const extra = it.ch === "whatsapp" && digits ? h("a", { class: "btn small", href: `https://wa.me/${digits}?text=${encodeURIComponent(ta.value)}`, target: "_blank", rel: "noopener" }, icon("chat"), "Open WhatsApp")
      : it.ch === "linkedin" && c.linkedin_url ? h("a", { class: "btn small", href: safeUrl(c.linkedin_url), target: "_blank", rel: "noopener" }, icon("linkedin"), "Open profile") : null;
    return h("div", { class: "msg" }, h("div", { class: "msg-head" }, icon(CH_ICON[it.ch]), h("span", { class: "ttl", text: it.label }), fuDates || when),
      ta, h("div", { class: "msg-foot" }, cnt, h("span", { class: "spacer" }), extra,
        h("button", { class: "btn small primary", type: "button", onclick: () => copyText(ta.value, it.label) }, icon("copy"), "Copy")));
  };
  return [notice, h("p", { class: "muted", style: "margin-top:0", text: "Edit anything you like; changes save automatically. Nothing is sent from here: copy a message and send it yourself." }),
    ...MSG_GROUPS.flatMap(g => [h("div", { class: "group-title", text: g.title }), ...g.items.map(card)])].filter(Boolean);
}

function drawerReport(d) {
  if (!d.report_url || !(d.analysis.report || {}).opportunities) return [statusNotice(d) || h("div", { class: "card empty", text: "The free report appears once research is done." })];
  return [h("div", { class: "row", style: "margin-bottom:10px" },
    h("p", { class: "muted", style: "margin:0;flex:1", text: "Send this 1-page review when they say yes. Open it, then use Save as PDF." }),
    h("a", { class: "btn primary", href: d.report_url, target: "_blank", rel: "noopener" }, icon("ext"), "Open & save as PDF")),
  h("iframe", { class: "report-frame", src: d.report_url, title: "Free website review" })];
}

function drawerCallPrep(d) {
  const a = d.analysis || {}, cp = a.call_prep || {};
  if (!cp.angle && !(a.findings || []).length) return [statusNotice(d) || h("div", { class: "card empty", text: "Call notes appear once research is done." })];
  return [
    h("div", { class: "section card" }, h("h3", { text: "Your angle for this person" }), h("p", { style: "margin:0", text: cp.angle || "—" })),
    (a.services_to_pitch || []).length ? h("div", { class: "section card" }, h("h3", { text: "Services to offer" }), h("div", { class: "row" }, a.services_to_pitch.map(s => h("span", { class: "chip info", text: s })))) : null,
    h("div", { class: "section card" }, h("h3", { text: "How we'd solve each opportunity (private)" }),
      (a.findings || []).map(f => h("div", { class: "finding" }, h("div", { class: "row" }, h("span", { class: "t", text: f.title }), h("span", { class: "spacer" }),
        f.complexity ? h("span", { class: "chip", text: { S: "Small job", M: "Medium job", L: "Large job" }[f.complexity] || f.complexity }) : null),
        h("div", { style: "margin-top:4px", text: f.our_solution || "" })))),
    (cp.questions || []).length ? h("div", { class: "section card" }, h("h3", { text: "Questions to ask" }), h("ol", { style: "margin:0;padding-left:20px" }, cp.questions.map(q => h("li", { text: q })))) : null,
    (cp.objections || []).length ? h("div", { class: "section card" }, h("h3", { text: "Likely objections" }), cp.objections.map(o => h("div", { class: "finding" },
      h("div", { class: "t", text: `“${o.objection}”` }), h("div", { style: "margin-top:4px", text: o.response })))) : null,
    cp.notes ? h("div", { class: "section card" }, h("h3", { text: "Notes" }), h("p", { style: "margin:0", text: cp.notes })) : null,
  ].filter(Boolean);
}

/* ---------------------------------------------------------------- add */
let upload = null; // {kind, data_b64|url, preview}
let previewHolder = null;

function renderAdd() {
  const root = $("#view-add");
  const field = (name, label, type, hint, full) => h("label", { class: "field" + (full ? " full" : "") }, label,
    type === "textarea" ? h("textarea", { name, rows: "3" }) : h("input", { name, type: type || "text" }), hint ? h("span", { class: "hint", text: hint }) : null);
  const form = h("form", { class: "card", onsubmit: async (e) => {
    e.preventDefault();
    const fields = Object.fromEntries(new FormData(e.target).entries());
    const btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
    try { const r = await api("/api/add_one", { fields });
      if (r.duplicate) toast("This person is already in your list.", true);
      else { toast(r.needs_website ? "Added. We still need their website to research them." : "Added. Research has started."); e.target.reset(); await refresh(); location.hash = `#/prospect/${r.contact_id}`; }
    } catch (err) { toast(err.message, true); } finally { btn.disabled = false; }
  } },
    h("h2", { text: "Add one prospect" }), h("p", { class: "sub", text: "The website is what we research. A phone number with country code gives better send times." }),
    h("div", { class: "form-grid" },
      field("full_name", "Name"), field("job_title", "Position"),
      field("company_name", "Company"), field("website", "Website", "url", "e.g. acmelogistics.com"),
      field("email", "Work email", "email"), field("phone", "Phone / WhatsApp", "tel", "with country code, e.g. +1 512 555 0100"),
      field("linkedin_url", "LinkedIn profile URL", "url", null, true), field("notes", "Notes (optional)", "textarea", null, true)),
    h("div", { class: "row", style: "margin-top:14px" }, h("button", { class: "btn primary", type: "submit" }, icon("plus"), "Add & start research")));

  const fileInput = h("input", { type: "file", accept: ".csv,.xlsx,.xls,text/csv", class: "sr-only", id: "fileInput", onchange: (e) => e.target.files[0] && readFile(e.target.files[0]) });
  const drop = h("div", { class: "drop", tabindex: "0", role: "button", "aria-label": "Upload an Excel or CSV file",
    onclick: () => fileInput.click(), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } },
    ondragover: (e) => { e.preventDefault(); drop.classList.add("over"); }, ondragleave: () => drop.classList.remove("over"),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) readFile(e.dataTransfer.files[0]); } },
    icon("upload"), h("div", { style: "font-weight:600;margin-top:6px", text: "Drop an Excel or CSV file here" }), h("div", { class: "muted", text: "or click to choose a file" }));
  const urlInput = h("input", { type: "url", placeholder: "https://docs.google.com/spreadsheets/d/…", "aria-label": "Google Sheet or CSV link" });
  const importCard = h("div", { class: "card" }, h("h2", { text: "Import a list" }),
    h("p", { class: "sub", text: "Any columns work. We'll match Name, Position, Company, Website, Email, Phone and LinkedIn automatically." }),
    drop, fileInput,
    h("div", { class: "row", style: "margin:14px 0 6px" }, h("strong", { text: "Or paste a Google Sheet link" })),
    h("div", { class: "row" }, h("div", { style: "flex:1" }, urlInput),
      h("button", { class: "btn", type: "button", onclick: () => urlInput.value.trim() ? loadPreview({ kind: "url", url: urlInput.value.trim() }) : toast("Paste a link first.", true) }, icon("link"), "Load sheet")),
    h("p", { class: "cell-sub", text: "In Google Sheets: Share → General access → Anyone with the link → copy link." }));
  previewHolder = h("div");
  root.replaceChildren(h("div", { class: "add-grid" }, form, h("div", { class: "grid" }, importCard, previewHolder)));
  if (upload && upload.preview) renderPreview();
}

function readFile(file) {
  if (file.size > 20 * 1024 * 1024) return toast("That file is larger than 20 MB.", true);
  const fr = new FileReader();
  fr.onload = () => loadPreview({ kind: "file", data_b64: String(fr.result).split(",")[1] || "", name: file.name });
  fr.onerror = () => toast("Couldn't read that file.", true);
  fr.readAsDataURL(file);
}

async function loadPreview(src) {
  try { const p = await api("/api/import/preview", src); upload = { ...src, preview: p, mapping: { ...p.mapping } }; renderPreview(); }
  catch (e) { toast(e.message, true); }
}

function renderPreview() {
  const holder = previewHolder; if (!holder || !upload) return;
  const p = upload.preview;
  const opts = (sel) => [h("option", { value: "", text: "Don't import" }), ...p.fields.map(f => h("option", { value: f, text: f.replace(/_/g, " "), selected: sel === f }))];
  const rows = p.headers.map(hdr => h("tr", {}, h("td", {}, h("strong", { text: hdr })),
    h("td", {}, h("select", { "aria-label": `Map column ${hdr}`, onchange: (e) => { upload.mapping[hdr] = e.target.value || null; } }, opts(upload.mapping[hdr])))));
  const ready = p.total - p.missing_website;
  holder.replaceChildren(h("div", { class: "card" }, h("h2", { text: `Check your columns${upload.name ? `: ${upload.name}` : ""}` }),
    h("div", { class: "stat-row" }, h("span", { class: "chip info", text: `${p.total} rows` }), h("span", { class: "chip strong", text: `${ready} with a website` }),
      p.missing_website ? h("span", { class: "chip possible", text: `${p.missing_website} need a website` }) : null,
      p.duplicate_domains ? h("span", { class: "chip", text: `${p.duplicate_domains} share a company` }) : null),
    h("table", { class: "chart-table mapping" }, h("thead", {}, h("tr", {}, h("th", { text: "Column in your file" }), h("th", { text: "Use it as" }))), h("tbody", {}, rows)),
    h("div", { class: "row", style: "margin-top:14px" },
      h("button", { class: "btn", type: "button", onclick: () => { upload = null; holder.replaceChildren(); } }, "Cancel"), h("span", { class: "spacer" }),
      h("button", { class: "btn primary", type: "button", onclick: commitImport }, icon("check"), `Add ${p.total} prospects`))));
}

async function commitImport(e) {
  const btn = e.currentTarget; btn.disabled = true;
  try {
    const body = { kind: upload.kind, data_b64: upload.data_b64, url: upload.url, mapping: upload.mapping };
    const r = await api("/api/import/commit", body);
    toast(`Added ${r.added} prospects (${r.companies_new} new companies)${r.duplicates ? `, skipped ${r.duplicates} duplicates` : ""}. Research has started.`);
    upload = null; await refresh(); location.hash = "#/prospects";
  } catch (err) { toast(err.message, true); btn.disabled = false; }
}

/* ---------------------------------------------------------------- settings */
function renderSettings() {
  const root = $("#view-settings");
  const s = S.settings; if (!s) { root.replaceChildren(h("div", { class: "card empty", text: "Loading…" })); return; }
  const inp = (path, label, opts = {}) => {
    const val = path.split(".").reduce((o, k) => (o || {})[k], s);
    const el = opts.textarea ? h("textarea", { "data-path": path, rows: opts.rows || "4" }) : h("input", { "data-path": path, type: opts.type || "text", placeholder: opts.placeholder || "" });
    if (opts.list) el.value = (val || []).join(opts.list === "comma" ? ", " : "\n");
    else if (opts.type === "checkbox") el.checked = !!val; else el.value = val ?? "";
    if (opts.list) el.dataset.list = opts.list;
    if (opts.type === "checkbox") return h("label", { class: "row", style: "font-weight:600;color:var(--text-2)" }, el, label);
    return h("label", { class: "field" + (opts.full ? " full" : "") }, label, opts.hint ? h("span", { class: "hint", text: opts.hint }) : null, el);
  };
  const win = (ch, label) => {
    const el = h("input", { type: "text", "data-window": ch, value: ((s.send_windows || {})[ch] || []).map(w => w.join("-")).join(", ") });
    return h("label", { class: "field" }, label, h("span", { class: "hint", text: "their local time, Tue–Thu; e.g. 08:00-10:00, 17:00-18:00" }), el);
  };
  const testOut = h("span", { class: "muted" });
  const pw = h("input", { type: "password", autocomplete: "new-password", placeholder: s.access_password_set ? "A password is set. Type a new one to change it" : "Not set" });
  const sysTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  root.replaceChildren(h("div", { class: "settings" },
    h("div", { class: "card" }, h("h2", { text: "You" }), h("p", { class: "sub", text: "Used to sign emails and follow-ups." }),
      h("div", { class: "form-grid" }, inp("sender.name", "Your name"), inp("sender.title", "Your title", { placeholder: "e.g. Business Development Manager" }),
        inp("sender.email", "Email", { type: "email" }), inp("sender.phone", "Phone (internal only)", { hint: "never included in messages or reports" }),
        inp("sender.booking_link", "Booking link (optional)", { full: true, placeholder: "https://calendly.com/…" }),
        inp("sender.signature", "Email signature (optional)", { textarea: true, rows: "3", full: true, hint: "leave empty to use your name, title and company; don't include a phone number" }))),
    h("div", { class: "card" }, h("h2", { text: "Company profile" }), h("p", { class: "sub", text: "Claude matches prospects against these services and only names these case studies." }),
      h("div", { class: "form-grid" }, inp("company.name", "Company name"), inp("company.short_name", "Short name"),
        inp("company.website", "Website"), inp("company.pitch", "One-line pitch"),
        inp("company.services", "Services", { textarea: true, list: "lines", hint: "one per line", full: true, rows: "6" }),
        inp("company.industries", "Industries you've served", { textarea: true, list: "lines", hint: "one per line" }),
        inp("company.case_studies", "Case studies Claude may mention", { textarea: true, list: "lines", hint: "one per line; only real work" }),
        inp("company.markets", "Markets", { list: "comma", hint: "comma separated" }),
        inp("company.logo", "Logo URL (shown on the free report)", { placeholder: "/brand/logo.png" }))),
    h("div", { class: "card" }, h("h2", { text: "Send times" }),
      h("div", { class: "form-grid" }, inp("user_timezone", "Your timezone", { placeholder: sysTz, hint: `leave empty to use this computer's (${sysTz})` }),
        h("label", { class: "field" }, "Follow-up spacing (business days)",
          h("input", { type: "text", "data-spacing": "1", value: (s.followup_spacing || []).map(([a, b]) => `${a}-${b}`).join(", ") }),
          h("span", { class: "hint", text: "after the previous touch, e.g. 2-3, 3-5, 4-7" })),
        win("email", "Email windows"), win("linkedin", "LinkedIn windows"), win("whatsapp", "WhatsApp windows"))),
    h("div", { class: "card" }, h("h2", { text: "Company SOP guide" }),
      h("p", { class: "sub", text: "Shown on each prospect as Cold / Warm / HOT. It never skips anyone." }),
      h("div", { class: "form-grid" },
        inp("sop.industries", "Target industries", { textarea: true, list: "lines", hint: "one per line" }),
        inp("sop.countries", "Target countries", { textarea: true, list: "lines", hint: "one per line; \"Europe\" covers European countries" }),
        inp("sop.size_min", "Company size from (employees)", { type: "number" }), inp("sop.size_max", "Company size to", { type: "number" }),
        inp("sop.weights.hiring_tech_roles", "Weight: hiring tech roles", { type: "number" }), inp("sop.weights.funding_news", "Weight: funding / news", { type: "number" }),
        inp("sop.weights.expansion_launch", "Weight: expansion / launch", { type: "number" }), inp("sop.weights.talks_tech_growth", "Weight: talks tech / growth", { type: "number" }),
        inp("sop.weights.changed_job_recently", "Weight: changed job recently", { type: "number" }), inp("sop.weights.active_on_linkedin", "Weight: active on LinkedIn", { type: "number" }))),
    h("div", { class: "card" }, h("h2", { text: "Research" }),
      h("div", { class: "form-grid" }, inp("max_pages", "Pages to read per site", { type: "number" }), inp("max_link_checks", "Links to check per site", { type: "number" }),
        inp("concurrency", "Companies at a time (1–3)", { type: "number", hint: "1 is gentlest on your Claude plan" }), inp("model", "Claude model (optional)", { placeholder: "default" }),
        inp("pagespeed.enabled", "Also run Google PageSpeed (mobile speed score)", { type: "checkbox" }), inp("pagespeed.api_key", "PageSpeed API key (optional)"))),
    h("div", { class: "card" }, h("h2", { text: "Access password" }),
      h("p", { class: "sub", text: "Required before sharing this app through a public link. The browser asks for it (any username works)." }),
      h("div", { class: "row" }, h("div", { style: "flex:1;max-width:420px" }, pw),
        h("button", { class: "btn", type: "button", onclick: async () => { if (!pw.value) return toast("Type a password first.", true);
          await api("/api/settings", { access_password: pw.value }); toast("Password set. Your browser may ask for it now."); pw.value = ""; await loadSettings(); } }, "Set password"))),
    h("div", { class: "card" }, h("h2", { text: "Claude connection" }),
      h("p", { class: "sub", text: "Research uses the Claude Code app signed in on this computer (your Claude plan)." }),
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: async (e) => { e.target.disabled = true; testOut.textContent = "Testing… (about 10 seconds)";
        try { const r = await api("/api/test_claude", {}); testOut.textContent = (r.ok ? "✓ " : "✗ ") + r.message; } catch (err) { testOut.textContent = err.message; } finally { e.target.disabled = false; } } }, "Test Claude connection"), testOut)),
    h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", type: "button", onclick: saveSettings }, icon("check"), "Save settings"))));
}

async function saveSettings() {
  const root = $("#view-settings"), patch = {};
  root.querySelectorAll("[data-path]").forEach(el => {
    let v = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.list) v = v.split(el.dataset.list === "comma" ? "," : "\n").map(x => x.trim()).filter(Boolean);
    if (el.type === "number") v = Number(v);
    const keys = el.dataset.path.split("."); let o = patch;
    keys.slice(0, -1).forEach(k => { o[k] = o[k] || {}; o = o[k]; }); o[keys.at(-1)] = v;
  });
  const windows = {}; let bad = false;
  root.querySelectorAll("[data-window]").forEach(el => {
    const spans = el.value.split(",").map(x => x.trim()).filter(Boolean).map(x => x.split("-").map(t => t.trim()));
    if (spans.some(sp => sp.length !== 2 || !sp.every(t => /^\d{1,2}:\d{2}$/.test(t)))) bad = true;
    windows[el.dataset.window] = spans.map(([a, b]) => [a.padStart(5, "0"), b.padStart(5, "0")]);
  });
  if (bad) return toast("Send windows should look like 08:00-10:00, 17:00-18:00", true);
  patch.send_windows = windows;
  const sp = root.querySelector("[data-spacing]");
  if (sp) {
    const pairs = sp.value.split(",").map(x => x.trim()).filter(Boolean).map(x => x.split("-").map(t => Number(t.trim())));
    if (!pairs.length || pairs.some(p => p.length !== 2 || p.some(n => !Number.isInteger(n) || n < 0))) return toast("Follow-up spacing should look like 2-3, 3-5, 4-7", true);
    patch.followup_spacing = pairs;
  }
  if (patch.pagespeed && !patch.pagespeed.api_key) delete patch.pagespeed.api_key;  // blank keeps the saved key
  try { S.settings = await api("/api/settings", patch); toast("Settings saved"); renderSettings(); refresh(); } catch (e) { toast(e.message, true); }
}

async function loadSettings() { try { S.settings = await api("/api/settings"); } catch (e) { toast(e.message, true); } }

/* Save the browser's IANA timezone once, so "your time" follows daylight saving correctly. */
async function autoTimezone() {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (S.settings && !S.settings.user_timezone && tz) {
    try { S.settings = await api("/api/settings", { user_timezone: tz }); } catch { /* not critical */ }
  }
}

/* ---------------------------------------------------------------- routing & polling */
const TITLES = { dashboard: "Dashboard", prospects: "Prospects", add: "Add prospects", settings: "Settings" };
function route() {
  const hash = location.hash || "#/dashboard";
  const m = hash.match(/^#\/prospect\/(\d+)(?:\/(\w+))?/);
  if (m && m[2]) S.tab = m[2];
  let view = (hash.match(/^#\/(\w+)/) || [])[1] || "dashboard";
  if (m) view = S.view && S.view !== "settings" && S.view !== "add" ? S.view : "prospects";
  if (!TITLES[view]) view = "dashboard";
  S.view = view;
  document.querySelectorAll(".nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("hidden", v.id !== `view-${view}`));
  $("#pageTitle").textContent = TITLES[view];
  ({ dashboard: renderDashboard, prospects: renderProspects, add: renderAdd, settings: renderSettings })[view]();
  if (m && Number(m[1]) !== S.detailId) openDrawer(Number(m[1]));
  else if (!m && S.detailId) closeDrawer();
}

let busyTick = 0;
async function refresh() {
  try {
    const [st, stats] = await Promise.all([api("/api/state"), api("/api/stats")]);
    S.state = st; S.stats = stats;
    renderShell();
    if (S.view === "prospects") renderProspectsTable();
    if (S.view === "dashboard") renderDashboard();
    const c = S.detail && S.detail.company, ct = S.detail && S.detail.contact;
    if (S.detailId && ((c && ["queued", "crawling", "analyzing"].includes(c.status)) || (ct && ["pending", "writing"].includes(ct.messages_status)) || (++busyTick % 5 === 0))) loadDetail(false);
  } catch (e) {
    if (String(e.message).includes("password")) location.reload();
  }
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.detailId) closeDrawer(); });
$("#overlay").addEventListener("click", closeDrawer);
window.addEventListener("hashchange", route);
(async function init() {
  await Promise.all([loadSettings(), refresh()]);
  await autoTimezone();
  route();
  setInterval(refresh, 3000);
})();
