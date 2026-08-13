/* Sample-data integrity + end-to-end calc invariants.
   Loads the committed demo dataset and runs the real pure calcs over it, so a
   regression in either the data or the math surfaces here. Run: node --test tests/sample.test.js */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { loanState, incomeTotalsForYear, computeGoldGain, maturityInfo,
        num, expenseTotal, externalHelpTotal,
        ledgerNet, ledgerCashback, ledgerTotals } = require("../public/finance-math.js");

const SAMPLE = path.join(__dirname, "..", "samples", "demo-finances.json");
const DB = JSON.parse(fs.readFileSync(SAMPLE, "utf8"));

test("sample: top-level shape has every section the app expects", () => {
  for (const k of ["settings", "income", "expenses", "monthlyInvestments",
                   "portfolio", "gold", "loans", "goals", "cards", "documents", "ledgers"]) {
    assert.ok(k in DB, `missing section: ${k}`);
  }
  assert.ok(Array.isArray(DB.income.persons) && DB.income.persons.length >= 2, "≥2 earners");
});

test("sample: lending ledgers cover 2 people, multi-year, %-cashback, cancelled & both directions", () => {
  assert.ok(Array.isArray(DB.ledgers) && DB.ledgers.length >= 2, "≥2 ledger people");
  const all = DB.ledgers.flatMap(L => L.transactions);
  assert.ok(all.some(t => t.cashbackMode === "percent"), "a %-cashback row to demo the feature");
  assert.ok(all.some(t => t.cancelled), "a cancelled (voided) row");
  assert.ok(all.some(t => num(t.extraCharges) > 0), "an extra-charges row");
  // a percent cashback resolves to a positive rupee value, and cancelled rows net zero
  const pct = all.find(t => t.cashbackMode === "percent" && !t.cancelled);
  assert.ok(ledgerCashback(pct) > 0, "percent cashback resolves to ₹");
  assert.equal(ledgerNet(all.find(t => t.cancelled)), 0, "cancelled row nets zero");
  // a multi-year person and both balance directions present
  assert.ok(DB.ledgers.some(L => ledgerTotals(L.transactions).byYear.length >= 2), "a multi-year ledger");
  const dirs = new Set(DB.ledgers.map(L => ledgerTotals(L.transactions).direction));
  assert.ok(dirs.has("receive"), "someone owes you");
  assert.ok(dirs.has("owe"), "you owe someone");
  // one ledger-year must exceed a UI page (25 rows) so the "Show more" flow stays demoable
  const maxYearCount = Math.max(...DB.ledgers.flatMap(
    L => ledgerTotals(L.transactions).byYear.map(y => y.count)));
  assert.ok(maxYearCount > 25, "a single ledger year with >25 transactions (pagination demo)");
});

test("sample: every earner's salary years compute positive CTC & in-hand", () => {
  for (const p of DB.income.persons) {
    assert.ok((p.salaryYears || []).length >= 1, `${p.name} has years`);
    for (const yr of p.salaryYears) {
      const t = incomeTotalsForYear(yr);
      assert.ok(t.monthlyCtc > 0, `${p.name} ${yr.year} CTC > 0`);
      assert.ok(t.inHand > 0, `${p.name} ${yr.year} in-hand > 0`);
      assert.ok(t.inHand <= t.gross, "in-hand never exceeds gross");
      assert.ok(t.monthlyCtc >= t.gross, "CTC never below gross");
    }
  }
});

test("sample: multi-year earner exists (drives the salary growth chart)", () => {
  assert.ok(DB.income.persons.some(p => (p.salaryYears || []).length >= 2),
    "at least one earner needs ≥2 years for the growth chart");
});

test("sample: at least one loan demonstrates a prepayment, and all loans price out", () => {
  assert.ok(DB.loans.length >= 1);
  assert.ok(DB.loans.some(L => (L.prepayments || []).length > 0), "a loan should demo prepayment");
  for (const L of DB.loans) {
    const st = loanState(L);
    assert.ok(st.emi > 0, `${L.name} EMI > 0`);
    assert.ok(st.totalInterest >= 0, `${L.name} interest ≥ 0`);
    assert.ok(st.sched.months > 0 && st.sched.months <= 1200, `${L.name} terminates`);
    assert.ok(st.balance >= 0, `${L.name} balance ≥ 0`);
  }
});

