/* Family Finance — app logic. No personal data lives in this file. */
"use strict";

let DB = null;            // the whole data document
let currentPage = "dashboard";
let saveTimer = null;
let incomeYear = null;          // null = auto-select latest year per earner
let hideValues = localStorage.getItem("ffa_hideValues") === "1";

/* ================= persistence ================= */
async function loadDB() {
  const r = await fetch("/api/data");
  DB = await r.json();
  migrate();
}
function migrate() {
  DB.settings = DB.settings || {};
  const d = { persons: [], locations: [], currency: "INR", locale: "en-IN", appName: "Family Finance" };
  for (const k in d) if (DB.settings[k] === undefined) DB.settings[k] = d[k];
  DB.income = DB.income || { persons: [] };
  for (const k of ["expenses","monthlyInvestments","portfolio","gold","loans","goals","cards","documents","ledgers"])
    DB[k] = DB[k] || [];

  /* ---- predefined value lists (seeded once from existing data) ---- */
  DB.settings.predefined = DB.settings.predefined || {};
  const P = DB.settings.predefined;
  const seed = (key, vals, defaults) => {
    if (!Array.isArray(P[key])) P[key] = [];
    [...(defaults || []), ...vals].forEach(v => { v = (v || "").trim(); if (v && !P[key].includes(v)) P[key].push(v); });
  };
  seed("expenseGroups", DB.expenses.map(e => e.group));
  if (!P.expenseCategories || typeof P.expenseCategories !== "object") P.expenseCategories = {};
  DB.expenses.forEach(e => {
    const g = (e.group || "Other").trim();
    P.expenseCategories[g] = P.expenseCategories[g] || [];
    if (e.category && !P.expenseCategories[g].includes(e.category)) P.expenseCategories[g].push(e.category);
  });
  seed("locations", [...(DB.settings.locations || []), ...DB.expenses.map(e => e.location)]);
  seed("investmentInstruments", DB.monthlyInvestments.map(i => i.category), ["PF", "NPS", "MF SIP", "Gold Scheme", "RD"]);
  seed("assetClasses", DB.portfolio.map(p => p.subCategory), ["Equity", "Debt", "Fixed", "Real estate", "Liquidity", "Gold", "Other"]);
  seed("assetNames", DB.portfolio.map(p => p.category));
  seed("goalTypes", DB.goals.map(g => g.type), ["Car", "Home", "Family Function", "Travel", "Education"]);
  seed("banks", DB.cards.map(c => c.bank));
  seed("cardTypes", DB.cards.map(c => c.type), ["Credit", "Debit", "Credit Virtual", "Debit Virtual", "Food Card", "Priority Pass", "Other"]);
  seed("networks", DB.cards.map(c => c.variant), ["VISA", "MasterCard", "RuPay", "AmEx"]);
  seed("lenders", DB.loans.map(L => L.lender));
  /* instruments for the Lending page = your card names + any savings accounts already used */
  seed("ledgerInstruments",
    DB.cards.map(c => c.name).concat((DB.ledgers || []).flatMap(L => (L.transactions || []).map(t => t.instrument))),
    ["ICICI Savings", "IDFC Savings", "HDFC Savings", "Jupiter", "Cash / UPI"]);
  seed("incomeComponents",
    (DB.income.persons || []).flatMap(p =>
      (p.salaryYears || []).flatMap(yr => (yr.components || []).map(c => c.component))
      .concat((p.components || p.gross || []).map(c => c.component))),
    ["BASIC SALARY", "HOUSE RENT ALLOWANCE", "PERSONAL ALLOWANCE", "BONUS", "EMPLOYER PF", "EMPLOYER NPS", "SPECIAL ALLOWANCE"]);
  seed("deductionComponents",
    (DB.income.persons || []).flatMap(p =>
      (p.salaryYears || []).flatMap(yr => (yr.deductions || []).map(c => c.component))
      .concat((p.deductions || []).map(c => c.component))),
    ["INCOME TAX (TDS)", "EMPLOYEE PF", "PROFESSIONAL TAX", "EMPLOYEE NPS"]);

  /* which extra dimension each expense section tracks: location | person | none */
  if (!P.expenseGroupMeta || typeof P.expenseGroupMeta !== "object") P.expenseGroupMeta = {};
  P.expenseGroups.forEach(g => {
    if (!P.expenseGroupMeta[g]) {
      const items = DB.expenses.filter(e => (e.group || "Other") === g);
      P.expenseGroupMeta[g] = { dim: items.some(e => e.location) ? "location" : items.some(e => e.person) ? "person" : "none" };
    }
  });

  /* ---- schema migrations for new fields ---- */
  DB.settings.personMeta = DB.settings.personMeta || {};   // { name: { dob: "YYYY-MM-DD" } }
  DB.portfolio.forEach(p => { if (p.maturityAge === undefined) p.maturityAge = null; });
  DB.loans.forEach(L => { if (!L.prepayments) L.prepayments = []; });
  /* ledgers: ensure each person has a transactions array and each row has the newer fields */
  DB.ledgers.forEach(L => {
    L.transactions = L.transactions || [];
    L.transactions.forEach(t => {
      if (t.cashbackMode === undefined) t.cashbackMode = "fixed";
      if (t.cashback === undefined) t.cashback = 0;
      if (t.extraCharges === undefined) t.extraCharges = 0;
      if (t.cancelled === undefined) t.cancelled = false;
      if (t.instrument === undefined) t.instrument = "";
      if (t.notes === undefined) t.notes = "";
    });
  });
  DB.gold.forEach(g => { if (g.purchasePrice === undefined) g.purchasePrice = null; });
  DB.goals.forEach(g => {
    if (g.currentMarketValue === undefined) g.currentMarketValue = null;
    if (g.lastPriceFetch === undefined) g.lastPriceFetch = null;
  });

  /* ---- income schema v2: flatten gross/ctc/inHand into components array ---- */
  (DB.income.persons || []).forEach(p => {
    if (p.components || p.salaryYears) return;
    const gross = p.gross || [], ctc = p.ctc || [], ded = p.deductions || [], inHand = p.inHand || [];
    const grossNames = new Set(gross.map(c => (c.component || "").trim().toUpperCase()));
    p.components = gross.map(c => ({ component: c.component, amount: num(c.amount), scope: "gross" }))
      .concat(ctc.filter(c => !grossNames.has((c.component || "").trim().toUpperCase()))
        .map(c => ({ component: c.component, amount: num(c.amount), scope: "ctc" })));
    p.deductions = ded.map(c => ({ component: c.component, amount: num(c.amount) }));
    const grossTot = gross.reduce((s, c) => s + num(c.amount), 0);
    const dedTot = p.deductions.reduce((s, c) => s + num(c.amount), 0);
    const ihTot = inHand.reduce((s, c) => s + num(c.amount), 0);
    const gap = grossTot - dedTot - ihTot;
    if (ihTot && gap > 1) p.deductions.push({ component: "Other deductions (derived)", amount: Math.round(gap) });
    delete p.ctc; delete p.gross; delete p.inHand;
  });
  /* ---- income schema v3: wrap components/deductions into salaryYears array ---- */
  (DB.income.persons || []).forEach(p => {
    if (p.salaryYears) return;
    const curYear = new Date().getFullYear();
    p.salaryYears = [{
      year: curYear,
      components: p.components || [],
      deductions: p.deductions || [],
      variablePctEligible: 0,
      variablePctEarned: null,
      oneTimeBonus: 0
    }];
    delete p.components;
    delete p.deductions;
  });
}

/* ---- predefined-list helpers ---- */
function predList(key) {
  const P = DB.settings.predefined;
  if (key === "persons") return DB.settings.persons;
  if (key.startsWith("expenseCategories:")) {
    const g = key.slice("expenseCategories:".length);
    return P.expenseCategories[g] = P.expenseCategories[g] || [];
  }
  return P[key] = P[key] || [];
}
function addPred(key, val) {
  val = (val || "").trim();
  if (!val) return;
  const l = predList(key);
  if (!l.includes(val)) l.push(val);
}
function save(immediate) {
  setSaveStatus("saving");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(doSave, immediate ? 0 : 600);
}
async function doSave() {
  try {
    const r = await fetch("/api/data", { method: "PUT", body: JSON.stringify(DB) });
    if (!r.ok) throw new Error((await r.json()).error || r.status);
    const j = await r.json();
    DB.settings.lastUpdated = j.lastUpdated;
    setSaveStatus("saved");
    el("#lastUpdated").textContent = "Updated " + fmtDateTime(j.lastUpdated);
  } catch (e) {
    setSaveStatus("error");
    toast("Save failed: " + e.message, true);
  }
}
function setSaveStatus(s) {
  const n = el("#saveStatus");
  n.className = "save-status" + (s === "saving" ? " saving" : s === "error" ? " error" : "");
  n.textContent = s === "saving" ? "Saving…" : s === "error" ? "Save failed!" : "All changes saved";
}

/* ================= utilities ================= */
const el = (q, root) => (root || document).querySelector(q);
const els = (q, root) => [...(root || document).querySelectorAll(q)];
const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/* num() and validAmount() live in finance-math.js (loaded first) */
/* small ⓘ tooltip — explains a term/column without cluttering the page (native title) */
const info = t => `<span class="info" title="${esc(t)}">ⓘ</span>`;
/* date of birth helpers (DOB stored in DB.settings.personMeta[name].dob) */
function personDob(name) { return (DB.settings.personMeta && DB.settings.personMeta[name] || {}).dob || null; }
function personBirthYear(name) { const d = personDob(name); if (!d) return null; const y = new Date(d).getFullYear(); return isFinite(y) ? y : null; }
const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/* Day / Month / Year dropdowns — reliable for far-past dates (native date pickers aren't).
   Keyed by the person's index so element ids stay safe regardless of name. */
function dobPickerHTML(i) {
  const dob = personDob(DB.settings.persons[i]) || "";
  const [y, m, d] = dob ? dob.split("-").map(n => parseInt(n, 10)) : ["", "", ""];
  const nowY = new Date().getFullYear();
  const opt = (v, lab, cur) => `<option value="${v}"${String(v) === String(cur) ? " selected" : ""}>${lab}</option>`;
  const days = Array.from({ length: 31 }, (_, k) => opt(k + 1, k + 1, d)).join("");
  const months = MONTH_ABBR.map((mn, k) => opt(k + 1, mn, m)).join("");
  const years = Array.from({ length: nowY - 1929 }, (_, k) => { const yy = nowY - k; return opt(yy, yy, y); }).join("");
  const sel = (id, ph, inner) => `<select id="dob_${id}_${i}" onchange="updPersonDobParts(${i})" style="width:auto;min-width:62px">
    <option value="">${ph}</option>${inner}</select>`;
  return `<div style="display:flex;gap:6px;flex-wrap:wrap">${sel("d", "Day", days)}${sel("m", "Mon", months)}${sel("y", "Year", years)}</div>`;
}
function updPersonDobParts(i) {
  const name = DB.settings.persons[i];
  const d = el("#dob_d_" + i).value, m = el("#dob_m_" + i).value, y = el("#dob_y_" + i).value;
  if (!y && !m && !d) { updPersonDob(name, ""); return; }   // cleared
  if (!y || !m || !d) return;                                // still choosing — wait for all three
  updPersonDob(name, `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`);
}

