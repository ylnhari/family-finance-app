/* Family Finance — pure financial math. No DOM, no DB, no personal data.
   Loaded as a classic script before app.js (functions become globals),
   and also require()-able under Node for unit tests (see tests/math.test.js). */
"use strict";

/* numeric coercion: strips commas/spaces, non-numbers -> 0 */
const num = v => { const n = parseFloat(String(v).replace(/[,\s]/g, "")); return isFinite(n) ? n : 0; };

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

/* full amortization schedule.
   extraMonthly = optional flat extra paid every month (recurring what-if).
   prepayments  = optional [{month, amount, adjustMode:"tenure"|"emi"}] lump sums,
                  applied at the end of their month (same semantics as loanState):
                  "tenure" keeps EMI and finishes sooner; "emi" keeps remaining
                  months and lowers the EMI. */
function amortSchedule(P, annualRate, n, emiOverride, extraMonthly, prepayments) {
  const r = annualRate / 12;
  let emi = emiOverride || calcEMI(P, annualRate, n);
  const pps = (prepayments || []).filter(p => num(p.amount) > 0)
    .sort((a, b) => num(a.month) - num(b.month));
  const rows = [];
  let bal = P, totInt = 0, m = 0, ppIdx = 0;
  while (bal > 0.5 && m < 1200) {
    m++;
    const interest = bal * r;
    let principal = emi - interest + (extraMonthly || 0);
    if (principal <= 0) return { rows, emi, totalInterest: Infinity, months: Infinity, diverges: true };
    if (principal > bal) principal = bal;
    bal -= principal;
    totInt += interest;
    let prepay = 0;
    while (ppIdx < pps.length && num(pps[ppIdx].month) === m) {
      const pp = pps[ppIdx];
      const amt = Math.min(num(pp.amount), bal);
      prepay += amt;
      bal -= amt;
      if (bal <= 0.5) bal = 0;
      if (bal > 0 && pp.adjustMode === "emi") {
        const remMonths = n - m;
        if (remMonths > 0) emi = calcEMI(bal, annualRate, remMonths);
      }
      ppIdx++;
    }
    rows.push({ m, emi: principal + interest, interest, principal, balance: bal, prepay });
  }
  return { rows, emi, totalInterest: totInt, months: m, diverges: false };
}

/* months elapsed since a date string (YYYY-MM-DD) */
function monthsSince(dateStr) {
  if (!dateStr) return 0;
  const d = new Date(dateStr), now = new Date();
  if (isNaN(d)) return 0;
  return Math.max(0, (now.getFullYear() - d.getFullYear()) * 12 + now.getMonth() - d.getMonth());
}

/* derived live state of a loan object — accounts for lump-sum prepayments.
   Each prepayment: {month, amount, adjustMode:"tenure"|"emi"}.
   "tenure" keeps EMI, loan finishes sooner. "emi" keeps remaining months, lowers EMI. */
function loanState(L) {
  const rate = num(L.annualRate);
  const r = rate / 12;
  const principal = num(L.principal);
  const tenure = num(L.tenureMonths);
  const baseEMI = L.emi ? num(L.emi) : calcEMI(principal, rate, tenure);
  const prepayments = ((L.prepayments || []).filter(p => num(p.amount) > 0)
    .sort((a, b) => num(a.month) - num(b.month)));

  let emi = baseEMI, bal = principal, totInt = 0;
  const rows = [];
  let ppIdx = 0;

  for (let m = 1; bal > 0.5 && m <= 1200; m++) {
    const interest = bal * r;
    let princ = emi - interest;
    if (princ <= 0) { rows.push({ m, emi, interest, principal: 0, balance: bal, prepay: 0 }); continue; }
    if (princ > bal) princ = bal;
    bal -= princ;
    totInt += interest;
    let prepay = 0;
    while (ppIdx < prepayments.length && num(prepayments[ppIdx].month) === m) {
      const pp = prepayments[ppIdx];
      const amt = Math.min(num(pp.amount), bal);
      prepay += amt;
      bal -= amt;
      if (bal <= 0.5) { bal = 0; }
      if (bal > 0 && pp.adjustMode === "emi") {
        const remMonths = tenure - m;
        if (remMonths > 0) emi = calcEMI(bal, rate, remMonths);
      }
      ppIdx++;
    }
    rows.push({ m, emi: princ + interest, interest, principal: princ, balance: bal, prepay });
    if (bal <= 0.5) break;
  }
  const sched = { rows, totalInterest: totInt, months: rows.length };
  const paidMonths = Math.min(monthsSince(L.startDate), rows.length);
  const cur = paidMonths > 0 ? rows[paidMonths - 1].balance : principal;
  const paidInterest = rows.slice(0, paidMonths).reduce((s, x) => s + x.interest, 0);
  const paidPrincipal = principal - cur;
  return { emi: baseEMI, sched, paidMonths, remMonths: Math.max(0, rows.length - paidMonths),
           balance: cur, paidInterest, paidPrincipal,
           totalPayable: principal + totInt, totalInterest: totInt };
}

