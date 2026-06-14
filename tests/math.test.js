/* Pure financial-math unit tests. Run: node --test tests/math.test.js
   Zero dependencies — uses Node's built-in test runner + assert. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  num, calcEMI, outstandingFromEMI, amortSchedule, loanState,
  validAmount, incomeTotalsForYear, computeGoldGain, computeGoldInvested, maturityInfo
} = require("../public/finance-math.js");

const approx = (a, b, tol = 1) => assert.ok(Math.abs(a - b) <= tol, `${a} ≈ ${b} (±${tol})`);

test("num coerces strings, commas, blanks", () => {
  assert.equal(num("1,00,000"), 100000);
  assert.equal(num("  2500 "), 2500);
  assert.equal(num("abc"), 0);
  assert.equal(num(null), 0);
});

test("calcEMI matches standard formula", () => {
  // 10,00,000 @ 9% p.a. for 120 months ≈ 12,668
  approx(calcEMI(1000000, 0.09, 120), 12668, 2);
  // zero-interest loan = principal / months
  assert.equal(calcEMI(120000, 0, 12), 10000);
  // guards
  assert.equal(calcEMI(0, 0.09, 12), 0);
  assert.equal(calcEMI(100000, 0.09, 0), 0);
});

test("outstandingFromEMI inverts calcEMI", () => {
  const P = 800000, rate = 0.085, n = 84;
  const emi = calcEMI(P, rate, n);
  // outstanding at the start (full remaining months) ≈ original principal
  approx(outstandingFromEMI(emi, rate, n), P, 5);
});

test("amortSchedule pays the loan off exactly", () => {
  const s = amortSchedule(500000, 0.10, 60);
  assert.equal(s.diverges, false);
  assert.equal(s.months, 60);
  approx(s.rows[s.rows.length - 1].balance, 0, 0.5);
  // total interest is positive and sane
  assert.ok(s.totalInterest > 0 && s.totalInterest < 500000);
});

test("amortSchedule flags diverging loan (EMI < monthly interest)", () => {
  const s = amortSchedule(1000000, 0.12, 6); // tiny tenure forces big EMI — won't diverge; use override
  const bad = amortSchedule(1000000, 0.12, 600, 100); // EMI=100 < interest
  assert.equal(bad.diverges, true);
  assert.equal(s.diverges, false);
});

test("loanState: no prepayment matches plain schedule", () => {
  const L = { principal: 1000000, annualRate: 0.09, tenureMonths: 120, startDate: null };
  const st = loanState(L);
  assert.equal(st.sched.months, 120);
  approx(st.balance, 1000000, 1);          // startDate null => nothing paid yet
  approx(st.totalPayable, 1000000 + st.totalInterest, 1);
});

test("loanState: tenure-mode prepayment shortens loan & cuts interest", () => {
  const base = loanState({ principal: 1000000, annualRate: 0.09, tenureMonths: 120, startDate: null });
  const pre = loanState({ principal: 1000000, annualRate: 0.09, tenureMonths: 120, startDate: null,
    prepayments: [{ month: 12, amount: 200000, adjustMode: "tenure" }] });
  assert.ok(pre.sched.months < base.sched.months, "fewer months after prepay");
  assert.ok(pre.totalInterest < base.totalInterest, "less interest after prepay");
});

test("loanState: emi-mode prepayment keeps months, lowers later EMI", () => {
  const pre = loanState({ principal: 1000000, annualRate: 0.09, tenureMonths: 120, startDate: null,
    prepayments: [{ month: 12, amount: 200000, adjustMode: "emi" }] });
  // EMI after prepay (month 13+) should be lower than the original base EMI
  const baseEMI = calcEMI(1000000, 0.09, 120);
  const laterRow = pre.sched.rows[20];     // well after the prepay
  assert.ok((laterRow.principal + laterRow.interest) < baseEMI, "EMI reduced after emi-mode prepay");
});

test("loanState: prepayment larger than balance closes the loan", () => {
  const pre = loanState({ principal: 300000, annualRate: 0.10, tenureMonths: 60, startDate: null,
    prepayments: [{ month: 6, amount: 5000000, adjustMode: "tenure" }] });
  assert.ok(pre.sched.months <= 6, "loan closes at/before prepay month");
  // final scheduled balance is zero (startDate null => st.balance is as-of-today, not final)
  approx(pre.sched.rows[pre.sched.rows.length - 1].balance, 0, 1);
});

test("loanState: balance reflects elapsed time when startDate is set", () => {
  // started 24 months ago — some principal should be repaid by now
  const d = new Date(); d.setMonth(d.getMonth() - 24);
  const start = d.toISOString().slice(0, 10);
  const st = loanState({ principal: 1000000, annualRate: 0.09, tenureMonths: 120, startDate: start });
  assert.ok(st.paidMonths >= 23 && st.paidMonths <= 25, "≈24 EMIs paid");
  assert.ok(st.balance < 1000000 && st.balance > 0, "partly repaid");
  assert.ok(st.paidPrincipal > 0 && st.paidInterest > 0);
});

/* ---------------- validAmount ---------------- */