function fmtMoney(v, compact) {
  if (v === null || v === undefined || isNaN(v)) return "–";
  if (hideValues) return "••••";
  const cur = DB.settings.currency || "INR", loc = DB.settings.locale || "en-IN";
  try {
    return new Intl.NumberFormat(loc, {
      style: "currency", currency: cur, maximumFractionDigits: 0,
      ...(compact ? { notation: "compact", maximumFractionDigits: 2 } : {})
    }).format(v);
  } catch (e) { return cur + " " + Math.round(v).toLocaleString(); }
}
const fmtNum = v => isFinite(v) ? new Intl.NumberFormat(DB.settings.locale || "en-IN", { maximumFractionDigits: 1 }).format(v) : "–";
const fmtPct = v => hideValues ? "•••" : (isFinite(v) ? (v * 100).toFixed(1) + "%" : "–");
function fmtDate(d) {
  if (!d) return "–";
  const dt = new Date(d);
  return isNaN(dt) ? esc(d) : dt.toLocaleDateString(DB.settings.locale || "en-IN", { year: "numeric", month: "short", day: "numeric" });
}
function fmtDateTime(d) {
  if (!d) return "";
  const dt = new Date(d);
  return isNaN(dt) ? "" : dt.toLocaleString(DB.settings.locale || "en-IN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function toast(msg, isErr) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " error" : "");
  t.textContent = msg;
  el("#toastRoot").appendChild(t);
  setTimeout(() => t.remove(), isErr ? 6000 : 2800);
}

/* ================= financial math ================= */
/* calcEMI, outstandingFromEMI, amortSchedule, monthsSince, loanState now live in
   finance-math.js (loaded before app.js) so they can be unit-tested under Node. */
function goldInvested() { return computeGoldInvested(DB.gold); }
function goldGain() { return computeGoldGain(DB.gold); }

/* ================= modal framework ================= */
function openModal(html, wide) {
  closeModal();
  const ov = document.createElement("div");
  ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal${wide ? " wide" : ""}">${html}</div>`;
  ov.addEventListener("mousedown", e => { if (e.target === ov) closeModal(); });
  el("#modalRoot").appendChild(ov);
  const first = el(".modal input,.modal select", ov);
  if (first) first.focus();
  return ov;
}
function closeModal() { el("#modalRoot").innerHTML = ""; }
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

/* build a form modal from field specs; onSubmit gets {name:value} */
function formModal(title, fields, onSubmit, opts) {
  const body = fields.map(f => {
    if (f.type === "row") return `<div class="fld-row">${f.fields.map(fieldHTML).join("")}</div>`;
    return fieldHTML(f);
  }).join("");
  const ov = openModal(`
    <h3>${esc(title)}</h3>
    <form id="mf">${body}
      ${opts && opts.note ? `<p class="muted small">${opts.note}</p>` : ""}
      <div class="modal-actions">
        <button type="button" class="btn ghost" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn">${esc((opts && opts.submitLabel) || "Save")}</button>
      </div>
    </form>`);
  const flat = fields.flatMap(f => f.type === "row" ? f.fields : [f]);
  el("#mf", ov).addEventListener("submit", e => {
    e.preventDefault();
    const vals = {};
    els("[name]", ov).forEach(i => {
      vals[i.name] = i.type === "checkbox" ? i.checked : i.value.trim();
    });
    /* required check (hidden combo inputs bypass native validation) */
    for (const f of flat) {
      if (f.required && f.name in vals && !String(vals[f.name]).trim()) {
        const fld = el(`[name="${f.name}"]`, ov).closest(".fld");
        if (!fld || fld.style.display !== "none") return toast(`Please fill in "${f.label}"`, true);
      }
    }
    /* persist combo values chosen via "Add new (save for reuse)" */
    els(".combo[data-mode=add]", ov).forEach(c => {
      if (c.dataset.list) addPred(c.dataset.list, el("input[type=hidden]", c).value);
    });
    onSubmit(vals);
  });
  initCombos(ov);
  if (opts && opts.onReady) opts.onReady(ov);
  return ov;
}
/* wire up combo fields: dropdown of predefined values + "add predefined" + "one-time custom" */
function initCombos(ov) {
  els(".combo", ov).forEach(c => {
    const sel = el(".combo-select", c), hid = el("input[type=hidden]", c),
          free = el(".combo-free", c), inp = el(".combo-input", c);
    const fire = () => c.dispatchEvent(new CustomEvent("combochange", { bubbles: true }));
    if (free.style.display === "none" && !hid.value) {
      if (["__add__", "__custom__"].includes(sel.value)) {
        /* empty predefined list: jump straight to free-text entry (saved as predefined) */
        c.dataset.mode = "add"; sel.style.display = "none"; free.style.display = "";
      } else hid.value = sel.value;
    }
    sel.addEventListener("change", () => {
      if (sel.value === "__add__" || sel.value === "__custom__") {
        c.dataset.mode = sel.value === "__add__" ? "add" : "custom";
        sel.style.display = "none"; free.style.display = "";
        hid.value = inp.value.trim(); inp.focus();
      } else { c.dataset.mode = ""; hid.value = sel.value; }
      fire();
    });
    inp.addEventListener("input", () => { hid.value = inp.value.trim(); fire(); });
    el(".combo-back", c).addEventListener("click", () => {
      free.style.display = "none"; sel.style.display = ""; c.dataset.mode = "";
      if (["__add__", "__custom__"].includes(sel.value)) sel.selectedIndex = 0;
      hid.value = ["__add__", "__custom__"].includes(sel.value) ? "" : sel.value;
      fire();
    });
  });
}
/* swap the option list of an existing combo (e.g. categories depend on selected group) */
function setComboOptions(c, options, listKey) {
  const sel = el(".combo-select", c), hid = el("input[type=hidden]", c);
  if (listKey !== undefined) c.dataset.list = listKey;
  const allowEmpty = sel.dataset.allowEmpty === "1";
  sel.innerHTML = (allowEmpty ? `<option value="">—</option>` : "") +
    options.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join("") +
    `<option value="__add__">➕ Add new (save for reuse)</option>
     <option value="__custom__">✏️ Custom (this entry only)</option>`;
  if (c.dataset.mode !== "add" && c.dataset.mode !== "custom") {
    if (options.includes(hid.value)) sel.value = hid.value;
    else { sel.selectedIndex = 0; hid.value = allowEmpty ? "" : (options[0] || ""); }
  }
}
function fieldHTML(f) {
  const v = f.value !== undefined && f.value !== null ? esc(f.value) : "";
  if (f.type === "combo") {
    const options = (f.options || (f.listKey ? predList(f.listKey) : [])).slice();
    const isCustom = !!f.value && !options.includes(f.value);
    return `<label class="fld combo" data-list="${esc(f.listKey || "")}" data-mode="${isCustom ? "custom" : ""}">
      <span>${esc(f.label)}</span>
      <input type="hidden" name="${f.name}" value="${v}">
      <select class="combo-select" data-allow-empty="${f.allowEmpty ? 1 : 0}" ${isCustom ? 'style="display:none"' : ""}>
        ${f.allowEmpty ? `<option value=""${!f.value ? " selected" : ""}>—</option>` : ""}
        ${options.map(o => `<option value="${esc(o)}"${String(o) === String(f.value) ? " selected" : ""}>${esc(o)}</option>`).join("")}
        <option value="__add__">➕ Add new (save for reuse)</option>
        <option value="__custom__">✏️ Custom (this entry only)</option>
      </select>
      <div class="combo-free" ${isCustom ? "" : 'style="display:none"'}>
        <input class="combo-input" type="text" value="${isCustom ? v : ""}" placeholder="Type a value…">
        <button type="button" class="icon-btn combo-back" title="Back to list">↩</button>
      </div></label>`;
  }
  if (f.type === "select")
    return `<label class="fld"><span>${esc(f.label)}</span><select name="${f.name}">${
      f.options.map(o => { const [val, lab] = Array.isArray(o) ? o : [o, o];
        return `<option value="${esc(val)}"${String(val) === String(f.value) ? " selected" : ""}>${esc(lab)}</option>`; }).join("")
    }</select></label>`;
  if (f.type === "textarea")
    return `<label class="fld"><span>${esc(f.label)}</span><textarea name="${f.name}" rows="2">${v}</textarea></label>`;
  if (f.type === "checkbox")
    return `<label class="fld"><span>${esc(f.label)}</span><input type="checkbox" name="${f.name}"${f.value ? " checked" : ""}></label>`;
  return `<label class="fld"><span>${esc(f.label)}</span><input name="${f.name}" type="${f.type || "text"}" value="${v}"
    ${f.type === "number" ? `min="${f.min ?? 0}"` : ""}
    ${f.step ? `step="${f.step}"` : ""} ${f.placeholder ? `placeholder="${esc(f.placeholder)}"` : ""} ${f.required ? "required" : ""}></label>`;
}
function confirmModal(msg, onYes, label) {
  const ov = openModal(`<h3>Confirm</h3><p>${esc(msg)}</p>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal()">Cancel</button>
      <button class="btn danger" id="cy">${esc(label || "Delete")}</button>
    </div>`);
  el("#cy", ov).onclick = () => { closeModal(); onYes(); };
}

/* ================= router ================= */
const PAGES = {};
/* "live" (Live Investments) is special: it's an embedded iframe, not an HTML string.
   Its DOM node lives outside #main (in #liveHost, a flex sibling — see index.html) so
   that the normal `#main.innerHTML = ...` render cycle never touches it. That keeps the
   iframe alive (unloaded/un-reset) across tab switches, preserving its scroll position
   and in-page state, per the spec's "persistent iframe" requirement. PAGES.live is kept
   as a router entry for consistency/discoverability even though render() special-cases it. */
PAGES.live = () => "";
function navigate(page) {
  currentPage = page;
  if (typeof _pageLimits === "object") _pageLimits = {};  // fresh "show more" limits per page visit
  els("#nav button").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  render();
}
function render() {
  const isLive = currentPage === "live";
  const main = el("#main"), liveHost = el("#liveHost");
  if (liveHost) liveHost.hidden = !isLive;
  if (main) main.hidden = isLive;
  if (isLive) {
    const frame = el("#liveFrame");
    if (frame && frame.getAttribute("src") === "about:blank") frame.src = "/invest?embed=1";
    return;
  }
  main.innerHTML = PAGES[currentPage] ? PAGES[currentPage]() : "<p>Unknown page</p>";
  const hook = window["after_" + currentPage];
  if (typeof hook === "function") hook();
}

/* ---- freshness: the embedded invest tab tells us (via postMessage) when a broker/NSE
   sync completed, so the main app can pick up newly-synced liveSync portfolio rows —
   same idea as the gold-price auto-fetch refreshing just that one value. ---- */
window.addEventListener("message", e => {
  if (e.origin !== location.origin) return;
  if (!e.data || e.data.type !== "invest-synced") return;
  onInvestSynced();
});
async function onInvestSynced() {
  try {
    clearTimeout(saveTimer);
    await doSave();          // flush any pending/debounced local edits first
    await loadDB();          // re-fetch /api/data (server-owned liveSync rows included)
    render();
    toast("Live investment values updated");
  } catch (e) {
    toast("Could not refresh live investment values: " + e.message, true);
  }
}
function pageHead(title, sub, actionsHTML) {
  return `<div class="page-head"><div><h1>${esc(title)}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div>
    <div class="head-actions">${actionsHTML || ""}</div></div>`;
}

/* ================= shared chart helpers ================= */
const PALETTE = ["#2456e6","#12a06b","#e6a323","#d6493f","#7a4fd6","#1799c6","#c64f9b","#5b7a99","#8aa632","#d97742"];
function barChart(items, opts) {
  const max = Math.max(...items.map(i => i.value), 1);
  return items.map((i, ix) => `
    <div class="bar-row">
      <div class="bar-label" title="${esc(i.label)}">${esc(i.label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(i.value / max * 100).toFixed(1)}%;background:${i.color || PALETTE[ix % PALETTE.length]}"></div></div>
      <div class="bar-val">${(opts && opts.fmt ? opts.fmt : fmtMoney)(i.value)}</div>
    </div>`).join("") || '<div class="empty">No data</div>';
}
function donutChart(items, size) {
  const total = items.reduce((s, i) => s + i.value, 0);
  if (!total) return '<div class="empty">No data</div>';
  size = size || 170;
  const r = size / 2 - 12, c = size / 2, circ = 2 * Math.PI * r;
  let off = 0;
  const segs = items.map((i, ix) => {
    const frac = i.value / total, dash = frac * circ;
    const s = `<circle r="${r}" cx="${c}" cy="${c}" fill="none" stroke="${i.color || PALETTE[ix % PALETTE.length]}"
      stroke-width="20" stroke-dasharray="${dash} ${circ - dash}" stroke-dashoffset="${-off}" transform="rotate(-90 ${c} ${c})"/>`;
    off += dash;
    return s;
  }).join("");
  const legend = items.map((i, ix) => `<span><i class="dot" style="background:${i.color || PALETTE[ix % PALETTE.length]}"></i>${esc(i.label)} <b>${fmtPct(i.value / total)}</b></span>`).join("");
  return `<div class="donut-wrap"><svg width="${size}" height="${size}">${segs}
    <text x="${c}" y="${c - 3}" text-anchor="middle" font-size="15" font-weight="700" fill="#1c2333">${fmtMoney(total, true)}</text>
    <text x="${c}" y="${c + 14}" text-anchor="middle" font-size="10.5" fill="#6b7385">total</text></svg>
    <div class="legend" style="flex-direction:column;align-items:flex-start">${legend}</div></div>`;
}

/* paired horizontal bars per row — e.g. Invested vs Current value, with return % */
function compareBars(items, aLabel, bLabel, aColor, bColor) {
  aColor = aColor || "#5b7a99"; bColor = bColor || "var(--green)";
  if (!items.length) return '<div class="empty">No data</div>';
  const max = Math.max(...items.flatMap(i => [i.a, i.b]), 1);
  const legend = `<div class="legend" style="margin-bottom:14px">
    <span><i class="dot" style="background:${aColor}"></i>${esc(aLabel)}</span>
    <span><i class="dot" style="background:${bColor}"></i>${esc(bLabel)}</span></div>`;
  const rows = items.map(i => {
    const ret = i.a ? (i.b - i.a) / i.a : 0;
    return `<div style="margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;gap:10px;font-size:13px;margin-bottom:5px">
        <span title="${esc(i.label)}" style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(i.label)}</span>
        <span class="small muted" style="white-space:nowrap">${fmtMoney(i.a)} → <b style="color:var(--ink)">${fmtMoney(i.b)}</b>
          <span class="${ret >= 0 ? "pos" : "neg"}">${i.a ? fmtPct(ret) : "–"}</span></span>
      </div>
      <div class="bar-track" style="height:11px;margin-bottom:4px"><div class="bar-fill" style="width:${(i.a / max * 100).toFixed(1)}%;background:${aColor}"></div></div>
      <div class="bar-track" style="height:11px"><div class="bar-fill" style="width:${(i.b / max * 100).toFixed(1)}%;background:${bColor}"></div></div>
    </div>`;
  }).join("");
  return legend + rows;
}

/* single horizontal bar split into proportional colored segments + legend */
function stackedBar(segments, opts) {
  opts = opts || {};
  segments = segments.filter(s => s.value > 0);
  const total = opts.total || segments.reduce((s, x) => s + x.value, 0);
  if (total <= 0) return '<div class="empty">No data</div>';
  const segs = segments.map((s, ix) => {
    const pct = s.value / total * 100;
    return `<div title="${esc(s.label)}: ${fmtMoney(s.value)} (${pct.toFixed(1)}%)"
      style="width:${pct.toFixed(2)}%;background:${s.color || PALETTE[ix % PALETTE.length]};display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:700;overflow:hidden;white-space:nowrap">${pct >= 9 ? Math.round(pct) + "%" : ""}</div>`;
  }).join("");
  const legend = segments.map((s, ix) =>
    `<span><i class="dot" style="background:${s.color || PALETTE[ix % PALETTE.length]}"></i>${esc(s.label)} <b>${fmtMoney(s.value)}</b></span>`).join("");
  return `<div style="display:flex;height:28px;border-radius:8px;overflow:hidden;box-shadow:var(--shadow)">${segs}</div>
    <div class="legend" style="margin-top:12px">${legend}</div>`;
}

/* semicircle gauge (0..1) with a big centre label */
function gaugeArc(frac, bigLabel, subLabel, color) {
  const p = Math.max(0, Math.min(1, frac || 0));
  color = color || "var(--accent)";
  const W = 200, H = 118, cx = 100, cy = 104, r = 80, sw = 18;
  const pt = a => [cx + r * Math.cos(a), cy - r * Math.sin(a)];
  const [lx, ly] = pt(Math.PI), [rx, ry] = pt(0), [ex, ey] = pt(Math.PI * (1 - p));
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;display:block;margin:0 auto">
    <path d="M ${lx.toFixed(1)} ${ly.toFixed(1)} A ${r} ${r} 0 0 1 ${rx.toFixed(1)} ${ry.toFixed(1)}" fill="none" stroke="#eef1f7" stroke-width="${sw}" stroke-linecap="round"/>
    <path d="M ${lx.toFixed(1)} ${ly.toFixed(1)} A ${r} ${r} 0 0 1 ${ex.toFixed(1)} ${ey.toFixed(1)}" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round"/>
    <text x="${cx}" y="${cy - 12}" text-anchor="middle" font-size="27" font-weight="700" fill="var(--ink)">${esc(bigLabel)}</text>
    <text x="${cx}" y="${cy + 7}" text-anchor="middle" font-size="11" fill="var(--muted)">${esc(subLabel || "")}</text>
  </svg>`;
}

/* diverging bars centred on zero — positive (gain) extends right/green, negative (loss) left/red */
function signedBars(items) {
  if (!items.length) return '<div class="empty">No data</div>';
  const max = Math.max(...items.map(i => Math.abs(i.value)), 1);
  return items.map(i => {
    const pct = Math.abs(i.value) / max * 50;
    const pos = i.value >= 0;
    return `<div class="bar-row" style="font-size:12.5px">
      <div class="bar-label" title="${esc(i.label)}">${esc(i.label)}</div>
      <div style="flex:1;display:flex;align-items:center;height:18px;position:relative">
        <div style="position:absolute;left:50%;top:1px;bottom:1px;border-left:1px solid var(--line)"></div>
        <div style="position:absolute;${pos ? "left" : "right"}:50%;width:${pct.toFixed(1)}%;height:11px;border-radius:4px;background:${pos ? "var(--green)" : "var(--red)"}"></div>
      </div>
      <div class="bar-val ${pos ? "pos" : "neg"}">${fmtMoney(i.value)}</div>
    </div>`;
  }).join("");
}

/* ================= aggregations ================= */
function latestYearEntry(p) {
  const yrs = p.salaryYears || [];
  if (!yrs.length) return null;
  return yrs.reduce((mx, y) => y.year > mx.year ? y : mx, yrs[0]);
}
function selectedYearEntry(p) {
  const yrs = p.salaryYears || [];
  if (!yrs.length) return null;
  if (incomeYear === null) return latestYearEntry(p);
  return yrs.find(y => y.year === incomeYear) || null;
}
/* incomeTotalsForYear(yr) lives in finance-math.js (loaded first) */
/* Uses latest year always — safe for dashboard regardless of income page filter */
function incomeTotals(p) { return incomeTotalsForYear(latestYearEntry(p)); }
function totalInHand() {
  return (DB.income.persons || []).reduce((s, p) => s + incomeTotals(p).inHand, 0);
}
function totalExpenses() {        // recurring monthly outflow incl. investments & loan EMIs
  return DB.expenses.reduce((s, e) => s + num(e.amount), 0)
       + DB.monthlyInvestments.filter(i => (i.deductedFrom || "IN HAND") === "IN HAND").reduce((s, i) => s + num(i.amount), 0)
       + activeLoans().reduce((s, L) => s + loanState(L).emi, 0);
}
function pureExpenses() { return DB.expenses.reduce((s, e) => s + num(e.amount), 0); }
function activeLoans() { return DB.loans.filter(l => l.status !== "closed"); }
function portfolioTotals() {
  let cur = 0, inv = 0;
  DB.portfolio.forEach(p => { cur += num(p.currentValue); inv += num(p.invested); });
  return { cur, inv };
}
function goldTotal() { return DB.gold.reduce((s, g) => s + num(g.grams) * num(g.perGramValue), 0); }
function liabilitiesTotal() { return activeLoans().reduce((s, L) => s + loanState(L).balance, 0); }

/* ================= DASHBOARD ================= */
PAGES.dashboard = () => {
  const pt = portfolioTotals(), gold = goldTotal(), liab = liabilitiesTotal();
  const gg = goldGain();
  const assets = pt.cur + gold, net = assets - liab;

  /* ---- cashflow ---- */
  const inHand = totalInHand();
  const pureExp = pureExpenses();
  const emiTotal = activeLoans().reduce((s, L) => s + loanState(L).emi, 0);
  const invInHand = DB.monthlyInvestments.filter(i => (i.deductedFrom || "IN HAND") === "IN HAND").reduce((s, i) => s + num(i.amount), 0);
  const outflow = pureExp + emiTotal + invInHand;
  const surplus = inHand - outflow;
  const savingsRate = inHand > 0 ? (invInHand + surplus) / inHand : 0;
  const gaugeColor = savingsRate >= 0.2 ? "var(--green)" : savingsRate >= 0 ? "var(--amber)" : "var(--red)";

  const allocSegs = [
    { label: "Living expenses", value: pureExp, color: "#d6493f" },
    { label: "Loan EMIs", value: emiTotal, color: "#e6a323" },
    { label: "Investments", value: invInHand, color: "#2456e6" },
    { label: "Surplus", value: Math.max(0, surplus), color: "#12a06b" },
  ];
  const allocTotal = Math.max(inHand, outflow) || 1;

  // spend by section (expense groups, no EMI)
  const byGroup = {};
  DB.expenses.forEach(e => { const k = e.group || "Other"; byGroup[k] = (byGroup[k] || 0) + num(e.amount); });
  const groupItems = Object.entries(byGroup).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
    .map(([label, value], ix) => ({ label, value, color: PALETTE[ix % PALETTE.length] }));

  // monthly outflow by group (expenses + EMIs) — bar
  const grp = { ...byGroup };
  if (emiTotal) grp["Loan EMIs"] = (grp["Loan EMIs"] || 0) + emiTotal;
  const grpItems = Object.entries(grp).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }));

  // monthly investment mix donut (all monthly investments by instrument)
  const investByGroup = {};
  DB.monthlyInvestments.forEach(i => { const k = i.category || "Other"; investByGroup[k] = (investByGroup[k] || 0) + num(i.amount); });
  const investItems = Object.entries(investByGroup).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }));

  /* ---- net worth & assets ---- */
  const alloc = {};
  DB.portfolio.forEach(p => { const k = p.subCategory || "Other"; alloc[k] = (alloc[k] || 0) + num(p.currentValue); });
  if (gold) alloc["Physical Gold"] = (alloc["Physical Gold"] || 0) + gold;
  const allocItems = Object.entries(alloc).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }));

  const TYPE_MAP = { "Equity": "Equity", "Debt": "Debt/Fixed", "Fixed": "Debt/Fixed", "Real estate": "Real Estate",
    "Liquidity": "Liquid", "Gold": "Gold", "Other": "Other" };
  const byType = {};
  DB.portfolio.forEach(p => { const k = TYPE_MAP[p.subCategory] || "Other"; byType[k] = (byType[k] || 0) + num(p.currentValue); });
  if (gold) byType["Gold"] = (byType["Gold"] || 0) + gold;
  const byTypeItems = Object.entries(byType).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }));

  const goalsOpen = DB.goals.filter(g => !g.fulfilled);

  // net worth segmented bar: Assets = Net Worth + Liabilities
  const nwSVG = (() => {
    const W = 420, H = 80, pad = 12, barY = 22, barH = 30, rx = 7;
    const usable = W - pad * 2;

    if (net >= 0) {
      const scale = assets > 0 ? usable / assets : 1;
      const nwW = Math.max(0, net * scale);
      const lW  = Math.max(0, liab * scale);
      const nwLX = pad + Math.min(nwW, usable) / 2;
      const lLX  = pad + nwW + lW / 2;
      return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;display:block;margin-top:8px">
        <text x="${W/2}" y="13" text-anchor="middle" font-size="10" fill="#6b7385">Total Assets · ${fmtMoney(assets, true)}</text>
        <defs><clipPath id="nwBarClip"><rect x="${pad}" y="${barY}" width="${usable}" height="${barH}" rx="${rx}"/></clipPath></defs>
        <rect x="${pad}" y="${barY}" width="${usable}" height="${barH}" rx="${rx}" fill="#e3f5ec"/>
        <rect x="${pad}" y="${barY}" width="${nwW}" height="${barH}" fill="#2456e6" clip-path="url(#nwBarClip)"/>
        ${liab > 0 ? `<rect x="${pad + nwW}" y="${barY}" width="${lW}" height="${barH}" fill="#e84255" clip-path="url(#nwBarClip)"/>` : ""}
        ${liab > 0 ? `<line x1="${pad + nwW}" y1="${barY}" x2="${pad + nwW}" y2="${barY + barH}" stroke="white" stroke-width="2"/>` : ""}
        <text x="${nwLX}" y="${barY + barH + 16}" text-anchor="middle" font-size="10" font-weight="600" fill="#2456e6">Net Worth · ${fmtMoney(net, true)}</text>
        ${liab > 0 ? `<text x="${lLX}" y="${barY + barH + 16}" text-anchor="middle" font-size="10" font-weight="600" fill="#cc3344">Liabilities · ${fmtMoney(liab, true)}</text>` : ""}
      </svg>`;
    } else {
      // liab > assets — negative net worth; scale to liabilities
      const scale = liab > 0 ? usable / liab : 1;
      const aW = Math.max(0, assets * scale);
      const excessW = usable - aW;
      return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;display:block;margin-top:8px">
        <text x="${W/2}" y="13" text-anchor="middle" font-size="10" fill="#cc3344">Net Worth · ${fmtMoney(net, true)}</text>
        <defs><clipPath id="nwBarClip"><rect x="${pad}" y="${barY}" width="${usable}" height="${barH}" rx="${rx}"/></clipPath></defs>
        <rect x="${pad}" y="${barY}" width="${usable}" height="${barH}" rx="${rx}" fill="#fbe9ec"/>
        <rect x="${pad}" y="${barY}" width="${aW}" height="${barH}" fill="#22a366" clip-path="url(#nwBarClip)"/>
        <line x1="${pad + aW}" y1="${barY}" x2="${pad + aW}" y2="${barY + barH}" stroke="white" stroke-width="2"/>
        <text x="${pad + aW / 2}" y="${barY + barH + 16}" text-anchor="middle" font-size="10" font-weight="600" fill="#108a4d">Assets · ${fmtMoney(assets, true)}</text>
        <text x="${pad + aW + excessW / 2}" y="${barY + barH + 16}" text-anchor="middle" font-size="10" font-weight="600" fill="#cc3344">Excess Debt · ${fmtMoney(liab - assets, true)}</text>
      </svg>`;
    }
  })();

  return pageHead(esc(DB.settings.appName || "Family Finance"),
      "Snapshot of your family's money — " + fmtDate(new Date().toISOString())) + `

  <div class="grid kpis">
    <div class="kpi"><div class="label">Net Worth</div><div class="value ${net >= 0 ? "pos" : "neg"}">${fmtMoney(net)}</div>
      <div class="delta muted">${fmtMoney(assets)} assets − ${fmtMoney(liab)} debt</div></div>
    <div class="kpi"><div class="label">Monthly In-Hand</div><div class="value">${fmtMoney(inHand)}</div></div>
    <div class="kpi"><div class="label">Monthly Outflow</div><div class="value">${fmtMoney(outflow)}</div>
      <div class="delta muted">incl. EMIs &amp; investments</div></div>
    <div class="kpi"><div class="label">Monthly Surplus</div><div class="value ${surplus >= 0 ? "pos" : "neg"}">${fmtMoney(surplus)}</div>
      <div class="delta muted">${surplus < 0 ? "overspending in-hand" : "after all outflow"}</div></div>
    <div class="kpi"><div class="label">Savings Rate</div><div class="value ${savingsRate >= 0 ? "pos" : "neg"}">${fmtPct(savingsRate)}</div>
      <div class="delta muted">invested + surplus vs in-hand</div></div>
  </div>

  <div class="section-title">Monthly Cashflow</div>
  <div class="panel"><h2>Where Your In-Hand Goes</h2>
    <div class="grid two-col" style="align-items:center">
      <div>${stackedBar(allocSegs, { total: allocTotal })}
        ${surplus < 0 ? `<p class="small neg mt">⚠ Outflow exceeds in-hand by ${fmtMoney(-surplus)}.</p>` : ""}</div>
      <div style="text-align:center">${gaugeArc(savingsRate, fmtPct(savingsRate), "of in-hand kept", gaugeColor)}
        <div class="small muted" style="margin-top:4px">Investments + surplus vs in-hand income</div></div>
    </div>
  </div>
  <div class="grid two-col">
    <div class="panel"><h2>Spend by Section</h2>${groupItems.length ? donutChart(groupItems) : '<div class="empty">No expenses yet</div>'}</div>
    <div class="panel"><h2>Monthly Investment Mix</h2>${investItems.length ? donutChart(investItems) : '<div class="empty">No investments yet</div>'}</div>
  </div>
  <div class="panel"><h2>Monthly Outflow by Group</h2>${barChart(grpItems)}</div>

  <div class="section-title">Net Worth &amp; Assets</div>
  <div class="panel">
    <h2>Assets · Liabilities · Net Worth</h2>
    <div class="grid kpis" style="margin-bottom:12px">
      <div class="kpi"><div class="label">Total Assets</div><div class="value pos">${fmtMoney(assets)}</div>
        <div class="delta muted">portfolio + gold</div></div>
      ${byTypeItems.map(t => `<div class="kpi"><div class="label">${esc(t.label)}</div>
        <div class="value">${fmtMoney(t.value)}</div>
        <div class="delta muted">${fmtPct(t.value / (assets || 1))} of assets</div></div>`).join("")}
      <div class="kpi"><div class="label">Total Liabilities</div><div class="value ${liab ? "neg" : ""}">${fmtMoney(liab)}</div>
        <div class="delta muted">${activeLoans().length} active loan(s)</div></div>
    </div>
    ${nwSVG}
  </div>
  <div class="grid two-col">
    <div class="panel"><h2>Asset Allocation</h2>${allocItems.length ? donutChart(allocItems) : '<div class="empty">No holdings yet</div>'}</div>
    <div class="panel"><h2>Investment Performance</h2>
      <div class="grid kpis" style="margin-bottom:0">
        <div class="kpi"><div class="label">Portfolio Invested</div><div class="value">${fmtMoney(pt.inv)}</div></div>
        <div class="kpi"><div class="label">Portfolio Value</div><div class="value">${fmtMoney(pt.cur)}</div></div>
        <div class="kpi"><div class="label">Portfolio Gain</div><div class="value ${pt.cur - pt.inv >= 0 ? "pos" : "neg"}">${fmtMoney(pt.cur - pt.inv)}</div>
          <div class="delta muted">${pt.inv ? fmtPct((pt.cur - pt.inv) / pt.inv) + " overall" : ""}</div></div>
        <div class="kpi"><div class="label">Gold Value</div><div class="value">${fmtMoney(gold)}</div>
          <div class="delta muted">${fmtNum(DB.gold.reduce((s, g) => s + num(g.grams), 0))} g</div></div>
        ${gg.inv > 0 ? `<div class="kpi"><div class="label">Gold Gain</div><div class="value ${gg.gain >= 0 ? "pos" : "neg"}">${fmtMoney(gg.gain)}</div>
          <div class="delta muted">${fmtPct(gg.gain / gg.inv)} on ${fmtMoney(gg.inv)} invested</div></div>` : ""}
      </div>
    </div>
  </div>

  <div class="section-title">Loans &amp; Goals</div>
  <div class="grid two-col">
    <div class="panel"><h2>Active Loans</h2>${(() => {
      const paged = pagedRows("dash:loans", activeLoans(), L => {
        const st = loanState(L);
        const pct = num(L.principal) ? st.paidPrincipal / num(L.principal) : 0;
        return `<div class="mb"><div style="display:flex;justify-content:space-between;font-size:13.5px">
          <b>${esc(L.name)}</b><span>${fmtMoney(st.balance)} left · EMI ${fmtMoney(st.emi)}</span></div>
          <div class="progress"><i style="width:${(pct * 100).toFixed(1)}%"></i></div>
          <div class="small muted">${st.paidMonths}/${st.sched.rows.length} months · ${fmtPct(pct)} principal repaid</div></div>`;
      });
      return (paged.html || '<div class="empty">No active loans 🎉</div>') + paged.footer;
    })()}</div>
    <div class="panel"><h2>Upcoming Goals</h2>${(() => {
      if (!goalsOpen.length) return '<div class="empty">No open goals</div>';
      const sorted = goalsOpen.sort((a, b) => new Date(a.estimatedDate || "2999") - new Date(b.estimatedDate || "2999"));
      const paged = pagedRows("dash:goals", sorted, g => `<tr><td>${esc(g.name)} <span class="chip gray">${esc(g.type || "")}</span></td>
            <td class="num">${fmtMoney(num(g.currentMarketValue || g.value))}</td><td>${fmtDate(g.estimatedDate)}</td></tr>`);
      return `<div class="table-wrap"><table><thead><tr><th>Goal</th><th class="num">Value</th><th>Target</th></tr></thead><tbody>${paged.html}</tbody></table></div>${paged.footer}`;
    })()}</div>
  </div>`;
};

/* ================= INCOME ================= */
PAGES.income = () => {
  const persons = DB.income.persons || [];
  const allYears = [...new Set(persons.flatMap(p => (p.salaryYears || []).map(y => y.year)))].sort((a, b) => b - a);
  const dl = k => predList(k).map(o => `<option value="${esc(o)}">`).join("");
  const hasMultiYear = persons.some(p => (p.salaryYears || []).length >= 2);
  const yearSel = allYears.length > 1 ? `<select onchange="incomeYear=this.value==='null'?null:+this.value;render()">
      <option value="null"${incomeYear === null ? " selected" : ""}>Latest year</option>
      ${allYears.map(y => `<option value="${y}"${incomeYear === y ? " selected" : ""}>${y}</option>`).join("")}
    </select>` : "";
  const controlBar = persons.length ? `<div class="filter-bar" style="margin-bottom:16px">${yearSel}</div>` : "";

  /* family-level totals */
  const familySummary = persons.length > 0 ? (() => {
    const tots = persons.map(p => incomeTotalsForYear(selectedYearEntry(p) || latestYearEntry(p)));
    const fMonthlyInHand = tots.reduce((s, t) => s + t.inHand, 0);
    const fMonthlyCtc    = tots.reduce((s, t) => s + t.monthlyCtc, 0);
    const fYearlyCtc     = tots.reduce((s, t) => s + t.yearlyCtc + t.oneTimeBonus, 0);
    const fBonus         = tots.reduce((s, t) => s + t.totalBonus, 0);
    return `<div class="panel earner-head" style="margin-bottom:18px">
      <h2 style="color:#fff;margin-bottom:12px">Family Total</h2>
      <div class="grid kpis" style="margin-bottom:0">
        <div class="kpi"><div class="label">Monthly In-Hand</div><div class="value">${fmtMoney(fMonthlyInHand)}</div>
          <div class="delta muted">${persons.map(p => esc(p.name)).join(" + ")}</div></div>
        <div class="kpi"><div class="label">Monthly CTC</div><div class="value">${fmtMoney(fMonthlyCtc)}</div></div>
        <div class="kpi"><div class="label">Yearly CTC (incl. bonus)</div><div class="value">${fmtMoney(fYearlyCtc)}</div></div>
        <div class="kpi"><div class="label">Annual Bonus</div><div class="value">${fmtMoney(fBonus)}</div>
          <div class="delta muted">variable + one-time across all earners</div></div>
      </div></div>`;
  })() : "";

  return pageHead("Income",
    "CTC = Gross + CTC-only · In-Hand = Gross − Deductions · Variable and bonus shown in yearly totals",
    `<button class="btn" onclick="addEarner()">+ Add Earner</button>`) +
    `<datalist id="dlIncomeComp">${dl("incomeComponents")}</datalist>
     <datalist id="dlDedComp">${dl("deductionComponents")}</datalist>` +
    controlBar + familySummary +
    (persons.length ? persons.map((p, pi) => incomeEarnerHTML(p, pi)).join("")
      : '<div class="panel"><div class="empty">No earners yet — add one to start.</div></div>') +
    (hasMultiYear ? salaryGrowthChart(persons) : "");
};

function incomeEarnerHTML(p, pi) {
  const yr = selectedYearEntry(p);
  const yi = yr ? (p.salaryYears || []).indexOf(yr) : -1;
  const allPersonYears = (p.salaryYears || []).map(y => y.year).sort((a, b) => b - a);

  const yearPills = `
    <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      ${allPersonYears.map(y => {
        const isActive = incomeYear === y || (incomeYear === null && y === allPersonYears[0]);
        return `<button class="chip${isActive ? " green" : ""}" onclick="incomeYear=${y};render()" style="cursor:pointer;border:0">${y}</button>`;
      }).join("")}
      <button class="btn small secondary" onclick="addSalaryYear(${pi})">+ Year</button>
    </div>`;

  const header = `<div class="panel earner-head">
    <h2 style="color:#fff">${esc(p.name)}
      <span>
        <button class="btn small secondary" onclick="renameEarner(${pi})">Rename</button>
        <button class="btn small danger" onclick="delEarner(${pi})">Delete</button>
      </span>
    </h2>${yearPills}`;

  if (!yr) {
    const lbl = incomeYear === null ? "any year" : String(incomeYear);
    return header + `</div><div class="panel"><div class="empty">No salary data for ${esc(lbl)}.
      <button class="btn small secondary" onclick="addSalaryYear(${pi})">+ Add salary for this year</button></div></div>`;
  }

  const t = incomeTotalsForYear(yr);

  const compRows = (yr.components || []).map((c, ci) => `
    <tr>
      <td><input class="inline-input wide" list="dlIncomeComp" value="${esc(c.component)}"
        onchange="updComp(${pi},${yi},${ci},'component',this.value)"></td>
      <td><select class="inline-select" onchange="updComp(${pi},${yi},${ci},'scope',this.value)">
        <option value="gross"${c.scope !== "ctc" ? " selected" : ""}>Gross</option>
        <option value="ctc"${c.scope === "ctc" ? " selected" : ""}>CTC only</option></select></td>
      <td class="num"><input class="inline-input" value="${num(c.amount)}"
        onchange="updComp(${pi},${yi},${ci},'amount',this.value)"></td>
      <td class="num muted">${fmtMoney(num(c.amount) * 12)}</td>
      <td style="width:30px"><button class="icon-btn danger" onclick="delComp(${pi},${yi},${ci})">✕</button></td>
    </tr>`).join("");

  const dedRows = (yr.deductions || []).map((c, ci) => `
    <tr>
      <td><input class="inline-input wide" list="dlDedComp" value="${esc(c.component)}"
        onchange="updDed(${pi},${yi},${ci},'component',this.value)"></td>
      <td class="num"><input class="inline-input" value="${num(c.amount)}"
        onchange="updDed(${pi},${yi},${ci},'amount',this.value)"></td>
      <td class="num muted">${fmtMoney(num(c.amount) * 12)}</td>
      <td style="width:30px"><button class="icon-btn danger" onclick="delDed(${pi},${yi},${ci})">✕</button></td>
    </tr>`).join("");

  const varLabel = t.earnedPct !== null
    ? `Earned: ${t.earnedPct}% (eligible: ${t.eligiblePct}%)`
    : `Eligible: ${t.eligiblePct}%`;
  const varBadge = t.earnedPct !== null && t.eligiblePct > 0 && t.earnedPct < t.eligiblePct
    ? ` <span class="chip amber">below target</span>` : t.earnedPct !== null && t.earnedPct > t.eligiblePct
    ? ` <span class="chip green">above target</span>` : "";

  const totalCtcWithBonus = t.yearlyCtc + t.oneTimeBonus;

  const summaryTable = `
    <div style="margin-top:20px">
      <h3 style="margin:0 0 10px;font-size:14px">Summary</h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Item</th><th class="num">Monthly</th><th class="num">Yearly</th></tr></thead>
        <tbody>
          <tr><td>Gross</td><td class="num">${fmtMoney(t.gross)}</td><td class="num">${fmtMoney(t.gross * 12)}</td></tr>
          <tr><td>CTC base</td><td class="num">${fmtMoney(t.monthlyCtc)}</td><td class="num">${fmtMoney(t.yearlyCtcBase)}</td></tr>
          <tr><td class="muted" style="padding-left:12px">↳ Variable pay</td><td class="num muted">—</td><td class="num">${fmtMoney(t.variablePay)}</td></tr>
          <tr><td class="muted" style="padding-left:12px">↳ One-time bonus</td><td class="num muted">—</td><td class="num">${fmtMoney(t.oneTimeBonus)}</td></tr>
          <tr class="subtotal"><td>Total CTC</td><td class="num">${fmtMoney(t.monthlyCtc)}</td><td class="num">${fmtMoney(t.yearlyCtc)}</td></tr>
          <tr class="subtotal"><td>Total CTC + bonus</td><td class="num muted">—</td><td class="num">${fmtMoney(totalCtcWithBonus)}</td></tr>
          <tr><td style="border-top:1px solid var(--line)">Deductions</td><td class="num neg" style="border-top:1px solid var(--line)">${fmtMoney(t.ded)}</td><td class="num neg" style="border-top:1px solid var(--line)">${fmtMoney(t.ded * 12)}</td></tr>
          <tr class="subtotal"><td>In-Hand</td>
            <td class="num ${t.inHand >= 0 ? "pos" : "neg"}">${fmtMoney(t.inHand)}</td>
            <td class="num ${t.inHand >= 0 ? "pos" : "neg"}">${fmtMoney(t.inHand * 12)}</td></tr>
        </tbody>
      </table></div>
    </div>`;

  return header + `
    <div class="grid kpis" style="margin-bottom:0;margin-top:12px">
      <div class="kpi"><div class="label">Monthly CTC</div><div class="value">${fmtMoney(t.monthlyCtc)}</div>
        <div class="delta muted">gross + ${fmtMoney(t.ctcOnly)} CTC-only</div></div>
      <div class="kpi"><div class="label">Yearly CTC</div><div class="value">${fmtMoney(t.yearlyCtc)}</div>
        <div class="delta muted">${fmtMoney(t.yearlyCtcBase)} base + ${fmtMoney(t.variablePay)} variable</div></div>
      <div class="kpi"><div class="label">Annual Bonus</div><div class="value">${fmtMoney(t.totalBonus)}</div>
        <div class="delta muted">variable ${fmtMoney(t.variablePay)} + one-time ${fmtMoney(t.oneTimeBonus)}</div></div>
      <div class="kpi"><div class="label">Monthly In-Hand</div><div class="value pos">${fmtMoney(t.inHand)}</div>
        <div class="delta muted">gross − deductions only</div></div>
    </div>
  </div>
  <div class="grid two-col">
    <div class="panel">
      <h2>Salary Components
        <button class="btn small secondary" onclick="addComp(${pi},${yi})">+ Row</button>
        <button class="btn small secondary" onclick="importPayslip(${pi},${yi})" style="margin-left:6px">↑ Import</button>
      </h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Component</th><th>Scope</th><th class="num">Monthly</th><th class="num">Annual</th><th></th></tr></thead>
        <tbody>${compRows}</tbody>
      </table></div>
      <p class="muted small mt">"CTC only" = employer-side money not in gross (e.g. Employer PF).</p>

      <h3 style="margin:16px 0 10px;font-size:14px">Variable Pay &amp; Bonus</h3>
      <div class="fld-row" style="gap:12px">
        <label class="fld"><span>Variable eligible (% of annual CTC)</span>
          <input type="number" step="any" min="0" max="100" style="width:100%"
            value="${t.eligiblePct || ""}" placeholder="0"
            onchange="updVariable(${pi},${yi},'variablePctEligible',this.value)">
        </label>
        <label class="fld"><span>Variable earned (% of CTC — blank = use eligible)</span>
          <input type="number" step="any" min="0" max="100" style="width:100%"
            value="${t.earnedPct !== null ? t.earnedPct : ""}" placeholder="same as eligible"
            onchange="updVariable(${pi},${yi},'variablePctEarned',this.value)">
        </label>
      </div>
      <div class="small muted" style="margin:-4px 0 10px">
        ${varLabel} × ${fmtMoney(t.yearlyCtcBase)}/yr = <b>${fmtMoney(t.variablePay)}</b>${varBadge}
      </div>
      <label class="fld"><span>One-time bonus (flat annual amount)</span>
        <input type="number" step="any" min="0" style="max-width:220px"
          value="${t.oneTimeBonus || ""}" placeholder="0"
          onchange="updOneTimeBonus(${pi},${yi},this.value)">
      </label>
    </div>
    <div class="panel">
      <h2>Deductions <button class="btn small secondary" onclick="addDed(${pi},${yi})">+ Row</button></h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Deduction</th><th class="num">Monthly</th><th class="num">Annual</th><th></th></tr></thead>
        <tbody>${dedRows}</tbody>
      </table></div>
      ${t.inHand < 0 ? '<p class="small neg mt">⚠ Deductions exceed gross — check amounts.</p>' : ""}
      ${summaryTable}
    </div>
  </div>`;
}
function addEarner() {
  formModal("Add earning member", [{ name: "name", label: "Name", required: true }], v => {
    const curYear = new Date().getFullYear();
    DB.income.persons.push({ name: v.name, salaryYears: [{
      year: curYear, components: [], deductions: [],
      variablePctEligible: 0, variablePctEarned: null, oneTimeBonus: 0
    }]});
    if (!DB.settings.persons.includes(v.name)) DB.settings.persons.push(v.name);
    closeModal(); save(); render();
  });
}
function renameEarner(pi) {
  const p = DB.income.persons[pi];
  formModal("Rename earner", [{ name: "name", label: "Name", value: p.name, required: true }], v => {
    p.name = v.name; closeModal(); save(); render();
  });
}
function delEarner(pi) {
  confirmModal(`Delete ${DB.income.persons[pi].name} and all their income data?`, () => {
    DB.income.persons.splice(pi, 1); save(); render();
  });
}
function addComp(pi, yi) {
  DB.income.persons[pi].salaryYears[yi].components.push({ component: "New component", amount: 0, scope: "gross" });
  save(true); render();  // flush immediately so an unedited new row survives a quick navigate-away
}
function addDed(pi, yi) {
  DB.income.persons[pi].salaryYears[yi].deductions.push({ component: "New deduction", amount: 0 });
  save(true); render();  // flush immediately so an unedited new row survives a quick navigate-away
}
function updComp(pi, yi, ci, field, val) {
  const c = DB.income.persons[pi].salaryYears[yi].components[ci];
  if (field === "amount") {
    const r = validAmount(val);
    if (!r.ok) { toast("Enter a valid non-negative number", true); render(); return; }
    c.amount = r.n;
  } else if (field === "component") {
    if (!val.trim()) { toast("Component name can't be empty", true); render(); return; }
    c.component = val.trim(); addPred("incomeComponents", c.component);
  } else c[field] = val;
  save(); render();
}
function updDed(pi, yi, ci, field, val) {
  const c = DB.income.persons[pi].salaryYears[yi].deductions[ci];
  if (field === "amount") {
    const r = validAmount(val);
    if (!r.ok) { toast("Enter a valid non-negative number", true); render(); return; }
    c.amount = r.n;
  } else {
    if (!val.trim()) { toast("Deduction name can't be empty", true); render(); return; }
    c.component = val.trim(); addPred("deductionComponents", c.component);
  }
  save(); render();
}
function delComp(pi, yi, ci) { DB.income.persons[pi].salaryYears[yi].components.splice(ci, 1); save(); render(); }
function delDed(pi, yi, ci) { DB.income.persons[pi].salaryYears[yi].deductions.splice(ci, 1); save(); render(); }
function updVariable(pi, yi, field, val) {
  const yr = DB.income.persons[pi].salaryYears[yi];
  if (field === "variablePctEarned") {
    yr.variablePctEarned = val.trim() === "" ? null : num(val);
  } else {
    yr[field] = num(val);
  }
  save(); render();
}
function updOneTimeBonus(pi, yi, val) {
  const r = validAmount(val);
  if (!r.ok) { toast("Enter a valid non-negative number", true); render(); return; }
  DB.income.persons[pi].salaryYears[yi].oneTimeBonus = r.n;
  save(); render();
}
async function importPayslip(pi, yi) {
  let status;
  try {
    status = await fetch("/api/gemini-status").then(r => r.json());
  } catch {
    toast("Could not reach server", true); return;
  }
  if (!status.available) {
    toast("Gemini API key not set. Add GEMINI_API_KEY to your environment and restart the server.", true);
    return;
  }

  const ov = openModal(`
    <h3>Import from Payslip / Offer Letter</h3>
    <p class="muted small" style="margin:8px 0 16px">PDF, PNG, JPG accepted &middot; file sent to Google Gemini for AI extraction</p>
    <div class="drop-zone" id="psipDrop" style="cursor:pointer;padding:32px 20px;text-align:center;border:2px dashed var(--line);border-radius:var(--radius)">
      Click or drop file here<br><span class="small muted">PDF, PNG, JPG, WEBP &mdash; up to 20 MB</span>
    </div>
    <input type="file" id="psipFile" accept=".pdf,.png,.jpg,.jpeg,.webp" style="display:none">
    <div id="psipStatus" style="display:none;margin-top:14px" class="muted small"></div>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">Cancel</button></div>`);

  const dz = el("#psipDrop", ov);
  const fi = el("#psipFile", ov);
  dz.onclick = () => fi.click();
  dz.ondragover = e => { e.preventDefault(); dz.style.background = "var(--bg)"; };
  dz.ondragleave = () => { dz.style.background = ""; };
  dz.ondrop = e => { e.preventDefault(); dz.style.background = ""; doExtract(e.dataTransfer.files[0]); };
  fi.onchange = () => doExtract(fi.files[0]);

  async function setStatus(msg, isErr) {
    const s = el("#psipStatus", ov);
    s.style.display = "";
    s.textContent = msg;
    s.className = isErr ? "neg small" : "muted small";
  }

  async function doExtract(file) {
    if (!file) return;
    dz.style.display = "none";
    setStatus("Uploading…");
    try {
      const upR = await fetch("/api/upload?name=" + encodeURIComponent(file.name),
        { method: "POST", body: file });
      const upJ = await upR.json();
      if (!upR.ok) throw new Error(upJ.error || "Upload failed");

      setStatus("Extracting with Gemini " + (status.preferred || "flash") + "…");
      const exR = await fetch("/api/extract-payslip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: upJ.name }),
      });
      const exJ = await exR.json();
      if (!exR.ok) throw new Error(exJ.error || "Extraction failed");

      closeModal();
      showExtractionPreview(pi, yi, exJ.extracted, exJ.model);
    } catch (e) {
      setStatus("Failed: " + e.message + " — enter details manually.", true);
      dz.style.display = "";
    }
  }
}