/* strict amount validation: numbers only, no negatives. Empty = 0. */
function validAmount(v) {
  const s = String(v == null ? "" : v).trim().replace(/[,\s]/g, "");
  if (s === "") return { ok: true, n: 0 };
  if (!/^\d*\.?\d+$/.test(s)) return { ok: false };
  const n = parseFloat(s);
  return isFinite(n) && n >= 0 ? { ok: true, n } : { ok: false };
}

/* income totals for one salary-year entry.
   gross = components with scope != "ctc"; ctcOnly = scope == "ctc".
   CTC = gross + ctcOnly. In-Hand = gross - deductions (bonus/variable NOT in hand).
   Variable pay = yearlyCtcBase * (earned% if given else eligible%). */
function incomeTotalsForYear(yr) {
  if (!yr) yr = { components: [], deductions: [], variablePctEligible: 0, variablePctEarned: null, oneTimeBonus: 0 };
  const gross = (yr.components || []).filter(c => c.scope !== "ctc").reduce((s, c) => s + num(c.amount), 0);
  const ctcOnly = (yr.components || []).filter(c => c.scope === "ctc").reduce((s, c) => s + num(c.amount), 0);
  const ded = (yr.deductions || []).reduce((s, c) => s + num(c.amount), 0);
  const monthlyCtc = gross + ctcOnly;
  const yearlyCtcBase = monthlyCtc * 12;
  const elPct = num(yr.variablePctEligible || 0) / 100;
  const hasEarned = yr.variablePctEarned !== null && yr.variablePctEarned !== undefined && yr.variablePctEarned !== "";
  const earnedPct = hasEarned ? num(yr.variablePctEarned) / 100 : null;
  const varPct = earnedPct !== null ? earnedPct : elPct;
  const variablePay = Math.round(yearlyCtcBase * varPct);
  const oneTimeBonus = num(yr.oneTimeBonus || 0);
  return {
    gross, ctcOnly, ctc: monthlyCtc, ded, inHand: gross - ded,
    monthlyCtc, yearlyCtcBase, yearlyCtc: yearlyCtcBase + variablePay,
    variablePay, oneTimeBonus, totalBonus: variablePay + oneTimeBonus,
    eligiblePct: num(yr.variablePctEligible || 0),
    earnedPct: hasEarned ? num(yr.variablePctEarned) : null
  };
}

/* physical-gold gain — only counts entries that have a purchase price recorded */
function computeGoldGain(goldArr) {
  let cur = 0, inv = 0;
  (goldArr || []).forEach(g => {
    if (g.purchasePrice !== null && g.purchasePrice !== undefined && num(g.purchasePrice) > 0) {
      cur += num(g.grams) * num(g.perGramValue);
      inv += num(g.grams) * num(g.purchasePrice);
    }
  });
  return { cur, inv, gain: cur - inv };
}
function computeGoldInvested(goldArr) { return computeGoldGain(goldArr).inv; }

