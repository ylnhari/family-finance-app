/* Family Finance - app logic. No personal data lives in this file. */
"use strict";

let DB = null;            // the whole data document
let currentPage = "dashboard";
let saveTimer = null;

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
  for (const k of ["expenses","monthlyInvestments","portfolio","gold","loans","goals","cards","documents"])
    DB[k] = DB[k] || [];
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
const num = v => { const n = parseFloat(String(v).replace(/[,\s]/g, "")); return isFinite(n) ? n : 0; };

function fmtMoney(v, compact) {
  if (v === null || v === undefined || isNaN(v)) return "–";
  const cur = DB.settings.currency || "INR", loc = DB.settings.locale || "en-IN";
  try {
    return new Intl.NumberFormat(loc, {
      style: "currency", currency: cur, maximumFractionDigits: 0,
      ...(compact ? { notation: "compact", maximumFractionDigits: 2 } : {})
    }).format(v);
  } catch (e) { return cur + " " + Math.round(v).toLocaleString(); }
}
const fmtNum = v => isFinite(v) ? new Intl.NumberFormat(DB.settings.locale || "en-IN", { maximumFractionDigits: 1 }).format(v) : "–";
const fmtPct = v => isFinite(v) ? (v * 100).toFixed(1) + "%" : "–";
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
/* EMI for principal P, annual rate (e.g. 0.0915), n months */
function calcEMI(P, annualRate, n) {
  if (!P || !n) return 0;
  const r = annualRate / 12;
  if (!r) return P / n;
  const f = Math.pow(1 + r, n);
  return P * r * f / (f - 1);
}
/* outstanding principal given EMI, rate, remaining months (joining a loan midway) */
function outstandingFromEMI(emi, annualRate, remMonths) {
  const r = annualRate / 12;
  if (!r) return emi * remMonths;
  return emi * (1 - Math.pow(1 + r, -remMonths)) / r;
}
/* full amortization schedule. extraMonthly = optional prepayment each month */
function amortSchedule(P, annualRate, n, emiOverride, extraMonthly) {
  const r = annualRate / 12;
  const emi = emiOverride || calcEMI(P, annualRate, n);
  const rows = [];
  let bal = P, totInt = 0, m = 0;
  while (bal > 0.5 && m < 1200) {
    m++;
    const interest = bal * r;
    let principal = emi - interest + (extraMonthly || 0);
    if (principal <= 0) return { rows, emi, totalInterest: Infinity, months: Infinity, diverges: true };
    if (principal > bal) principal = bal;
    bal -= principal;
    totInt += interest;
    rows.push({ m, emi: principal + interest, interest, principal, balance: bal });
  }
  return { rows, emi, totalInterest: totInt, months: m, diverges: false };
}
/* months elapsed since a date */
function monthsSince(dateStr) {
  if (!dateStr) return 0;
  const d = new Date(dateStr), now = new Date();
  if (isNaN(d)) return 0;
  return Math.max(0, (now.getFullYear() - d.getFullYear()) * 12 + now.getMonth() - d.getMonth());
}
/* derived live state of a loan object */
function loanState(L) {
  const rate = num(L.annualRate);
  const emi = L.emi ? num(L.emi) : calcEMI(num(L.principal), rate, num(L.tenureMonths));
  const sched = amortSchedule(num(L.principal), rate, num(L.tenureMonths), emi || null);
  const paidMonths = Math.min(monthsSince(L.startDate), sched.rows.length);
  const cur = paidMonths > 0 ? sched.rows[paidMonths - 1].balance : num(L.principal);
  const paidInterest = sched.rows.slice(0, paidMonths).reduce((s, x) => s + x.interest, 0);
  const paidPrincipal = num(L.principal) - cur;
  return { emi, sched, paidMonths, remMonths: Math.max(0, sched.rows.length - paidMonths),
           balance: cur, paidInterest, paidPrincipal,
           totalPayable: num(L.principal) + sched.totalInterest, totalInterest: sched.totalInterest };
}

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
  el("#mf", ov).addEventListener("submit", e => {
    e.preventDefault();
    const vals = {};
    els("[name]", ov).forEach(i => {
      vals[i.name] = i.type === "checkbox" ? i.checked : i.value.trim();
    });
    onSubmit(vals);
  });
  if (opts && opts.onReady) opts.onReady(ov);
  return ov;
}
function fieldHTML(f) {
  const v = f.value !== undefined && f.value !== null ? esc(f.value) : "";
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
    ${f.step ? `step="${f.step}"` : ""} ${f.placeholder ? `placeholder="${esc(f.placeholder)}"` : ""} ${f.required ? "required" : ""}></label>`;
}
function confirmModal(msg, onYes) {
  const ov = openModal(`<h3>Confirm</h3><p>${esc(msg)}</p>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal()">Cancel</button>
      <button class="btn danger" id="cy">Delete</button>
    </div>`);
  el("#cy", ov).onclick = () => { closeModal(); onYes(); };
}

/* ================= router ================= */
const PAGES = {};
function navigate(page) {
  currentPage = page;
  els("#nav button").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  render();
}
function render() {
  el("#main").innerHTML = PAGES[currentPage] ? PAGES[currentPage]() : "<p>Unknown page</p>";
  const hook = window["after_" + currentPage];
  if (typeof hook === "function") hook();
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

/* ================= aggregations ================= */
function totalInHand() {
  return (DB.income.persons || []).reduce((s, p) =>
    s + (p.inHand || []).reduce((a, c) => a + num(c.amount), 0), 0);
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
  const assets = pt.cur + gold, net = assets - liab;
  const inHand = totalInHand(), exp = totalExpenses(), balance = inHand - exp;
  const investMonthly = DB.monthlyInvestments.reduce((s, i) => s + num(i.amount), 0);

  // asset allocation by subCategory
  const alloc = {};
  DB.portfolio.forEach(p => { const k = p.subCategory || "Other"; alloc[k] = (alloc[k] || 0) + num(p.currentValue); });
  if (gold) alloc["Physical Gold"] = (alloc["Physical Gold"] || 0) + gold;
  const allocItems = Object.entries(alloc).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }));

  // expense by group
  const grp = {};
  DB.expenses.forEach(e => { const k = e.group || "Other"; grp[k] = (grp[k] || 0) + num(e.amount); });
  const emiTotal = activeLoans().reduce((s, L) => s + loanState(L).emi, 0);
  if (emiTotal) grp["Loan EMIs"] = (grp["Loan EMIs"] || 0) + emiTotal;
  const grpItems = Object.entries(grp).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }));

  const goalsOpen = DB.goals.filter(g => !g.fulfilled);

  return pageHead(esc(DB.settings.appName || "Family Finance"),
      "Snapshot of your family's money — " + fmtDate(new Date().toISOString())) + `
  <div class="grid kpis">
    <div class="kpi"><div class="label">Net Worth</div><div class="value ${net >= 0 ? "pos" : "neg"}">${fmtMoney(net)}</div>
      <div class="delta muted">${fmtMoney(assets)} assets − ${fmtMoney(liab)} debt</div></div>
    <div class="kpi"><div class="label">Monthly In-hand Income</div><div class="value">${fmtMoney(inHand)}</div></div>
    <div class="kpi"><div class="label">Monthly Outflow</div><div class="value">${fmtMoney(exp)}</div>
      <div class="delta muted">incl. EMIs & investments</div></div>
    <div class="kpi"><div class="label">Monthly Balance</div><div class="value ${balance >= 0 ? "pos" : "neg"}">${fmtMoney(balance)}</div>
      <div class="delta muted">${inHand ? fmtPct(balance / inHand) + " of in-hand" : ""}</div></div>
    <div class="kpi"><div class="label">Investing / Month</div><div class="value">${fmtMoney(investMonthly)}</div>
      <div class="delta muted">${inHand ? fmtPct(investMonthly / inHand) + " savings rate (vs in-hand)" : ""}</div></div>
  </div>
  <div class="grid two-col">
    <div class="panel"><h2>Asset Allocation</h2>${donutChart(allocItems)}</div>
    <div class="panel"><h2>Monthly Expenses by Group</h2>${barChart(grpItems)}</div>
    <div class="panel"><h2>Active Loans</h2>${
      activeLoans().length ? activeLoans().map(L => {
        const st = loanState(L);
        const pct = num(L.principal) ? st.paidPrincipal / num(L.principal) : 0;
        return `<div class="mb"><div style="display:flex;justify-content:space-between;font-size:13.5px">
          <b>${esc(L.name)}</b><span>${fmtMoney(st.balance)} left · EMI ${fmtMoney(st.emi)}</span></div>
          <div class="progress"><i style="width:${(pct * 100).toFixed(1)}%"></i></div>
          <div class="small muted">${st.paidMonths}/${st.sched.rows.length} months · ${fmtPct(pct)} principal repaid</div></div>`;
      }).join("") : '<div class="empty">No active loans 🎉</div>'
    }</div>
    <div class="panel"><h2>Upcoming Goals</h2>${
      goalsOpen.length ? `<div class="table-wrap"><table><thead><tr><th>Goal</th><th class="num">Value</th><th>Target</th></tr></thead><tbody>${
        goalsOpen.sort((a, b) => new Date(a.estimatedDate || "2999") - new Date(b.estimatedDate || "2999"))
          .map(g => `<tr><td>${esc(g.name)} <span class="chip gray">${esc(g.type || "")}</span></td>
            <td class="num">${fmtMoney(num(g.value))}</td><td>${fmtDate(g.estimatedDate)}</td></tr>`).join("")
      }</tbody></table></div>` : '<div class="empty">No open goals</div>'
    }</div>
  </div>
  <div class="panel"><h2>Investment Performance</h2>
    <div class="grid kpis" style="margin-bottom:0">
      <div class="kpi"><div class="label">Invested</div><div class="value">${fmtMoney(pt.inv)}</div></div>
      <div class="kpi"><div class="label">Current Value</div><div class="value">${fmtMoney(pt.cur)}</div></div>
      <div class="kpi"><div class="label">Unrealised Gain</div><div class="value ${pt.cur - pt.inv >= 0 ? "pos" : "neg"}">${fmtMoney(pt.cur - pt.inv)}</div>
        <div class="delta muted">${pt.inv ? fmtPct((pt.cur - pt.inv) / pt.inv) + " overall return" : ""}</div></div>
      <div class="kpi"><div class="label">Physical Gold</div><div class="value">${fmtMoney(gold)}</div>
        <div class="delta muted">${fmtNum(DB.gold.reduce((s, g) => s + num(g.grams), 0))} grams</div></div>
    </div></div>`;
};

/* ================= INCOME ================= */
const INCOME_SECTIONS = [["ctc","Monthly CTC"],["gross","Gross Income"],["deductions","Deductions"],["inHand","In-Hand Salary"]];
PAGES.income = () => {
  const persons = DB.income.persons || [];
  return pageHead("Income", "Salary structure per earning member — click any amount to edit",
    `<button class="btn" onclick="addEarner()">+ Add Earner</button>`) +
    (persons.length ? persons.map((p, pi) => {
      const sections = INCOME_SECTIONS.map(([key, label]) => {
        const rows = (p[key] || []).map((c, ci) => `
          <tr><td><input class="inline-input wide" value="${esc(c.component)}" onchange="updIncome(${pi},'${key}',${ci},'component',this.value)"></td>
          <td class="num"><input class="inline-input" value="${num(c.amount)}" onchange="updIncome(${pi},'${key}',${ci},'amount',this.value)"></td>
          <td style="width:30px"><button class="icon-btn danger" title="Remove" onclick="delIncome(${pi},'${key}',${ci})">✕</button></td></tr>`).join("");
        const sub = (p[key] || []).reduce((s, c) => s + num(c.amount), 0);
        return `<div class="panel"><h2>${label} <button class="btn small secondary" onclick="addIncomeRow(${pi},'${key}')">+ Row</button></h2>
          <div class="table-wrap"><table><thead><tr><th>Component</th><th class="num">Amount</th><th></th></tr></thead>
          <tbody>${rows}<tr class="subtotal"><td>Subtotal</td><td class="num">${fmtMoney(sub)}</td><td></td></tr></tbody></table></div></div>`;
      }).join("");
      const ctc = (p.ctc || []).reduce((s, c) => s + num(c.amount), 0);
      const gross = (p.gross || []).reduce((s, c) => s + num(c.amount), 0);
      const ded = (p.deductions || []).reduce((s, c) => s + num(c.amount), 0);
      const ih = (p.inHand || []).reduce((s, c) => s + num(c.amount), 0);
      return `<div class="panel" style="background:#10182c;color:#fff">
        <h2 style="color:#fff">${esc(p.name)}
          <span><button class="btn small secondary" onclick="renameEarner(${pi})">Rename</button>
          <button class="btn small danger" onclick="delEarner(${pi})">Delete</button></span></h2>
        <div class="grid kpis" style="margin-bottom:0">
          <div class="kpi"><div class="label">Monthly CTC</div><div class="value">${fmtMoney(ctc)}</div></div>
          <div class="kpi"><div class="label">Gross</div><div class="value">${fmtMoney(gross)}</div></div>
          <div class="kpi"><div class="label">Deductions</div><div class="value neg">${fmtMoney(ded)}</div></div>
          <div class="kpi"><div class="label">In-Hand</div><div class="value pos">${fmtMoney(ih)}</div>
            <div class="delta muted">gross − deductions = ${fmtMoney(gross - ded)}</div></div>
        </div></div>
        <div class="grid two-col">${sections}</div>`;
    }).join("") : '<div class="panel"><div class="empty">No earners yet — add one to start.</div></div>');
};
function addEarner() {
  formModal("Add earning member", [{ name: "name", label: "Name", required: true }], v => {
    DB.income.persons.push({ name: v.name, ctc: [], gross: [], deductions: [], inHand: [] });
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
  confirmModal(`Delete ${DB.income.persons[pi].name} and all their income rows?`, () => {
    DB.income.persons.splice(pi, 1); save(); render();
  });
}
function addIncomeRow(pi, key) {
  DB.income.persons[pi][key] = DB.income.persons[pi][key] || [];
  DB.income.persons[pi][key].push({ component: "New component", amount: 0 });
  save(); render();
}
function updIncome(pi, key, ci, field, val) {
  DB.income.persons[pi][key][ci][field] = field === "amount" ? num(val) : val;
  save(); render();
}
function delIncome(pi, key, ci) { DB.income.persons[pi][key].splice(ci, 1); save(); render(); }

/* ================= EXPENSES ================= */
PAGES.expenses = () => {
  const groups = {};
  DB.expenses.forEach((e, i) => {
    const g = e.group || "Other";
    (groups[g] = groups[g] || []).push([e, i]);
  });
  const total = pureExpenses();
  const emiTotal = activeLoans().reduce((s, L) => s + loanState(L).emi, 0);
  const invTotal = DB.monthlyInvestments.filter(i => (i.deductedFrom || "IN HAND") === "IN HAND").reduce((s, i) => s + num(i.amount), 0);
  return pageHead("Monthly Expenses",
    `Recurring spend: <b>${fmtMoney(total)}</b> &nbsp;·&nbsp; + loan EMIs ${fmtMoney(emiTotal)} &nbsp;·&nbsp; + in-hand investments ${fmtMoney(invTotal)} &nbsp;=&nbsp; <b>${fmtMoney(total + emiTotal + invTotal)}</b> total outflow`,
    `<button class="btn" onclick="addExpense()">+ Add Expense</button>`) +
    Object.entries(groups).map(([g, items]) => {
      const sub = items.reduce((s, [e]) => s + num(e.amount), 0);
      return `<div class="panel"><h2>${esc(g)} <span class="chip">${fmtMoney(sub)}</span></h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Category</th><th>Location / Person</th><th class="num">Monthly Cost</th><th style="width:70px"></th></tr></thead>
        <tbody>${items.map(([e, i]) => `
          <tr><td>${esc(e.category)}</td><td class="muted">${esc(e.location || e.person || "—")}</td>
          <td class="num"><input class="inline-input" value="${num(e.amount)}" onchange="updExpense(${i},this.value)"></td>
          <td><button class="icon-btn" title="Edit" onclick="editExpense(${i})">✎</button>
              <button class="icon-btn danger" title="Delete" onclick="delExpense(${i})">✕</button></td></tr>`).join("")}
        </tbody></table></div></div>`;
    }).join("") || '<div class="panel"><div class="empty">No expenses yet.</div></div>';
};
function expenseFields(e) {
  e = e || {};
  const groups = [...new Set(DB.expenses.map(x => x.group).filter(Boolean))];
  return [
    { name: "group", label: "Group (e.g. Housing, Food, Insurance…)", value: e.group || "", placeholder: groups.join(", ") || "Housing", required: true },
    { name: "category", label: "Category", value: e.category || "", required: true },
    { type: "row", fields: [
      { name: "location", label: "Location (optional)", value: e.location || "" },
      { name: "amount", label: "Monthly cost", type: "number", step: "any", value: e.amount ?? "", required: true }]},
    { name: "notes", label: "Notes (optional)", value: e.notes || "" },
  ];
}
function addExpense() {
  formModal("Add expense", expenseFields(), v => {
    DB.expenses.push({ id: uid(), group: v.group, category: v.category, location: v.location, amount: num(v.amount), notes: v.notes });
    closeModal(); save(); render();
  });
}
function editExpense(i) {
  formModal("Edit expense", expenseFields(DB.expenses[i]), v => {
    Object.assign(DB.expenses[i], { group: v.group, category: v.category, location: v.location, amount: num(v.amount), notes: v.notes });
    closeModal(); save(); render();
  });
}
function updExpense(i, val) { DB.expenses[i].amount = num(val); save(); render(); }
function delExpense(i) {
  confirmModal(`Delete "${DB.expenses[i].category}"?`, () => { DB.expenses.splice(i, 1); save(); render(); });
}

/* ================= MONTHLY INVESTMENTS ================= */
PAGES.investments = () => {
  const total = DB.monthlyInvestments.reduce((s, i) => s + num(i.amount), 0);
  const byPerson = {};
  DB.monthlyInvestments.forEach(i => { const p = i.person || "—"; byPerson[p] = (byPerson[p] || 0) + num(i.amount); });
  return pageHead("Monthly Investments / Savings",
    `Total committed per month: <b>${fmtMoney(total)}</b>`,
    `<button class="btn" onclick="addInvestment()">+ Add</button>`) + `
  <div class="grid kpis">${Object.entries(byPerson).map(([p, v]) =>
    `<div class="kpi"><div class="label">${esc(p)}</div><div class="value">${fmtMoney(v)}</div></div>`).join("")}</div>
  <div class="panel"><div class="table-wrap"><table>
    <thead><tr><th>Instrument</th><th>Person</th><th>Deducted From</th><th class="num">Monthly</th><th style="width:70px"></th></tr></thead>
    <tbody>${DB.monthlyInvestments.map((iv, i) => `
      <tr><td>${esc(iv.category)}</td><td>${esc(iv.person || "—")}</td>
      <td><span class="chip ${iv.deductedFrom === "CTC" ? "amber" : iv.deductedFrom === "GROSS" ? "gray" : "green"}">${esc(iv.deductedFrom || "IN HAND")}</span></td>
      <td class="num"><input class="inline-input" value="${num(iv.amount)}" onchange="updInvestment(${i},this.value)"></td>
      <td><button class="icon-btn" onclick="editInvestment(${i})">✎</button>
          <button class="icon-btn danger" onclick="delInvestment(${i})">✕</button></td></tr>`).join("")}
      <tr class="subtotal"><td colspan="3">Total</td><td class="num">${fmtMoney(total)}</td><td></td></tr>
    </tbody></table></div>
    <p class="muted small mt">"Deducted From" shows where the money comes out: IN HAND reduces your spendable salary; CTC / GROSS are deducted before in-hand (e.g. PF, employer NPS).</p>
  </div>`;
};
function investmentFields(iv) {
  iv = iv || {};
  return [
    { name: "category", label: "Instrument (e.g. NPS, PF, MF SIP, Gold Scheme…)", value: iv.category || "", required: true },
    { type: "row", fields: [
      { name: "person", label: "Person", type: "select", value: iv.person || "", options: ["", ...DB.settings.persons] },
      { name: "deductedFrom", label: "Deducted from", type: "select", value: iv.deductedFrom || "IN HAND", options: ["IN HAND", "GROSS", "CTC"] }]},
    { name: "amount", label: "Monthly amount", type: "number", step: "any", value: iv.amount ?? "", required: true },
  ];
}
function addInvestment() {
  formModal("Add monthly investment", investmentFields(), v => {
    DB.monthlyInvestments.push({ id: uid(), category: v.category, person: v.person, deductedFrom: v.deductedFrom, amount: num(v.amount) });
    closeModal(); save(); render();
  });
}
function editInvestment(i) {
  formModal("Edit monthly investment", investmentFields(DB.monthlyInvestments[i]), v => {
    Object.assign(DB.monthlyInvestments[i], { category: v.category, person: v.person, deductedFrom: v.deductedFrom, amount: num(v.amount) });
    closeModal(); save(); render();
  });
}
function updInvestment(i, val) { DB.monthlyInvestments[i].amount = num(val); save(); render(); }
function delInvestment(i) {
  confirmModal(`Delete "${DB.monthlyInvestments[i].category}"?`, () => { DB.monthlyInvestments.splice(i, 1); save(); render(); });
}

/* ================= PORTFOLIO ================= */
PAGES.portfolio = () => {
  const owners = [...new Set(DB.portfolio.map(p => p.owner || "Family"))];
  const pt = portfolioTotals(), gold = goldTotal();
  return pageHead("Portfolio",
    `Current <b>${fmtMoney(pt.cur + gold)}</b> (incl. gold) on invested ${fmtMoney(pt.inv)}`,
    `<button class="btn" onclick="addHolding()">+ Add Holding</button>
     <button class="btn secondary" onclick="addGold()">+ Add Gold</button>`) +
  owners.map(o => {
    const items = DB.portfolio.map((p, i) => [p, i]).filter(([p]) => (p.owner || "Family") === o);
    const cur = items.reduce((s, [p]) => s + num(p.currentValue), 0);
    const inv = items.reduce((s, [p]) => s + num(p.invested), 0);
    return `<div class="panel"><h2>${esc(o)}'s Holdings
      <span class="chip ${cur >= inv ? "green" : "red"}">${fmtMoney(cur)} · ${inv ? fmtPct((cur - inv) / inv) : "–"}</span></h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Asset</th><th>Class</th><th class="num">Invested</th><th class="num">Current</th><th class="num">Return</th><th class="num">Maturity (mo)</th><th style="width:70px"></th></tr></thead>
      <tbody>${items.map(([p, i]) => {
        const roi = num(p.invested) ? (num(p.currentValue) - num(p.invested)) / num(p.invested) : 0;
        return `<tr><td><b>${esc(p.category)}</b>${p.notes ? `<div class="small muted">${esc(p.notes)}</div>` : ""}</td>
          <td><span class="chip gray">${esc(p.subCategory || "—")}</span></td>
          <td class="num"><input class="inline-input" value="${num(p.invested)}" onchange="updHolding(${i},'invested',this.value)"></td>
          <td class="num"><input class="inline-input" value="${num(p.currentValue)}" onchange="updHolding(${i},'currentValue',this.value)"></td>
          <td class="num ${roi >= 0 ? "pos" : "neg"}">${fmtPct(roi)}</td>
          <td class="num">${num(p.maturityMonths) || "—"}</td>
          <td><button class="icon-btn" onclick="editHolding(${i})">✎</button>
              <button class="icon-btn danger" onclick="delHolding(${i})">✕</button></td></tr>`;
      }).join("")}
      <tr class="subtotal"><td colspan="2">Subtotal</td><td class="num">${fmtMoney(inv)}</td><td class="num">${fmtMoney(cur)}</td>
        <td class="num">${inv ? fmtPct((cur - inv) / inv) : "–"}</td><td></td><td></td></tr>
      </tbody></table></div></div>`;
  }).join("") + `
  <div class="panel"><h2>Physical Gold <span class="chip amber">${fmtMoney(gold)}</span></h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Person</th><th class="num">Grams</th><th class="num">Per-gram Value</th><th class="num">Total</th><th style="width:70px"></th></tr></thead>
      <tbody>${DB.gold.map((g, i) => `
        <tr><td>${esc(g.person)}</td>
        <td class="num"><input class="inline-input" value="${num(g.grams)}" onchange="updGold(${i},'grams',this.value)"></td>
        <td class="num"><input class="inline-input" value="${num(g.perGramValue)}" onchange="updGold(${i},'perGramValue',this.value)"></td>
        <td class="num">${fmtMoney(num(g.grams) * num(g.perGramValue))}</td>
        <td><button class="icon-btn danger" onclick="delGold(${i})">✕</button></td></tr>`).join("")}
      <tr class="subtotal"><td>Total</td><td class="num">${fmtNum(DB.gold.reduce((s, g) => s + num(g.grams), 0))} g</td><td></td>
        <td class="num">${fmtMoney(gold)}</td><td></td></tr>
      </tbody></table></div>
    <p class="muted small mt">Tip: update the per-gram value periodically; totals recompute automatically.</p></div>`;
};
function holdingFields(p) {
  p = p || {};
  return [
    { name: "category", label: "Asset name (e.g. NPS, Stocks, Mutual Funds…)", value: p.category || "", required: true },
    { type: "row", fields: [
      { name: "subCategory", label: "Asset class", type: "select", value: p.subCategory || "Equity",
        options: ["Equity", "Debt", "Fixed", "Real estate", "Liquidity", "Gold", "Other"] },
      { name: "owner", label: "Owner", type: "select", value: p.owner || "", options: ["", ...DB.settings.persons, "Family"] }]},
    { type: "row", fields: [
      { name: "invested", label: "Amount invested", type: "number", step: "any", value: p.invested ?? "", required: true },
      { name: "currentValue", label: "Current value", type: "number", step: "any", value: p.currentValue ?? "", required: true }]},
    { name: "maturityMonths", label: "Maturity / lock-in (months, 0 = none)", type: "number", value: p.maturityMonths ?? 0 },
    { name: "notes", label: "Notes (optional)", value: p.notes || "" },
  ];
}
function addHolding() {
  formModal("Add holding", holdingFields(), v => {
    DB.portfolio.push({ id: uid(), category: v.category, subCategory: v.subCategory, owner: v.owner,
      invested: num(v.invested), currentValue: num(v.currentValue), maturityMonths: num(v.maturityMonths), notes: v.notes });
    closeModal(); save(); render();
  });
}
function editHolding(i) {
  formModal("Edit holding", holdingFields(DB.portfolio[i]), v => {
    Object.assign(DB.portfolio[i], { category: v.category, subCategory: v.subCategory, owner: v.owner,
      invested: num(v.invested), currentValue: num(v.currentValue), maturityMonths: num(v.maturityMonths), notes: v.notes });
    closeModal(); save(); render();
  });
}
function updHolding(i, f, val) { DB.portfolio[i][f] = num(val); save(); render(); }
function delHolding(i) {
  confirmModal(`Delete "${DB.portfolio[i].category}" (${DB.portfolio[i].owner || "Family"})?`, () => { DB.portfolio.splice(i, 1); save(); render(); });
}
function addGold() {
  formModal("Add physical gold", [
    { name: "person", label: "Person", required: true },
    { type: "row", fields: [
      { name: "grams", label: "Grams", type: "number", step: "any", required: true },
      { name: "perGramValue", label: "Per-gram value", type: "number", step: "any", required: true }]},
  ], v => {
    DB.gold.push({ id: uid(), person: v.person, grams: num(v.grams), perGramValue: num(v.perGramValue) });
    closeModal(); save(); render();
  });
}
function updGold(i, f, val) { DB.gold[i][f] = num(val); save(); render(); }
function delGold(i) { confirmModal(`Remove ${DB.gold[i].person}'s gold entry?`, () => { DB.gold.splice(i, 1); save(); render(); }); }

/* ================= LOANS ================= */
PAGES.loans = () => {
  const act = DB.loans.map((L, i) => [L, i]).filter(([L]) => L.status !== "closed");
  const closed = DB.loans.map((L, i) => [L, i]).filter(([L]) => L.status === "closed");
  const totBal = act.reduce((s, [L]) => s + loanState(L).balance, 0);
  const totEMI = act.reduce((s, [L]) => s + loanState(L).emi, 0);
  return pageHead("Loans & Debts",
    `Outstanding <b>${fmtMoney(totBal)}</b> across ${act.length} active loan(s) · ${fmtMoney(totEMI)}/month in EMIs`,
    `<button class="btn" onclick="addLoanFull()">+ New Loan</button>
     <button class="btn secondary" onclick="addLoanMidway()">+ Add Existing Loan (midway)</button>
     <button class="btn ghost" onclick="emiCalculator()">EMI Calculator</button>`) +
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
      <div><span>Months Left</span><b>${st.remMonths} / ${st.sched.rows.length}</b></div>
    </div>
    <div class="progress"><i style="width:${(pct * 100).toFixed(1)}%"></i></div>
    <div class="small muted mt" style="margin-top:6px">${fmtPct(pct)} of principal repaid · interest paid so far ${fmtMoney(st.paidInterest)}</div>
    <div class="mt" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      ${docs.map(d => `<a class="chip" href="${d.type === "file" ? "/files/" + encodeURIComponent(d.ref) : esc(d.ref)}" target="_blank" title="${esc(d.title)}">📎 ${esc(d.title)}</a>`).join("")}
      <button class="btn small ghost" onclick="attachDocToLoan('${L.id}')">+ Attach file / link</button>
    </div></div>`;
}
function addLoanFull() {
  formModal("New loan (from the beginning)", [
    { name: "name", label: "Loan name", required: true },
    { type: "row", fields: [
      { name: "lender", label: "Lender / bank (optional)" },
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
    { name: "lender", label: "Lender / bank (optional)" },
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
      { name: "lender", label: "Lender", value: L.lender || "" },
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
function showAmort(i, extraMonthly) {
  const L = DB.loans[i];
  extraMonthly = extraMonthly || 0;
  const st = loanState(L);
  const base = amortSchedule(num(L.principal), num(L.annualRate), num(L.tenureMonths), st.emi || null);
  const what = extraMonthly ? amortSchedule(num(L.principal), num(L.annualRate), num(L.tenureMonths), st.emi || null, extraMonthly) : base;
  const saved = base.totalInterest - what.totalInterest;
  const rows = what.rows.map(r => `
    <tr${r.m === st.paidMonths ? ' style="border-bottom:2px solid var(--accent)"' : ""}>
      <td class="num">${r.m}${r.m <= st.paidMonths ? ' <span class="chip green" style="font-size:10px">paid</span>' : ""}</td>
      <td class="num">${fmtMoney(r.emi)}</td><td class="num">${fmtMoney(r.principal)}</td>
      <td class="num">${fmtMoney(r.interest)}</td><td class="num">${fmtMoney(r.balance)}</td></tr>`).join("");
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
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal()">Close</button></div>`, true);
  el("#applyExtra").onclick = () => showAmort(i, num(el("#extraIn").value));
}
function exportAmortCSV(i, extraMonthly) {
  const L = DB.loans[i];
  const st = loanState(L);
  const s = amortSchedule(num(L.principal), num(L.annualRate), num(L.tenureMonths), st.emi || null, extraMonthly || 0);
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
PAGES.goals = () => {
  const open = DB.goals.map((g, i) => [g, i]).filter(([g]) => !g.fulfilled);
  const done = DB.goals.map((g, i) => [g, i]).filter(([g]) => g.fulfilled);
  const totOpen = open.reduce((s, [g]) => s + num(g.value), 0);
  const row = ([g, i]) => {
    const emi = num(g.emi) || (num(g.loanAmount) && num(g.tenureMonths) ? calcEMI(num(g.loanAmount), num(g.interest), num(g.tenureMonths)) : 0);
    return `<tr>
      <td><b>${esc(g.name)}</b><div class="small muted">${esc(g.type || "")}</div></td>
      <td class="num">${fmtMoney(num(g.value))}</td>
      <td class="num">${num(g.downPayment) ? fmtMoney(num(g.downPayment)) : "—"}</td>
      <td class="num">${num(g.loanAmount) ? fmtMoney(num(g.loanAmount)) + `<div class="small muted">${fmtPct(num(g.interest))} · ${num(g.tenureMonths)}mo</div>` : "—"}</td>
      <td class="num">${emi ? fmtMoney(emi) : "—"}</td>
      <td>${fmtDate(g.estimatedDate)}</td>
      <td><span class="chip ${g.fulfilled ? "green" : "amber"}">${g.fulfilled ? "Fulfilled" : "Planned"}</span></td>
      <td style="white-space:nowrap">
        <button class="icon-btn" title="Toggle fulfilled" onclick="toggleGoal(${i})">${g.fulfilled ? "↩" : "✓"}</button>
        ${!g.fulfilled && num(g.loanAmount) ? `<button class="icon-btn" title="Convert to loan" onclick="goalToLoan(${i})">⇒</button>` : ""}
        <button class="icon-btn" title="Edit" onclick="editGoal(${i})">✎</button>
        <button class="icon-btn danger" onclick="delGoal(${i})">✕</button></td></tr>`;
  };
  const tbl = items => `<div class="table-wrap"><table>
    <thead><tr><th>Goal</th><th class="num">Value</th><th class="num">Down Payment</th><th class="num">Loan</th><th class="num">EMI</th><th>Target</th><th>Status</th><th></th></tr></thead>
    <tbody>${items.map(row).join("")}</tbody></table></div>`;
  return pageHead("Goals & Future Purchases", `Planned spending ahead: <b>${fmtMoney(totOpen)}</b>`,
    `<button class="btn" onclick="addGoal()">+ Add Goal</button>`) +
    `<div class="panel"><h2>Planned</h2>${open.length ? tbl(open) : '<div class="empty">Nothing planned.</div>'}</div>` +
    (done.length ? `<div class="panel"><h2>Fulfilled</h2>${tbl(done)}</div>` : "");
};
function goalFields(g) {
  g = g || {};
  return [
    { name: "name", label: "Goal name", value: g.name || "", required: true },
    { type: "row", fields: [
      { name: "type", label: "Type (Car Loan, Family Function…)", value: g.type || "" },
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
function goalToLoan(i) {
  const g = DB.goals[i];
  DB.loans.push({ id: uid(), name: g.name, principal: num(g.loanAmount), annualRate: num(g.interest),
    tenureMonths: num(g.tenureMonths), emi: null, startDate: new Date().toISOString().slice(0, 7) + "-01",
    status: "active", notes: "Created from goal", addedMidway: false });
  g.fulfilled = true;
  save(); navigate("loans"); toast("Loan created from goal");
}

/* ================= CARDS ================= */
let cardFilter = { owner: "", type: "", status: "active", q: "" };
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
  const list = DB.cards.map((c, i) => [c, i]).filter(([c]) => {
    if (cardFilter.owner && c.owner !== cardFilter.owner) return false;
    if (cardFilter.type && c.type !== cardFilter.type) return false;
    const st = c.status === "closed" ? "closed" : "active";
    if (cardFilter.status && st !== cardFilter.status) return false;
    if (cardFilter.q && !(c.name + " " + c.bank + " " + (c.variant || "")).toLowerCase().includes(cardFilter.q.toLowerCase())) return false;
    return true;
  });
  const activeN = DB.cards.filter(c => c.status !== "closed").length;
  const feesTotal = DB.cards.filter(c => c.status !== "closed").reduce((s, c) => s + num(c.fees), 0);
  return pageHead("Cards", `${activeN} active card(s) · annual fees ${fmtMoney(feesTotal)} · details stay in your local data file`,
    `<button class="btn" onclick="addCard()">+ Add Card</button>`) + `
  <div class="filter-bar">
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
  <div class="grid card-grid">${list.map(([c, i]) => {
    const r = revealed[c.id];
    return `<div class="pay-card${c.status === "closed" ? " closed" : ""}" style="background:${cardColor(c)}">
      <div class="pc-top"><div>
        <div class="pc-name">${esc(c.name)}</div>
        <div class="small" style="opacity:.85">${esc(c.bank || "")} · ${esc(c.type || "")}${c.variant ? " · " + esc(c.variant) : ""}${c.variantSubType ? " " + esc(c.variantSubType) : ""}</div>
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
  }).join("") || '<div class="empty">No cards match the filter.</div>'}</div>`;
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
      { name: "bank", label: "Bank", value: c.bank || "" },
      { name: "owner", label: "Owner", type: "select", value: c.owner || "", options: ["", ...DB.settings.persons] }]},
    { type: "row", fields: [
      { name: "type", label: "Type", type: "select", value: c.type || "Credit",
        options: ["Credit", "Debit", "Credit Virtual", "Debit Virtual", "Food Card", "Priority Pass", "Other"] },
      { name: "variant", label: "Network (VISA / MasterCard / RuPay…)", value: c.variant || "" }]},
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
  const fileDocs = serverFiles.map(f => {
    const meta = DB.documents.find(d => d.type === "file" && d.ref === f.name);
    return `<div class="doc-row"><div class="doc-ico">📄</div>
      <div class="doc-main"><a href="/files/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a>
      <div class="doc-sub">${(f.size / 1024).toFixed(1)} KB · ${fmtDate(f.modified)}${meta && meta.linkedId ? " · " + esc(docLinkedLabel(meta)) : ""}</div></div>
      <button class="icon-btn danger" title="Delete file" onclick="deleteServerFile('${esc(f.name)}')">✕</button></div>`;
  }).join("");
  const linkDocs = DB.documents.filter(d => d.type === "link").map(d => `
    <div class="doc-row"><div class="doc-ico">🔗</div>
      <div class="doc-main"><a href="${esc(d.ref)}" target="_blank" rel="noopener">${esc(d.title || d.ref)}</a>
      <div class="doc-sub">${esc(d.ref)}${d.linkedId ? " · " + esc(docLinkedLabel(d)) : ""}${d.notes ? " · " + esc(d.notes) : ""}</div></div>
      <button class="icon-btn danger" onclick="delDocument('${d.id}')">✕</button></div>`).join("");
  box.innerHTML = (fileDocs + linkDocs) || '<div class="empty">No documents yet.</div>';
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
    <div class="panel"><h2>Family Members</h2>
      <div class="mb">${DB.settings.persons.map((p, i) =>
        `<span class="chip" style="margin:0 6px 6px 0">${esc(p)} <a style="cursor:pointer" onclick="delPerson(${i})">✕</a></span>`).join("") || '<span class="muted">None yet</span>'}</div>
      <button class="btn secondary small" onclick="addPerson()">+ Add person</button>
      <p class="muted small mt">People appear as owners for portfolio, cards and investments.</p>
    </div>
    <div class="panel"><h2>Backup & Restore</h2>
      <p class="muted small mb">All data lives in <b>data/finances.json</b> next to the app (plus uploaded files in data/files/). Copy the whole <b>data</b> folder to move machines; daily backups are kept in data/backups/.</p>
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
function addPerson() {
  formModal("Add family member", [{ name: "name", label: "Name", required: true }], v => {
    if (!DB.settings.persons.includes(v.name)) DB.settings.persons.push(v.name);
    closeModal(); save(); render();
  });
}
function delPerson(i) { DB.settings.persons.splice(i, 1); save(); render(); }
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
    save(true); render();
  });
}

/* ================= init ================= */
els("#nav button").forEach(b => b.onclick = () => navigate(b.dataset.page));
loadDB().then(() => {
  el("#brandName").textContent = DB.settings.appName || "Family Finance";
  if (DB.settings.lastUpdated) el("#lastUpdated").textContent = "Updated " + fmtDateTime(DB.settings.lastUpdated);
  render();
}).catch(e => {
  el("#main").innerHTML = `<div class="panel"><div class="empty">Could not load data: ${esc(e.message)}<br>Is the server running via <code>python server.py</code>?</div></div>`;
});