function showExtractionPreview(pi, yi, data, modelUsed) {
  const p = DB.income.persons[pi];
  const comps  = (data.components  || []).map((c, i) => ({ ...c, _i: i }));
  const deds   = (data.deductions   || []).map((c, i) => ({ ...c, _i: i }));
  const varPct = data.variablePctEligible != null ? data.variablePctEligible : "";
  const bonus  = data.oneTimeBonus != null ? data.oneTimeBonus : "";
  const yr     = data.year || new Date().getFullYear();

  const compRows = comps.map((c, i) => `
    <tr>
      <td><input class="inline-input wide" id="ec_name_${i}" value="${esc(c.component)}"></td>
      <td><select class="inline-select" id="ec_scope_${i}">
        <option value="gross"${c.scope !== "ctc" ? " selected" : ""}>Gross</option>
        <option value="ctc"${c.scope === "ctc" ? " selected" : ""}>CTC only</option></select></td>
      <td class="num"><input class="inline-input" id="ec_amt_${i}" value="${num(c.amount)}"></td>
    </tr>`).join("");

  const dedRows = deds.map((c, i) => `
    <tr>
      <td><input class="inline-input wide" id="ed_name_${i}" value="${esc(c.component)}"></td>
      <td class="num"><input class="inline-input" id="ed_amt_${i}" value="${num(c.amount)}"></td>
    </tr>`).join("");

  openModal(`
    <h3>Review Extracted Data — ${esc(p.name)}</h3>
    <p class="muted small" style="margin:4px 0 16px">Extracted via ${esc(modelUsed || "Gemini")} &middot; Review and edit before applying &middot; All amounts monthly</p>
    ${comps.length ? `
      <h4 style="margin:0 0 8px;font-size:13px">Salary Components</h4>
      <div class="table-wrap" style="margin-bottom:16px"><table>
        <thead><tr><th>Component</th><th>Scope</th><th class="num">Monthly (₹)</th></tr></thead>
        <tbody>${compRows}</tbody>
      </table></div>` : '<p class="muted small">No salary components extracted.</p>'}
    ${deds.length ? `
      <h4 style="margin:0 0 8px;font-size:13px">Deductions</h4>
      <div class="table-wrap" style="margin-bottom:16px"><table>
        <thead><tr><th>Deduction</th><th class="num">Monthly (₹)</th></tr></thead>
        <tbody>${dedRows}</tbody>
      </table></div>` : ""}
    <div class="fld-row" style="gap:12px;margin-bottom:16px">
      <label class="fld"><span>Variable eligible (% of CTC)</span>
        <input type="number" step="any" min="0" max="100" id="ep_varPct" value="${varPct}" placeholder="0" style="width:100%"></label>
      <label class="fld"><span>One-time bonus (annual ₹)</span>
        <input type="number" step="any" min="0" id="ep_bonus" value="${bonus}" placeholder="0" style="width:100%"></label>
      <label class="fld"><span>Salary year</span>
        <input type="number" min="2000" max="2100" id="ep_year" value="${yr}" style="width:100%"></label>
    </div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal()">Cancel</button>
      <button class="btn" onclick="applyExtraction(${pi})">Apply to ${esc(p.name)}</button>
    </div>
    <p class="muted small" style="margin-top:10px">Applying will replace the existing components &amp; deductions for the selected year.</p>`,
    { comps, deds, compLen: comps.length, dedLen: deds.length });
}

