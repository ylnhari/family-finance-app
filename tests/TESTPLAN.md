# Test Plan — Family Finance

Regression suite. Run after **every** change; if it's green, core behaviour is intact.

```bash
./test.sh          # Windows: test.bat
```

No frameworks, no installs. Pure math runs under Node's built-in runner; server/API
under Python's `unittest`. Total: **60 cases** (~2s).

## Strategy (pyramid)

```
        server HTTP (integration) ── 20 cases, real server + temp data dir
      sample-data integrity (e2e-ish) ── 10 cases, real calcs over demo file
   pure math + gemini logic (unit) ── 30 cases, fast, no I/O
```

Most coverage sits in fast unit tests over the **pure computational core**
(`public/finance-math.js`, `server.py` parse helpers). Integration tests cover the
HTTP layer and persistence. The sample-data tests double as e2e: real calcs over the
committed demo dataset catch both data drift and math regressions.

## What's covered

| Area | Type | File | Key cases |
|---|---|---|---|
| EMI / amortization | unit | `math.test.js` | standard formula, zero-interest, payoff to zero, diverging-loan guard |
| **Loan prepayment** (new) | unit | `math.test.js` | tenure-mode shortens loan, emi-mode lowers EMI, overpay closes loan, elapsed-time balance |
| Income totals | unit | `math.test.js` | gross/ctc/in-hand split, variable earned-vs-eligible, bonus excluded from in-hand, empty year |
| Validation | unit | `math.test.js` | blanks→0, commas, reject junk/negatives/percent |
| **Gold gain** (new) | unit | `math.test.js` | only priced entries count, zero/empty cases |
| Gemini JSON parse | unit | `test_gemini.py` | markdown-fence strip, bare fence, invalid raises |
| Gemini price parse | unit | `test_gemini.py` | commas, decimals, null word, extract-from-noise |
| Gemini model fallback | unit | `test_gemini.py` | preference order, extras appended, empty→full list |
| Sample-data integrity | e2e | `sample.test.js` | every section present, all earners/loans price out, prepayment saves interest, goals/cards/expenses/investments cover all variants |
| GET/PUT persistence | integration | `test_server.py` | roundtrip, full-document sections preserved |
| **Concurrent writes** (regression) | integration | `test_server.py` | 20 parallel PUTs, no `Errno 13`, file stays valid (the save-lock fix) |
| Static serving | integration | `test_server.py` | index.html, finance-math.js, content types |
| File lifecycle | integration | `test_server.py` | upload→list→download→delete, dedupe, empty rejected |
| Security / errors | integration | `test_server.py` | path-traversal blocked, unknown route 404, invalid JSON 400, delete-missing 404 |
| Backup / restore | integration | `test_server.py` | snapshot→restore roundtrip, missing backup 404 |
| AI graceful-degrade | integration | `test_server.py` | no key → status `available:false`, extract/price → 503 |

## Deliberately NOT covered (and why)

- **Live Gemini calls** — costs quota, needs network, non-deterministic. Parse + fallback
  logic is unit-tested instead; the HTTP boundary is mocked out by testing helpers.
- **`migrate()` (client schema upgrade)** — runs only in the browser against the `DB`
  global. Not reachable from Node/Python harness without invasive refactor. Covered
  manually (see checklist) by loading old exports.
- **DOM rendering / `PAGES.*` HTML** — would need a headless browser. The data the views
  consume is fully unit-tested; rendering bugs are caught by the manual smoke pass.
- **CSS / visual layout** — manual only.

## Manual smoke checklist (after UI-affecting changes)

1. `cp data/sample-finances.json data/finances.json && python server.py` — boots clean.
2. Visit every nav page — Dashboard, Income, Expenses, Investments, Portfolio, Loans,
   Goals, Cards, Documents, Settings — no console errors.
3. Dashboard shows the net-worth bar, investment-mix + asset donuts, gold gain.
4. Income: year pills switch; multi-year earner shows the growth chart.
5. Loans: open a loan → "Prepay" → record → schedule/months/interest update.
6. Goals: `⟳` button on a goal (needs `GEMINI_API_KEY`) updates market value or toasts a fallback.
7. Add/edit a row anywhere → save status flips "Saving…" → "All changes saved".
8. Hard-refresh (Ctrl+Shift+R) → data persisted.
9. Import an **old** export (pre-prepayment schema) in Settings → migrates, no crash.

## Coverage targets

- Pure financial/income/gold math: **100% of branches** — these are the money-correctness core.
- Server endpoints: every route has at least one happy-path + one error-path case.
- Sample dataset: every feature toggle/variant the UI can render is represented (asserted by `sample.test.js`).

## Adding tests (mandatory for every feature — see CLAUDE.md rule 6)

Every new feature ships with **both** test coverage and demo data:

- New pure calc → put it in `public/finance-math.js`, export it, add cases to `math.test.js`.
- New endpoint → add happy + error cases to `test_server.py`.
- New AI parse/logic → add cases to `test_gemini.py` (no network).
- **Demo data** → extend `samples/demo-finances.json` so the feature is visibly demoable via
  `python server.py --demo`, then assert its presence in `sample.test.js` so the demo can't
  silently lose coverage.

A change is not "done" until `./test.sh` is green AND the feature shows up in the demo dataset.