test("validAmount accepts blanks, numbers, commas; rejects junk & negatives", () => {
  assert.deepEqual(validAmount(""), { ok: true, n: 0 });
  assert.deepEqual(validAmount("  "), { ok: true, n: 0 });
  assert.deepEqual(validAmount("2500"), { ok: true, n: 2500 });
  assert.deepEqual(validAmount("1,00,000"), { ok: true, n: 100000 });
  assert.equal(validAmount("12.5").n, 12.5);
  assert.equal(validAmount("abc").ok, false);
  assert.equal(validAmount("-5").ok, false);
  assert.equal(validAmount("5%").ok, false);
});

/* ---------------- income totals ---------------- */

const sampleYear = {
  components: [
    { component: "BASIC", amount: 100000, scope: "gross" },
    { component: "HRA", amount: 50000, scope: "gross" },
    { component: "EMPLOYER PF", amount: 12000, scope: "ctc" },
  ],
  deductions: [
    { component: "TDS", amount: 30000 },
    { component: "PF", amount: 12000 },
  ],
  variablePctEligible: 15,
  variablePctEarned: 12,
  oneTimeBonus: 100000,
};

test("incomeTotalsForYear: gross / ctc / in-hand split", () => {
  const t = incomeTotalsForYear(sampleYear);
  assert.equal(t.gross, 150000);            // BASIC + HRA (scope != ctc)
  assert.equal(t.ctcOnly, 12000);           // EMPLOYER PF (scope == ctc)
  assert.equal(t.monthlyCtc, 162000);       // gross + ctcOnly
  assert.equal(t.ded, 42000);               // TDS + PF
  assert.equal(t.inHand, 108000);           // gross - ded (bonus NOT counted)
  assert.equal(t.yearlyCtcBase, 162000 * 12);
});

test("incomeTotalsForYear: variable uses earned% when present, else eligible%", () => {
  const earned = incomeTotalsForYear(sampleYear);
  // earned 12% of yearly base
  assert.equal(earned.variablePay, Math.round(162000 * 12 * 0.12));
  assert.equal(earned.earnedPct, 12);

  const noEarned = incomeTotalsForYear({ ...sampleYear, variablePctEarned: null });
  assert.equal(noEarned.variablePay, Math.round(162000 * 12 * 0.15)); // falls back to eligible
  assert.equal(noEarned.earnedPct, null);
});

test("incomeTotalsForYear: bonus is variable + one-time, excluded from in-hand", () => {
  const t = incomeTotalsForYear(sampleYear);
  assert.equal(t.oneTimeBonus, 100000);
  assert.equal(t.totalBonus, t.variablePay + 100000);
  assert.equal(t.yearlyCtc, t.yearlyCtcBase + t.variablePay); // one-time NOT in yearlyCtc
});

test("incomeTotalsForYear: empty / missing year is all zeros", () => {
  const t = incomeTotalsForYear(null);
  assert.equal(t.gross, 0);
  assert.equal(t.inHand, 0);
  assert.equal(t.variablePay, 0);
  assert.equal(t.totalBonus, 0);
});

/* ---------------- gold gain ---------------- */

test("computeGoldGain only counts entries with a purchase price", () => {
  const gold = [
    { grams: 100, perGramValue: 7000, purchasePrice: 5000 }, // counted
    { grams: 50, perGramValue: 7000, purchasePrice: null },  // ignored (no buy price)
    { grams: 20, perGramValue: 7000, purchasePrice: 0 },     // ignored (zero)
  ];
  const g = computeGoldGain(gold);
  assert.equal(g.cur, 100 * 7000);          // only the first entry
  assert.equal(g.inv, 100 * 5000);
  assert.equal(g.gain, 200000);
  assert.equal(computeGoldInvested(gold), 500000);
});

test("computeGoldGain on empty / all-unpriced gold is zero", () => {
  assert.deepEqual(computeGoldGain([]), { cur: 0, inv: 0, gain: 0 });
  assert.deepEqual(computeGoldGain([{ grams: 10, perGramValue: 7000, purchasePrice: null }]),
    { cur: 0, inv: 0, gain: 0 });
});

/* ---------------- holding maturity (age + year) ---------------- */

test("maturityInfo: target age + owner DOB -> age and calendar year", () => {
  // born 1988, matures at 60 -> year 2048
  assert.deepEqual(maturityInfo({ maturityAge: 60 }, 1988, 2026), { age: 60, year: 2048 });
});

test("maturityInfo: lock-in months -> year from now, age if DOB known", () => {
  // 36 months from 2026 -> 2029; born 1991 -> age 38 that year
  assert.deepEqual(maturityInfo({ maturityMonths: 36 }, 1991, 2026), { age: 38, year: 2029 });
  // no DOB -> year only
  assert.deepEqual(maturityInfo({ maturityMonths: 24 }, null, 2026), { age: null, year: 2028 });
});

test("maturityInfo: target age but no DOB -> age only, no year", () => {
  assert.deepEqual(maturityInfo({ maturityAge: 60 }, null, 2026), { age: 60, year: null });
});

test("maturityInfo: nothing set -> all null; age preferred over months", () => {
  assert.deepEqual(maturityInfo({}, 1988, 2026), { age: null, year: null });
  // both present -> age wins
  assert.deepEqual(maturityInfo({ maturityAge: 60, maturityMonths: 12 }, 1988, 2026), { age: 60, year: 2048 });
});