function applyExtraction(pi) {
  const year = parseInt(el("#ep_year").value, 10);
  if (!year) { toast("Invalid year", true); return; }

  const components = [];
  let i = 0;
  while (el("#ec_name_" + i)) {
    const name = (el("#ec_name_" + i).value || "").trim();
    const amt  = parseFloat(el("#ec_amt_"  + i).value) || 0;
    const scp  = el("#ec_scope_" + i).value;
    if (name) components.push({ component: name, amount: amt, scope: scp });
    i++;
  }

  const deductions = [];
  let j = 0;
  while (el("#ed_name_" + j)) {
    const name = (el("#ed_name_" + j).value || "").trim();
    const amt  = parseFloat(el("#ed_amt_"  + j).value) || 0;
    if (name) deductions.push({ component: name, amount: amt });
    j++;
  }

  const varPct  = el("#ep_varPct").value.trim();
  const bonus   = el("#ep_bonus").value.trim();
  const p       = DB.income.persons[pi];
  let yr        = (p.salaryYears || []).find(y => y.year === year);

  if (!yr) {
    yr = { year, components: [], deductions: [], variablePctEligible: 0, variablePctEarned: null, oneTimeBonus: 0 };
    (p.salaryYears = p.salaryYears || []).push(yr);
    p.salaryYears.sort((a, b) => b.year - a.year);
  }

  yr.components = components;
  yr.deductions = deductions;
  if (varPct !== "") yr.variablePctEligible = parseFloat(varPct) || 0;
  if (bonus  !== "") yr.oneTimeBonus        = parseFloat(bonus)  || 0;

  incomeYear = year;
  closeModal(); save(); render();
  toast("Applied extracted data for " + year);
}

