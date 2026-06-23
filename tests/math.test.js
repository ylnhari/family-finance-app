/* Pure financial-math unit tests. Run: node --test tests/math.test.js
   Zero dependencies — uses Node's built-in test runner + assert. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  num, calcEMI, outstandingFromEMI, amortSchedule, loanState,
  validAmount, incomeTotalsForYear, computeGoldGain, computeGoldInvested, maturityInfo,
  ledgerCashback, ledgerNet, ledgerYear, ledgerTotals
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

test("amortSchedule applies lump-sum prepayments and matches loanState", () => {
  const args = [721000, 0.099, 96, 10941];
  const prepays = [
    { month: 3, amount: 20999, adjustMode: "tenure" },
    { month: 9, amount: 165000, adjustMode: "tenure" }
  ];
  const plain = amortSchedule(...args);
  const withPre = amortSchedule(...args, 0, prepays);
  // lump sums shorten the schedule and cut total interest vs the plain run
  assert.ok(withPre.months < plain.months, "prepayments shorten payoff");
  assert.ok(withPre.totalInterest < plain.totalInterest, "prepayments cut interest");
  // and it agrees with the dashboard's prepayment-aware engine (loanState)
  const st = loanState({ principal: 721000, annualRate: 0.099, tenureMonths: 96,
    emi: 10941, startDate: null, prepayments: prepays });
  assert.equal(withPre.months, st.sched.months);
  approx(withPre.totalInterest, st.totalInterest, 1);
  approx(withPre.rows[withPre.rows.length - 1].balance, 0, 0.5);
});

test("amortSchedule emi-mode prepayment keeps tenure, lowers EMI", () => {
  const base = amortSchedule(1000000, 0.09, 120, calcEMI(1000000, 0.09, 120));
  const pre = amortSchedule(1000000, 0.09, 120, calcEMI(1000000, 0.09, 120), 0,
    [{ month: 12, amount: 200000, adjustMode: "emi" }]);
  // emi mode does not shorten beyond the prepay credit; payment after month 12 drops
  const laterRow = pre.rows[20];
  assert.ok((laterRow.principal + laterRow.interest) < base.rows[20].emi, "EMI reduced after emi-mode prepay");
});

test("amortSchedule without prepayments is unchanged (back-compat)", () => {
  const s = amortSchedule(500000, 0.10, 60);
  assert.equal(s.months, 60);
  assert.equal(s.rows[0].prepay, 0);
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

test("loanState: two prepayments in one month == one combined prepayment (regression)", () => {
  // Regression for the `bal -= prepay` cumulative-subtraction bug: two lump sums in
  // the same month must reduce the balance by exactly their sum, no more.
  const base = { principal: 1000000, annualRate: 0.09, tenureMonths: 120, startDate: null };
  const split = loanState({ ...base, prepayments: [
    { month: 12, amount: 100000, adjustMode: "tenure" },
    { month: 12, amount: 100000, adjustMode: "tenure" },
  ] });
  const combined = loanState({ ...base, prepayments: [
    { month: 12, amount: 200000, adjustMode: "tenure" },
  ] });
  assert.equal(split.sched.months, combined.sched.months);
  approx(split.totalInterest, combined.totalInterest, 1);
  // and it agrees with the (already-correct) amortSchedule engine
  const amort = amortSchedule(1000000, 0.09, 120, calcEMI(1000000, 0.09, 120), 0,
    [{ month: 12, amount: 100000, adjustMode: "tenure" },
     { month: 12, amount: 100000, adjustMode: "tenure" }]);
  assert.equal(split.sched.months, amort.months);
  approx(split.totalInterest, amort.totalInterest, 1);
});

test("loanState: emi-mode prepayment that nearly closes the loan still terminates cleanly", () => {
  const st = loanState({ principal: 300000, annualRate: 0.10, tenureMonths: 60, startDate: null,
    prepayments: [{ month: 6, amount: 295000, adjustMode: "emi" }] });
  assert.ok(Number.isFinite(st.sched.months) && st.sched.months <= 60, "finite, within tenure");
  assert.ok(Number.isFinite(st.totalInterest), "interest is finite (no divergence)");
  approx(st.sched.rows[st.sched.rows.length - 1].balance, 0, 1);
});

test("loanState: long tenure completes without hitting the 1200-month cap", () => {
  const st = loanState({ principal: 1000000, annualRate: 0.085, tenureMonths: 300, startDate: null });
  assert.equal(st.sched.months, 300);
  assert.ok(st.sched.months < 1200, "well under the safety cap");
  approx(st.sched.rows[st.sched.rows.length - 1].balance, 0, 0.5);
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

/* ---------------- lending & borrowing ledger ---------------- */
test("ledgerCashback: fixed returns the flat value; percent is % of amount", () => {
  assert.equal(ledgerCashback({ amount: 10000, cashbackMode: "fixed", cashback: 500 }), 500);
  assert.equal(ledgerCashback({ amount: 10000, cashbackMode: "percent", cashback: 5 }), 500);
  assert.equal(ledgerCashback({ amount: 8000, cashbackMode: "percent", cashback: 2.5 }), 200);
  assert.equal(ledgerCashback({ amount: 8000 }), 0);            // no cashback fields -> 0
  assert.equal(ledgerCashback(null), 0);
});