/* maturity of a holding expressed as the owner's age + the calendar year it matures.
   Prefers an explicit target age (e.g. PPF/NPS at 60). Falls back to a months-from-now
   lock-in. Returns { age, year } with nulls where unknowable. */
function maturityInfo(h, birthYear, nowYear) {
  const age = num(h.maturityAge);
  const months = num(h.maturityMonths);
  if (age > 0 && birthYear) return { age, year: birthYear + age };
  if (months > 0) {
    const year = nowYear + Math.floor(months / 12);
    return { age: birthYear ? year - birthYear : null, year };
  }
  if (age > 0) return { age, year: null };   // target age set but owner DOB unknown
  return { age: null, year: null };
}

/* ===================== Lending & Borrowing ledger =====================
   A self-contained tracker of money lent to / borrowed from a person.
   Each transaction: { date:"YYYY-MM-DD", type:"debit"|"credit", instrument,
     amount, cashbackMode:"fixed"|"percent", cashback, extraCharges, cancelled, notes }
   Sign convention for the running balance = "what the other person owes you":
     debit  (you paid on their behalf)  -> +(amount + extraCharges - cashback)
     credit (they repaid you)           -> -(amount)
     cancelled                          -> 0  (voided, regardless of type) */

/* resolved cashback in currency for a row: a flat rupee value, or a % of the amount */
function ledgerCashback(t) {
  if (!t) return 0;
  const amt = num(t.amount);
  if ((t.cashbackMode || "fixed") === "percent") return amt * num(t.cashback) / 100;
  return num(t.cashback);
}

/* signed net effect of one row on the balance the other person owes you */
function ledgerNet(t) {
  if (!t || t.cancelled) return 0;
  const amt = num(t.amount);
  if ((t.type || "debit") === "credit") return -amt;
  return amt + num(t.extraCharges) - ledgerCashback(t);
}

/* calendar year (number) from a YYYY-MM-DD / ISO date string; 0 when unknown */
function ledgerYear(dateStr) {
  if (!dateStr) return 0;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(dateStr));
  if (m) return parseInt(m[1], 10);
  const d = new Date(dateStr);
  return isNaN(d) ? 0 : d.getFullYear();
}

/* aggregate a person's transactions into overall + per-year totals.
   net > 0 => they owe you (you'll receive); net < 0 => you owe them. */
function ledgerTotals(transactions) {
  const rows = transactions || [];
  const years = {};
  let net = 0, lent = 0, repaid = 0, cashback = 0, extra = 0, cancelled = 0;
  for (const t of rows) {
    const yKey = ledgerYear(t.date) || "—";
    const yr = years[yKey] || (years[yKey] = { year: yKey, net: 0, lent: 0, repaid: 0, cashback: 0, count: 0, cancelled: 0 });
    const n = ledgerNet(t);
    yr.net += n; net += n;
    if (t.cancelled) { cancelled++; yr.cancelled++; continue; }
    yr.count++;
    const cb = ledgerCashback(t);
    cashback += cb; yr.cashback += cb;
    if ((t.type || "debit") === "credit") {
      repaid += num(t.amount); yr.repaid += num(t.amount);
    } else {
      const eff = num(t.amount) + num(t.extraCharges) - cb;
      lent += eff; yr.lent += eff; extra += num(t.extraCharges);
    }
  }
  const byYear = Object.values(years).sort((a, b) =>
    a.year === "—" ? 1 : b.year === "—" ? -1 : b.year - a.year);
  return { net, lent, repaid, cashback, extra, cancelled, count: rows.length, byYear,
           direction: net > 0.5 ? "receive" : net < -0.5 ? "owe" : "settled",
           abs: Math.abs(net) };
}

/* Node test access only — ignored in the browser */
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    num, calcEMI, outstandingFromEMI, amortSchedule, monthsSince, loanState,
    validAmount, incomeTotalsForYear, computeGoldGain, computeGoldInvested, maturityInfo,
    ledgerCashback, ledgerNet, ledgerYear, ledgerTotals
  };
}