function addSalaryYear(pi) {
  const p = DB.income.persons[pi];
  const existing = (p.salaryYears || []).map(y => y.year);
  const curYear = new Date().getFullYear();
  const nextYear = existing.length ? Math.max(...existing) + 1 : curYear;
  formModal(`Add salary year — ${esc(p.name)}`, [
    { name: "year", label: "Year", type: "number", value: nextYear, required: true, min: 2000 },
    { name: "copyFrom", label: "Copy from year (empty = start fresh)", type: "select",
      value: "", options: [["", "— Start fresh —"], ...(p.salaryYears || []).map(y => [String(y.year), String(y.year)])] },
  ], v => {
    const yr = num(v.year);
    if (existing.includes(yr)) { toast(`Year ${yr} already exists for ${p.name}`, true); return; }
    let entry;
    if (v.copyFrom) {
      const src = (p.salaryYears || []).find(y => y.year === num(v.copyFrom));
      if (src) entry = { ...JSON.parse(JSON.stringify(src)), year: yr, variablePctEarned: null, oneTimeBonus: 0 };
    }
    if (!entry) entry = { year: yr, components: [], deductions: [], variablePctEligible: 0, variablePctEarned: null, oneTimeBonus: 0 };
    p.salaryYears.push(entry);
    incomeYear = yr;
    closeModal(); save(); render();
  });
}
function salaryGrowthChart(persons) {
  const multi = persons.filter(p => (p.salaryYears || []).length >= 2);
  if (!multi.length) return "";
  const allYears = [...new Set(multi.flatMap(p => (p.salaryYears || []).map(y => y.year)))].sort((a, b) => a - b);
  if (allYears.length < 2) return "";
  const series = multi.map((p, i) => {
    const map = {};
    (p.salaryYears || []).forEach(yr => { map[yr.year] = incomeTotalsForYear(yr).yearlyCtc; });
    return { name: p.name, color: PALETTE[i % PALETTE.length], map };
  });
  const maxVal = Math.max(...series.flatMap(s => Object.values(s.map)), 1);
  const W = 560, H = 220, PL = 82, PR = 20, PT = 20, PB = 40;
  const cW = W - PL - PR, cH = H - PT - PB;
  const xStep = allYears.length > 1 ? cW / (allYears.length - 1) : cW;
  const xOf = i => PL + i * xStep;
  const yOf = v => PT + cH - (v / maxVal * cH);
  const grid = [0, 0.25, 0.5, 0.75, 1].map(pct => {
    const y = PT + cH * (1 - pct), v = maxVal * pct;
    return `<line x1="${PL}" y1="${y}" x2="${W - PR}" y2="${y}" stroke="#e4e8f0" stroke-dasharray="4,4"/>
      <text x="${PL - 6}" y="${y + 4}" text-anchor="end" font-size="11" fill="#6b7385">${fmtMoney(v, true)}</text>`;
  }).join("");
  const xLabels = allYears.map((y, i) =>
    `<text x="${xOf(i)}" y="${H - 8}" text-anchor="middle" font-size="12" fill="#1c2333">${y}</text>`).join("");
  const seriesSVG = series.map(s => {
    const pts = allYears.map((y, i) => s.map[y] !== undefined ? { x: xOf(i), y: yOf(s.map[y]), v: s.map[y] } : null).filter(Boolean);
    if (!pts.length) return "";
    const line = pts.length >= 2 ? `<polyline points="${pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ")}"
      fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>` : "";
    const dots = pts.map(p => `<g><circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="5" fill="${s.color}" stroke="#fff" stroke-width="2"/>
      <title>${s.name} ${fmtMoney(p.v)}/yr</title></g>`).join("");
    return line + dots;
  }).join("");
  const legend = series.map(s =>
    `<span style="display:flex;align-items:center;gap:5px;font-size:12.5px">
      <i class="dot" style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
  const growthKpis = series.map(s => {
    const yrs = allYears.filter(y => s.map[y] !== undefined);
    if (yrs.length < 2) return "";
    const first = s.map[yrs[0]], last = s.map[yrs[yrs.length - 1]];
    const totalGrowth = first ? (last - first) / first : 0;
    const yoy = s.map[yrs[yrs.length - 2]] ? (last / s.map[yrs[yrs.length - 2]]) - 1 : 0;
    return `<div class="kpi"><div class="label" style="display:flex;align-items:center;gap:6px">
      <i class="dot" style="background:${s.color}"></i>${esc(s.name)}</div>
      <div class="value">${fmtMoney(last, true)}/yr</div>
      <div class="delta muted">Total: ${fmtPct(totalGrowth)} · YoY: ${fmtPct(yoy)}</div></div>`;
  }).join("");
  return `<div class="panel"><h2>Salary Growth</h2>
    <div style="overflow-x:auto">
      <svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:${W}px;display:block">
        ${grid}
        <line x1="${PL}" y1="${PT}" x2="${PL}" y2="${PT + cH}" stroke="#e4e8f0"/>
        ${xLabels}${seriesSVG}
      </svg>
    </div>
    <div class="legend" style="padding-left:${PL}px">${legend}</div>
    <div class="grid kpis" style="margin-top:12px">${growthKpis}</div>
  </div>`;
}

/* ================= EXPENSES ================= */
function groupDim(g) {
  const meta = DB.settings.predefined.expenseGroupMeta || {};
  return (meta[g] && meta[g].dim) || "none";
}
const DIM_LABEL = { location: "Location", person: "Person", none: "" };
PAGES.expenses = () => {
  const groups = {};
  DB.expenses.forEach((e, i) => {
    const g = e.group || "Other";
    (groups[g] = groups[g] || []).push([e, i]);
  });
  /* sections created explicitly but still empty */
  Object.entries(DB.settings.predefined.expenseGroupMeta || {}).forEach(([g, m]) => {
    if (m.created && !groups[g]) groups[g] = [];
  });
  const total = pureExpenses();
  const emiTotal = activeLoans().reduce((s, L) => s + loanState(L).emi, 0);
  const invTotal = DB.monthlyInvestments.filter(i => (i.deductedFrom || "IN HAND") === "IN HAND").reduce((s, i) => s + num(i.amount), 0);

  /* ---------- analytics ---------- */
  const inHand = totalInHand();
  const surplus = inHand - total - emiTotal - invTotal;
  const kept = invTotal + surplus;                 // money not consumed (saved/invested + leftover)
  const savingsRate = inHand > 0 ? kept / inHand : 0;

  const byGroup = {};
  DB.expenses.forEach(e => { const g = e.group || "Other"; byGroup[g] = (byGroup[g] || 0) + num(e.amount); });
  const groupItems = Object.entries(byGroup).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
    .map(([label, value], ix) => ({ label, value, color: PALETTE[ix % PALETTE.length] }));

  const byCat = {};
  DB.expenses.forEach(e => { const k = e.category || "Other"; byCat[k] = (byCat[k] || 0) + num(e.amount); });
  const catItems = Object.entries(byCat).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 8)
    .map(([label, value]) => ({ label, value }));
  const topCat = catItems[0];

  const byPerson = {}; let shared = 0;
  DB.expenses.forEach(e => {
    const amt = num(e.amount);
    if (groupDim(e.group) === "person" && e.person) byPerson[e.person] = (byPerson[e.person] || 0) + amt;
    else shared += amt;
  });
  const personItems = [...Object.entries(byPerson).map(([label, value]) => ({ label, value })),
    ...(shared > 0 ? [{ label: "Household / Shared", value: shared, color: "#5b7a99" }] : [])]
    .sort((a, b) => b.value - a.value);

  const byLoc = {};
  DB.expenses.forEach(e => { if (groupDim(e.group) === "location" && e.location) byLoc[e.location] = (byLoc[e.location] || 0) + num(e.amount); });
  const locItems = Object.entries(byLoc).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
    .map(([label, value], ix) => ({ label, value, color: PALETTE[(ix + 4) % PALETTE.length] }));

  const hasPerson = Object.keys(byPerson).length > 0;
  const hasLoc = locItems.length > 0;

  const allocSegs = [
    { label: "Living expenses", value: total, color: "#d6493f" },
    { label: "Loan EMIs", value: emiTotal, color: "#e6a323" },
    { label: "Investments", value: invTotal, color: "#2456e6" },
    { label: "Surplus", value: Math.max(0, surplus), color: "#12a06b" },
  ];
  const allocTotal = Math.max(inHand, total + emiTotal + invTotal) || 1;
  const gaugeColor = savingsRate >= 0.2 ? "var(--green)" : savingsRate >= 0 ? "var(--amber)" : "var(--red)";

  const sectionBreakdown = Object.entries(byGroup).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([g]) => {
    const cats = {};
    DB.expenses.filter(e => (e.group || "Other") === g).forEach(e => { cats[e.category || "Other"] = (cats[e.category || "Other"] || 0) + num(e.amount); });
    const segs = Object.entries(cats).sort((a, b) => b[1] - a[1]).map(([label, value], ix) => ({ label, value, color: PALETTE[ix % PALETTE.length] }));
    return `<div style="margin-bottom:18px">
      <div style="display:flex;justify-content:space-between;font-size:13px;font-weight:600;margin-bottom:7px">
        <span>${esc(g)}</span><span class="muted">${fmtMoney(byGroup[g])}</span></div>
      ${stackedBar(segs, { total: byGroup[g] })}</div>`;
  }).join("");

  const analytics = (total || inHand) ? `
  <div class="grid kpis">
    <div class="kpi"><div class="label">Recurring Spend</div><div class="value">${fmtMoney(total)}</div>
      <div class="delta muted">${inHand ? fmtPct(total / inHand) + " of in-hand" : ""}</div></div>
    <div class="kpi"><div class="label">Total Outflow</div><div class="value">${fmtMoney(total + emiTotal + invTotal)}</div>
      <div class="delta muted">incl. EMIs &amp; investments</div></div>
    <div class="kpi"><div class="label">Biggest Category</div><div class="value" style="font-size:18px">${topCat ? esc(topCat.label) : "—"}</div>
      <div class="delta muted">${topCat ? fmtMoney(topCat.value) + "/mo" : ""}</div></div>
    <div class="kpi"><div class="label">Monthly Surplus</div><div class="value ${surplus >= 0 ? "pos" : "neg"}">${fmtMoney(surplus)}</div>
      <div class="delta muted">${surplus < 0 ? "overspending in-hand" : "after all outflow"}</div></div>
  </div>
  <div class="panel"><h2>Where Your In-Hand Goes</h2>
    <div class="grid two-col" style="align-items:center">
      <div>${stackedBar(allocSegs, { total: allocTotal })}
        ${surplus < 0 ? `<p class="small neg mt">⚠ Outflow exceeds in-hand by ${fmtMoney(-surplus)}.</p>` : ""}</div>
      <div style="text-align:center">${gaugeArc(savingsRate, fmtPct(savingsRate), "of in-hand kept", gaugeColor)}
        <div class="small muted" style="margin-top:4px">Investments + surplus vs in-hand income</div></div>
    </div>
  </div>
  <div class="grid two-col">
    <div class="panel"><h2>Spend by Section</h2>${donutChart(groupItems)}</div>
    <div class="panel"><h2>Top Categories</h2>${barChart(catItems)}</div>
  </div>
  ${(hasPerson || hasLoc) ? `<div class="grid two-col">
    ${hasPerson ? `<div class="panel"><h2>Spend by Person</h2>${barChart(personItems)}
      <p class="small muted mt">Sections that track a person are attributed; everything else counts as Household / Shared.</p></div>` : ""}
    ${hasLoc ? `<div class="panel"><h2>Spend by Location</h2>${donutChart(locItems)}</div>` : ""}
  </div>` : ""}
  <div class="panel"><h2>Section Breakdown by Category</h2>${sectionBreakdown || '<div class="empty">No data</div>'}</div>` : "";

  return pageHead("Monthly Expenses",
    `Recurring spend: <b>${fmtMoney(total)}</b> &nbsp;·&nbsp; + loan EMIs ${fmtMoney(emiTotal)} &nbsp;·&nbsp; + in-hand investments ${fmtMoney(invTotal)} &nbsp;=&nbsp; <b>${fmtMoney(total + emiTotal + invTotal)}</b> total outflow`,
    `<button class="btn" onclick="addExpense()">+ Add Expense</button>
     <button class="btn secondary" onclick="addExpenseSection()">+ Add Section</button>`) +
    analytics +
    (Object.entries(groups).map(([g, items]) => {
      const sub = items.reduce((s, [e]) => s + num(e.amount), 0);
      const dim = groupDim(g);
      const paged = pagedRows(`expenses:${g}`, items, ([e, i]) => `
          <tr><td>${esc(e.category)}</td>${dim !== "none" ? `<td class="muted">${esc((dim === "location" ? e.location : e.person) || "—")}</td>` : ""}
          <td class="num"><input class="inline-input" value="${num(e.amount)}" onchange="updExpense(${i},this.value)"></td>
          <td><button class="icon-btn" title="Edit" onclick="editExpense(${i})">✎</button>
              <button class="icon-btn danger" title="Delete" onclick="delExpense(${i})">✕</button></td></tr>`);
      return `<div class="panel"><h2>${esc(g)} <span class="chip">${fmtMoney(sub)}</span>
        <span style="margin-left:auto;display:flex;gap:6px;align-items:center">
          <span class="chip gray" title="What each entry tracks besides category">${dim === "none" ? "category only" : "by " + dim}</span>
          <button class="btn small ghost" title="Section settings" onclick="editExpenseSection('${esc(g)}')">⚙</button>
          <button class="btn small secondary" onclick="addExpense('${esc(g)}')">+ Add</button></span></h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Category</th>${dim !== "none" ? `<th>${DIM_LABEL[dim]}</th>` : ""}<th class="num">Monthly Cost</th><th style="width:70px"></th></tr></thead>
        <tbody>${paged.html || `<tr><td colspan="4" class="muted">No entries yet — click + Add.</td></tr>`}
        </tbody></table></div>${paged.footer}</div>`;
    }).join("") || '<div class="panel"><div class="empty">No expenses yet.</div></div>');
};
function addExpenseSection(g) {
  const meta = DB.settings.predefined.expenseGroupMeta;
  const existing = g ? meta[g] : null;
  formModal(g ? `Section settings — ${g}` : "Add expense section", [
    ...(g ? [] : [{ name: "name", label: "Section name (e.g. Housing, Food…)", required: true }]),
    { name: "dim", label: "Besides category & amount, each entry tracks…", type: "select",
      value: existing ? existing.dim : "none",
      options: [["none", "Nothing — just category & amount"], ["location", "Location"], ["person", "Person"]] },
  ], v => {
    const name = g || v.name;
    addPred("expenseGroups", name);
    meta[name] = Object.assign(meta[name] || {}, { dim: v.dim, created: true });
    closeModal(); save(); render();
  });
}
function editExpenseSection(g) { addExpenseSection(g); }
function expenseFields(e, presetGroup) {
  e = e || {};
  const g = e.group || presetGroup || predList("expenseGroups")[0] || "Other";
  return [
    { name: "group", label: "Section / Group", type: "combo", listKey: "expenseGroups", value: g },
    { name: "category", label: "Category", type: "combo", listKey: "expenseCategories:" + g,
      value: e.category || "", options: predList("expenseCategories:" + g) },
    { name: "dimMode", label: "This section tracks", type: "select", value: groupDim(g),
      options: [["none", "Nothing — just category & amount"], ["location", "Location"], ["person", "Person"]] },
    { name: "location", label: "Location", type: "combo", listKey: "locations", value: e.location || "", allowEmpty: true },
    { name: "person", label: "Person", type: "combo", listKey: "persons", value: e.person || "", allowEmpty: true },
    { name: "amount", label: "Monthly cost", type: "number", step: "any", value: e.amount ?? "", required: true },
    { name: "notes", label: "Notes (optional)", value: e.notes || "" },
  ];
}
/* show/hide location & person fields based on the chosen group's dimension */
function wireExpenseModal(ov) {
  const groupCombo = els(".combo", ov).find(c => el("input[type=hidden]", c).name === "group");
  const catCombo = els(".combo", ov).find(c => el("input[type=hidden]", c).name === "category");
  const dimSel = el('[name="dimMode"]', ov);
  const fldOf = n => el(`[name="${n}"]`, ov).closest(".fld");
  const sync = () => {
    const dim = dimSel.value;
    fldOf("location").style.display = dim === "location" ? "" : "none";
    fldOf("person").style.display = dim === "person" ? "" : "none";
  };
  dimSel.addEventListener("change", sync);
  groupCombo.addEventListener("combochange", () => {
    const g = el("input[type=hidden]", groupCombo).value || "Other";
    setComboOptions(catCombo, predList("expenseCategories:" + g), "expenseCategories:" + g);
    dimSel.value = groupDim(g);
    sync();
  });
  sync();
}
function expenseVals(v) {
  return { group: v.group || "Other", category: v.category,
    location: v.dimMode === "location" ? v.location : "",
    person: v.dimMode === "person" ? v.person : "",
    amount: num(v.amount), notes: v.notes };
}
function saveExpenseMeta(v) {
  const meta = DB.settings.predefined.expenseGroupMeta;
  const g = v.group || "Other";
  addPred("expenseGroups", g);
  meta[g] = Object.assign(meta[g] || {}, { dim: v.dimMode });
}
function addExpense(presetGroup) {
  formModal("Add expense", expenseFields(null, presetGroup), v => {
    if (!v.category) return toast("Please choose or enter a category", true);
    saveExpenseMeta(v);
    DB.expenses.push({ id: uid(), ...expenseVals(v) });
    closeModal(); save(); render();
  }, { onReady: wireExpenseModal });
}
function editExpense(i) {
  formModal("Edit expense", expenseFields(DB.expenses[i]), v => {
    if (!v.category) return toast("Please choose or enter a category", true);
    saveExpenseMeta(v);
    Object.assign(DB.expenses[i], expenseVals(v));
    closeModal(); save(); render();
  }, { onReady: wireExpenseModal });
}
function updExpense(i, val) {
  const r = validAmount(val);
  if (!r.ok) { toast("Enter a valid non-negative number", true); render(); return; }
  DB.expenses[i].amount = r.n; save(); render();
}
function delExpense(i) {
  confirmModal(`Delete "${DB.expenses[i].category}"?`, () => { DB.expenses.splice(i, 1); save(); render(); });
}

/* ================= MONTHLY INVESTMENTS ================= */
PAGES.investments = () => {
  const total = DB.monthlyInvestments.reduce((s, i) => s + num(i.amount), 0);
  const byPerson = {};
  DB.monthlyInvestments.forEach(i => { const p = i.person || "—"; byPerson[p] = (byPerson[p] || 0) + num(i.amount); });
  const paged = pagedRows("minv", DB.monthlyInvestments.map((iv, i) => [iv, i]), ([iv, i]) => `
      <tr><td>${esc(iv.category)}</td><td>${esc(iv.person || "—")}</td>
      <td><span class="chip ${iv.deductedFrom === "CTC" ? "amber" : iv.deductedFrom === "GROSS" ? "gray" : "green"}">${esc(iv.deductedFrom || "IN HAND")}</span></td>
      <td class="num"><input class="inline-input" value="${num(iv.amount)}" onchange="updInvestment(${i},this.value)"></td>
      <td><button class="icon-btn" onclick="editInvestment(${i})">✎</button>
          <button class="icon-btn danger" onclick="delInvestment(${i})">✕</button></td></tr>`);
  return pageHead("Monthly Investments / Savings",
    `Total committed per month: <b>${fmtMoney(total)}</b>`,
    `<button class="btn" onclick="addInvestment()">+ Add</button>`) + `
  <div class="page-note">ⓘ&nbsp;<div><b>These are family-level commitments.</b>
    <b>Person</b> is whose <i>name</i> the investment is held in — not who earns it.
    <b>Deducted From</b> says where the money leaves the <i>family's</i> salary:
    <b>IN HAND</b> reduces spendable income, while <b>GROSS</b> / <b>CTC</b> come out before in-hand (e.g. EPF, employer NPS).</div></div>
  <div class="grid kpis">${Object.entries(byPerson).map(([p, v]) =>
    `<div class="kpi"><div class="label">In ${esc(p)}'s name</div><div class="value">${fmtMoney(v)}</div></div>`).join("")}</div>
  <div class="panel"><div class="table-wrap"><table>
    <thead><tr><th>Instrument</th>
      <th>Held in name of${info("Whose name the investment is under — not necessarily the earner. Family-level commitment.")}</th>
      <th>Deducted From${info("Where it leaves the family salary: IN HAND = from spendable income; GROSS / CTC = before in-hand (e.g. EPF, employer NPS).")}</th>
      <th class="num">Monthly</th><th style="width:70px"></th></tr></thead>
    <tbody>${paged.html}
      <tr class="subtotal"><td colspan="3">Total</td><td class="num">${fmtMoney(total)}</td><td></td></tr>
    </tbody></table></div>${paged.footer}
  </div>`;
};
function investmentFields(iv) {
  iv = iv || {};
  return [
    { name: "category", label: "Instrument", type: "combo", listKey: "investmentInstruments", value: iv.category || "" },
    { type: "row", fields: [
      { name: "person", label: "Held in name of", type: "combo", listKey: "persons", value: iv.person || "", allowEmpty: true },
      { name: "deductedFrom", label: "Deducted from (family salary)", type: "select", value: iv.deductedFrom || "IN HAND", options: ["IN HAND", "GROSS", "CTC"] }]},
    { name: "amount", label: "Monthly amount", type: "number", step: "any", value: iv.amount ?? "", required: true },
  ];
}
function addInvestment() {
  formModal("Add monthly investment", investmentFields(), v => {
    if (!v.category) return toast("Please choose or enter an instrument", true);
    DB.monthlyInvestments.push({ id: uid(), category: v.category, person: v.person, deductedFrom: v.deductedFrom, amount: num(v.amount) });
    closeModal(); save(); render();
  });
}
function editInvestment(i) {
  formModal("Edit monthly investment", investmentFields(DB.monthlyInvestments[i]), v => {
    if (!v.category) return toast("Please choose or enter an instrument", true);
    Object.assign(DB.monthlyInvestments[i], { category: v.category, person: v.person, deductedFrom: v.deductedFrom, amount: num(v.amount) });
    closeModal(); save(); render();
  });
}
function updInvestment(i, val) {
  const r = validAmount(val);
  if (!r.ok) { toast("Enter a valid non-negative number", true); render(); return; }
  DB.monthlyInvestments[i].amount = r.n; save(); render();
}
function delInvestment(i) {
  confirmModal(`Delete "${DB.monthlyInvestments[i].category}"?`, () => { DB.monthlyInvestments.splice(i, 1); save(); render(); });
}

/* ================= PORTFOLIO ================= */
PAGES.portfolio = () => {
  const owners = [...new Set(DB.portfolio.map(p => p.owner || "Family"))];
  const pt = portfolioTotals(), gold = goldTotal(), gg = goldGain();
  const totalGrams = DB.gold.reduce((s, g) => s + num(g.grams), 0);

  /* ---- aggregate by asset class (invested vs current), gold = its own class ---- */
  const invByClass = {}, curByClass = {};
  DB.portfolio.forEach(p => {
    const k = p.subCategory || "Other";
    invByClass[k] = (invByClass[k] || 0) + num(p.invested);
    curByClass[k] = (curByClass[k] || 0) + num(p.currentValue);
  });
  if (gold > 0) curByClass["Physical Gold"] = (curByClass["Physical Gold"] || 0) + gold;
  if (gg.inv > 0) invByClass["Physical Gold"] = (invByClass["Physical Gold"] || 0) + gg.inv;

  const classes = [...new Set([...Object.keys(invByClass), ...Object.keys(curByClass)])];
  const classColor = {};
  classes.forEach((c, ix) => { classColor[c] = PALETTE[ix % PALETTE.length]; });
  const investedItems = classes.map(c => ({ label: c, value: invByClass[c] || 0, color: classColor[c] }))
    .filter(x => x.value > 0).sort((a, b) => b.value - a.value);
  const currentItems = classes.map(c => ({ label: c, value: curByClass[c] || 0, color: classColor[c] }))
    .filter(x => x.value > 0).sort((a, b) => b.value - a.value);
  const classCompare = classes.map(c => ({ label: c, a: invByClass[c] || 0, b: curByClass[c] || 0 }))
    .filter(x => x.a || x.b).sort((a, b) => b.b - a.b);

  /* ---- gold split by person (share of value) ---- */
  const goldByPerson = {};
  DB.gold.forEach(g => { goldByPerson[g.person || "—"] = (goldByPerson[g.person || "—"] || 0) + num(g.grams) * num(g.perGramValue); });
  const goldPersonItems = Object.entries(goldByPerson).filter(([, v]) => v > 0)
    .map(([label, value], ix) => ({ label, value, color: PALETTE[(ix + 2) % PALETTE.length] }));

  /* ---- combined performance ---- */
  const totInv = pt.inv + gg.inv, totCur = pt.cur + gold, totGain = totCur - totInv;
  const holdingGains = DB.portfolio.map(p => ({ label: p.category, value: num(p.currentValue) - num(p.invested) }))
    .filter(x => x.value !== 0).sort((a, b) => b.value - a.value);

  const goldPaged = pagedRows("gold", DB.gold.map((g, i) => [g, i]), ([g, i]) => {
    const cur = num(g.grams) * num(g.perGramValue);
    const hasBuy = g.purchasePrice !== null && g.purchasePrice !== undefined && num(g.purchasePrice) > 0;
    const inv = hasBuy ? num(g.grams) * num(g.purchasePrice) : 0;
    const gain = hasBuy ? cur - inv : null;
    /* Buy Price/g + Current/g are money (format like Total Value/Gain, which already use
       fmtMoney below); Grams is a quantity, left as a plain editable number. hideValues
       folds these into static formatted text so they match the already-masked Gain cell. */
    const buyCell = hideValues
      ? `<td class="num">${hasBuy ? fmtMoney(num(g.purchasePrice)) : "—"}</td>`
      : `<td class="num"><input class="inline-input" value="${hasBuy ? num(g.purchasePrice).toLocaleString("en-IN") : ""}"
        placeholder="—" onchange="updGold(${i},'purchasePrice',this.value||null)"></td>`;
    const curPriceCell = hideValues
      ? `<td class="num">${fmtMoney(num(g.perGramValue))}</td>`
      : `<td class="num"><input class="inline-input" value="${num(g.perGramValue).toLocaleString("en-IN")}" onchange="updGold(${i},'perGramValue',this.value)"></td>`;
    return `<tr><td>${esc(g.person)}</td>
      <td class="num"><input class="inline-input" value="${num(g.grams)}" onchange="updGold(${i},'grams',this.value)"></td>
      ${buyCell}
      ${curPriceCell}
      <td class="num">${fmtMoney(cur)}</td>
      <td class="num ${gain !== null ? (gain >= 0 ? "pos" : "neg") : ""}">${gain !== null ? fmtMoney(gain) : "—"}</td>
      <td><button class="icon-btn danger" onclick="delGold(${i})">✕</button></td></tr>`;
  });

  return pageHead("Portfolio",
    `Current <b>${fmtMoney(totCur)}</b> (incl. gold) on invested ${fmtMoney(totInv)}`,
    `<button class="btn" onclick="addHolding()">+ Add Holding</button>
     <button class="btn secondary" onclick="addGold()">+ Add Gold</button>`) +
  (DB.liveRowsError ? `<div class="page-note warn">⚠&nbsp;<div><b>Live investment sync issue:</b> ${esc(DB.liveRowsError)} — showing the last values we have.</div></div>` : "") +
  `<div class="page-note">ⓘ&nbsp;<div>Holdings track <b>invested vs current value</b> per owner; <b>Maturity</b> shows the owner's age and the year an instrument unlocks (set a date of birth in Settings). Physical gold values use one current rate you can auto-fetch or set for all rows.</div></div>` +

  /* ---- TOP: family asset allocation by class ---- */
  `<div class="grid two-col">
    <div class="panel"><h2>Asset Allocation — Invested</h2>${donutChart(investedItems)}</div>
    <div class="panel"><h2>Asset Allocation — Current Value</h2>${donutChart(currentItems)}</div>
  </div>
  <div class="grid two-col">
    <div class="panel"><h2>Physical Gold by Person</h2>${
      goldPersonItems.length ? donutChart(goldPersonItems) : '<div class="empty">No gold entries yet</div>'}</div>
    <div class="panel"><h2>Invested vs Current by Class</h2>${
      compareBars(classCompare, "Invested", "Current value")}</div>
  </div>` +

  owners.map(o => {
    const items = DB.portfolio.map((p, i) => [p, i]).filter(([p]) => (p.owner || "Family") === o);
    const cur = items.reduce((s, [p]) => s + num(p.currentValue), 0);
    const inv = items.reduce((s, [p]) => s + num(p.invested), 0);
    const birthYear = personBirthYear(o);
    const paged = pagedRows(`portfolio:${o}`, items, ([p, i]) => {
      const roi = num(p.invested) ? (num(p.currentValue) - num(p.invested)) / num(p.invested) : 0;
      const m = maturityInfo(p, birthYear, new Date().getFullYear());
      /* liveSync rows are server-injected from synced broker/NSE accounts (invest.html);
         client edits to invested/currentValue are discarded server-side on save, so show
         them read-only with a badge instead of editable inputs + edit/delete actions. */
      /* hideValues: fold into the same "static formatted" branch as liveSync so a hidden
         row matches its own (already-masked) Return% cell instead of leaking raw digits. */
      const invCell = (p.liveSync || hideValues) ? `<td class="num">${fmtMoney(num(p.invested))}</td>`
        : `<td class="num"><input class="inline-input" value="${num(p.invested).toLocaleString("en-IN")}" onchange="updHolding(${i},'invested',this.value)"></td>`;
      const curCell = (p.liveSync || hideValues) ? `<td class="num">${fmtMoney(num(p.currentValue))}</td>`
        : `<td class="num"><input class="inline-input" value="${num(p.currentValue).toLocaleString("en-IN")}" onchange="updHolding(${i},'currentValue',this.value)"></td>`;
      const actionsCell = p.liveSync
        ? `<td><span class="chip live" title="${esc(p.category)} · synced ${p.lastSynced ? fmtDateTime(p.lastSynced) : "—"}">⟳ live</span></td>`
        : `<td><button class="icon-btn" onclick="editHolding(${i})">✎</button>
              <button class="icon-btn danger" onclick="delHolding(${i})">✕</button></td>`;
      return `<tr><td><b>${esc(p.category)}</b>${p.notes ? `<div class="small muted">${esc(p.notes)}</div>` : ""}</td>
          <td><span class="chip gray">${esc(p.subCategory || "—")}</span></td>
          ${invCell}
          ${curCell}
          <td class="num ${roi >= 0 ? "pos" : "neg"}">${fmtPct(roi)}</td>
          <td class="num">${m.age != null ? m.age + " yrs" : "—"}</td>
          <td class="num">${m.year != null ? m.year : "—"}</td>
          ${actionsCell}</tr>`;
    });
    return `<div class="panel"><h2>${esc(o)}'s Holdings
      <span class="chip ${cur >= inv ? "green" : "red"}">${fmtMoney(cur)} · ${inv ? fmtPct((cur - inv) / inv) : "–"}</span></h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Asset</th><th>Class</th><th class="num">Invested</th><th class="num">Current</th><th class="num">Return</th>
        <th class="num">Matures (age)${info("Owner's age when this holding matures/unlocks. Needs the owner's date of birth (set it in Settings).")}</th>
        <th class="num">Matures (year)</th><th style="width:70px"></th></tr></thead>
      <tbody>${paged.html}
      <tr class="subtotal"><td colspan="2">Subtotal</td><td class="num">${fmtMoney(inv)}</td><td class="num">${fmtMoney(cur)}</td>
        <td class="num">${inv ? fmtPct((cur - inv) / inv) : "–"}</td><td></td><td></td><td></td></tr>
      </tbody></table></div>${paged.footer}</div>`;
  }).join("") + `
  <div class="panel">
    <h2>Physical Gold <span class="chip amber">${fmtMoney(gold)}</span>
      ${gg.inv > 0 ? `<span class="chip ${gg.gain >= 0 ? "green" : "red"}" style="font-size:12px">gain ${fmtMoney(gg.gain)} (${fmtPct(gg.gain/gg.inv)})</span>` : ""}
    </h2>
    ${DB.gold.length ? `<div class="filter-bar" style="margin-bottom:14px;align-items:center">
      <span class="small muted">Current rate — applies to all entries:</span>
      <input id="goldRate" type="number" step="any" placeholder="₹ / gram" style="max-width:150px"
        value="${(() => { const v = [...new Set(DB.gold.map(g => num(g.perGramValue)))]; return v.length === 1 && v[0] ? v[0] : ""; })()}">
      <button class="btn small secondary" onclick="setGoldRateFromInput()">Apply to all</button>
      <button class="btn small" id="goldFetchBtn" onclick="fetchGoldPrice()">⟳ Auto-fetch (24K)</button>
    </div>` : ""}
    <div class="table-wrap"><table>
      <thead><tr><th>Person</th><th class="num">Grams</th><th class="num">Buy Price/g</th><th class="num">Current/g</th><th class="num">Total Value</th><th class="num">Gain/Loss</th><th style="width:50px"></th></tr></thead>
      <tbody>${goldPaged.html}
      <tr class="subtotal"><td>Total</td><td class="num">${fmtNum(totalGrams)} g</td><td></td>
        <td></td><td class="num">${fmtMoney(gold)}</td>
        <td class="num ${gg.inv > 0 ? (gg.gain >= 0 ? "pos" : "neg") : ""}">${gg.inv > 0 ? fmtMoney(gg.gain) : "—"}</td><td></td></tr>
      </tbody></table></div>${goldPaged.footer}
    <p class="muted small mt">Buy Price/g: price paid per gram when purchased — used to calculate investment gain.</p>
  </div>

  <div class="panel">
    <h2>Investment Performance</h2>
    <div class="grid kpis" style="margin-bottom:18px">
      <div class="kpi"><div class="label">Total Invested</div><div class="value">${fmtMoney(totInv)}</div>
        <div class="delta muted">portfolio + priced gold</div></div>
      <div class="kpi"><div class="label">Current Value</div><div class="value">${fmtMoney(totCur)}</div></div>
      <div class="kpi"><div class="label">Total Gain</div><div class="value ${totGain >= 0 ? "pos" : "neg"}">${fmtMoney(totGain)}</div>
        <div class="delta muted">${totInv ? fmtPct(totGain / totInv) + " overall return" : ""}</div></div>
      <div class="kpi"><div class="label">Gold Gain</div><div class="value ${gg.gain >= 0 ? "pos" : "neg"}">${gg.inv ? fmtMoney(gg.gain) : "–"}</div>
        <div class="delta muted">${gg.inv ? fmtPct(gg.gain / gg.inv) + " on priced gold" : "no buy price set"}</div></div>
    </div>
    <div class="grid two-col">
      <div>
        <h3 style="font-size:14px;margin-bottom:10px">Invested vs Current by Asset Class</h3>
        ${compareBars(classCompare, "Invested", "Current value")}
      </div>
      <div>
        <h3 style="font-size:14px;margin-bottom:10px">Gain / Loss by Holding</h3>
        ${signedBars(holdingGains)}
      </div>
    </div>
  </div>`;
};
function holdingFields(p) {
  p = p || {};
  return [
    { name: "category", label: "Asset name", type: "combo", listKey: "assetNames", value: p.category || "", required: true },
    { type: "row", fields: [
      { name: "subCategory", label: "Asset class", type: "combo", listKey: "assetClasses", value: p.subCategory || "Equity" },
      { name: "owner", label: "Owner", type: "select", value: p.owner || "", options: ["", ...DB.settings.persons, "Family"] }]},
    { type: "row", fields: [
      { name: "invested", label: "Amount invested", type: "number", step: "any", value: p.invested ?? "", required: true },
      { name: "currentValue", label: "Current value", type: "number", step: "any", value: p.currentValue ?? "", required: true }]},
    { type: "row", fields: [
      { name: "maturityAge", label: "Matures at owner's age (e.g. 60 for PPF/NPS; 0 = none)", type: "number", value: p.maturityAge ?? 0 },
      { name: "maturityMonths", label: "…or lock-in from now (months)", type: "number", value: p.maturityMonths ?? 0 }]},
    { name: "notes", label: "Notes (optional)", value: p.notes || "" },
  ];
}
function addHolding() {
  formModal("Add holding", holdingFields(), v => {
    DB.portfolio.push({ id: uid(), category: v.category, subCategory: v.subCategory, owner: v.owner,
      invested: num(v.invested), currentValue: num(v.currentValue), maturityAge: num(v.maturityAge) || null,
      maturityMonths: num(v.maturityMonths), notes: v.notes });
    closeModal(); save(); render();
  }, { note: "Maturity age needs the owner's date of birth (Settings → Family Members). Use lock-in months for Family-owned or undated holdings." });
}
function editHolding(i) {
  formModal("Edit holding", holdingFields(DB.portfolio[i]), v => {
    Object.assign(DB.portfolio[i], { category: v.category, subCategory: v.subCategory, owner: v.owner,
      invested: num(v.invested), currentValue: num(v.currentValue), maturityAge: num(v.maturityAge) || null,
      maturityMonths: num(v.maturityMonths), notes: v.notes });
    closeModal(); save(); render();
  });
}
function updHolding(i, f, val) { DB.portfolio[i][f] = num(val); save(); render(); }
function delHolding(i) {
  confirmModal(`Delete "${DB.portfolio[i].category}" (${DB.portfolio[i].owner || "Family"})?`, () => { DB.portfolio.splice(i, 1); save(); render(); });
}
function addGold() {
  formModal("Add physical gold", [
    { name: "person", label: "Person", type: "combo", listKey: "persons", required: true },
    { type: "row", fields: [
      { name: "grams", label: "Grams", type: "number", step: "any", required: true },
      { name: "perGramValue", label: "Current value / g", type: "number", step: "any", required: true }]},
    { name: "purchasePrice", label: "Buy price / g (optional — enables gain)", type: "number", step: "any" },
  ], v => {
    const buy = String(v.purchasePrice || "").trim();
    DB.gold.push({ id: uid(), person: v.person, grams: num(v.grams), perGramValue: num(v.perGramValue),
      purchasePrice: buy ? num(buy) : null });
    closeModal(); save(); render();
  });
}
function updGold(i, f, val) {
  DB.gold[i][f] = (val === null || val === "null" || val === "") ? null : num(val);
  save(); render();
}
function delGold(i) { confirmModal(`Remove ${DB.gold[i].person}'s gold entry?`, () => { DB.gold.splice(i, 1); save(); render(); }); }
/* set the current per-gram value on EVERY gold row at once */
function applyGoldRate(rate) {
  if (!(rate > 0)) { toast("Enter a valid gold rate", true); return; }
  DB.gold.forEach(g => { g.perGramValue = rate; });
  save(); render();
  toast(`Updated ${DB.gold.length} gold ${DB.gold.length === 1 ? "entry" : "entries"} to ${fmtMoney(rate)}/g`);
}
function setGoldRateFromInput() {
  const r = validAmount((el("#goldRate") || {}).value);
  if (!r.ok || !r.n) { toast("Enter a valid gold rate per gram", true); return; }
  applyGoldRate(r.n);
}
async function fetchGoldPrice() {
  const btn = el("#goldFetchBtn");
  if (btn) { btn.textContent = "…"; btn.disabled = true; }
  try {
    const status = await fetch("/api/gemini-status").then(r => r.json());
    if (!status.available) { toast("Gemini API key not set — enter the rate manually instead", true); return; }
    const res = await fetch("/api/fetch-gold-price", { method: "POST" });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || "fetch failed");
    if (j.price === null || j.price === undefined) { toast("Could not determine today's gold rate — enter it manually", true); return; }
    applyGoldRate(j.price);   // applies to all rows + toasts + saves
    toast(`Gold rate ${fmtMoney(j.price)}/g via ${j.model} — applied to all`);
  } catch (e) {
    toast("Gold price fetch failed: " + e.message + " — enter manually", true);
  } finally {
    if (btn) { btn.textContent = "⟳ Auto-fetch (24K)"; btn.disabled = false; }
  }
}