test("ledgerNet: debit adds (amount+extra−cashback), credit subtracts amount, cancelled is zero", () => {
  approx(ledgerNet({ type: "debit", amount: 10000, cashbackMode: "percent", cashback: 5 }), 9500);
  approx(ledgerNet({ type: "debit", amount: 8000, extraCharges: 40 }), 8040);
  approx(ledgerNet({ type: "credit", amount: 5000 }), -5000);
  assert.equal(ledgerNet({ type: "debit", amount: 9999, cancelled: true }), 0);
  assert.equal(ledgerNet({ type: "credit", amount: 9999, cancelled: true }), 0);
});

test("ledgerYear extracts the year from an ISO date", () => {
  assert.equal(ledgerYear("2024-03-14"), 2024);
  assert.equal(ledgerYear("2025-12-31"), 2025);
  assert.equal(ledgerYear(""), 0);
});

test("ledgerTotals: net, direction, per-year breakdown, cashback & cancelled tallies", () => {
  const txns = [
    { date: "2024-03-14", type: "debit", amount: 10000, cashbackMode: "percent", cashback: 5 }, // +9500
    { date: "2024-06-20", type: "debit", amount: 8000, extraCharges: 40 },                       // +8040
    { date: "2024-08-01", type: "credit", amount: 5000 },                                        // -5000
    { date: "2024-09-10", type: "debit", amount: 6000, cancelled: true },                        // 0
    { date: "2025-01-12", type: "credit", amount: 3000 }                                         // -3000
  ];
  const t = ledgerTotals(txns);
  approx(t.net, 9540);
  assert.equal(t.direction, "receive");
  assert.equal(t.cancelled, 1);
  approx(t.cashback, 500);
  assert.equal(t.count, 5);
  assert.equal(t.byYear.length, 2);
  approx(t.byYear.find(y => y.year === 2024).net, 12540);   // 9500 + 8040 − 5000
  approx(t.byYear.find(y => y.year === 2025).net, -3000);
  assert.equal(t.byYear[0].year, 2025);                          // sorted newest-first
});

test("ledgerTotals: per-year count excludes cancelled rows, tracked separately", () => {
  const t = ledgerTotals([
    { date: "2024-03-14", type: "debit", amount: 10000 },
    { date: "2024-06-20", type: "debit", amount: 8000 },
    { date: "2024-09-10", type: "debit", amount: 6000, cancelled: true },
    { date: "2025-01-12", type: "credit", amount: 3000 },
  ]);
  const y2024 = t.byYear.find(y => y.year === 2024);
  const y2025 = t.byYear.find(y => y.year === 2025);
  assert.equal(y2024.count, 2, "active rows only");
  assert.equal(y2024.cancelled, 1, "voided rows counted apart");
  assert.equal(y2025.count, 1);
  assert.equal(y2025.cancelled, 0);
  assert.equal(t.count, 4, "overall count still includes every row");
});

test("ledgerTotals: net-negative flips direction to 'owe'", () => {
  const t = ledgerTotals([
    { date: "2024-02-10", type: "credit", amount: 43000 },
    { date: "2024-05-05", type: "debit", amount: 6000 }
  ]);
  approx(t.net, -37000);
  assert.equal(t.direction, "owe");
});
