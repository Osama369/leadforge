/* LeadForge dashboard — vanilla JS, no build step. Every scraped value goes through esc(). */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = u => /^https?:\/\//i.test(u || "") ? esc(u) : "";
const fmt = n => Number(n || 0).toLocaleString();
const ago = iso => {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
const titleCase = s => String(s || "").replace(/\b\w/g, c => c.toUpperCase());

async function api(method, url, body) {
  const headers = { "X-Requested-With": "leadforge" };
  if (body) headers["Content-Type"] = "application/json";
  const r = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
  if (r.status === 401 && !url.startsWith("/api/auth/")) { showAuth(false); throw new Error("Your session ended. Please log in again."); }
  let data = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    let msg = data && data.detail;
    if (Array.isArray(msg)) msg = msg.map(d => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    throw new Error(msg || `Request failed (${r.status})`);
  }
  return data;
}
const GET = u => api("GET", u), POST = (u, b) => api("POST", u, b), PUT = (u, b) => api("PUT", u, b),
  PATCH = (u, b) => api("PATCH", u, b), DEL = u => api("DELETE", u);

let toastT;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toastT); toastT = setTimeout(() => t.className = "toast", err ? 5000 : 2600);
}
const fail = e => toast(e.message || String(e), true);

function tierOf(score) {
  const t = S.meta?.tiers || { hot: 70, warm: 40 };
  return score >= t.hot ? "hot" : score >= t.warm ? "warm" : "cold";
}
const scorePill = s => s == null ? `<span class="score na">–</span>` : `<span class="score ${tierOf(s)}">${s}</span>`;
const STATUS_LABEL = { new: "New", contacted: "Contacted", replied: "Replied", meeting: "Meeting", won: "Won", lost: "Lost" };
const STATUS_CLASS = { new: "", contacted: "brand", replied: "brand", meeting: "warm", won: "ok", lost: "cold" };

// ------------------------------------------------------------------ state
const S = { meta: null, catalog: null, job: null, poll: null, user: null };
const isAdmin = () => S.user?.role === "admin";
const ADMIN_ROUTES = new Set(["services", "niches", "locations", "settings", "team"]);
const svcLabel = id => (S.catalog?.services || S.meta?.services || []).find(s => s.id === id)?.label || id;
const countryName = cc => (S.catalog?.countries || S.meta?.countries || []).find(c => c.code === cc)?.name || cc;

async function loadMeta() {
  [S.meta, S.catalog] = await Promise.all([GET("/api/meta"), GET("/api/catalog")]);
  $("#ws-name").textContent = S.meta.workspace_name || "LeadForge";
  document.title = `${S.meta.workspace_name || "LeadForge"} · Leads`;
  $("#version").textContent = "v" + S.meta.version;
  const src = S.meta.sources.filter(s => s.needs);
  $("#side-sources").innerHTML = `<div class="faint" style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase">Data sources</div>` +
    src.map(s => `<div class="row"><span class="dot ${s.ready ? "ok" : ""}"></span><span>${esc(s.label)}</span></div>`).join("") +
    `<div class="row"><span class="dot ${S.meta.features.ai_pitch ? "ok" : ""}"></span><span>AI pitches (Claude)</span></div>`;
}

// ------------------------------------------------------------------ multi-select dropdown
/**
 * new MultiSelect(el, {options:[{value,label,group,hint}], value:[], placeholder, onChange,
 *                       create:(text)=>Promise<value|null>, createLabel, single, max})
 */
class MultiSelect {
  constructor(el, o) {
    this.el = el; this.o = Object.assign({ placeholder: "Select…", maxChips: 6 }, o);
    this.sel = new Set(o.value || []); this.q = ""; this.active = -1;
    el.classList.add("ms");
    el.innerHTML = `<div class="ms-control" tabindex="0" role="combobox" aria-expanded="false"></div>
      <div class="ms-panel"><div class="ms-search"><input class="input" placeholder="Search or type to add…" aria-label="Search"></div>
      ${o.single ? "" : `<div class="ms-actions"><button type="button" data-a="all">Select all</button><button type="button" data-a="none">Clear</button><span class="grow"></span><span class="faint" data-count></span></div>`}
      <div class="ms-list" role="listbox"></div><div class="ms-create hidden"></div></div>`;
    this.ctrl = $(".ms-control", el); this.input = $(".ms-search input", el); this.list = $(".ms-list", el);
    this.createEl = $(".ms-create", el);
    this.ctrl.addEventListener("click", e => {
      const x = e.target.closest("[data-rm]");
      if (x) { e.stopPropagation(); this.toggle(x.dataset.rm); return; }
      this.open(!el.classList.contains("open"));
    });
    this.ctrl.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") { e.preventDefault(); this.open(true); } });
    this.input.addEventListener("input", () => { this.q = this.input.value.trim(); this.active = -1; this.renderList(); });
    this.input.addEventListener("keydown", e => this.key(e));
    this.list.addEventListener("mousedown", e => e.preventDefault());
    this.list.addEventListener("click", e => { const o = e.target.closest(".ms-opt"); if (o) this.toggle(o.dataset.v); });
    this.createEl.addEventListener("click", () => this.doCreate());
    const acts = $(".ms-actions", el);
    if (acts) acts.addEventListener("click", e => {
      const a = e.target.closest("[data-a]")?.dataset.a; if (!a) return;
      if (a === "all") this.filtered().forEach(x => this.sel.add(x.value)); else this.sel.clear();
      this.changed();
    });
    this.outside = e => { if (!el.contains(e.target)) this.open(false); };
    document.addEventListener("mousedown", this.outside);
    this.render();
  }
  get value() { return [...this.sel]; }
  set value(v) { this.sel = new Set(v || []); this.render(); }
  setOptions(opts) {
    this.o.options = opts;
    const valid = new Set(opts.map(o => o.value));
    if (!this.o.keepUnknown) [...this.sel].forEach(v => { if (!valid.has(v)) this.sel.delete(v); });
    this.render();
  }
  open(on) {
    this.el.classList.toggle("open", on); this.ctrl.setAttribute("aria-expanded", on);
    if (on) { this.renderList(); setTimeout(() => this.input.focus(), 0); }
    else { this.q = ""; this.input.value = ""; }
  }
  filtered() {
    const q = this.q.toLowerCase();
    return (this.o.options || []).filter(o => !q || o.label.toLowerCase().includes(q) || (o.hint || "").toLowerCase().includes(q) || (o.group || "").toLowerCase().includes(q));
  }
  toggle(v) {
    if (this.o.single) { this.sel = new Set(this.sel.has(v) ? [] : [v]); this.changed(); this.open(false); return; }
    this.sel.has(v) ? this.sel.delete(v) : this.sel.add(v);
    this.changed();
  }
  changed() { this.render(); if (this.el.classList.contains("open")) this.renderList(); this.o.onChange?.(this.value); }
  label(v) { return (this.o.options || []).find(o => o.value === v)?.label ?? v; }
  render() {
    const v = this.value;
    if (!v.length) { this.ctrl.innerHTML = `<span class="ms-ph">${esc(this.o.placeholder)}</span>`; return; }
    const shown = v.slice(0, this.o.maxChips);
    this.ctrl.innerHTML = shown.map(x => `<span class="tag">${esc(this.label(x))}<button type="button" data-rm="${esc(x)}" aria-label="Remove">×</button></span>`).join("") +
      (v.length > shown.length ? `<span class="ms-more">+${v.length - shown.length} more</span>` : "");
  }
  renderList() {
    const items = this.filtered();
    const cnt = $("[data-count]", this.el); if (cnt) cnt.textContent = `${this.sel.size} selected`;
    let html = "", group = null;
    items.forEach((o, i) => {
      if (o.group && o.group !== group) { group = o.group; html += `<div class="ms-group">${esc(group)}</div>`; }
      html += `<div class="ms-opt${this.sel.has(o.value) ? " sel" : ""}${i === this.active ? " active" : ""}" data-v="${esc(o.value)}" role="option" aria-selected="${this.sel.has(o.value)}">
        <span class="box"></span><span>${esc(o.label)}</span>${o.hint ? `<small>${esc(o.hint)}</small>` : ""}</div>`;
    });
    this.list.innerHTML = html || `<div class="ms-empty">${this.q ? "No matches" : "Nothing here yet"}</div>`;
    const exact = (this.o.options || []).some(o => o.label.toLowerCase() === this.q.toLowerCase());
    const canCreate = this.o.create && (this.q ? !exact : this.o.createAlways);
    this.createEl.classList.toggle("hidden", !canCreate);
    if (canCreate) this.createEl.innerHTML = `＋ ${this.q ? `${esc(this.o.createLabel || "Add")} “${esc(this.q)}”` : esc(this.o.createLabel || "Add new")}`;
    const a = $(".ms-opt.active", this.list); if (a) a.scrollIntoView({ block: "nearest" });
  }
  key(e) {
    const items = this.filtered();
    if (e.key === "ArrowDown") { e.preventDefault(); this.active = Math.min(items.length - 1, this.active + 1); this.renderList(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); this.active = Math.max(0, this.active - 1); this.renderList(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (this.active >= 0 && items[this.active]) this.toggle(items[this.active].value);
      else if (items.length === 1 && this.q) { this.toggle(items[0].value); this.input.value = this.q = ""; this.renderList(); }
      else if (this.o.create && this.q) this.doCreate();
    } else if (e.key === "Escape") { this.open(false); this.ctrl.focus(); }
  }
  async doCreate() {
    const text = this.q;
    this.open(false);
    try {
      const v = await this.o.create(text);
      if (v != null) { this.sel.add(v); this.render(); this.o.onChange?.(this.value); }
    } catch (e) { fail(e); }
  }
  destroy() { document.removeEventListener("mousedown", this.outside); }
}
let liveWidgets = [];
const widget = w => (liveWidgets.push(w), w);

// ------------------------------------------------------------------ modal + drawer
function modal(html, { wide = false } = {}) {
  const m = $("#modal"), c = $("#modal-card");
  c.className = "modal-card" + (wide ? " wide" : ""); c.innerHTML = html;
  m.classList.add("open"); $("#scrim").classList.add("open");
  setTimeout(() => $("input,select,textarea", c)?.focus(), 30);
  return c;
}
function closeModal() { $("#modal").classList.remove("open"); if (!$("#drawer").classList.contains("open")) $("#scrim").classList.remove("open"); }
function closeDrawer() { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); if (!$("#modal").classList.contains("open")) $("#scrim").classList.remove("open"); }
$("#modal").addEventListener("mousedown", e => { if (e.target.id === "modal") closeModal(); });
$("#scrim").addEventListener("click", () => { closeModal(); closeDrawer(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$(".ms.open")) { if ($("#modal").classList.contains("open")) closeModal(); else closeDrawer(); } });

function confirmBox(title, body, okLabel = "Delete", danger = true) {
  return new Promise(res => {
    const c = modal(`<div class="modal-h"><h2>${esc(title)}</h2></div><div class="modal-b muted">${body}</div>
      <div class="modal-f"><button class="btn" data-x>Cancel</button><button class="btn ${danger ? "primary" : "primary"}" data-ok style="${danger ? "background:var(--err);border-color:var(--err);color:#fff" : ""}">${esc(okLabel)}</button></div>`);
    $("[data-x]", c).onclick = () => { closeModal(); res(false); };
    $("[data-ok]", c).onclick = () => { closeModal(); res(true); };
  });
}