/* ================= LOANS ================= */
PAGES.loans = () => {
  const act = DB.loans.map((L, i) => [L, i]).filter(([L]) => L.status !== "closed");
  const closed = DB.loans.map((L, i) => [L, i]).filter(([L]) => L.status === "closed");
  const totBal = act.reduce((s, [L]) => s + loanState(L).balance, 0);
  const totEMI = act.reduce((s, [L]) => s + loanState(L).emi, 0);
  return pageHead("Loans & Debts",
    `Outstanding <b>${fmtMoney(totBal)}</b> across ${act.length} active loan(s) · ${fmtMoney(totEMI)}/month in EMIs
     <br><span class="muted">As of ${fmtDate(new Date().toISOString())} — EMIs paid, balances and months left update automatically from each loan's start date</span>`,
    `<button class="btn" onclick="addLoanFull()">+ New Loan</button>
     <button class="btn secondary" onclick="addLoanMidway()">+ Add Existing Loan (midway)</button>
     <button class="btn ghost" onclick="emiCalculator()">EMI Calculator</button>`) +
  `<div class="page-note">ⓘ&nbsp;<div>EMIs paid, outstanding balance and months left are computed automatically from each loan's start date. Use <b>Prepay</b> to record a lump-sum payment (shorten tenure or reduce EMI) — all numbers update. <b>Amortization</b> shows the full schedule with a what-if.</div></div>` +
  `<div class="panel">` +
  (act.length ? act.map(([L, i]) => loanCardHTML(L, i)).join("") : '<div class="empty">No active loans 🎉</div>') +
  `</div>` +
  (closed.length ? `<div class="section-title">Closed loans</div><div class="panel">${closed.map(([L, i]) => loanCardHTML(L, i)).join("")}</div>` : "");
};
function loanCardHTML(L, i) {
  const st = loanState(L);
  const pct = num(L.principal) ? st.paidPrincipal / num(L.principal) : 0;
  const docs = DB.documents.filter(d => d.linkedType === "loan" && d.linkedId === L.id);
  return `<div class="loan-card${L.status === "closed" ? '" style="opacity:.6' : ""}">
    <div class="loan-top"><div>
      <h3>${esc(L.name)} ${L.status === "closed" ? '<span class="chip gray">closed</span>' : ""}
        ${L.lender ? `<span class="chip">${esc(L.lender)}</span>` : ""}</h3>
      <div class="small muted">${fmtPct(num(L.annualRate))} p.a. · ${num(L.tenureMonths)} months${L.startDate ? " · started " + fmtDate(L.startDate) : ""}${L.notes ? " · " + esc(L.notes) : ""}</div>
    </div>
    <div>
      <button class="btn small secondary" onclick="showAmort(${i})">Amortization</button>
      ${L.status !== "closed" ? `<button class="btn small secondary" onclick="recordPrepayment(${i})">Prepay</button>` : ""}
      <button class="btn small ghost" onclick="editLoan(${i})">Edit</button>
      ${L.status !== "closed"
        ? `<button class="btn small ghost" onclick="closeLoan(${i})">Mark Closed</button>`
        : `<button class="btn small ghost" onclick="reopenLoan(${i})">Reopen</button>`}
      <button class="btn small danger" onclick="delLoan(${i})">Delete</button>
    </div></div>
    <div class="loan-meta">
      <div><span>EMI</span><b>${fmtMoney(st.emi)}</b></div>
      <div><span>Outstanding</span><b class="neg">${fmtMoney(st.balance)}</b></div>
      <div><span>Principal</span><b>${fmtMoney(num(L.principal))}</b></div>
      <div><span>Total Interest</span><b>${fmtMoney(st.totalInterest)}</b></div>
      <div><span>Total Payable</span><b>${fmtMoney(st.totalPayable)}</b></div>
      <div><span>EMIs Paid</span><b>${st.paidMonths}</b></div>
      <div><span>Months Left</span><b>${st.remMonths} / ${st.sched.rows.length}</b></div>
    </div>
    <div class="progress"><i style="width:${(pct * 100).toFixed(1)}%"></i></div>
    <div class="small muted mt" style="margin-top:6px">${fmtPct(pct)} of principal repaid · interest paid so far ${fmtMoney(st.paidInterest)}</div>
    ${(L.prepayments || []).length ? `<div class="small muted mt" style="margin-top:4px">
      Prepayments: ${(L.prepayments || []).map(p => `${fmtMoney(num(p.amount))} @ month ${p.month} (${p.adjustMode === "emi" ? "EMI adjusted" : "tenure shortened"})`).join(", ")}
    </div>` : ""}
    <div class="mt" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      ${docs.map(d => `<a class="chip" href="${d.type === "file" ? "/files/" + encodeURIComponent(d.ref) : esc(d.ref)}" target="_blank" title="${esc(d.title)}">📎 ${esc(d.title)}</a>`).join("")}
      <button class="btn small ghost" onclick="attachDocToLoan('${L.id}')">+ Attach file / link</button>
    </div></div>`;
}
function recordPrepayment(i) {
  const L = DB.loans[i];
  const st = loanState(L);
  const curMonth = st.paidMonths;
  formModal(`Record Prepayment — ${esc(L.name)}`, [
    { name: "amount", label: "Prepayment amount (lump sum)", type: "number", step: "any", required: true,
      placeholder: "e.g. 100000" },
    { name: "month", label: `At end of which EMI month (current: ~${curMonth})`,
      type: "number", value: curMonth + 1, min: 1 },
    { name: "adjustMode", label: "After prepayment, adjust…", type: "select",
      value: "tenure",
      options: [["tenure", "Shorten tenure (keep same EMI)"], ["emi", "Reduce EMI (keep same remaining months)"]] },
  ], v => {
    const amt = num(v.amount);
    if (!amt) return toast("Enter a valid amount", true);
    if (!L.prepayments) L.prepayments = [];
    L.prepayments.push({ id: uid(), month: num(v.month), amount: amt, adjustMode: v.adjustMode });
    L.prepayments.sort((a, b) => a.month - b.month);
    closeModal(); save(); render();
    const newSt = loanState(L);
    toast(`Prepayment recorded. New payoff in ${newSt.remMonths} months, interest saved ~${fmtMoney(st.totalInterest - newSt.totalInterest)}`);
  }, { note: "Prepayment is applied at the end of the specified EMI month. All schedules and balances update automatically." });
}
function addLoanFull() {
  formModal("New loan (from the beginning)", [
    { name: "name", label: "Loan name", required: true },
    { type: "row", fields: [
      { name: "lender", label: "Lender / bank (optional)", type: "combo", listKey: "lenders", allowEmpty: true },
      { name: "startDate", label: "First EMI month", type: "month", value: new Date().toISOString().slice(0, 7) }]},
    { type: "row", fields: [
      { name: "principal", label: "Principal amount", type: "number", step: "any", required: true },
      { name: "annualRate", label: "Interest rate % p.a.", type: "number", step: "any", required: true }]},
    { type: "row", fields: [
      { name: "tenureMonths", label: "Tenure (months)", type: "number", required: true },
      { name: "emi", label: "EMI (leave blank to auto-calculate)", type: "number", step: "any" }]},
    { name: "notes", label: "Notes (optional)" },
  ], v => {
    DB.loans.push({ id: uid(), name: v.name, lender: v.lender, principal: num(v.principal),
      annualRate: num(v.annualRate) / 100, tenureMonths: num(v.tenureMonths),
      emi: v.emi ? num(v.emi) : null, startDate: v.startDate ? v.startDate + "-01" : null,
      status: "active", notes: v.notes, addedMidway: false });
    closeModal(); save(); render(); toast("Loan added");
  }, { note: "EMI auto-calculates from principal, rate and tenure if left blank." });
}
function addLoanMidway() {
  formModal("Add a loan you're already paying (midway)", [
    { name: "name", label: "Loan name", required: true },
    { name: "lender", label: "Lender / bank (optional)", type: "combo", listKey: "lenders", allowEmpty: true },
    { type: "row", fields: [
      { name: "emi", label: "Monthly EMI you pay", type: "number", step: "any", required: true },
      { name: "remMonths", label: "Months remaining", type: "number", required: true }]},
    { name: "annualRate", label: "Interest rate % p.a.", type: "number", step: "any", required: true },
    { name: "notes", label: "Notes (optional)" },
  ], v => {
    const rate = num(v.annualRate) / 100;
    const outstanding = outstandingFromEMI(num(v.emi), rate, num(v.remMonths));
    DB.loans.push({ id: uid(), name: v.name, lender: v.lender,
      principal: Math.round(outstanding), annualRate: rate, tenureMonths: num(v.remMonths),
      emi: num(v.emi), startDate: new Date().toISOString().slice(0, 7) + "-01",
      status: "active", notes: v.notes, addedMidway: true });
    closeModal(); save(); render();
    toast("Outstanding principal computed: " + fmtMoney(outstanding));
  }, { note: "Just the EMI, months left and rate — the app derives the outstanding principal for you.", submitLabel: "Compute & Add" });
}
function editLoan(i) {
  const L = DB.loans[i];
  formModal("Edit loan", [
    { name: "name", label: "Loan name", value: L.name, required: true },
    { type: "row", fields: [
      { name: "lender", label: "Lender", type: "combo", listKey: "lenders", value: L.lender || "", allowEmpty: true },
      { name: "startDate", label: "First EMI month", type: "month", value: (L.startDate || "").slice(0, 7) }]},
    { type: "row", fields: [
      { name: "principal", label: "Principal", type: "number", step: "any", value: L.principal },
      { name: "annualRate", label: "Rate % p.a.", type: "number", step: "any", value: (num(L.annualRate) * 100).toFixed(2) }]},
    { type: "row", fields: [
      { name: "tenureMonths", label: "Tenure (months)", type: "number", value: L.tenureMonths },
      { name: "emi", label: "EMI (blank = auto)", type: "number", step: "any", value: L.emi || "" }]},
    { name: "notes", label: "Notes", value: L.notes || "" },
  ], v => {
    Object.assign(L, { name: v.name, lender: v.lender, principal: num(v.principal),
      annualRate: num(v.annualRate) / 100, tenureMonths: num(v.tenureMonths),
      emi: v.emi ? num(v.emi) : null, startDate: v.startDate ? v.startDate + "-01" : null, notes: v.notes });
    closeModal(); save(); render();
  });
}
function closeLoan(i) { DB.loans[i].status = "closed"; save(); render(); toast("Loan marked closed"); }
function reopenLoan(i) { DB.loans[i].status = "active"; save(); render(); }
function delLoan(i) {
  confirmModal(`Delete loan "${DB.loans[i].name}" permanently?`, () => { DB.loans.splice(i, 1); save(); render(); });
}
/* ── amortization modal: capped at AMORT_PAGE rows; render() doesn't rebuild
   modals, so "show more" here bumps amortLimit and re-invokes showAmort directly ── */