test("sample: a prepayment actually reduces interest vs the same loan without it", () => {
  const L = DB.loans.find(x => (x.prepayments || []).length > 0);
  const withPre = loanState(L);
  const without = loanState({ ...L, prepayments: [] });
  assert.ok(withPre.totalInterest < without.totalInterest, "prepayment saves interest");
  assert.ok(withPre.sched.months <= without.sched.months, "prepayment doesn't lengthen loan");
});

test("sample: gold has both priced (gain shown) and unpriced entries", () => {
  const priced = DB.gold.filter(g => g.purchasePrice != null && g.purchasePrice > 0);
  const unpriced = DB.gold.filter(g => g.purchasePrice == null);
  assert.ok(priced.length >= 1, "≥1 gold entry with buy price (demo gain calc)");
  assert.ok(unpriced.length >= 1, "≥1 gold entry without buy price (demo skip)");
  const g = computeGoldGain(DB.gold);
  assert.ok(g.inv > 0 && g.cur > 0, "gold gain computes over priced entries");
});

test("sample: all gold rows carry a current per-gram rate (drives auto-fetch / apply-all)", () => {
  assert.ok(DB.gold.length >= 1);
  DB.gold.forEach(g => assert.ok(g.perGramValue > 0, "every gold row has a current rate"));
});

test("sample: goals include loan-funded, cash, fulfilled, and a market-priced one", () => {
  assert.ok(DB.goals.some(g => g.loanAmount > 0), "a goal funded by loan");
  assert.ok(DB.goals.some(g => !g.loanAmount), "a cash goal");
  assert.ok(DB.goals.some(g => g.fulfilled), "a fulfilled goal");
  // a goal pre-loaded with a fetched price (demos the goal-price refresh feature end-to-end)
  const priced = DB.goals.find(g => g.currentMarketValue != null);
  assert.ok(priced, "a goal with fetched market value");
  assert.ok(priced.lastPriceFetch, "...and its last-fetch date, so the UI shows 'price as of'");
});

test("sample: cards cover bank + no-bank + a closed card", () => {
  assert.ok(DB.cards.some(c => (c.bank || "").trim()), "a card with a bank");
  assert.ok(DB.cards.some(c => !(c.bank || "").trim()), "a no-bank card (food card)");
  assert.ok(DB.cards.some(c => c.status === "closed"), "a closed card");
});

test("sample: expense sections exercise all three dimension modes", () => {
  const meta = DB.settings.predefined.expenseGroupMeta || {};
  const dims = new Set(Object.values(meta).map(m => m.dim));
  for (const d of ["location", "person", "none"]) {
    assert.ok(dims.has(d), `expense dimension "${d}" demonstrated`);
  }
});

test("sample: outside help is present and excluded from expenses", () => {
  const meta = DB.settings.predefined.expenseGroupMeta || {};
  const help = externalHelpTotal(DB.expenses, meta);
  const expenses = expenseTotal(DB.expenses, meta);
  const raw = DB.expenses.reduce((sum, e) => sum + num(e.amount), 0);
  assert.ok(help > 0, "demo includes potential outside help");
  assert.ok(DB.expenses.some(e => /external help|family support/i.test(e.group || "")));
  assert.equal(expenses + help, raw, "outside help is not counted as expense");
  assert.equal(meta["External Help / Family Support"].kind, "externalHelp");
});

test("sample: monthly investments cover IN HAND / GROSS / CTC sources", () => {
  const sources = new Set(DB.monthlyInvestments.map(i => i.deductedFrom));
  for (const s of ["IN HAND", "GROSS", "CTC"]) assert.ok(sources.has(s), `${s} source present`);
});

test("sample: family members have dates of birth (drives age-based maturity)", () => {
  const meta = DB.settings.personMeta || {};
  for (const name of DB.settings.persons) {
    assert.ok(meta[name] && meta[name].dob, `${name} has a DOB`);
    assert.ok(!isNaN(new Date(meta[name].dob)), `${name} DOB is a valid date`);
  }
});

test("sample: an age-based maturity holding resolves to an age + future year", () => {
  const h = DB.portfolio.find(p => p.maturityAge > 0);
  assert.ok(h, "a holding matures at a target age (e.g. PPF/NPS at 60)");
  const birthYear = new Date(DB.settings.personMeta[h.owner].dob).getFullYear();
  const m = maturityInfo(h, birthYear, new Date().getFullYear());
  assert.equal(m.age, h.maturityAge);
  assert.equal(m.year, birthYear + h.maturityAge);
  assert.ok(m.year > new Date().getFullYear(), "maturity year is in the future");
});