// ------------------------------------------------------------------ router
const ROUTES = {};
function route(name, fn) { ROUTES[name] = fn; }
async function go() {
  liveWidgets.forEach(w => w.destroy?.()); liveWidgets = [];
  const [name, ...rest] = location.hash.replace(/^#\/?/, "").split("?")[0].split("/");
  const r = ROUTES[name] && (isAdmin() || !ADMIN_ROUTES.has(name)) ? name : "overview";
  $$("#nav a").forEach(a => a.classList.toggle("on", a.dataset.r === r));
  setMobileNav(false);
  const view = $("#view");
  try { await ROUTES[r](view, rest); } catch (e) { view.innerHTML = `<div class="card empty"><b>Something went wrong</b>${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", go);
const header = (title, sub, actions = "") => `<div class="row" style="margin-bottom:20px;align-items:flex-start">
  <div class="grow"><h1>${esc(title)}</h1>${sub ? `<div class="page-sub">${sub}</div>` : ""}</div><div class="row">${actions}</div></div>`;

// ================================================================== OVERVIEW
route("overview", async view => {
  const o = await GET("/api/overview");
  const st = o.stats;
  const keysReady = S.meta.features.places || S.meta.features.serp;
  if (!st.leads) {
    view.innerHTML = header(`Welcome to ${S.meta.workspace_name}`, "Find local businesses that need your services, with the reason why and a ready pitch.") + `
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>Get started in 3 steps</h2></div><div class="card-b checklist">
        <div class="item"><span class="n ${keysReady ? "ok" : ""}">${keysReady ? "✓" : "1"}</span><div class="grow"><b>Connect a data source</b>
          <div class="muted">Add a Google Places or Serper API key so LeadForge can find businesses on Google Maps.</div>
          ${keysReady ? "" : `<a class="btn sm" href="#/settings" style="margin-top:8px">Add API key</a>`}</div></div>
        <div class="item"><span class="n ok">✓</span><div class="grow"><b>Review what you sell</b>
          <div class="muted">${S.meta.services.length} services and ${S.meta.niches.length} target niches are ready. Add your own anytime.</div>
          <a class="btn sm" href="#/services" style="margin-top:8px">Customize services</a></div></div>
        <div class="item"><span class="n">3</span><div class="grow"><b>Run your first campaign</b>
          <div class="muted">Pick services, niches and cities. We discover, audit and score every business.</div>
          <a class="btn sm primary" href="#/campaign" style="margin-top:8px">Start a campaign</a></div></div>
      </div></div>
      <div class="card"><div class="card-h"><h2>How leads are qualified</h2></div><div class="card-b stack muted">
        <div><b style="color:var(--text)">1. Discover</b> — businesses from Google Maps & directories, with phone and website.</div>
        <div><b style="color:var(--text)">2. Audit</b> — we visit each website: speed, mobile, SEO, ads pixels, chat, booking.</div>
        <div><b style="color:var(--text)">3. Score</b> — each service gets a 0–100 score. <span class="badge hot">Hot ${S.meta.tiers.hot}+</span> <span class="badge warm">Warm ${S.meta.tiers.warm}+</span></div>
        <div><b style="color:var(--text)">4. Pitch</b> — a personalised opener built from the real problems we found.</div>
      </div></div>
    </div>`;
    return;
  }
  const maxSvc = Math.max(1, ...Object.values(o.hot_by_service));
  const maxPipe = Math.max(1, ...Object.values(o.pipeline));
  view.innerHTML = header("Overview", `${fmt(st.leads)} businesses analysed across ${fmt(st.runs)} campaigns`,
    `<a class="btn" href="#/leads?tier=hot">View hot leads</a><a class="btn primary" href="#/campaign">＋ New campaign</a>`) + `
  <div class="grid g5" style="margin-bottom:16px">
    ${kpi("Total leads", fmt(st.leads), "")}
    ${kpi("Hot leads", fmt(st.hot), `score ${S.meta.tiers.hot}+`, "var(--hot)")}
    ${kpi("With email", fmt(st.with_email), st.leads ? Math.round(st.with_email / st.leads * 100) + "% of leads" : "")}
    ${kpi("In pipeline", fmt(st.contacted), "contacted or further")}
    ${kpi("Won", fmt(st.won), "", "var(--ok)")}
  </div>
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card"><div class="card-h"><h2>Hot leads by service</h2></div><div class="card-b">
      ${Object.entries(o.hot_by_service).map(([k, v]) => `<a class="hbar" href="#/leads?service=${esc(k)}&tier=hot" style="color:inherit;text-decoration:none"><span>${esc(svcLabel(k))}</span><span class="track"><span class="fill" style="width:${v / maxSvc * 100}%;display:block"></span></span><b>${fmt(v)}</b></a>`).join("") || `<div class="muted">No services enabled</div>`}
    </div></div>
    <div class="card"><div class="card-h"><h2>Sales pipeline</h2></div><div class="card-b">
      ${Object.entries(o.pipeline).map(([k, v]) => `<a class="hbar" href="#/leads?status=${k}" style="color:inherit;text-decoration:none"><span>${STATUS_LABEL[k]}</span><span class="track"><span class="fill" style="width:${v / maxPipe * 100}%;display:block;background:${k === "won" ? "var(--ok)" : k === "lost" ? "var(--faint)" : "var(--brand)"}"></span></span><b>${fmt(v)}</b></a>`).join("")}
    </div></div>
    <div class="card"><div class="card-h"><h2>Best niches</h2></div><div class="card-b">
      <table class="t"><thead><tr><th>Niche</th><th style="text-align:right">Hot</th><th style="text-align:right">Total</th></tr></thead><tbody>
      ${o.by_niche.map(n => `<tr class="click" data-href="#/leads?niche=${encodeURIComponent(n.niche)}"><td>${esc(titleCase(n.niche))}</td><td style="text-align:right"><b>${fmt(n.hot)}</b></td><td style="text-align:right" class="muted">${fmt(n.total)}</td></tr>`).join("")}
      </tbody></table>
    </div></div>
  </div>
  <div class="grid g2">
    <div class="card"><div class="card-h"><h2>Uncontacted hot leads</h2><span class="grow"></span><a href="#/leads?tier=hot&status=new">See all</a></div>
      ${o.top_hot.length ? `<div class="table-wrap"><table class="t"><tbody>${o.top_hot.map(l => `<tr class="click" data-lead="${esc(l.key)}">
        <td class="biz"><b>${esc(l.name)}</b><small>${esc(titleCase(l.niche))} · ${esc(l.city || countryName(l.country))}</small></td>
        <td><span class="badge">${esc(svcLabel(l.best_service))}</span></td><td style="text-align:right">${scorePill(l.best_score)}</td></tr>`).join("")}</tbody></table></div>`
      : st.hot ? `<div class="empty"><b>All caught up</b>Every hot lead has been contacted.</div>`
        : `<div class="empty"><b>No hot leads yet</b>Run a campaign with a Google Maps source to find businesses with real website problems.</div>`}
    </div>
    <div class="card"><div class="card-h"><h2>Recent campaigns</h2><span class="grow"></span><a href="#/campaigns">History</a></div>
      <div class="table-wrap"><table class="t"><tbody>${o.runs.map(r => runRow(r)).join("") || `<tr><td class="muted">No campaigns yet</td></tr>`}</tbody></table></div>
    </div>
  </div>`;
  bindRowClicks(view);
});
const kpi = (label, val, sub, color) => `<div class="card kpi"><span>${esc(label)}</span><b style="${color ? `color:${color}` : ""}">${val}</b><small>${esc(sub)}</small></div>`;
function runRow(r) {
  const p = r.params || {};
  const what = p.name || [(p.niches || [p.Niche]).filter(Boolean).slice(0, 3).join(", "), (p.countries || [p.Country]).filter(Boolean).map(countryName).join(", ")].filter(Boolean).join(" · ");
  return `<tr><td class="biz"><b>${esc(what || "Campaign")}</b><small>${esc(ago(r.started_at))}</small></td>
    <td style="text-align:right"><span class="badge">${fmt(r.found)} found</span> <span class="badge hot">${fmt(r.qualified)} hot</span></td></tr>`;
}
function bindRowClicks(root) {
  $$("[data-href]", root).forEach(tr => tr.onclick = () => location.hash = tr.dataset.href);
  $$("[data-lead]", root).forEach(tr => tr.onclick = e => { if (!e.target.closest("input,select,a,button")) openLead(tr.dataset.lead); });
}

// ================================================================== NEW CAMPAIGN (wizard)
const WZ_STEPS = ["Services", "Niches", "Locations", "Sources", "Review"];
let WZ = null;
function freshWizard(prefill = {}) {
  const ready = S.meta.sources.filter(s => s.needs && s.ready).map(s => s.id);
  WZ = Object.assign({
    step: 0, name: "", services: [], niches: [], countries: [], cities: [], top_cities: 3,
    sources: ready.length ? ready : S.meta.sources.filter(s => s.ready).map(s => s.id),
    limit: 60, expand: false, serp: S.meta.features.serp, pagespeed: false, ai_pitch: false, only_new: true,
  }, prefill);
}

route("campaign", async (view) => {
  if (S.job && S.job.status === "running") return renderProgress(view);
  if (!WZ) freshWizard();
  view.innerHTML = header("New campaign", "Tell LeadForge what you sell and where. It finds, audits and scores the businesses for you.") + `
  <div class="wizard">
    <div><div class="steps" id="wz-steps"></div><div class="card"><div class="card-b" id="wz-body"></div>
      <div class="modal-f" style="justify-content:space-between"><button class="btn" id="wz-back">← Back</button>
      <div class="row"><span class="muted" id="wz-err" style="color:var(--err)"></span><button class="btn primary" id="wz-next">Continue →</button></div></div></div></div>
    <div class="card summary" id="wz-summary"></div>
  </div>`;
  $("#wz-back").onclick = () => { if (WZ.step > 0) { WZ.step--; drawStep(); } };
  $("#wz-next").onclick = () => wizardNext();
  drawStep();
});

function stepValid(i) {
  if (i === 0 && !WZ.services.length) return "Pick at least one service";
  if (i === 1 && !WZ.niches.length) return "Pick at least one niche";
  if (i === 2 && !WZ.countries.length) return "Pick at least one country";
  if (i === 3 && !WZ.sources.length) return "Turn on at least one source";
  if (i === 4) { const e = estimate(); if (e.searches > 400) return `${e.searches} searches is over the 400 limit`; }
  return "";
}
async function wizardNext() {
  const err = stepValid(WZ.step);
  $("#wz-err").textContent = err;
  if (err) return;
  if (WZ.step < WZ_STEPS.length - 1) { WZ.step++; drawStep(); return; }
  await launch();
}
function drawStep() {
  liveWidgets.forEach(w => w.destroy?.()); liveWidgets = [];
  $("#wz-err").textContent = "";
  $("#wz-steps").innerHTML = WZ_STEPS.map((s, i) => `<button class="step ${i === WZ.step ? "on" : ""} ${i < WZ.step ? "done" : ""}" data-i="${i}" type="button"><i>${i < WZ.step ? "✓" : i + 1}</i>${s}</button>`).join("");
  $$("#wz-steps .step").forEach(b => b.onclick = () => {
    const to = +b.dataset.i;
    for (let i = 0; i < to; i++) { const e = stepValid(i); if (e) { WZ.step = i; drawStep(); $("#wz-err").textContent = e; return; } }
    WZ.step = to; drawStep();
  });
  $("#wz-back").style.visibility = WZ.step ? "visible" : "hidden";
  $("#wz-next").textContent = WZ.step === WZ_STEPS.length - 1 ? "🚀 Launch campaign" : "Continue →";
  const body = $("#wz-body");
  [stepServices, stepNiches, stepLocations, stepSources, stepReview][WZ.step](body);
  drawSummary();
}

function stepServices(body) {
  body.innerHTML = `<h2>What do you want to sell?</h2><p class="muted" style="margin:4px 0 16px">Each lead gets a score for every service you pick, so you know which offer to lead with.</p>
    <div class="field"><span>Services</span><div id="ms-svc"></div><div class="hint">Can't find yours? Type it and press “Add”, then choose which website problems make a business a good fit.</div></div>
    <div class="grid g2" id="svc-cards" style="margin-top:16px"></div>`;
  const ms = widget(new MultiSelect($("#ms-svc", body), {
    options: S.meta.services.map(s => ({ value: s.id, label: s.label, hint: s.builtin ? "" : "custom" })),
    value: WZ.services, placeholder: "Choose one or more services…", createLabel: "Create service",
    onChange: v => { WZ.services = v; cards(); drawSummary(); },
    create: !isAdmin() ? null : async text => { const id = await editService(null, { label: text }); return id || null; },
  }));
  const cards = () => {
    $("#svc-cards", body).innerHTML = S.meta.services.map(s => `<div class="choice ${WZ.services.includes(s.id) ? "on" : ""}" data-id="${esc(s.id)}">
      <div class="grow"><b>${esc(s.label)}</b><small>${esc(s.description || "Custom service")}</small></div>
      <label class="switch"><input type="checkbox" ${WZ.services.includes(s.id) ? "checked" : ""}><i></i></label></div>`).join("");
    $$("#svc-cards .choice", body).forEach(c => c.onclick = e => {
      e.preventDefault();
      const id = c.dataset.id;
      WZ.services = WZ.services.includes(id) ? WZ.services.filter(x => x !== id) : [...WZ.services, id];
      ms.value = WZ.services; cards(); drawSummary();
    });
  };
  cards();
}

function nicheOptions() {
  const picked = new Set(WZ.services);
  return S.meta.niches.map(n => {
    const fits = n.services.filter(s => picked.has(s));
    return {
      value: n.name, label: titleCase(n.name), _rank: fits.length ? 0 : 1,
      group: fits.length ? "Recommended for your services" : "Other niches",
      hint: [n.high_value ? "high value" : "", fits.map(svcLabel).join(", ")].filter(Boolean).join(" · "),
    };
  }).sort((a, b) => a._rank - b._rank || a.label.localeCompare(b.label));
}
function stepNiches(body) {
  body.innerHTML = `<h2>Which kinds of businesses?</h2><p class="muted" style="margin:4px 0 16px">Niches that buy your services most are listed first. Add any niche you like, it's saved for next time.</p>
    <div class="field"><span>Target niches</span><div id="ms-niche"></div></div>
    <div class="row" style="margin-top:12px" id="niche-quick"></div>
    <label class="switch" style="margin-top:18px"><input type="checkbox" id="wz-expand" ${WZ.expand ? "checked" : ""}><i></i>
      <span><b>Search related phrasings too</b><span class="muted" style="display:block;font-size:13px">e.g. “dentist” also searches “dental clinic”. Finds more businesses, uses more searches.</span></span></label>`;
  const ms = widget(new MultiSelect($("#ms-niche", body), {
    options: nicheOptions(), value: WZ.niches, placeholder: "Choose niches, e.g. dentist, plumber…",
    createLabel: "Add niche",
    onChange: v => { WZ.niches = v; quick(); drawSummary(); },
    create: !isAdmin() ? null : async text => {
      const name = text.trim().toLowerCase();
      await POST("/api/catalog/niches", { name, services: WZ.services });
      await loadMeta(); ms.setOptions(nicheOptions()); toast(`Niche “${name}” added`);
      return name;
    },
  }));
  const quick = () => {
    const rec = nicheOptions().filter(o => o._rank === 0 && !WZ.niches.includes(o.value)).slice(0, 8);
    $("#niche-quick", body).innerHTML = rec.length ? `<span class="muted" style="font-size:13px">Quick add:</span>` +
      rec.map(o => `<button class="btn sm" type="button" data-n="${esc(o.value)}">＋ ${esc(o.label)}</button>`).join("") : "";
    $$("#niche-quick [data-n]", body).forEach(b => b.onclick = () => { WZ.niches = [...WZ.niches, b.dataset.n]; ms.value = WZ.niches; quick(); drawSummary(); });
  };
  quick();
  $("#wz-expand", body).onchange = e => { WZ.expand = e.target.checked; drawSummary(); };
}

const cityOptions = () => S.meta.countries.filter(c => WZ.countries.includes(c.code))
  .flatMap(c => c.cities.map(ct => ({ value: `${ct}|${c.code}`, label: ct, group: c.name })));
function stepLocations(body) {
  body.innerHTML = `<h2>Where are they?</h2><p class="muted" style="margin:4px 0 16px">Pick countries, then the cities to search. Countries with no city picked use their biggest cities.</p>
    <div class="grid g2">
      <div class="field"><span>Countries</span><div id="ms-cc"></div><div class="hint">Missing a country? <a href="#/locations">Add it under Locations</a>.</div></div>
      <div class="field"><span>Cities</span><div id="ms-city"></div><div class="hint">Type a city name and press “Add city” to save a new one.</div></div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <label class="field"><span>Biggest cities per country (when none picked)</span>
        <select class="input" id="wz-top">${[1, 2, 3, 5, 8, 10, 15].map(n => `<option ${n === WZ.top_cities ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <div id="cc-note" class="hint" style="align-self:end"></div>
    </div>`;
  const cityMs = widget(new MultiSelect($("#ms-city", body), {
    options: cityOptions(), value: WZ.cities, placeholder: "All of the biggest cities", createLabel: "Add city", maxChips: 8,
    onChange: v => { WZ.cities = v; note(); drawSummary(); },
    create: !isAdmin() ? null : async text => {
      if (!WZ.countries.length) throw new Error("Pick a country first");
      const cc = WZ.countries.length === 1 ? WZ.countries[0] : await pickCountry(text);
      if (!cc) return null;
      const c = S.meta.countries.find(x => x.code === cc);
      const city = titleCase(text.trim());
      if (!c.cities.some(x => x.toLowerCase() === city.toLowerCase()))
        await PUT(`/api/catalog/countries/${cc}`, { ...c, cities: [...c.cities, city] });
      await loadMeta(); cityMs.setOptions(cityOptions()); toast(`${city} added to ${c.name}`);
      return `${city}|${cc}`;
    },
  }));
  widget(new MultiSelect($("#ms-cc", body), {
    options: S.meta.countries.map(c => ({ value: c.code, label: c.name, hint: `${c.cities.length} cities` })),
    value: WZ.countries, placeholder: "Choose countries…",
    onChange: v => { WZ.countries = v; cityMs.setOptions(cityOptions()); WZ.cities = cityMs.value; note(); drawSummary(); },
  }));
  const note = () => {
    const withCity = new Set(WZ.cities.map(c => c.split("|")[1]));
    const rest = WZ.countries.filter(c => !withCity.has(c));
    $("#cc-note", body).textContent = rest.length ? `${rest.map(countryName).join(", ")}: top ${WZ.top_cities} cities will be searched.` : "";
  };
  note();
  $("#wz-top", body).onchange = e => { WZ.top_cities = +e.target.value; note(); drawSummary(); };
}
function pickCountry(city) {
  return new Promise(res => {
    const c = modal(`<div class="modal-h"><h2>Add “${esc(city)}” to which country?</h2></div><div class="modal-b">
      <select class="input" id="pc">${WZ.countries.map(cc => `<option value="${esc(cc)}">${esc(countryName(cc))}</option>`).join("")}</select></div>
      <div class="modal-f"><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>Add city</button></div>`);
    $("[data-x]", c).onclick = () => { closeModal(); res(null); };
    $("[data-ok]", c).onclick = () => { const v = $("#pc", c).value; closeModal(); res(v); };
  });
}

function stepSources(body) {
  const f = S.meta.features;
  body.innerHTML = `<h2>Where to look, and how deep</h2><p class="muted" style="margin:4px 0 16px">Google Maps gives the best data (phone, website, reviews). Directories are free but often blocked.</p>
    <div class="stack" id="src-list"></div>
    <div class="section-t">Analysis</div>
    <div class="grid g2">
      ${opt("serp", "Google ranking check", "Do they show up on page 1? Who beats them? (1 Serper search per niche + city)", f.serp, "Needs SERPER_API_KEY")}
      ${opt("pagespeed", "Mobile speed test", "Google PageSpeed score for every website. Slower but convincing for web & SEO.", true)}
      ${opt("ai_pitch", "AI-written pitches", "Claude writes a custom opener for each hot lead.", f.ai_pitch, "Needs ANTHROPIC_API_KEY")}
      ${opt("only_new", "Only new businesses", "Skip anyone found in an earlier campaign.", true)}
    </div>
    <label class="field" style="margin-top:16px;max-width:320px"><span>Max businesses per search</span>
      <select class="input" id="wz-limit">${[20, 40, 60, 100, 150, 200].map(n => `<option ${n === WZ.limit ? "selected" : ""}>${n}</option>`).join("")}</select>
      <div class="hint">Google Places returns 20 per call, so 60 = up to 3 calls per search.</div></label>`;
  $("#src-list", body).innerHTML = S.meta.sources.map(s => {
    const on = WZ.sources.includes(s.id), ok = s.ready;
    return `<div class="choice ${on ? "on" : ""} ${ok ? "" : "off"}" data-src="${esc(s.id)}">
      <div class="grow"><b>${esc(s.label)} ${ok ? "" : `<span class="badge err">Needs key</span>`}${s.country ? ` <span class="badge">${esc(s.country)} only</span>` : ""}</b>
      <small>${esc(ok ? (s.note || "Ready") : `Add ${s.needs} in Settings to use this`)}</small></div>
      <label class="switch"><input type="checkbox" ${on ? "checked" : ""} ${ok ? "" : "disabled"}><i></i></label></div>`;
  }).join("");
  $$("#src-list .choice", body).forEach(c => c.onclick = e => {
    e.preventDefault();
    if (c.classList.contains("off")) { location.hash = "#/settings"; return; }
    const id = c.dataset.src;
    WZ.sources = WZ.sources.includes(id) ? WZ.sources.filter(x => x !== id) : [...WZ.sources, id];
    stepSources(body); drawSummary();
  });
  $$("[data-opt]", body).forEach(c => c.onclick = e => {
    e.preventDefault(); if (c.classList.contains("off")) return;
    WZ[c.dataset.opt] = !WZ[c.dataset.opt]; stepSources(body); drawSummary();
  });
  $("#wz-limit", body).onchange = e => { WZ.limit = +e.target.value; drawSummary(); };
}
function opt(key, title, sub, available, need) {
  const on = available && WZ[key];
  return `<div class="choice ${on ? "on" : ""} ${available ? "" : "off"}" data-opt="${key}"><div class="grow"><b>${esc(title)}${available ? "" : ` <span class="badge err">${esc(need)}</span>`}</b><small>${esc(sub)}</small></div>
    <label class="switch"><input type="checkbox" ${on ? "checked" : ""} ${available ? "" : "disabled"}><i></i></label></div>`;
}

function stepReview(body) {
  const e = estimate();
  body.innerHTML = `<h2>Review & launch</h2><p class="muted" style="margin:4px 0 16px">Check everything, give it a name, and go. You can keep using the dashboard while it runs.</p>
    <label class="field"><span>Campaign name (optional)</span><input class="input" id="wz-name" maxlength="60" placeholder="e.g. Houston dentists — SEO" value="${esc(WZ.name)}"></label>
    <div class="grid g3" style="margin-top:16px">
      ${kpi("Searches", fmt(e.searches), "niche × city combinations")}
      ${kpi("Businesses (max)", fmt(e.maxLeads), "before removing duplicates")}
      ${kpi("API calls (max)", fmt(e.calls), "capped in Settings")}
    </div>
    ${e.searches > 400 ? `<div class="badge err" style="margin-top:12px;height:auto;padding:8px 12px">Too big: ${e.searches} searches (max 400). Remove some niches or cities.</div>` : ""}
    ${!S.meta.sources.some(s => s.needs && s.ready) ? `<div class="reason-block" style="margin-top:16px;background:var(--warm-soft);border-color:transparent"><b>No Google key yet.</b> <span class="muted">The campaign will only use free directories, which are often blocked. <a href="#/settings">Add a key</a> for reliable results.</span></div>` : ""}`;
  $("#wz-name", body).oninput = ev => { WZ.name = ev.target.value; };
}

function estimate() {
  const syn = WZ.expand ? WZ.niches.reduce((a, n) => a + 1 + (S.meta.niches.find(x => x.name === n)?.synonyms.length || 0), 0) : WZ.niches.length;
  const withCity = {};
  WZ.cities.forEach(c => { const [, cc] = c.split("|"); withCity[cc] = (withCity[cc] || 0) + 1; });
  const cities = WZ.countries.reduce((a, cc) => {
    const c = S.meta.countries.find(x => x.code === cc);
    return a + (withCity[cc] || Math.min(WZ.top_cities, c?.cities.length || 1) || 1);
  }, 0);
  const searches = syn * cities;
  const apiSrc = WZ.sources.filter(s => s === "google_places" || s === "serper_places").length;
  const calls = searches * apiSrc * Math.ceil(WZ.limit / 20) + (WZ.serp ? WZ.niches.length * cities : 0);
  return { searches, maxLeads: searches * Math.max(1, WZ.sources.length) * WZ.limit, calls };
}
function drawSummary() {
  const e = estimate();
  const chips = (arr, f = x => x) => arr.length ? arr.map(x => `<span class="tag plain">${esc(f(x))}</span>`).join("") : `<span class="faint">Not set</span>`;
  const cities = WZ.cities.length ? WZ.cities.map(c => c.split("|")[0]) : (WZ.countries.length ? [`Top ${WZ.top_cities} per country`] : []);
  $("#wz-summary").innerHTML = `<div class="card-h"><h2>Campaign summary</h2></div><div class="card-b"><dl>
    <dt>Services</dt><dd>${chips(WZ.services, svcLabel)}</dd>
    <dt>Niches</dt><dd>${chips(WZ.niches.slice(0, 10), titleCase)}${WZ.niches.length > 10 ? `<span class="faint">+${WZ.niches.length - 10}</span>` : ""}</dd>
    <dt>Countries</dt><dd>${chips(WZ.countries, countryName)}</dd>
    <dt>Cities</dt><dd>${chips(cities.slice(0, 10))}${cities.length > 10 ? `<span class="faint">+${cities.length - 10}</span>` : ""}</dd>
    <dt>Sources</dt><dd>${chips(WZ.sources, id => S.meta.sources.find(s => s.id === id)?.label || id)}</dd></dl>
    <div class="estimate"><div class="row"><span class="grow">Searches</span><b style="color:${e.searches > 400 ? "var(--err)" : "inherit"}">${fmt(e.searches)} / 400</b></div>
    <div class="row"><span class="grow">Max API calls</span><b>${fmt(e.calls)}</b></div></div></div>`;
}

async function launch() {
  const btn = $("#wz-next"); btn.disabled = true; btn.textContent = "Starting…";
  try {
    const body = {
      name: WZ.name.trim(), niches: WZ.niches, countries: WZ.countries, cities: WZ.cities, top_cities: WZ.top_cities,
      services: WZ.services, sources: WZ.sources, limit: WZ.limit, expand: WZ.expand,
      serp: WZ.serp && S.meta.features.serp, pagespeed: WZ.pagespeed, ai_pitch: WZ.ai_pitch && S.meta.features.ai_pitch, only_new: WZ.only_new,
    };
    S.job = await POST("/api/runs", body);
    WZ = null; startPolling(); renderProgress($("#view"));
  } catch (e) { fail(e); btn.disabled = false; btn.textContent = "🚀 Launch campaign"; }
}

function startPolling() {
  clearInterval(S.poll);
  const tick = async () => {
    try {
      const { live } = await GET("/api/runs");
      const j = live.find(x => x.id === S.job?.id) || live[0];
      if (!j) return;
      const wasRunning = S.job?.status === "running";
      S.job = j;
      runPill();
      if ($("#progress-view")) renderProgress($("#view"));
      if (j.status !== "running") {
        clearInterval(S.poll);
        if (wasRunning) { j.status === "done" ? toast(`Campaign finished: ${j.found} leads, ${j.hot} hot`) : toast("Campaign failed: " + j.error, true); loadMeta(); }
      }
    } catch { /* server restarting, keep polling */ }
  };
  tick(); S.poll = setInterval(tick, 1500);
}
function runPill() {
  const j = S.job;
  $("#nav-run").innerHTML = j && j.status === "running" ? `<span class="spinner" style="width:12px;height:12px"></span>` : "";
  $("#run-pill").innerHTML = j && j.status === "running"
    ? `<a class="btn sm" href="#/campaign"><span class="spinner"></span>Campaign running…</a>` : "";
}
function renderProgress(view) {
  const j = S.job, p = j.params || {};
  const running = j.status === "running";
  view.innerHTML = header(p.name || "Campaign", `${fmt(j.queries)} searches · started ${esc(ago(j.started_at))}`,
    running ? "" : `<button class="btn" id="pg-new">＋ New campaign</button><a class="btn primary" href="#/leads">View leads →</a>`) + `
  <div id="progress-view" class="grid g2">
    <div class="card"><div class="card-h">${running ? `<span class="spinner"></span><h2>Working on it…</h2>` : j.status === "done" ? `<span class="badge ok">Done</span><h2>Campaign complete</h2>` : `<span class="badge err">Failed</span><h2>Campaign failed</h2>`}</div>
      <div class="card-b">
        ${j.stages.length ? j.stages.map(s => `<div class="stage"><div class="row"><span class="grow">${esc(s.stage)}</span><b class="mono">${s.done}/${s.total}</b></div>
          <div class="bar"><i style="width:${s.total ? Math.round(s.done / s.total * 100) : 100}%"></i></div></div>`).join("") : `<div class="muted">Preparing searches…</div>`}
        ${j.error ? `<div class="reason-block" style="color:var(--err)">${esc(j.error)}</div>` : ""}
        ${!running ? `<div class="grid g2" style="margin-top:16px">${kpi("Leads found", fmt(j.found), "")}${kpi("Hot leads", fmt(j.hot), "", "var(--hot)")}</div>` : ""}
      </div></div>
    <div class="card"><div class="card-h"><h2>Activity log</h2></div><div class="card-b"><div class="log">${esc((j.log || []).join("\n") || "Waiting for first results…")}</div></div></div>
  </div>`;
  const nb = $("#pg-new"); if (nb) nb.onclick = () => { S.job = null; runPill(); freshWizard(); go(); };
}

// ================================================================== LEADS
const LF = { q: "", service: "", tier: "", status: "", country: "", niche: "", has_email: false, offset: 0, limit: 50 };
let LSEL = new Set(), LROWS = [];
route("leads", async (view) => {
  const qs = new URLSearchParams(location.hash.split("?")[1] || "");
  if ([...qs.keys()].length) { Object.assign(LF, { q: "", service: "", tier: "", status: "", country: "", niche: "", has_email: false, offset: 0 }); qs.forEach((v, k) => { if (k in LF) LF[k] = v; }); history.replaceState(null, "", "#/leads"); }
  const f = S.meta.facets;
  view.innerHTML = header("Leads", "Every business you've found, best opportunities first. Click a row for the full audit and pitch.",
    `<div class="ms" id="exp-wrap" style="position:relative"><button class="btn" id="exp-btn">⤓ Export</button>
      <div class="ms-panel" style="left:auto;right:0;width:240px;padding:4px">
        <div class="ms-opt" data-fmt="xlsx">Excel (.xlsx)<small>tabs per tier</small></div>
        <div class="ms-opt" data-fmt="csv">CSV</div>
        <div class="ms-opt" data-fmt="gsheet">Google Sheets${S.meta.features.gsheets ? "" : "<small>not set up</small>"}</div></div></div>`) + `
  <div class="card">
    <div class="toolbar">
      <input class="input search" id="lf-q" placeholder="Search name, website, email…" value="${esc(LF.q)}">
      <select class="input" id="lf-service" title="Score by service"><option value="">Best service</option>${S.meta.services.map(s => `<option value="${esc(s.id)}" ${LF.service === s.id ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select>
      <select class="input" id="lf-tier"><option value="">All tiers</option>${["hot", "warm", "cold"].map(t => `<option value="${t}" ${LF.tier === t ? "selected" : ""}>${titleCase(t)}</option>`).join("")}</select>
      <select class="input" id="lf-status"><option value="">Any status</option>${S.meta.statuses.map(s => `<option value="${s}" ${LF.status === s ? "selected" : ""}>${STATUS_LABEL[s]}</option>`).join("")}</select>
      <select class="input" id="lf-country"><option value="">All countries</option>${f.countries.map(c => `<option value="${esc(c)}" ${LF.country === c ? "selected" : ""}>${esc(countryName(c))}</option>`).join("")}</select>
      <select class="input" id="lf-niche"><option value="">All niches</option>${f.niches.map(n => `<option value="${esc(n)}" ${LF.niche === n ? "selected" : ""}>${esc(titleCase(n))}</option>`).join("")}</select>
      <label class="switch"><input type="checkbox" id="lf-email" ${LF.has_email ? "checked" : ""}><i></i><span>Has email</span></label>
    </div>
    <div class="bulkbar hidden" id="bulk"><span id="bulk-n"></span><span class="grow"></span>
      <select class="input" id="bulk-status" style="height:32px;width:auto"><option value="">Set status…</option>${S.meta.statuses.map(s => `<option value="${s}">${STATUS_LABEL[s]}</option>`).join("")}</select>
      <button class="btn sm" id="bulk-xlsx">Export selected</button><button class="btn sm ghost" id="bulk-clear">Clear</button></div>
    <div class="table-wrap" id="lt"></div>
    <div class="pager" id="lp"></div>
  </div>`;
  let deb;
  $("#lf-q").oninput = e => { clearTimeout(deb); deb = setTimeout(() => { LF.q = e.target.value.trim(); LF.offset = 0; loadLeads(); }, 250); };
  [["lf-service", "service"], ["lf-tier", "tier"], ["lf-status", "status"], ["lf-country", "country"], ["lf-niche", "niche"]].forEach(([id, k]) =>
    $("#" + id).onchange = e => { LF[k] = e.target.value; LF.offset = 0; loadLeads(); });
  $("#lf-email").onchange = e => { LF.has_email = e.target.checked; LF.offset = 0; loadLeads(); };
  const ew = $("#exp-wrap");
  $("#exp-btn").onclick = () => ew.classList.toggle("open");
  const off = e => { if (!ew.contains(e.target)) ew.classList.remove("open"); };
  document.addEventListener("mousedown", off); widget({ destroy: () => document.removeEventListener("mousedown", off) });
  $$("[data-fmt]", ew).forEach(o => o.onclick = () => { ew.classList.remove("open"); exportLeads(o.dataset.fmt); });
  $("#bulk-status").onchange = async e => {
    const st = e.target.value; if (!st) return;
    try { const r = await POST("/api/leads/bulk-status", { keys: [...LSEL], status: st }); toast(`${r.updated} leads marked ${STATUS_LABEL[st]}`); LSEL.clear(); loadLeads(); } catch (er) { fail(er); }
  };
  $("#bulk-xlsx").onclick = () => exportLeads("xlsx", [...LSEL]);
  $("#bulk-clear").onclick = () => { LSEL.clear(); drawLeads(); };
  LSEL = new Set();
  await loadLeads();
});
const leadQS = () => { const p = new URLSearchParams(); Object.entries(LF).forEach(([k, v]) => { if (v && !["offset", "limit"].includes(k)) p.set(k, v); }); return p; };
async function loadLeads() {
  const p = leadQS(); p.set("limit", LF.limit); p.set("offset", LF.offset);
  const r = await GET("/api/leads?" + p);
  LROWS = r.rows; LROWS.total = r.total;
  $("#nav-leads").textContent = "";
  drawLeads();
}
function drawLeads() {
  const rows = LROWS, total = rows.total || 0;
  const svc = LF.service;
  $("#lt").innerHTML = rows.length ? `<table class="t"><thead><tr>
    <th style="width:36px"><input type="checkbox" id="sel-all" aria-label="Select all" ${rows.every(r => LSEL.has(r.key)) ? "checked" : ""}></th>
    <th>Business</th><th>Contact</th><th>${svc ? esc(svcLabel(svc)) : "Best fit"}</th><th>Why they need you</th><th>Status</th></tr></thead><tbody>
    ${rows.map(l => {
      const sc = svc ? l.scores?.[svc] : l.scores?.[l.best_service];
      const id = svc || l.best_service;
      return `<tr class="click ${LSEL.has(l.key) ? "sel" : ""}" data-lead="${esc(l.key)}">
      <td><input type="checkbox" data-sel="${esc(l.key)}" ${LSEL.has(l.key) ? "checked" : ""} aria-label="Select"></td>
      <td class="biz"><b>${esc(l.name)}</b><small>${esc(titleCase(l.niche))} · ${esc([l.city, countryName(l.country)].filter(Boolean).join(", "))}</small>
        ${l.rating ? `<small style="display:block">★ ${esc(l.rating)} (${fmt(l.review_count)})</small>` : ""}</td>
      <td style="font-size:13px">${l.email ? `<div>${esc(l.email)}</div>` : ""}${l.phone ? `<div class="muted">${esc(l.phone)}</div>` : ""}
        ${l.website ? `<a href="${safeUrl(l.website)}" target="_blank" rel="noopener noreferrer">${esc(l.domain || "website")}</a>` : `<span class="badge warm">No website</span>`}</td>
      <td>${scorePill(sc?.score)}<div class="muted" style="font-size:12px;margin-top:4px">${esc(id ? svcLabel(id) : "")}</div></td>
      <td class="why">${esc((sc?.reasons || []).slice(0, 2).join(" · "))}</td>
      <td><select class="input" data-st="${esc(l.key)}" style="height:32px;width:auto;min-width:112px">${S.meta.statuses.map(s => `<option value="${s}" ${l.status === s ? "selected" : ""}>${STATUS_LABEL[s]}</option>`).join("")}</select></td></tr>`;
    }).join("")}</tbody></table>`
    : `<div class="empty"><b>No leads match</b>${[...leadQS()].length ? "Try removing a filter." : `Run a campaign to start finding businesses. <div style="margin-top:12px"><a class="btn primary" href="#/campaign">Start a campaign</a></div>`}</div>`;
  const from = total ? LF.offset + 1 : 0, to = Math.min(total, LF.offset + rows.length);
  $("#lp").innerHTML = `<span>${fmt(from)}–${fmt(to)} of ${fmt(total)}</span><div class="row">
    <button class="btn sm" id="pp" ${LF.offset ? "" : "disabled"}>← Previous</button><button class="btn sm" id="pn" ${to < total ? "" : "disabled"}>Next →</button></div>`;
  $("#pp").onclick = () => { LF.offset = Math.max(0, LF.offset - LF.limit); loadLeads(); };
  $("#pn").onclick = () => { LF.offset += LF.limit; loadLeads(); };
  const bulk = $("#bulk"); bulk.classList.toggle("hidden", !LSEL.size); $("#bulk-n").textContent = `${LSEL.size} selected`; $("#bulk-status").value = "";
  const sa = $("#sel-all");
  if (sa) sa.onchange = e => { rows.forEach(r => e.target.checked ? LSEL.add(r.key) : LSEL.delete(r.key)); drawLeads(); };
  $$("[data-sel]").forEach(c => c.onchange = e => { e.target.checked ? LSEL.add(c.dataset.sel) : LSEL.delete(c.dataset.sel); drawLeads(); });
  $$("[data-st]").forEach(s => s.onchange = async e => {
    try { await PATCH(`/api/leads/${encodeURIComponent(s.dataset.st)}`, { status: e.target.value }); toast(`Marked ${STATUS_LABEL[e.target.value]}`); const r = LROWS.find(x => x.key === s.dataset.st); if (r) r.status = e.target.value; } catch (er) { fail(er); }
  });
  bindRowClicks($("#lt"));
}
async function exportLeads(fmtName, keys) {
  const p = leadQS(); p.set("fmt", fmtName);
  if (keys?.length) p.set("keys", keys.join(","));
  if (fmtName === "gsheet") {
    if (!S.meta.features.gsheets) return toast("Add GOOGLE_SHEETS_CREDENTIALS in Settings first", true);
    toast("Creating Google Sheet…");
    try { const r = await GET("/api/export?" + p); window.open(r.url, "_blank", "noopener"); } catch (e) { fail(e); }
    return;
  }
  // check first so an empty result shows a message instead of downloading an error page
  const r = await fetch("/api/export?" + p);
  if (!r.ok) { let m = "Export failed"; try { m = (await r.json()).detail; } catch { } return toast(m, true); }
  const blob = await r.blob();
  const name = (r.headers.get("content-disposition") || "").match(/filename="?([^"]+)"?/)?.[1] || `leads.${fmtName}`;
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast("Export downloaded");
}

// ------------------------------------------------------------------ lead drawer
async function openLead(key) {
  const d = $("#drawer");
  d.innerHTML = `<div class="drawer-b"><span class="spinner"></span></div>`;
  d.classList.add("open"); d.setAttribute("aria-hidden", "false"); $("#scrim").classList.add("open");
  let l;
  try { l = await GET(`/api/leads/${encodeURIComponent(key)}`); } catch (e) { d.innerHTML = `<div class="drawer-b">${esc(e.message)}</div>`; return; }
  const a = l.audit || {};
  const scores = Object.values(l.scores || {}).sort((x, y) => y.score - x.score);
  const yes = (v, good = true) => v ? `<span class="badge ${good ? "ok" : "warm"}">Yes</span>` : `<span class="badge ${good ? "warm" : "ok"}">No</span>`;
  const fact = (k, v) => v === "" || v == null ? "" : `<div><span>${esc(k)}</span>${v}</div>`;
  d.innerHTML = `<div class="drawer-h"><div class="grow"><h2 style="font-size:18px">${esc(l.name)}</h2>
      <div class="muted">${esc(titleCase(l.niche))} · ${esc([l.city, countryName(l.country)].filter(Boolean).join(", "))}</div>
      <div class="row" style="margin-top:8px">${scores[0] ? `<span class="badge ${scores[0].tier}">${esc(titleCase(scores[0].tier))} for ${esc(svcLabel(scores[0].service))}</span>` : ""}
      ${l.rating ? `<span class="badge">★ ${esc(l.rating)} · ${fmt(l.review_count)} reviews</span>` : ""}</div></div>
      <button class="btn icon ghost" id="dr-x" aria-label="Close">✕</button></div>
    <div class="drawer-b">
      <div class="facts">
        ${fact("Email", l.email ? `<a href="mailto:${esc(l.email)}">${esc(l.email)}</a>` + (l.email_status === "valid_mx" ? ` <span class="badge ok">verified domain</span>` : "") : `<span class="faint">Not found</span>`)}
        ${fact("Phone", l.phone ? `<a href="tel:${esc(l.phone)}">${esc(l.phone)}</a>` : `<span class="faint">Not found</span>`)}
        ${fact("Website", l.website ? `<a href="${safeUrl(l.website)}" target="_blank" rel="noopener noreferrer">${esc(l.domain || l.website)}</a>` : `<span class="badge warm">No website</span>`)}
        ${fact("Google Maps", l.maps_url ? `<a href="${safeUrl(l.maps_url)}" target="_blank" rel="noopener noreferrer">Open listing</a>` : "")}
        ${fact("Address", esc(l.address || ""))}
        ${fact("Found via", esc((l.sources || []).join(", ")))}
      </div>
      <div class="section-t">Sales status</div>
      <div class="row"><select class="input" id="dr-status" style="width:auto">${S.meta.statuses.map(s => `<option value="${s}" ${l.status === s ? "selected" : ""}>${STATUS_LABEL[s]}</option>`).join("")}</select></div>
      <textarea class="input" id="dr-notes" placeholder="Notes: who you spoke to, next step…" style="margin-top:8px">${esc(l.notes)}</textarea>
      <div class="section-t">Pitch</div>
      ${l.pitch ? `<div class="pitch" id="dr-pitch">${esc(l.pitch)}</div><button class="btn sm" id="dr-copy" style="margin-top:8px">Copy pitch</button>` : `<div class="muted">No pitch: this business scored too low.</div>`}
      <div class="section-t">Scores by service</div>
      ${scores.map(s => `<div class="reason-block"><div class="row"><b class="grow">${esc(svcLabel(s.service))}</b>${scorePill(s.score)}</div>
        ${s.reasons.length ? `<ul>${s.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}</div>`).join("") || `<div class="muted">Not scored</div>`}
      ${l.audit ? `<div class="section-t">Website audit</div><div class="facts">
        ${fact("Reachable", yes(a.reachable))}${fact("HTTPS", yes(a.https))}
        ${fact("Mobile friendly", yes(a.has_viewport))}${fact("Load time", a.load_ms ? `${(a.load_ms / 1000).toFixed(1)}s` : "")}
        ${fact("Mobile speed", a.pagespeed_mobile != null ? scorePill(a.pagespeed_mobile) : "")}
        ${fact("Built with", esc(a.cms || ""))}
        ${fact("Google rank", l.serp_checked ? (l.serp_rank ? `#${l.serp_rank} for “${esc(l.serp_query)}”` : `Not in top 20 for “${esc(l.serp_query)}”`) : "")}
        ${fact("Top competitor", esc(l.serp_leader || ""))}
        ${fact("Google Ads tag", yes(a.has_google_ads_tag))}${fact("Meta pixel", yes(a.has_meta_pixel))}
        ${fact("Analytics", yes(a.has_ga4 || a.has_gtm))}${fact("Live chat", a.has_chat_widget ? `<span class="badge ok">${esc(a.chat_widget || "Yes")}</span>` : yes(false))}
        ${fact("Online booking", yes(a.has_booking))}${fact("Copyright year", esc(a.copyright_year || ""))}
        ${fact("Socials", Object.entries(a.socials || {}).map(([k, u]) => `<a href="${safeUrl(u)}" target="_blank" rel="noopener noreferrer">${esc(k)}</a>`).join(", ") || `<span class="faint">None found</span>`)}
      </div>${(a.seo_issues || []).length ? `<div class="reason-block" style="margin-top:12px"><b>SEO issues</b><ul>${a.seo_issues.map(i => `<li>${esc(i)}</li>`).join("")}</ul></div>` : ""}` : ""}
    </div>
    <div class="drawer-f"><button class="btn primary" id="dr-save">Save</button><span class="grow"></span>
      <button class="btn danger" id="dr-supp" title="Never export this business again">Do not contact</button></div>`;
  $("#dr-x").onclick = closeDrawer;
  const cp = $("#dr-copy"); if (cp) cp.onclick = () => navigator.clipboard.writeText(l.pitch).then(() => toast("Pitch copied"), () => toast("Copy failed", true));
  const save = async (quiet) => {
    try {
      await PATCH(`/api/leads/${encodeURIComponent(key)}`, { status: $("#dr-status").value, notes: $("#dr-notes").value });
      if (!quiet) toast("Saved");
      const r = LROWS.find(x => x.key === key); if (r) { r.status = $("#dr-status").value; if ($("#lt")) drawLeads(); }
    } catch (e) { fail(e); }
  };
  $("#dr-status").onchange = () => save();
  $("#dr-save").onclick = () => save();
  $("#dr-supp").onclick = async () => {
    const vals = [l.email, l.domain, l.phone].filter(Boolean);
    if (!vals.length) return toast("No email, domain or phone to block", true);
    if (!await confirmBox("Do not contact?", `${esc(l.name)} (${vals.map(esc).join(", ")}) will be excluded from every future campaign and export.`, "Block contact")) return;
    try { for (const v of vals) await POST("/api/suppress", { value: v }); await PATCH(`/api/leads/${encodeURIComponent(key)}`, { status: "lost" }); toast("Contact blocked"); closeDrawer(); if ($("#lt")) loadLeads(); } catch (e) { fail(e); }
  };
}

// ================================================================== CAMPAIGNS
route("campaigns", async view => {
  const { live, history } = await GET("/api/runs");
  const running = live.find(j => j.status === "running");
  view.innerHTML = header("Campaigns", "Every search you've run. Re-run one to find businesses that opened since.", `<a class="btn primary" href="#/campaign">＋ New campaign</a>`) +
    (running ? `<a class="card card-b row" href="#/campaign" style="margin-bottom:16px;color:inherit;text-decoration:none"><span class="spinner"></span><b class="grow">${esc(running.params.name || "Campaign")} is running</b><span class="muted">View progress →</span></a>` : "") + `
    <div class="card"><div class="table-wrap">${history.length ? `<table class="t"><thead><tr><th>Campaign</th><th>Services</th><th>Where</th><th>Found</th><th>Hot</th><th>When</th><th></th></tr></thead><tbody>
    ${history.map(r => {
      const p = r.params || {};
      const isWeb = Array.isArray(p.niches);
      return `<tr><td class="biz"><b>${esc(p.name || (isWeb ? p.niches.slice(0, 3).map(titleCase).join(", ") : p.Niche || "CLI run"))}</b>
        <small>${esc(isWeb ? `${p.niches.length} niches${p.by ? ` · by ${p.by}` : ""}` : "from command line")}</small></td>
        <td>${isWeb ? (p.services.length ? p.services.map(s => `<span class="tag plain">${esc(svcLabel(s))}</span>`).join(" ") : `<span class="muted">All</span>`) : esc(p.Services || "")}</td>
        <td style="font-size:13px">${esc(isWeb ? [p.countries.map(countryName).join(", "), p.cities.length ? `${p.cities.length} cities` : `top ${p.top_cities} cities`].join(" · ") : [p.Country, p.Cities].filter(Boolean).join(" · "))}</td>
        <td><b>${fmt(r.found)}</b></td><td><span class="badge hot">${fmt(r.qualified)}</span></td>
        <td class="muted" style="white-space:nowrap">${esc(ago(r.started_at))}</td>
        <td>${isWeb ? `<button class="btn sm" data-rerun="${r.id}">Run again</button>` : ""}</td></tr>`;
    }).join("")}</tbody></table>` : `<div class="empty"><b>No campaigns yet</b>Your first campaign will show up here.<div style="margin-top:12px"><a class="btn primary" href="#/campaign">Start a campaign</a></div></div>`}</div></div>`;
  $$("[data-rerun]", view).forEach(b => b.onclick = () => {
    const p = history.find(r => r.id === +b.dataset.rerun).params;
    const okSvc = new Set(S.meta.services.map(s => s.id)), okCc = new Set(S.meta.countries.map(c => c.code));
    freshWizard({ ...p, name: p.name ? `${p.name} (again)` : "", services: p.services.filter(s => okSvc.has(s)),
      countries: p.countries.filter(c => okCc.has(c)), step: 4, only_new: true });
    location.hash = "#/campaign";
  });
});

// ================================================================== SERVICES
route("services", async view => {
  await loadMeta();
  const all = S.catalog.services;
  view.innerHTML = header("Services", "What your agency sells. Each service decides which website problems make a business a good lead.",
    `<button class="btn primary" id="svc-new">＋ New service</button>`) + `
    <div class="grid g3">${all.map(s => `<div class="card svc-card" style="${s.enabled ? "" : "opacity:.65"}">
      <div class="top"><div class="grow"><h3 style="font-size:15px">${esc(s.label)}</h3><div class="muted" style="font-size:13px">${esc(s.description || "No description")}</div></div>
        <label class="switch" title="${s.enabled ? "Enabled" : "Disabled"}"><input type="checkbox" data-en="${esc(s.id)}" ${s.enabled ? "checked" : ""}><i></i></label></div>
      <div class="meta">${s.builtin ? `<span class="badge">Built-in</span>` : `<span class="badge brand">Custom</span>`}
        <span class="badge">${Object.keys(s.signals).length} signals</span><span class="badge">Base ${s.base}</span>
        <span class="badge">${S.catalog.niches.filter(n => n.services.includes(s.id)).length} niches</span></div>
      <div class="chips">${Object.entries(s.signals).sort((a, b) => b[1].points - a[1].points).slice(0, 4).map(([k, v]) => `<span class="tag plain" style="font-size:12px">${esc(S.catalog.signals[k]?.label || k)} <b style="margin-left:4px;color:${v.points < 0 ? "var(--err)" : "var(--ok)"}">${v.points > 0 ? "+" : ""}${v.points}</b></span>`).join("")}</div>
      <div class="row" style="margin-top:auto"><button class="btn sm" data-edit="${esc(s.id)}">Edit scoring</button><span class="grow"></span>
        ${s.builtin ? "" : `<button class="btn sm ghost danger" data-del="${esc(s.id)}">Delete</button>`}</div></div>`).join("")}</div>`;
  $("#svc-new").onclick = async () => { if (await editService(null)) go(); };
  $$("[data-edit]", view).forEach(b => b.onclick = async () => { if (await editService(b.dataset.edit)) go(); });
  $$("[data-en]", view).forEach(c => c.onchange = async () => {
    const s = all.find(x => x.id === c.dataset.en);
    try { await PUT(`/api/catalog/services/${s.id}`, { ...s, enabled: c.checked }); toast(`${s.label} ${c.checked ? "enabled" : "disabled"}`); go(); } catch (e) { fail(e); c.checked = !c.checked; }
  });
  $$("[data-del]", view).forEach(b => b.onclick = async () => {
    const s = all.find(x => x.id === b.dataset.del);
    if (!await confirmBox(`Delete ${s.label}?`, "Existing leads keep their old scores, but new campaigns won't score this service. This can't be undone.")) return;
    try { await DEL(`/api/catalog/services/${s.id}`); toast("Service deleted"); go(); } catch (e) { fail(e); }
  });
});

/** Create/edit a service in a modal. Resolves to the saved id, or null if cancelled. */
function editService(sid, prefill = {}) {
  return new Promise(async res => {
    if (!S.catalog) await loadMeta();
    const sigs = S.catalog.signals;
    const s = sid ? JSON.parse(JSON.stringify(S.catalog.services.find(x => x.id === sid)))
      : { label: "", description: "", base: 15, signals: {}, pitch_hook: "", enabled: true, ...prefill };
    const groups = [...new Set(Object.values(sigs).map(x => x.group))];
    const sigOptions = sel => groups.map(g => `<optgroup label="${esc(g)}">${Object.entries(sigs).filter(([, v]) => v.group === g)
      .map(([k, v]) => `<option value="${esc(k)}" ${k === sel ? "selected" : ""}>${esc(v.label)}</option>`).join("")}</optgroup>`).join("");
    const c = modal(`<div class="modal-h"><div class="grow"><h2>${sid ? `Edit ${esc(s.label)}` : "New service"}</h2>
      <div class="muted" style="font-size:13px">A business starts at the base score, then gains or loses points for each problem we detect on it.</div></div></div>
      <div class="modal-b">
        <div class="grid g2"><label class="field"><span>Service name</span><input class="input" id="sv-label" maxlength="60" value="${esc(s.label)}" placeholder="e.g. Social media management"></label>
        <label class="field"><span>Short description</span><input class="input" id="sv-desc" maxlength="120" value="${esc(s.description)}" placeholder="What you deliver"></label></div>
        <div class="grid g2" style="margin-top:12px"><label class="field"><span>Base score (0–100)</span><input class="input" type="number" id="sv-base" min="0" max="100" value="${s.base}">
          <div class="hint">Starting point before signals are added.</div></label>
          <label class="field"><span>Pitch hook</span><input class="input" id="sv-hook" maxlength="200" value="${esc(s.pitch_hook)}" placeholder="We help {niche}s in {city} get more…">
          <div class="hint">Closing line of the pitch. Use {niche} and {city}.</div></label></div>
        <div class="section-t">Scoring signals</div>
        <div class="muted" style="font-size:13px;margin-bottom:10px">Pick what makes a business need this service. Use negative points for things that make them a worse fit.</div>
        <div id="sv-sigs"></div>
        <button class="btn sm" id="sv-add" type="button">＋ Add signal</button>
        <div class="hint" id="sv-max"></div>
      </div>
      <div class="modal-f"><span class="muted grow" id="sv-err" style="color:var(--err)"></span><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>${sid ? "Save changes" : "Create service"}</button></div>`, { wide: true });
    let rows = Object.entries(s.signals).map(([k, v]) => ({ k, points: v.points, reason: v.reason || "" }));
    const draw = () => {
      $("#sv-sigs", c).innerHTML = rows.length ? `<div class="sig-row muted" style="font-size:12px;font-weight:600"><span>Signal</span><span>Points</span><span class="reason">Reason shown to sales rep / in pitch</span><span></span></div>` +
        rows.map((r, i) => `<div class="sig-row" data-i="${i}"><select class="input" data-f="k">${sigOptions(r.k)}</select>
          <input class="input" type="number" data-f="points" min="-100" max="100" value="${r.points}">
          <input class="input reason" data-f="reason" maxlength="200" value="${esc(r.reason)}" placeholder="${esc(sigs[r.k]?.reason || "")}">
          <button class="btn icon sm ghost" data-rm="${i}" type="button" aria-label="Remove">✕</button></div>`).join("")
        : `<div class="empty" style="padding:20px">No signals yet. Add one to start scoring.</div>`;
      const max = Math.min(100, (+$("#sv-base", c).value || 0) + rows.reduce((a, r) => a + Math.max(0, +r.points || 0), 0));
      $("#sv-max", c).textContent = rows.length ? `Best possible score: ${max}. Hot leads need ${S.catalog.scoring.hot}+.` : "";
      $$("[data-f]", c).forEach(inp => inp.oninput = inp.onchange = () => {
        const i = +inp.closest(".sig-row").dataset.i, f = inp.dataset.f;
        rows[i][f] = f === "points" ? +inp.value : inp.value;
        if (f === "k") { rows[i].reason = ""; draw(); } else if (f === "points") draw.max();
      });
      $$("[data-rm]", c).forEach(b => b.onclick = () => { rows.splice(+b.dataset.rm, 1); draw(); });
    };
    draw.max = () => { const max = Math.min(100, (+$("#sv-base", c).value || 0) + rows.reduce((a, r) => a + Math.max(0, +r.points || 0), 0)); $("#sv-max", c).textContent = `Best possible score: ${max}. Hot leads need ${S.catalog.scoring.hot}+.`; };
    draw();
    $("#sv-base", c).oninput = () => rows.length && draw.max();
    $("#sv-add", c).onclick = () => {
      const used = new Set(rows.map(r => r.k));
      const k = Object.keys(sigs).find(x => !used.has(x)) || Object.keys(sigs)[0];
      rows.push({ k, points: 20, reason: "" }); draw();
    };
    $("[data-x]", c).onclick = () => { closeModal(); res(null); };
    $("[data-ok]", c).onclick = async () => {
      const signals = {};
      for (const r of rows) signals[r.k] = { points: +r.points || 0, reason: r.reason.trim() || sigs[r.k]?.reason || "" };
      const body = { label: $("#sv-label", c).value.trim(), description: $("#sv-desc", c).value.trim(), base: +$("#sv-base", c).value || 0,
        pitch_hook: $("#sv-hook", c).value.trim(), signals, enabled: s.enabled };
      if (!body.label) return $("#sv-err", c).textContent = "Give the service a name";
      if (!rows.length) return $("#sv-err", c).textContent = "Add at least one signal";
      try {
        const r = sid ? await PUT(`/api/catalog/services/${sid}`, body) : await POST("/api/catalog/services", body);
        closeModal(); await loadMeta(); toast(sid ? "Service saved" : `Service “${body.label}” created`); res(r.id);
      } catch (e) { $("#sv-err", c).textContent = e.message; }
    };
  });
}

// ================================================================== NICHES
let NF = { q: "", service: "" };
route("niches", async view => {
  await loadMeta();
  view.innerHTML = header("Niches", "Types of businesses you target. Link each niche to the services it usually buys, so campaigns can suggest it.",
    `<button class="btn primary" id="n-new">＋ Add niche</button>`) + `
  <div class="card"><div class="toolbar"><input class="input search" id="n-q" placeholder="Search niches…" value="${esc(NF.q)}">
    <select class="input" id="n-svc"><option value="">All services</option>${S.catalog.services.map(s => `<option value="${esc(s.id)}" ${NF.service === s.id ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select>
    <span class="grow"></span><span class="muted" id="n-count"></span></div>
    <div class="table-wrap" id="n-table"></div></div>`;
  const draw = () => {
    const q = NF.q.toLowerCase();
    const rows = S.catalog.niches.filter(n => (!q || n.name.includes(q) || n.synonyms.some(s => s.includes(q))) && (!NF.service || n.services.includes(NF.service)));
    $("#n-count").textContent = `${rows.length} niches`;
    $("#n-table").innerHTML = rows.length ? `<table class="t"><thead><tr><th>Niche</th><th>Sells well for</th><th>Also searches</th><th>Flags</th><th></th></tr></thead><tbody>
      ${rows.map(n => `<tr><td><b>${esc(titleCase(n.name))}</b></td>
        <td>${n.services.map(s => `<span class="tag plain" style="margin:2px">${esc(svcLabel(s))}</span>`).join("") || `<span class="faint">—</span>`}</td>
        <td class="muted" style="font-size:13px;max-width:280px">${esc(n.synonyms.join(", ")) || "—"}</td>
        <td>${n.high_value ? `<span class="badge ok">High value</span> ` : ""}${n.appointment ? `<span class="badge brand">Appointments</span>` : ""}</td>
        <td style="text-align:right;white-space:nowrap"><button class="btn sm" data-edit="${esc(n.name)}">Edit</button> <button class="btn sm ghost danger" data-del="${esc(n.name)}">Delete</button></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty"><b>No niches found</b>Add one with the button above.</div>`;
    $$("[data-edit]").forEach(b => b.onclick = () => editNiche(b.dataset.edit));
    $$("[data-del]").forEach(b => b.onclick = async () => {
      if (!await confirmBox(`Delete “${titleCase(b.dataset.del)}”?`, "It will no longer be suggested in campaigns. Leads already found are kept.")) return;
      try { await DEL(`/api/catalog/niches/${encodeURIComponent(b.dataset.del)}`); await loadMeta(); toast("Niche deleted"); draw(); } catch (e) { fail(e); }
    });
  };
  $("#n-q").oninput = e => { NF.q = e.target.value.trim(); draw(); };
  $("#n-svc").onchange = e => { NF.service = e.target.value; draw(); };
  $("#n-new").onclick = () => editNiche(null);
  draw();
  async function editNiche(name) {
    const n = name ? S.catalog.niches.find(x => x.name === name) : { name: "", services: NF.service ? [NF.service] : [], synonyms: [], high_value: false, appointment: false };
    const c = modal(`<div class="modal-h"><h2>${name ? "Edit niche" : "Add niche"}</h2></div><div class="modal-b stack">
      <label class="field"><span>Niche name</span><input class="input" id="ni-name" maxlength="60" value="${esc(n.name)}" placeholder="e.g. med spa"><div class="hint">Use the words people type into Google Maps.</div></label>
      <div class="field"><span>Services they usually buy</span><div id="ni-svc"></div></div>
      <label class="field"><span>Related search phrases</span><input class="input" id="ni-syn" value="${esc(n.synonyms.join(", "))}" placeholder="e.g. medical spa, aesthetics clinic"><div class="hint">Comma-separated. Used when “search related phrasings” is on.</div></label>
      <label class="switch"><input type="checkbox" id="ni-hv" ${n.high_value ? "checked" : ""}><i></i><span><b>High-value customers</b><span class="muted" style="display:block;font-size:13px">Each customer is worth a lot (lawyers, dentists…). Boosts SEO & Ads scores.</span></span></label>
      <label class="switch"><input type="checkbox" id="ni-ap" ${n.appointment ? "checked" : ""}><i></i><span><b>Runs on appointments</b><span class="muted" style="display:block;font-size:13px">Bookings matter (clinics, salons…). Boosts chatbot & booking scores.</span></span></label>
      </div><div class="modal-f"><span class="muted grow" id="ni-err" style="color:var(--err)"></span><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>${name ? "Save" : "Add niche"}</button></div>`);
    const ms = new MultiSelect($("#ni-svc", c), { options: S.catalog.services.map(s => ({ value: s.id, label: s.label })), value: n.services, placeholder: "Choose services…" });
    $("[data-x]", c).onclick = () => { ms.destroy(); closeModal(); };
    $("[data-ok]", c).onclick = async () => {
      const body = { name: $("#ni-name", c).value.trim().toLowerCase(), services: ms.value,
        synonyms: $("#ni-syn", c).value.split(",").map(x => x.trim().toLowerCase()).filter(Boolean),
        high_value: $("#ni-hv", c).checked, appointment: $("#ni-ap", c).checked };
      if (!body.name) return $("#ni-err", c).textContent = "Enter a name";
      try {
        name ? await PUT(`/api/catalog/niches/${encodeURIComponent(name)}`, body) : await POST("/api/catalog/niches", body);
        ms.destroy(); closeModal(); await loadMeta(); toast(name ? "Niche saved" : `“${body.name}” added`); draw();
      } catch (e) { $("#ni-err", c).textContent = e.message; }
    };
  }
});

// ================================================================== LOCATIONS
let LOC_SEL = null;
route("locations", async view => {
  await loadMeta();
  const cs = S.catalog.countries;
  if (!LOC_SEL || !cs.some(c => c.code === LOC_SEL)) LOC_SEL = cs[0]?.code || null;
  view.innerHTML = header("Locations", "Countries and cities you can target in campaigns.", `<button class="btn primary" id="cc-new">＋ Add country</button>`) + `
  <div class="split"><div class="card"><div class="toolbar"><input class="input search" id="cc-q" placeholder="Search countries…"></div><div id="cc-list"></div></div>
  <div class="card" id="cc-detail"></div></div>`;
  const list = (q = "") => {
    $("#cc-list").innerHTML = cs.filter(c => !q || c.name.toLowerCase().includes(q) || c.code.toLowerCase() === q).map(c => `<div class="list-item ${c.code === LOC_SEL ? "on" : ""}" data-cc="${esc(c.code)}">
      <span class="code">${esc(c.code)}</span><div class="grow"><b>${esc(c.name)}</b><small>${c.cities.length} cities</small></div>
      ${c.enabled ? "" : `<span class="badge">Off</span>`}</div>`).join("") || `<div class="empty">No countries</div>`;
    $$("[data-cc]").forEach(i => i.onclick = () => { LOC_SEL = i.dataset.cc; list($("#cc-q").value.trim().toLowerCase()); detail(); });
  };
  const detail = () => {
    const c = cs.find(x => x.code === LOC_SEL);
    const box = $("#cc-detail");
    if (!c) { box.innerHTML = `<div class="empty"><b>No countries yet</b>Add one to start.</div>`; return; }
    box.innerHTML = `<div class="card-h"><span class="code">${esc(c.code)}</span><h2 class="grow">${esc(c.name)}</h2>
      <label class="switch"><input type="checkbox" id="cd-en" ${c.enabled ? "checked" : ""}><i></i><span>Available in campaigns</span></label></div>
      <div class="card-b">
        <div class="section-t" style="margin-top:0">Cities <span class="faint">(${c.cities.length}, biggest first)</span></div>
        <div class="row" style="margin-bottom:12px"><input class="input grow" id="cd-city" placeholder="Add cities, comma-separated: Austin, Miami" style="flex:1 1 220px"><button class="btn" id="cd-add">Add</button></div>
        <div class="chips" id="cd-cities">${c.cities.map((ct, i) => `<span class="tag">${i < 3 ? "★ " : ""}${esc(ct)}<button type="button" data-rmc="${i}" aria-label="Remove ${esc(ct)}">×</button></span>`).join("") || `<span class="muted">No cities yet. Campaigns will search the whole country.</span>`}</div>
        <div class="hint" style="margin-top:10px">★ The first cities are used when a campaign doesn't pick any. Order matters: put the biggest first.</div>
        <div class="section-t">Details</div>
        <div class="grid g3"><label class="field"><span>Country name</span><input class="input" id="cd-name" value="${esc(c.name)}"></label>
        <label class="field"><span>Code</span><input class="input" value="${esc(c.code)}" disabled></label>
        <label class="field"><span>Google region (gl)</span><input class="input" id="cd-gl" maxlength="2" value="${esc(c.gl)}"></label></div>
        <div class="row" style="margin-top:16px"><button class="btn primary" id="cd-save">Save details</button><span class="grow"></span><button class="btn danger" id="cd-del">Delete country</button></div>
      </div>`;
    const put = async (patch, msg) => {
      try { await PUT(`/api/catalog/countries/${c.code}`, { ...c, ...patch }); Object.assign(c, patch); await loadMeta(); toast(msg); list(); detail(); } catch (e) { fail(e); }
    };
    const add = () => {
      const have = new Set(c.cities.map(x => x.toLowerCase()));
      const fresh = $("#cd-city").value.split(",").map(x => x.trim()).filter(x => x && !have.has(x.toLowerCase()));
      if (!fresh.length) return;
      put({ cities: [...c.cities, ...fresh.map(titleCase)] }, `${fresh.length} ${fresh.length > 1 ? "cities" : "city"} added`);
    };
    $("#cd-add").onclick = add;
    $("#cd-city").onkeydown = e => { if (e.key === "Enter") add(); };
    $$("[data-rmc]").forEach(b => b.onclick = () => put({ cities: c.cities.filter((_, i) => i !== +b.dataset.rmc) }, "City removed"));
    $("#cd-en").onchange = e => put({ enabled: e.target.checked }, e.target.checked ? "Country enabled" : "Country hidden from campaigns");
    $("#cd-save").onclick = () => put({ name: $("#cd-name").value.trim() || c.name, gl: $("#cd-gl").value.trim().toLowerCase() || c.code.toLowerCase() }, "Saved");
    $("#cd-del").onclick = async () => {
      if (!await confirmBox(`Delete ${c.name}?`, `${c.cities.length} cities will be removed. Leads already found in ${esc(c.name)} are kept.`)) return;
      try { await DEL(`/api/catalog/countries/${c.code}`); LOC_SEL = null; toast("Country deleted"); go(); } catch (e) { fail(e); }
    };
  };
  $("#cc-q").oninput = e => list(e.target.value.trim().toLowerCase());
  $("#cc-new").onclick = () => {
    const c = modal(`<div class="modal-h"><h2>Add country</h2></div><div class="modal-b stack">
      <div class="grid g2"><label class="field"><span>Country name</span><input class="input" id="nc-name" placeholder="e.g. Germany"></label>
      <label class="field"><span>2-letter code</span><input class="input" id="nc-code" maxlength="2" placeholder="DE" style="text-transform:uppercase"><div class="hint">ISO code, used for phone numbers.</div></label></div>
      <label class="field"><span>Cities</span><textarea class="input" id="nc-cities" placeholder="Berlin, Hamburg, Munich, Cologne, Frankfurt"></textarea><div class="hint">Comma-separated, biggest first.</div></label>
      </div><div class="modal-f"><span class="muted grow" id="nc-err" style="color:var(--err)"></span><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>Add country</button></div>`);
    $("[data-x]", c).onclick = closeModal;
    $("[data-ok]", c).onclick = async () => {
      const code = $("#nc-code", c).value.trim().toUpperCase();
      const body = { code, name: $("#nc-name", c).value.trim(), gl: code.toLowerCase(), enabled: true,
        cities: $("#nc-cities", c).value.split(/[,\n]/).map(x => x.trim()).filter(Boolean).map(titleCase) };
      if (!body.name) return $("#nc-err", c).textContent = "Enter a name";
      if (!/^[A-Z]{2}$/.test(code)) return $("#nc-err", c).textContent = "Code must be 2 letters";
      try { await POST("/api/catalog/countries", body); closeModal(); LOC_SEL = code; toast(`${body.name} added`); go(); } catch (e) { $("#nc-err", c).textContent = e.message; }
    };
  };
  list(); detail();
});

// ================================================================== SETTINGS
const KEY_INFO = [
  ["GOOGLE_PLACES_API_KEY", "Google Places API", "Main source for businesses on Google Maps, with phone and website.", "https://console.cloud.google.com/apis/library/places.googleapis.com"],
  ["SERPER_API_KEY", "Serper.dev", "Google Maps backup source + Google ranking check. 2,500 free searches.", "https://serper.dev"],
  ["ANTHROPIC_API_KEY", "Anthropic (Claude)", "Writes a custom pitch for every hot lead.", "https://console.anthropic.com"],
  ["PAGESPEED_API_KEY", "Google PageSpeed", "Optional: higher quota for mobile speed tests.", "https://developers.google.com/speed/docs/insights/v5/get-started"],
  ["GOOGLE_SHEETS_CREDENTIALS", "Google Sheets credentials", "Path to the service-account JSON file, for Sheets export.", ""],
  ["GOOGLE_SHEET_ID", "Google Sheet", "Link or ID of your sheet. Share it (Editor) with the service-account email first.", ""],
  ["PROXY_URL", "Proxy", "Optional: for scraping directories at volume. http://user:pass@host:port", ""],
];
route("settings", async view => {
  const st = await GET("/api/settings");
  view.innerHTML = header("Settings", "Workspace, data sources and lead scoring.") + `
  <div class="grid" style="gap:16px">
    <div class="card"><div class="card-h"><h2>Workspace</h2></div><div class="card-b grid g2">
      <label class="field"><span>Workspace / agency name</span><input class="input" id="st-ws" maxlength="40" value="${esc(st.workspace_name)}"><div class="hint">Shown in the sidebar and in export file names.</div></label></div></div>
    <div class="card"><div class="card-h"><h2>API keys</h2><span class="grow"></span><span class="muted" style="font-size:13px">Saved to the local .env file on this computer</span></div><div class="card-b">
      ${KEY_INFO.map(([k, name, desc, link]) => `<div class="keyrow"><div><div class="row"><span class="dot ${st.env[k] ? "ok" : ""}"></span><b>${esc(name)}</b></div>
        <div class="muted" style="font-size:13px">${esc(desc)} ${link ? `<a href="${link}" target="_blank" rel="noopener noreferrer">Get key ↗</a>` : ""}</div></div>
        <input class="input mono" data-env="${k}" type="${["GOOGLE_SHEETS_CREDENTIALS", "GOOGLE_SHEET_ID"].includes(k) ? "text" : "password"}" autocomplete="off" spellcheck="false" placeholder="${st.env[k] ? esc(st.env[k]) : "Not set"}">
        <div class="row">${st.env[k] ? `<button class="btn sm ghost danger" data-clear="${k}">Remove</button>` : ""}</div></div>`).join("")}
    </div></div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h2>Lead scoring</h2></div><div class="card-b grid g3">
        <label class="field"><span>Hot from</span><input class="input" type="number" id="st-hot" min="1" max="100" value="${st.scoring.hot}"></label>
        <label class="field"><span>Warm from</span><input class="input" type="number" id="st-warm" min="1" max="100" value="${st.scoring.warm}"></label>
        <label class="field"><span>No-contact cap</span><input class="input" type="number" id="st-cap" min="0" max="100" value="${st.scoring.no_contact_cap}"></label>
        <div class="hint" style="grid-column:1/-1">Businesses with no email or phone can never score above the cap, so you only chase leads you can reach.</div></div></div>
      <div class="card"><div class="card-h"><h2>Bill protection & performance</h2></div><div class="card-b grid g2">
        <label class="field"><span>Max Google Places calls / campaign</span><input class="input" type="number" min="1" data-envnum="MAX_PLACES_CALLS_PER_RUN" value="${esc(st.env.MAX_PLACES_CALLS_PER_RUN || 300)}"></label>
        <label class="field"><span>Max Serper calls / campaign</span><input class="input" type="number" min="1" data-envnum="MAX_SERPER_CALLS_PER_RUN" value="${esc(st.env.MAX_SERPER_CALLS_PER_RUN || 300)}"></label>
        <label class="field"><span>Websites audited at once</span><input class="input" type="number" min="1" max="50" data-envnum="CONCURRENCY" value="${esc(st.env.CONCURRENCY || 20)}"></label>
        <label class="field"><span>Re-audit websites after (days)</span><input class="input" type="number" min="0" data-envnum="CACHE_DAYS" value="${esc(st.env.CACHE_DAYS || 14)}"></label>
        <label class="field" style="grid-column:1/-1"><span>Claude model for pitches</span><input class="input mono" data-envnum="PITCH_MODEL" value="${esc(st.env.PITCH_MODEL || "claude-opus-5-5")}"></label></div></div>
    </div>
    <div class="row" style="justify-content:flex-end"><span class="muted" id="st-err" style="color:var(--err)"></span><button class="btn primary" id="st-save">Save settings</button></div>
  </div>`;
  const save = async (extraEnv = {}) => {
    const env = { ...extraEnv };
    $$("[data-env]").forEach(i => { if (i.value.trim()) env[i.dataset.env] = i.value.trim(); });
    $$("[data-envnum]").forEach(i => { const v = i.value.trim(); if (v && v !== String(st.env[i.dataset.envnum] || "")) env[i.dataset.envnum] = v; });
    const body = { env, workspace_name: $("#st-ws").value, hot: +$("#st-hot").value, warm: +$("#st-warm").value, no_contact_cap: +$("#st-cap").value };
    $("#st-err").textContent = "";
    try { await PUT("/api/settings", body); await loadMeta(); toast("Settings saved"); go(); } catch (e) { $("#st-err").textContent = e.message; }
  };
  $("#st-save").onclick = () => save();
  $$("[data-clear]").forEach(b => b.onclick = async () => {
    if (!await confirmBox("Remove this key?", "Features that need it will stop working until you add it again.", "Remove")) return;
    save({ [b.dataset.clear]: null });
  });
});

// ------------------------------------------------------------------ boot
(function theme() {
  let t = null; try { t = localStorage.getItem("lf-theme"); } catch { }
  if (t) document.documentElement.dataset.theme = t;
  $("#theme-btn").onclick = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("lf-theme", document.documentElement.dataset.theme); } catch { }
  };
})();
// sidebar: on desktop the button collapses it to an icon rail (remembered), on mobile it opens a drawer
const isMobile = () => matchMedia("(max-width:960px)").matches;
function setMobileNav(open) {
  $("#sidebar").classList.toggle("open", open); $("#side-scrim").classList.toggle("open", open);
  $("#menu-btn").setAttribute("aria-expanded", open);
}
function setCollapsed(on) {
  $("#app").classList.toggle("collapsed", on);
  $("#menu-btn").setAttribute("aria-expanded", !on);
  try { localStorage.setItem("lf-collapsed", on ? "1" : ""); } catch { }
}
$$("#nav a").forEach(a => a.title = a.querySelector(".txt")?.textContent.trim() || "");
try { if (localStorage.getItem("lf-collapsed")) setCollapsed(true); } catch { }
$("#menu-btn").onclick = () => isMobile() ? setMobileNav(!$("#sidebar").classList.contains("open")) : setCollapsed(!$("#app").classList.contains("collapsed"));
$("#side-scrim").onclick = () => setMobileNav(false);
document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#sidebar").classList.contains("open")) setMobileNav(false); });
matchMedia("(max-width:960px)").addEventListener("change", () => setMobileNav(false));

// ================================================================== TEAM (admin)
route("team", async view => {
  const { users } = await GET("/api/users");
  view.innerHTML = header("Team", "Who can log in. Admins manage API keys, scoring, services, niches, locations and the team. Members run campaigns and work leads.",
    `<button class="btn primary" id="u-new">＋ Add user</button>`) + `
  <div class="card"><div class="table-wrap"><table class="t"><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Last login</th><th></th></tr></thead><tbody>
  ${users.map(u => `<tr><td class="biz"><b>${esc(u.name || u.email)}</b><small>${esc(u.email)}${u.id === S.user.id ? " · you" : ""}</small></td>
    <td><span class="badge ${u.role === "admin" ? "brand" : ""}">${u.role === "admin" ? "Admin" : "Member"}</span></td>
    <td>${u.active ? `<span class="badge ok">Active</span>` : `<span class="badge">Disabled</span>`}</td>
    <td class="muted">${esc(u.last_login ? ago(u.last_login) : "Never")}</td>
    <td style="text-align:right;white-space:nowrap">${u.id === S.user.id ? "" : `<button class="btn sm" data-edit="${u.id}">Edit</button> <button class="btn sm ghost danger" data-del="${u.id}">Remove</button>`}</td></tr>`).join("")}
  </tbody></table></div></div>`;
  $("#u-new").onclick = () => editUser(null);
  $$("[data-edit]", view).forEach(b => b.onclick = () => editUser(users.find(u => u.id === +b.dataset.edit)));
  $$("[data-del]", view).forEach(b => b.onclick = async () => {
    const u = users.find(x => x.id === +b.dataset.del);
    if (!await confirmBox(`Remove ${u.email}?`, "They will be logged out and can't sign in again. Leads and campaigns stay.", "Remove")) return;
    try { await DEL(`/api/users/${u.id}`); toast("User removed"); go(); } catch (e) { fail(e); }
  });
  function editUser(u) {
    const c = modal(`<div class="modal-h"><h2>${u ? `Edit ${esc(u.email)}` : "Add user"}</h2></div><div class="modal-b stack">
      ${u ? "" : `<label class="field"><span>Email</span><input class="input" id="u-email" type="email" autocomplete="off"></label>`}
      <label class="field"><span>Name</span><input class="input" id="u-name" maxlength="80" value="${esc(u?.name || "")}"></label>
      <label class="field"><span>Role</span><select class="input" id="u-role"><option value="member">Member: campaigns and leads</option><option value="admin" ${u?.role === "admin" ? "selected" : ""}>Admin: everything</option></select></label>
      ${u ? `<label class="switch"><input type="checkbox" id="u-active" ${u.active ? "checked" : ""}><i></i><span>Can log in</span></label>` : ""}
      <label class="field"><span>${u ? "New password (leave empty to keep)" : "Temporary password"}</span><input class="input" id="u-pw" type="password" autocomplete="new-password" minlength="8">
        <div class="hint">At least 8 characters. Share it privately; they can change it after logging in.</div></label>
      </div><div class="modal-f"><span class="muted grow" id="u-err" style="color:var(--err)"></span><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>${u ? "Save" : "Add user"}</button></div>`);
    $("[data-x]", c).onclick = closeModal;
    $("[data-ok]", c).onclick = async () => {
      const pw = $("#u-pw", c).value;
      try {
        if (u) await PATCH(`/api/users/${u.id}`, { name: $("#u-name", c).value, role: $("#u-role", c).value, active: $("#u-active", c).checked, password: pw || null });
        else await POST("/api/users", { email: $("#u-email", c).value, name: $("#u-name", c).value, role: $("#u-role", c).value, password: pw });
        closeModal(); toast(u ? "User saved" : "User added"); go();
      } catch (e) { $("#u-err", c).textContent = e.message; }
    };
  }
});

// ================================================================== AUTH
function showAuth(setup) {
  clearInterval(S.poll);
  closeModal(); closeDrawer();
  $("#app").classList.add("hidden");
  const box = $("#auth"); box.classList.remove("hidden");
  box.innerHTML = `<div class="auth-card">
    <div class="brand"><div class="logo">L</div><div><b>LeadForge</b><small>Lead intelligence</small></div></div>
    <h1>${setup ? "Create the admin account" : "Log in"}</h1>
    <div class="muted" style="margin-bottom:18px">${setup ? "First start: this account can manage keys, settings and the team." : "Welcome back. Log in to your workspace."}</div>
    <form id="auth-form" novalidate>
      ${setup ? `<label class="field"><span>Your name</span><input class="input" id="a-name" autocomplete="name"></label>` : ""}
      <label class="field"><span>Email</span><input class="input" id="a-email" type="email" autocomplete="username" required></label>
      <label class="field"><span>Password</span><input class="input" id="a-pw" type="password" autocomplete="${setup ? "new-password" : "current-password"}" required>
        ${setup ? `<div class="hint">At least 8 characters.</div>` : ""}</label>
      <div id="a-err" style="color:var(--err);min-height:20px;font-size:13px"></div>
      <button class="btn primary" type="submit">${setup ? "Create account" : "Log in"}</button>
    </form>
    ${setup ? "" : `<div class="hint" style="margin-top:14px;text-align:center">Forgot your password? Ask your admin to reset it.</div>`}</div>`;
  setTimeout(() => $(setup ? "#a-name" : "#a-email")?.focus(), 30);
  $("#auth-form").onsubmit = async e => {
    e.preventDefault();
    const btn = $("#auth-form button"); btn.disabled = true;
    try {
      const body = { email: $("#a-email").value, password: $("#a-pw").value };
      if (setup) body.name = $("#a-name").value;
      await POST(setup ? "/api/auth/setup" : "/api/auth/login", body);
      location.reload();
    } catch (er) { $("#a-err").textContent = er.message; btn.disabled = false; }
  };
}

function drawUserBox() {
  const u = S.user;
  document.body.classList.toggle("is-admin", isAdmin());
  $("#user-box").innerHTML = `<span class="avatar" title="${esc(u.email)}">${esc((u.name || u.email)[0].toUpperCase())}</span>
    <div class="who"><b>${esc(u.name || u.email)}</b><small>${u.role === "admin" ? "Admin" : "Member"}</small></div>
    <button class="btn icon sm ghost" id="pw-btn" type="button" title="Change password" aria-label="Change password">🔑</button>
    <button class="btn icon sm ghost" id="logout-btn" type="button" title="Log out" aria-label="Log out">⎋</button>`;
  $("#logout-btn").onclick = async () => { try { await POST("/api/auth/logout", {}); } catch { } location.hash = ""; location.reload(); };
  $("#pw-btn").onclick = () => {
    const c = modal(`<div class="modal-h"><h2>Change password</h2></div><div class="modal-b stack">
      <label class="field"><span>Current password</span><input class="input" id="pw-cur" type="password" autocomplete="current-password"></label>
      <label class="field"><span>New password</span><input class="input" id="pw-new" type="password" autocomplete="new-password"><div class="hint">At least 8 characters. Other devices will be logged out.</div></label>
      </div><div class="modal-f"><span class="muted grow" id="pw-err" style="color:var(--err)"></span><button class="btn" data-x>Cancel</button><button class="btn primary" data-ok>Change password</button></div>`);
    $("[data-x]", c).onclick = closeModal;
    $("[data-ok]", c).onclick = async () => {
      try { await POST("/api/auth/password", { current: $("#pw-cur", c).value, new: $("#pw-new", c).value }); closeModal(); toast("Password changed"); }
      catch (e) { $("#pw-err", c).textContent = e.message; }
    };
  };
}

(async function boot() {
  let me;
  try {
    const r = await fetch("/api/auth/me", { credentials: "same-origin" });
    me = await r.json();
    if (r.status === 401) return showAuth(!!me.setup);
    if (!r.ok) throw new Error(me.detail || `Server error ${r.status}`);
  } catch (e) {
    $("#app").classList.remove("hidden");
    $("#view").innerHTML = `<div class="card empty"><b>Can't reach the LeadForge server</b>${esc(e.message)}</div>`; return;
  }
  S.user = me.user;
  $("#app").classList.remove("hidden");
  drawUserBox();
  try { await loadMeta(); } catch (e) { $("#view").innerHTML = `<div class="card empty"><b>Can't load data</b>${esc(e.message)}</div>`; return; }
  try { const { live } = await GET("/api/runs"); const j = live.find(x => x.status === "running"); if (j) { S.job = j; startPolling(); } } catch { }
  go();
})();