const AMORT_PAGE = 120;
let amortLimit = AMORT_PAGE;
let amortExtra = 0;          // stashed extra-payment value so amortMore() can re-invoke showAmort with it
let amortKeepLimit = false;  // true only while amortMore() is re-invoking showAmort, to preserve the bumped limit
function showAmort(i, extraMonthly) {
  const L = DB.loans[i];
  extraMonthly = extraMonthly || 0;
  amortExtra = extraMonthly;
  if (!amortKeepLimit) amortLimit = AMORT_PAGE;
  amortKeepLimit = false;
  const st = loanState(L);
  const base = amortSchedule(num(L.principal), num(L.annualRate), num(L.tenureMonths), st.emi || null, 0, L.prepayments);
  const what = extraMonthly ? amortSchedule(num(L.principal), num(L.annualRate), num(L.tenureMonths), st.emi || null, extraMonthly, L.prepayments) : base;
  const saved = base.totalInterest - what.totalInterest;
  const shownRows = what.rows.slice(0, amortLimit);
  const rest = what.rows.length - shownRows.length;
  const rows = shownRows.map(r => `
    <tr${r.m === st.paidMonths ? ' style="border-bottom:2px solid var(--accent)"' : ""}>
      <td class="num">${r.m}${r.m <= st.paidMonths ? ' <span class="chip green" style="font-size:10px">paid</span>' : ""}</td>
      <td class="num">${fmtMoney(r.emi)}</td><td class="num">${fmtMoney(r.principal)}</td>
      <td class="num">${fmtMoney(r.interest)}</td><td class="num">${fmtMoney(r.balance)}</td></tr>`).join("");
  const more = rest > 0
    ? `<div class="load-more"><button class="btn ghost" onclick="amortMore(${i})">Show ${Math.min(AMORT_PAGE, rest)} more · ${rest} remaining</button></div>`
    : "";
  openModal(`
    <h3>Amortization — ${esc(L.name)}</h3>
    <div class="loan-meta" style="margin-top:0">
      <div><span>EMI</span><b>${fmtMoney(st.emi)}</b></div>
      <div><span>Months</span><b>${what.months}</b></div>
      <div><span>Total Interest</span><b>${fmtMoney(what.totalInterest)}</b></div>
      <div><span>Total Paid</span><b>${fmtMoney(num(L.principal) + what.totalInterest)}</b></div>
      ${extraMonthly ? `<div><span>Interest Saved</span><b class="pos">${fmtMoney(saved)}</b></div>
      <div><span>Months Saved</span><b class="pos">${base.months - what.months}</b></div>` : ""}
    </div>
    <div class="mb" style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
      <label class="fld" style="margin:0;max-width:230px"><span>What-if: extra payment per month</span>
        <input id="extraIn" type="number" step="any" value="${extraMonthly || ""}" placeholder="e.g. 5000"></label>
      <button class="btn secondary" id="applyExtra">Recalculate</button>
      <button class="btn ghost" onclick="exportAmortCSV(${i},${extraMonthly})">Download CSV</button>
    </div>
    <div class="table-wrap" style="max-height:46vh;overflow-y:auto">
      <table><thead><tr><th class="num">#</th><th class="num">Payment</th><th class="num">Principal</th><th class="num">Interest</th><th class="num">Balance</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${more}
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">Close</button></div>`, true);
  el("#applyExtra").onclick = () => showAmort(i, num(el("#extraIn").value));
}
function amortMore(i) {
  amortLimit += AMORT_PAGE;
  amortKeepLimit = true;
  showAmort(i, amortExtra);
}
function exportAmortCSV(i, extraMonthly) {
  const L = DB.loans[i];
  const st = loanState(L);
  const s = amortSchedule(num(L.principal), num(L.annualRate), num(L.tenureMonths), st.emi || null, extraMonthly || 0, L.prepayments);
  let csv = "Month,Payment,Principal,Interest,Balance\n" +
    s.rows.map(r => [r.m, r.emi.toFixed(2), r.principal.toFixed(2), r.interest.toFixed(2), r.balance.toFixed(2)].join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = L.name.replace(/\W+/g, "_") + "_amortization.csv";
  a.click();
}
function emiCalculator() {
  openModal(`
    <h3>EMI Calculator</h3>
    <div class="fld-row">
      <label class="fld"><span>Principal</span><input id="cP" type="number" step="any" value="1000000"></label>
      <label class="fld"><span>Rate % p.a.</span><input id="cR" type="number" step="any" value="9"></label>
    </div>
    <label class="fld"><span>Tenure (months)</span><input id="cN" type="number" value="60"></label>
    <div class="panel" style="margin:0;background:var(--accent-soft);box-shadow:none" id="cOut"></div>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">Close</button></div>`);
  const upd = () => {
    const P = num(el("#cP").value), R = num(el("#cR").value) / 100, N = num(el("#cN").value);
    const emi = calcEMI(P, R, N);
    el("#cOut").innerHTML = `<b style="font-size:19px">${fmtMoney(emi)}</b> / month
      <div class="small muted">Total interest ${fmtMoney(emi * N - P)} · total payable ${fmtMoney(emi * N)}</div>`;
  };
  ["cP", "cR", "cN"].forEach(id => el("#" + id).addEventListener("input", upd));
  upd();
}
function attachDocToLoan(loanId) { addDocument({ linkedType: "loan", linkedId: loanId }); }

/* ================= GOALS ================= */
/* ================= LENDING & BORROWING ================= */
/* Standalone tracker of money lent to / borrowed from specific people.
   Intentionally NOT added to net worth / liabilities — it lives only on this page. */
let lendingPerson = null;   // active person id
let lendingYear = null;     // active year tab (per selected person)

/* ── paged lists: no table renders more than PAGE_SIZE rows at once ──
   pagedRows(key, rows, renderRow) returns {html, footer}; the footer holds a
   "Show more" button that raises this key's limit. Limits reset on navigate(). */
const PAGE_SIZE = 25;
let _pageLimits = {};
function pagedRows(key, rows, renderRow) {
  const limit = _pageLimits[key] || PAGE_SIZE;
  const shown = rows.slice(0, limit);
  const rest = rows.length - shown.length;
  return {
    html: shown.map(renderRow).join(""),
    footer: rest > 0
      ? `<div class="load-more"><button class="btn ghost" onclick="loadMore('${key}')">Show ${Math.min(rest, PAGE_SIZE)} more · ${rest} remaining</button></div>`
      : "",
  };
}
function loadMore(key) { _pageLimits[key] = (_pageLimits[key] || PAGE_SIZE) + PAGE_SIZE; render(); }

function ledgerById(id) { return DB.ledgers.find(L => L.id === id) || null; }
function activeLedger() {
  if (!DB.ledgers.length) return null;
  let L = ledgerById(lendingPerson);
  if (!L) { L = DB.ledgers[0]; lendingPerson = L.id; }
  return L;
}
const ledgerSigned = n => (n >= 0 ? "+" : "−") + fmtMoney(Math.abs(n));

PAGES.lending = () => {
  const head = pageHead("Lending & Borrowing",
    "Money you lend to / borrow from people · stays on this page, not added to net worth",
    `<button class="btn" onclick="addLedgerPerson()">+ Add Person</button>`);
  if (!DB.ledgers.length) {
    return head + `<div class="page-note">ⓘ&nbsp;<div>Add a person, then log each transaction: a <b>debit</b> is money you spent on their behalf (they owe you), a <b>credit</b> is a repayment. Mark a row <b>cancelled</b> to void it; record <b>cashback</b> as a flat amount or a % of the order — it reduces what they owe.</div></div>
      <div class="empty">No people yet — click <b>+ Add Person</b> to start a ledger.</div>`;
  }
  const L = activeLedger();
  const tabs = DB.ledgers.map(p => {
    const pt = ledgerTotals(p.transactions);
    const nzYears = pt.byYear.filter(y => Math.abs(y.net) > 0.5).length;
    return `<button class="ltab${p.id === L.id ? " active" : ""}" onclick="selectLedger('${p.id}')">
      ${esc(p.person)} <span class="small muted">${p.transactions.length} txns${nzYears ? ` · ${nzYears} active yr${nzYears > 1 ? "s" : ""}` : ""}</span></button>`;
  }).join("");
  const rowsByYear = {};
  L.transactions.forEach((tx, i) => {
    const y = ledgerYear(tx.date) || "—";
    (rowsByYear[y] = rowsByYear[y] || []).push([tx, i]);
  });
  const years = Object.keys(rowsByYear).sort((a, b) => (a === "—" ? 1 : b === "—" ? -1 : b - a));
  // one year at a time: a tab per year (with its own aggregation), never merged
  const activeYear = years.includes(lendingYear) ? lendingYear : years[0];
  const yearTabs = years.map(y => {
    const yt = ledgerTotals(rowsByYear[y].map(([tx]) => tx));
    const nz = Math.abs(yt.net) > 0.5;
    const scls = !nz ? " zero" : yt.net > 0 ? " pos" : " neg";
    return `<button class="ltab year${scls}${y === activeYear ? " active" : ""}" onclick="selectLedgerYear('${y}')">
      ${y} <span class="small muted">${nz ? ledgerSigned(yt.net) : "settled"} · ${yt.count}</span></button>`;
  }).join("");
  let yearView = "";
  if (activeYear !== undefined) {
    const list = rowsByYear[activeYear].slice().sort((a, b) => (String(a[0].date) < String(b[0].date) ? 1 : -1));
    const ytot = ledgerTotals(list.map(([tx]) => tx));
    const ysum = `<div class="ledger-kpis year">
      <div class="kpi"><div class="label">${activeYear} lent</div><div class="value">${fmtMoney(ytot.lent)}</div></div>
      <div class="kpi"><div class="label">${activeYear} repaid</div><div class="value">${fmtMoney(ytot.repaid)}</div></div>
      <div class="kpi"><div class="label">${activeYear} cashback</div><div class="value">${fmtMoney(ytot.cashback)}</div></div>
      <div class="kpi"><div class="label">${activeYear} net</div><div class="value">${ledgerSigned(ytot.net)}</div></div>
    </div>`;
    const paged = pagedRows(`lending:${L.id}:${activeYear}`, list, ([tx, i]) => {
      const cb = ledgerCashback(tx), net = ledgerNet(tx);
      return `<tr class="${tx.cancelled ? "row-cancelled" : ""}">
        <td>${fmtDate(tx.date)}</td>
        <td><span class="chip ${tx.type === "credit" ? "green" : "amber"}">${tx.type === "credit" ? "credit" : "debit"}</span></td>
        <td>${esc(tx.instrument || "—")}${tx.notes ? `<div class="small muted">${esc(tx.notes)}</div>` : ""}</td>
        <td class="num">${fmtMoney(num(tx.amount))}</td>
        <td class="num">${cb ? fmtMoney(cb) + (tx.cashbackMode === "percent" ? ` <span class="small muted">(${fmtNum(num(tx.cashback))}%)</span>` : "") : "–"}</td>
        <td class="num">${num(tx.extraCharges) ? fmtMoney(num(tx.extraCharges)) : "–"}</td>
        <td class="num">${tx.cancelled
          ? `<span class="chip gray">void</span>${num(tx.extraCharges) ? " " + ledgerSigned(net) + '<div class="small muted">fee</div>' : ""}`
          : ledgerSigned(net)}</td>
        <td class="row-actions">
          <button class="icon-btn" title="Edit" onclick="editLedgerTx('${L.id}',${i})">✎</button>
          <button class="icon-btn danger" title="Delete" onclick="delLedgerTx('${L.id}',${i})">✕</button>
        </td></tr>`;
    });
    yearView = `<div class="section-title" style="margin-top:6px">Balance by year — ${esc(L.person)}
        <span class="small muted" style="font-weight:400">· each year stands alone (carried forward manually), so they're not summed · non-zero years highlighted</span></div>
      <div class="ltabs years">${yearTabs}</div>
      ${ysum}
      <div class="table-wrap"><table>
        <thead><tr><th>Date</th><th>Type</th><th>Instrument / note</th><th class="num">Amount</th><th class="num">Cashback</th><th class="num">Extra</th><th class="num">Net</th><th></th></tr></thead>
        <tbody>${paged.html}</tbody></table></div>${paged.footer}`;
  }
  return head + `
    <div class="ltabs">${tabs}</div>
    <div class="page-note">ⓘ&nbsp;<div><b>debit</b> = you paid on their behalf (they owe you) · <b>credit</b> = a repayment · <b>cancelled</b> voids the row · cashback (flat or %) reduces what they owe. Balances are shown <b>per year</b> — not summed into one running total, since carry-forwards are entered by hand — and are standalone, not part of your net worth.</div></div>
    <div class="head-actions" style="margin:10px 0 4px">
      <button class="btn" onclick="addLedgerTx('${L.id}')">+ Add Transaction</button>
      <button class="btn ghost" onclick="renameLedgerPerson('${L.id}')">Rename</button>
      <button class="btn ghost danger" onclick="delLedgerPerson('${L.id}')">Delete person</button>
    </div>
    ${L.transactions.length ? yearView : '<div class="empty">No transactions yet for ' + esc(L.person) + '.</div>'}`;
};

function selectLedger(id) { lendingPerson = id; lendingYear = null; render(); }
function selectLedgerYear(y) { lendingYear = y; render(); }
function addLedgerPerson() {
  formModal("Add person", [{ name: "person", label: "Person's name", required: true }], v => {
    const L = { id: uid(), person: v.person, transactions: [] };
    DB.ledgers.push(L); lendingPerson = L.id; closeModal(); save(); render();
  });
}
function renameLedgerPerson(id) {
  const L = ledgerById(id); if (!L) return;
  formModal("Rename person", [{ name: "person", label: "Person's name", value: L.person, required: true }], v => {
    L.person = v.person; closeModal(); save(); render();
  });
}
function delLedgerPerson(id) {
  const L = ledgerById(id); if (!L) return;
  confirmModal(`Delete "${L.person}" and all ${L.transactions.length} transaction(s)? This can't be undone.`, () => {
    DB.ledgers = DB.ledgers.filter(x => x.id !== id);
    if (lendingPerson === id) lendingPerson = DB.ledgers[0] ? DB.ledgers[0].id : null;
    save(); render();
  });
}
function ledgerTxFields(tx) {
  tx = tx || {};
  return [
    { type: "row", fields: [
      { name: "date", label: "Date", type: "date", value: tx.date || new Date().toISOString().slice(0, 10), required: true },
      { name: "type", label: "Type", type: "select", value: tx.type || "debit",
        options: [["debit", "Debit — I paid for them"], ["credit", "Credit — they repaid"]] }]},
    { type: "row", fields: [
      { name: "instrument", label: "Instrument (card / account)", type: "combo", listKey: "ledgerInstruments", value: tx.instrument || "", allowEmpty: true },
      { name: "amount", label: "Amount", type: "number", step: "any", value: tx.amount ?? "", required: true }]},
    { type: "row", fields: [
      { name: "cashbackMode", label: "Cashback type", type: "select", value: tx.cashbackMode || "fixed",
        options: [["fixed", "Flat amount (₹)"], ["percent", "% of amount"]] },
      { name: "cashback", label: "Cashback value", type: "number", step: "any", value: tx.cashback ?? 0 }]},
    { type: "row", fields: [
      { name: "extraCharges", label: "Extra charges (₹)", type: "number", step: "any", value: tx.extraCharges ?? 0 },
      { name: "cancelled", label: "Cancelled (void this row)", type: "checkbox", value: !!tx.cancelled }]},
    { name: "notes", label: "Note (optional)", value: tx.notes || "" },
  ];
}
function ledgerTxVals(v) {
  return { date: v.date, type: v.type, instrument: v.instrument,
    amount: num(v.amount), cashbackMode: v.cashbackMode, cashback: num(v.cashback),
    extraCharges: num(v.extraCharges), cancelled: !!v.cancelled, notes: v.notes };
}
function addLedgerTx(id) {
  const L = ledgerById(id); if (!L) return;
  formModal(`Add transaction — ${esc(L.person)}`, ledgerTxFields(), v => {
    L.transactions.push({ id: uid(), ...ledgerTxVals(v) });
    addPred("ledgerInstruments", v.instrument);
    closeModal(); save(); render();
  }, { note: "Cashback (flat or %) is subtracted; extra charges are added. Cancelled rows count as zero." });
}
function editLedgerTx(id, i) {
  const L = ledgerById(id); if (!L) return;
  formModal(`Edit transaction — ${esc(L.person)}`, ledgerTxFields(L.transactions[i]), v => {
    Object.assign(L.transactions[i], ledgerTxVals(v));
    addPred("ledgerInstruments", v.instrument);
    closeModal(); save(); render();
  });
}
function delLedgerTx(id, i) {
  const L = ledgerById(id); if (!L) return;
  const tx = L.transactions[i];
  confirmModal(`Delete this ${esc(tx.type)} of ${fmtMoney(num(tx.amount))} on ${fmtDate(tx.date)}?`, () => {
    L.transactions.splice(i, 1); save(); render();
  });
}

PAGES.goals = () => {
  const open = DB.goals.map((g, i) => [g, i]).filter(([g]) => !g.fulfilled);
  const done = DB.goals.map((g, i) => [g, i]).filter(([g]) => g.fulfilled);
  const totOpen = open.reduce((s, [g]) => s + num(g.value), 0);
  const row = ([g, i]) => {
    const emi = num(g.emi) || (num(g.loanAmount) && num(g.tenureMonths) ? calcEMI(num(g.loanAmount), num(g.interest), num(g.tenureMonths)) : 0);
    return `<tr>
      <td><b>${esc(g.name)}</b><div class="small muted">${esc(g.type || "")}</div></td>
      <td class="num">
        ${g.currentMarketValue ? `<b>${fmtMoney(num(g.currentMarketValue))}</b><div class="small muted">market · goal: ${fmtMoney(num(g.value))}</div>` : fmtMoney(num(g.value))}
      </td>
      <td class="num">${num(g.downPayment) ? fmtMoney(num(g.downPayment)) : "—"}</td>
      <td class="num">${num(g.loanAmount) ? fmtMoney(num(g.loanAmount)) + `<div class="small muted">${fmtPct(num(g.interest))} · ${num(g.tenureMonths)}mo</div>` : "—"}</td>
      <td class="num">${emi ? fmtMoney(emi) : "—"}</td>
      <td>${fmtDate(g.estimatedDate)}
        ${g.lastPriceFetch ? `<div class="small muted">price as of ${fmtDate(g.lastPriceFetch)}</div>` : ""}</td>
      <td><span class="chip ${g.fulfilled ? "green" : "amber"}">${g.fulfilled ? "Fulfilled" : "Planned"}</span></td>
      <td style="white-space:nowrap">
        <button class="icon-btn" title="Toggle fulfilled" onclick="toggleGoal(${i})">${g.fulfilled ? "↩" : "✓"}</button>
        ${!g.fulfilled && num(g.loanAmount) ? `<button class="icon-btn" title="Convert to loan" onclick="goalToLoan(${i})">⇒</button>` : ""}
        <button class="icon-btn" title="Edit" onclick="editGoal(${i})">✎</button>
        <button class="icon-btn" title="Refresh market price via AI" id="gpBtn${i}" onclick="refreshGoalPrice(${i})">⟳</button>
        <button class="icon-btn danger" onclick="delGoal(${i})">✕</button></td></tr>`;
  };
  const tbl = (items, key) => {
    const paged = pagedRows(key, items, row);
    return `<div class="table-wrap"><table>
    <thead><tr><th>Goal</th><th class="num">Value</th><th class="num">Down Payment</th><th class="num">Loan</th><th class="num">EMI</th><th>Target</th><th>Status</th><th></th></tr></thead>
    <tbody>${paged.html}</tbody></table></div>${paged.footer}`;
  };
  return pageHead("Goals & Future Purchases", `Planned spending ahead: <b>${fmtMoney(totOpen)}</b>`,
    `<button class="btn" onclick="addGoal()">+ Add Goal</button>`) +
    `<div class="page-note">ⓘ&nbsp;<div>Plan future purchases with down-payment / loan / EMI. The <b>⟳</b> button fetches today's market price via AI (when a Gemini key is set); otherwise the value you entered is kept. <b>⇒</b> turns a goal into a live loan.</div></div>` +
    `<div class="panel"><h2>Planned</h2>${open.length ? tbl(open, "goals:open") : '<div class="empty">Nothing planned.</div>'}</div>` +
    (done.length ? `<div class="panel"><h2>Fulfilled</h2>${tbl(done, "goals:done")}</div>` : "");
};
function goalFields(g) {
  g = g || {};
  return [
    { name: "name", label: "Goal name", value: g.name || "", required: true },
    { type: "row", fields: [
      { name: "type", label: "Type", type: "combo", listKey: "goalTypes", value: g.type || "", allowEmpty: true },
      { name: "estimatedDate", label: "Estimated date", type: "date", value: (g.estimatedDate || "").slice(0, 10) }]},
    { type: "row", fields: [
      { name: "value", label: "Total value", type: "number", step: "any", value: g.value ?? "", required: true },
      { name: "downPayment", label: "Down payment", type: "number", step: "any", value: g.downPayment ?? 0 }]},
    { type: "row", fields: [
      { name: "loanAmount", label: "Loan amount (0 = no loan)", type: "number", step: "any", value: g.loanAmount ?? 0 },
      { name: "interest", label: "Loan rate % p.a.", type: "number", step: "any", value: g.interest != null ? (num(g.interest) * 100).toFixed(2) : 0 }]},
    { name: "tenureMonths", label: "Loan tenure (months)", type: "number", value: g.tenureMonths ?? 0 },
  ];
}
function addGoal() {
  formModal("Add goal", goalFields(), v => {
    DB.goals.push({ id: uid(), name: v.name, type: v.type, value: num(v.value), downPayment: num(v.downPayment),
      loanAmount: num(v.loanAmount), interest: num(v.interest) / 100, tenureMonths: num(v.tenureMonths),
      emi: 0, fulfilled: false, estimatedDate: v.estimatedDate });
    closeModal(); save(); render();
  }, { note: "EMI is auto-computed from loan amount, rate and tenure." });
}
function editGoal(i) {
  formModal("Edit goal", goalFields(DB.goals[i]), v => {
    Object.assign(DB.goals[i], { name: v.name, type: v.type, value: num(v.value), downPayment: num(v.downPayment),
      loanAmount: num(v.loanAmount), interest: num(v.interest) / 100, tenureMonths: num(v.tenureMonths), estimatedDate: v.estimatedDate });
    closeModal(); save(); render();
  });
}
function toggleGoal(i) { DB.goals[i].fulfilled = !DB.goals[i].fulfilled; save(); render(); }
function delGoal(i) { confirmModal(`Delete goal "${DB.goals[i].name}"?`, () => { DB.goals.splice(i, 1); save(); render(); }); }
async function refreshGoalPrice(i) {
  const g = DB.goals[i];
  const btn = el("#gpBtn" + i);
  if (btn) { btn.textContent = "…"; btn.disabled = true; }
  try {
    const r = await fetch("/api/gemini-status").then(x => x.json());
    if (!r.available) { toast("Gemini API key not set — cannot fetch price", true); return; }
    const res = await fetch("/api/fetch-goal-price", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: g.name, type: g.type || "" })
    });
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || "fetch failed");
    if (j.price === null || j.price === undefined) {
      toast(`Could not determine market price for "${g.name}" — using existing value`, true);
      return;
    }
    g.currentMarketValue = j.price;
    g.lastPriceFetch = new Date().toISOString().slice(0, 10);
    save(); render();
    toast(`Price updated to ${fmtMoney(j.price)} via ${j.model}`);
  } catch (e) {
    toast("Price fetch failed: " + e.message + " — using last value", true);
  } finally {
    if (btn) { btn.textContent = "⟳"; btn.disabled = false; }
  }
}
function goalToLoan(i) {
  const g = DB.goals[i];
  DB.loans.push({ id: uid(), name: g.name, principal: num(g.loanAmount), annualRate: num(g.interest),
    tenureMonths: num(g.tenureMonths), emi: null, startDate: new Date().toISOString().slice(0, 7) + "-01",
    status: "active", notes: "Created from goal", addedMidway: false });
  g.fulfilled = true;
  save(); navigate("loans"); toast("Loan created from goal");
}

/* ================= CARDS ================= */
let cardFilter = { owner: "", type: "", bank: "", status: "active", q: "" };
const NO_BANK = "No Bank / Other";   // category for cards where a bank doesn't apply (Priority Pass, food cards…)
let revealed = {};   // cardId -> bool (session only, never persisted)
const CARD_COLORS = ["linear-gradient(135deg,#243b80,#3a5fd0)","linear-gradient(135deg,#5b2580,#9347c9)",
  "linear-gradient(135deg,#0e6e52,#19b285)","linear-gradient(135deg,#8a3324,#d2693a)",
  "linear-gradient(135deg,#1c5d80,#2ba0c9)","linear-gradient(135deg,#6d1f45,#c14a7e)"];
function cardColor(c) { let h = 0; for (const ch of (c.bank || c.name || "")) h = (h * 31 + ch.charCodeAt(0)) % 997; return CARD_COLORS[h % CARD_COLORS.length]; }
function maskNum(n) {
  const digits = String(n || "").replace(/\s+/g, "");
  if (digits.length < 4) return "••••";
  return "•••• •••• •••• " + digits.slice(-4);
}
function groupDigits(n) { return String(n || "").replace(/\s+/g, "").replace(/(.{4})/g, "$1 ").trim(); }
PAGES.cards = () => {
  const owners = [...new Set(DB.cards.map(c => c.owner).filter(Boolean))];
  const types = [...new Set(DB.cards.map(c => c.type).filter(Boolean))];
  const banks = [...new Set(DB.cards.map(c => (c.bank || "").trim()).filter(Boolean))].sort();
  const hasNoBank = DB.cards.some(c => !(c.bank || "").trim());
  const list = DB.cards.map((c, i) => [c, i]).filter(([c]) => {
    if (cardFilter.owner && c.owner !== cardFilter.owner) return false;
    if (cardFilter.type && c.type !== cardFilter.type) return false;
    if (cardFilter.bank) {
      const b = (c.bank || "").trim();
      if (cardFilter.bank === "__none__" ? !!b : b !== cardFilter.bank) return false;
    }
    const st = c.status === "closed" ? "closed" : "active";
    if (cardFilter.status && st !== cardFilter.status) return false;
    if (cardFilter.q && !(c.name + " " + c.bank + " " + (c.variant || "")).toLowerCase().includes(cardFilter.q.toLowerCase())) return false;
    return true;
  });
  const activeN = DB.cards.filter(c => c.status !== "closed").length;
  const feesTotal = DB.cards.filter(c => c.status !== "closed").reduce((s, c) => s + num(c.fees), 0);
  const cardsPaged = pagedRows("cards", list, ([c, i]) => {
    const r = revealed[c.id];
    return `<div class="pay-card${c.status === "closed" ? " closed" : ""}" style="background:${cardColor(c)}">
      <div class="pc-top"><div>
        <div class="pc-name">${esc(c.name)}</div>
        <div class="small" style="opacity:.85">${esc((c.bank || "").trim() || NO_BANK)} · ${esc(c.type || "")}${c.variant ? " · " + esc(c.variant) : ""}${c.variantSubType ? " " + esc(c.variantSubType) : ""}</div>
      </div></div>
      <div class="pc-actions">
        <button class="icon-btn" title="${r ? "Hide" : "Reveal"} details" onclick="toggleReveal('${c.id}')">${r ? "🙈" : "👁"}</button>
        <button class="icon-btn" title="Copy number" onclick="copyCardNum(${i})">⧉</button>
        <button class="icon-btn" title="Edit" onclick="editCard(${i})">✎</button>
        <button class="icon-btn" title="${c.status === "closed" ? "Reactivate" : "Deactivate"}" onclick="toggleCardStatus(${i})">${c.status === "closed" ? "↺" : "⊘"}</button>
        <button class="icon-btn" title="Delete" onclick="delCard(${i})">✕</button>
      </div>
      <div>
        <div class="pc-num">${r ? esc(groupDigits(c.number)) : maskNum(c.number)}</div>
        <div class="pc-row">
          <span>EXP ${r ? fmtDate(c.expiry).replace(/\s\d+,/, "") : "••/••"}</span>
          <span>CVV ${r && c.cvv ? esc(c.cvv) : "•••"}</span>
          ${c.pin ? `<span>PIN ${r ? esc(c.pin) : "••••"}</span>` : ""}
          ${num(c.fees) ? `<span>Fee ${fmtMoney(num(c.fees))}</span>` : "<span>Free</span>"}
        </div>
        ${c.benefits ? `<div class="small mt" style="opacity:.85;margin-top:8px">🎁 ${esc(c.benefits)}</div>` : ""}
        ${c.lounge ? `<div class="small" style="opacity:.85">🛋 ${esc(c.lounge)}${c.loungeCriteria ? " — " + esc(c.loungeCriteria) : ""}</div>` : ""}
        <div class="small" style="opacity:.7;margin-top:4px">${esc(c.owner || "")}</div>
      </div></div>`;
  });
  return pageHead("Cards", `${activeN} active card(s) · annual fees ${fmtMoney(feesTotal)} · details stay in your local data file`,
    `<button class="btn" onclick="addCard()">+ Add Card</button>`) + `
  <div class="page-note">ⓘ&nbsp;<div>Card numbers, CVV and PIN are <b>masked by default</b> — click <b>👁</b> on a card to reveal. Everything stays in your local data file; nothing is sent anywhere. Filter by bank, owner, type or status below.</div></div>
  <div class="filter-bar">
    <select onchange="cardFilter.bank=this.value;render()">
      <option value="">All banks</option>${banks.map(b => `<option value="${esc(b)}"${cardFilter.bank === b ? " selected" : ""}>${esc(b)}</option>`).join("")}
      ${hasNoBank ? `<option value="__none__"${cardFilter.bank === "__none__" ? " selected" : ""}>${NO_BANK}</option>` : ""}</select>
    <select onchange="cardFilter.owner=this.value;render()">
      <option value="">All owners</option>${owners.map(o => `<option${cardFilter.owner === o ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>
    <select onchange="cardFilter.type=this.value;render()">
      <option value="">All types</option>${types.map(t => `<option${cardFilter.type === t ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>
    <select onchange="cardFilter.status=this.value;render()">
      <option value="active"${cardFilter.status === "active" ? " selected" : ""}>Active</option>
      <option value="closed"${cardFilter.status === "closed" ? " selected" : ""}>Closed</option>
      <option value=""${cardFilter.status === "" ? " selected" : ""}>All</option></select>
    <input placeholder="Search…" value="${esc(cardFilter.q)}" oninput="cardFilter.q=this.value;render()" style="max-width:180px">
  </div>
  <div class="grid card-grid">${cardsPaged.html || '<div class="empty">No cards match the filter.</div>'}</div>${cardsPaged.footer}`;
};
function toggleReveal(id) { revealed[id] = !revealed[id]; render(); }
async function copyCardNum(i) {
  try { await navigator.clipboard.writeText(String(DB.cards[i].number || "").replace(/\s+/g, "")); toast("Card number copied"); }
  catch (e) { toast("Copy failed — reveal and copy manually", true); }
}
function cardFields(c) {
  c = c || {};
  return [
    { name: "name", label: "Card name (e.g. HDFC Regalia Gold)", value: c.name || "", required: true },
    { type: "row", fields: [
      { name: "bank", label: "Bank (leave empty if not applicable)", type: "combo", listKey: "banks", value: c.bank || "", allowEmpty: true },
      { name: "owner", label: "Owner", type: "select", value: c.owner || "", options: ["", ...DB.settings.persons] }]},
    { type: "row", fields: [
      { name: "type", label: "Type", type: "combo", listKey: "cardTypes", value: c.type || "Credit" },
      { name: "variant", label: "Network", type: "combo", listKey: "networks", value: c.variant || "", allowEmpty: true }]},
    { name: "number", label: "Card number", value: c.number || "" },
    { type: "row", fields: [
      { name: "expiry", label: "Expiry", type: "month", value: (c.expiry || "").slice(0, 7) },
      { name: "cvv", label: "CVV", value: c.cvv || "" }]},
    { type: "row", fields: [
      { name: "pin", label: "PIN (optional)", value: c.pin || "" },
      { name: "fees", label: "Annual fee", type: "number", step: "any", value: c.fees ?? 0 }]},
    { name: "benefits", label: "Benefits / offers (e.g. BookMyShow 25% off…)", value: c.benefits || "" },
    { type: "row", fields: [
      { name: "lounge", label: "Lounge access", value: c.lounge || "" },
      { name: "loungeCriteria", label: "Lounge criteria", value: c.loungeCriteria || "" }]},
  ];
}
function addCard() {
  formModal("Add card", cardFields(), v => {
    DB.cards.push({ id: uid(), ...cardVals(v), status: "" });
    closeModal(); save(); render();
  }, { note: "Card details are saved only to your local data file — never inside the app code." });
}
function editCard(i) {
  formModal("Edit card", cardFields(DB.cards[i]), v => {
    Object.assign(DB.cards[i], cardVals(v)); closeModal(); save(); render();
  });
}
function cardVals(v) {
  return { name: v.name, bank: v.bank, owner: v.owner, type: v.type, variant: v.variant,
    number: v.number, expiry: v.expiry ? v.expiry + "-01" : "", cvv: v.cvv, pin: v.pin,
    fees: num(v.fees), benefits: v.benefits, lounge: v.lounge, loungeCriteria: v.loungeCriteria };
}
function toggleCardStatus(i) {
  DB.cards[i].status = DB.cards[i].status === "closed" ? "" : "closed";
  save(); render();
}
function delCard(i) {
  confirmModal(`Delete card "${DB.cards[i].name}" permanently? (Deactivating keeps it for records.)`,
    () => { DB.cards.splice(i, 1); save(); render(); });
}

/* ================= DOCUMENTS ================= */
let serverFiles = [];
PAGES.documents = () => {
  const linkedLabel = d => {
    if (d.linkedType === "loan") { const L = DB.loans.find(x => x.id === d.linkedId); return L ? "Loan: " + L.name : ""; }
    return "";
  };
  return pageHead("Documents & Links", "Store financial files (statements, policies, agreements) or links to them",
    `<button class="btn" onclick="addDocument()">+ Add Link / Note</button>`) + `
  <div class="panel">
    <div class="drop-zone" id="dropZone">📁 Click or drop files here to upload<br>
      <span class="small">Saved into your data folder (data/files/) — travels with your data</span></div>
    <input type="file" id="fileInput" multiple style="display:none">
    <div id="docList"></div>
  </div>`;
};
window.after_documents = async () => {
  const dz = el("#dropZone"), fi = el("#fileInput");
  if (dz) {
    dz.onclick = () => fi.click();
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("drag"); };
    dz.ondragleave = () => dz.classList.remove("drag");
    dz.ondrop = e => { e.preventDefault(); dz.classList.remove("drag"); uploadFiles(e.dataTransfer.files); };
    fi.onchange = () => uploadFiles(fi.files);
  }
  await refreshDocList();
};
async function refreshDocList() {
  try { serverFiles = await (await fetch("/api/files")).json(); } catch (e) { serverFiles = []; }
  const box = el("#docList");
  if (!box) return;
  const filesPaged = pagedRows("docs:files", serverFiles, f => {
    const meta = DB.documents.find(d => d.type === "file" && d.ref === f.name);
    return `<div class="doc-row"><div class="doc-ico">📄</div>
      <div class="doc-main"><a href="/files/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a>
      <div class="doc-sub">${(f.size / 1024).toFixed(1)} KB · ${fmtDate(f.modified)}${meta && meta.linkedId ? " · " + esc(docLinkedLabel(meta)) : ""}</div></div>
      <button class="icon-btn danger" title="Delete file" onclick="deleteServerFile('${esc(f.name)}')">✕</button></div>`;
  });
  const linksPaged = pagedRows("docs:links", DB.documents.filter(d => d.type === "link"), d => `
    <div class="doc-row"><div class="doc-ico">🔗</div>
      <div class="doc-main"><a href="${esc(d.ref)}" target="_blank" rel="noopener">${esc(d.title || d.ref)}</a>
      <div class="doc-sub">${esc(d.ref)}${d.linkedId ? " · " + esc(docLinkedLabel(d)) : ""}${d.notes ? " · " + esc(d.notes) : ""}</div></div>
      <button class="icon-btn danger" onclick="delDocument('${d.id}')">✕</button></div>`);
  const combined = filesPaged.html + filesPaged.footer + linksPaged.html + linksPaged.footer;
  box.innerHTML = combined || '<div class="empty">No documents yet.</div>';
}
function docLinkedLabel(d) {
  if (d.linkedType === "loan") { const L = DB.loans.find(x => x.id === d.linkedId); return L ? "Loan: " + L.name : ""; }
  return "";
}
async function uploadFiles(files) {
  for (const f of files) {
    try {
      const r = await fetch("/api/upload?name=" + encodeURIComponent(f.name), { method: "POST", body: f });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "upload failed");
      DB.documents.push({ id: uid(), type: "file", title: j.name, ref: j.name, addedOn: new Date().toISOString() });
      toast("Uploaded " + j.name);
    } catch (e) { toast("Upload failed: " + e.message, true); }
  }
  save();
  await refreshDocList();
}
async function deleteServerFile(name) {
  confirmModal(`Delete file "${name}" from your data folder?`, async () => {
    await fetch("/api/files/" + encodeURIComponent(name), { method: "DELETE" });
    DB.documents = DB.documents.filter(d => !(d.type === "file" && d.ref === name));
    save(); await refreshDocList(); render();
  });
}
function addDocument(preset) {
  preset = preset || {};
  const loanOpts = [["", "— none —"], ...DB.loans.map(L => [L.id, "Loan: " + L.name])];
  formModal("Add link", [
    { name: "title", label: "Title", required: true },
    { name: "ref", label: "URL or path (https://… or a note about where it is)", required: true },
    { name: "linkedId", label: "Attach to", type: "select", value: preset.linkedId || "", options: loanOpts },
    { name: "notes", label: "Notes (optional)" },
  ], v => {
    DB.documents.push({ id: uid(), type: "link", title: v.title, ref: v.ref,
      linkedType: v.linkedId ? "loan" : "", linkedId: v.linkedId, notes: v.notes, addedOn: new Date().toISOString() });
    closeModal(); save(); render();
    if (currentPage === "documents") refreshDocList();
  });
}
function delDocument(id) {
  DB.documents = DB.documents.filter(d => d.id !== id);
  save(); refreshDocList(); render();
}

/* ================= SETTINGS ================= */
PAGES.settings = () => {
  return pageHead("Settings", "App preferences, people, and your data") + `
  <div class="grid two-col">
    <div class="panel"><h2>General</h2>
      <label class="fld"><span>App name</span><input value="${esc(DB.settings.appName)}" onchange="DB.settings.appName=this.value;save();el('#brandName').textContent=this.value"></label>
      <div class="fld-row">
        <label class="fld"><span>Currency code</span><input value="${esc(DB.settings.currency)}" onchange="DB.settings.currency=this.value.toUpperCase();save();render()"></label>
        <label class="fld"><span>Locale</span><input value="${esc(DB.settings.locale)}" onchange="DB.settings.locale=this.value;save();render()"></label>
      </div>
      <p class="muted small">Examples: INR / en-IN, USD / en-US, EUR / de-DE. Formatting updates everywhere instantly.</p>
    </div>
    <div class="panel"><h2>Family Members ${info("Date of birth powers age-based maturity (e.g. PPF/NPS maturing at 60). Optional.")}</h2>
      ${DB.settings.persons.length ? `<div class="table-wrap mb"><table>
        <thead><tr><th>Name</th><th>Date of birth</th><th class="num">Age</th><th style="width:40px"></th></tr></thead>
        <tbody>${DB.settings.persons.map((p, i) => {
          const dob = personDob(p), by = personBirthYear(p);
          const age = by ? new Date().getFullYear() - by : null;
          return `<tr><td><b>${esc(p)}</b></td>
            <td>${dobPickerHTML(i)}</td>
            <td class="num muted">${age != null ? age + " yrs" : "—"}</td>
            <td><button class="icon-btn danger" onclick="delPerson(${i})">✕</button></td></tr>`;
        }).join("")}</tbody></table></div>` : '<div class="mb"><span class="muted">None yet</span></div>'}
      <button class="btn secondary small" onclick="addPerson()">+ Add person</button>
      <p class="muted small mt">People appear as owners for portfolio, cards and investments. Add a date of birth to enable age-based maturities.</p>
    </div>
    <div class="panel"><h2>Backup & Restore <button class="btn small" onclick="backupNow()">🗄 Backup now</button></h2>
      <p class="muted small mb">All data lives in <b>data/finances.json</b> (plus uploaded files in data/files/).
      <b>Automatic:</b> the first time you save anything each day, a snapshot is stored in data/backups/ (last 14 daily backups kept).
      <b>Manual:</b> click "Backup now" anytime — manual backups are never auto-deleted. Restore replaces current data (a safety backup is taken first).</p>
      <div id="bakList" class="mb"><div class="empty">Loading backups…</div></div>
      <button class="btn secondary" onclick="exportData()">⬇ Export data (JSON)</button>
      <button class="btn ghost" onclick="el('#importFile').click()">⬆ Import data (replace)</button>
      <input type="file" id="importFile" accept=".json" style="display:none" onchange="importData(this.files[0])">
    </div>
    <div class="panel"><h2>Danger Zone</h2>
      <p class="muted small mb">Wipes every record from the data file (a dated backup is kept in data/backups/).</p>
      <button class="btn danger" onclick="wipeData()">Erase all data</button>
    </div>
  </div>`;
};
window.after_settings = () => { refreshBackupList(); };
async function refreshBackupList() {
  const box = el("#bakList");
  if (!box) return;
  try {
    const items = await (await fetch("/api/backups")).json();
    const paged = pagedRows("backups", items, b => {
      const kind = b.name.includes("-manual-") ? "manual" : b.name.includes("-prerestore-") ? "pre-restore safety" : "auto (daily)";
      return `<div class="doc-row"><div class="doc-ico">🗄</div>
        <div class="doc-main"><a href="/backups/${encodeURIComponent(b.name)}" target="_blank">${esc(b.name)}</a>
        <div class="doc-sub">${(b.size / 1024).toFixed(1)} KB · ${fmtDateTime(b.modified)} · ${kind}</div></div>
        <button class="btn small secondary" onclick="restoreBackup('${esc(b.name)}')">Restore</button>
        <button class="icon-btn danger" title="Delete backup" onclick="deleteBackup('${esc(b.name)}')">✕</button></div>`;
    });
    box.innerHTML = items.length ? (paged.html + paged.footer) : '<div class="empty">No backups yet — one is created on your first save each day, or click "Backup now".</div>';
  } catch (e) { box.innerHTML = '<div class="empty">Could not load backups: ' + esc(e.message) + "</div>"; }
}
async function backupNow() {
  try {
    const r = await fetch("/api/backup", { method: "POST" });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.status);
    toast("Backup created: " + j.name);
    refreshBackupList();
  } catch (e) { toast("Backup failed: " + e.message, true); }
}
function restoreBackup(name) {
  confirmModal(`Restore "${name}"? Your current data will be replaced (a safety backup is taken first).`, async () => {
    try {
      const r = await fetch("/api/restore?name=" + encodeURIComponent(name), { method: "POST" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.status);
      await loadDB(); render(); toast("Restored from " + name);
    } catch (e) { toast("Restore failed: " + e.message, true); }
  }, "Restore");
}
function deleteBackup(name) {
  confirmModal(`Delete backup "${name}"?`, async () => {
    try {
      const r = await fetch("/api/backups/" + encodeURIComponent(name), { method: "DELETE" });
      if (!r.ok) throw new Error((await r.json()).error || r.status);
      refreshBackupList();
    } catch (e) { toast("Delete failed: " + e.message, true); }
  });
}
function addPerson() {
  formModal("Add family member", [{ name: "name", label: "Name", required: true }], v => {
    if (!DB.settings.persons.includes(v.name)) DB.settings.persons.push(v.name);
    closeModal(); save(); render();
  }, { note: "Set their date of birth from the Day / Month / Year dropdowns in the members table." });
}
function updPersonDob(name, dob) {
  DB.settings.personMeta = DB.settings.personMeta || {};
  DB.settings.personMeta[name] = Object.assign(DB.settings.personMeta[name] || {}, { dob: dob || undefined });
  save(); render();
}
function delPerson(i) {
  const name = DB.settings.persons[i];
  DB.settings.persons.splice(i, 1);
  if (DB.settings.personMeta) delete DB.settings.personMeta[name];
  save(); render();
}
function exportData() {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(DB, null, 2)], { type: "application/json" }));
  a.download = "finances-export-" + new Date().toISOString().slice(0, 10) + ".json";
  a.click();
  toast("Exported");
}
function importData(file) {
  if (!file) return;
  const rd = new FileReader();
  rd.onload = () => {
    try {
      const d = JSON.parse(rd.result);
      if (!d || typeof d !== "object" || !("settings" in d || "loans" in d || "expenses" in d))
        throw new Error("Doesn't look like a Family Finance export");
      DB = d; migrate(); save(true); render(); toast("Data imported");
    } catch (e) { toast("Import failed: " + e.message, true); }
  };
  rd.readAsText(file);
}
function wipeData() {
  confirmModal("Erase ALL data? A dated backup will remain in data/backups/.", () => {
    const name = DB.settings.appName;
    DB = { schemaVersion: 1, settings: { appName: name, currency: "INR", locale: "en-IN", persons: [], locations: [] },
      income: { persons: [] }, expenses: [], monthlyInvestments: [], portfolio: [], gold: [], loans: [], goals: [], cards: [], documents: [] };
    migrate(); save(true); render();
  });
}

/* ================= hide values toggle ================= */
function toggleHideValues() {
  hideValues = !hideValues;
  localStorage.setItem("ffa_hideValues", hideValues ? "1" : "0");
  syncHideValuesBtn();
  render();
}
function syncHideValuesBtn() {
  const btn = el("#toggleValues");
  if (!btn) return;
  btn.querySelector(".hv-label").textContent = hideValues ? "Show values" : "Hide values";
  btn.title = hideValues ? "Values hidden — click to reveal" : "Click to hide all values";
  btn.classList.toggle("hv-hidden", hideValues);
}
// keep hide-values in sync when the embedded /invest page (or another tab)
// toggles it — the key is shared, so mirror the change here live.
window.addEventListener("storage", e => {
  if (e.key !== "ffa_hideValues" && e.key !== null) return;
  const now = localStorage.getItem("ffa_hideValues") === "1";
  if (now === hideValues) return;
  hideValues = now;
  syncHideValuesBtn();
  render();
});

/* ================= init ================= */
els("#nav button").forEach(b => b.onclick = () =>
  b.dataset.href ? (window.location.href = b.dataset.href) : navigate(b.dataset.page));
loadDB().then(() => {
  el("#brandName").textContent = DB.settings.appName || "Family Finance";
  if (DB.settings.lastUpdated) el("#lastUpdated").textContent = "Updated " + fmtDateTime(DB.settings.lastUpdated);
  syncHideValuesBtn();
  render();
}).catch(e => {
  el("#main").innerHTML = `<div class="panel"><div class="empty">Could not load data: ${esc(e.message)}<br>Is the server running via <code>python server.py</code>?</div></div>`;
});
