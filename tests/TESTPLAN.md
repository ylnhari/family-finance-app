# Test Plan — Family Finance

Regression suite. Run after **every** change; if it's green, core behaviour is intact.

```bash
./test.sh          # Windows: test.bat
```

No frameworks, no installs. Pure math runs under Node's built-in runner; server/API/CLI
under Python's `unittest`. **Total: 257 cases** (210 Python + 47 Node) — see "FINAL counts"
at the bottom for the exact per-file breakdown from an actual run.

## Strategy (pyramid)

```
        server + CLI HTTP/process (integration) ── 101 cases (test_server.py + test_docs.py + test_cli.py)
      investlib business logic (unit, temp-dir isolated) ── 75 cases, test_investlib.py
   port resolver + zero-install guards (regression) ── 21 cases (test_portlib.py + test_no_extras.py)
   pure math + gemini logic (unit) ── 46 cases, fast, no I/O
       sample-data integrity (e2e-ish) ── 14 cases, real calcs over demo file
```

Most coverage sits in fast unit tests over the **pure computational core**
(`public/finance-math.js`) and the **investments business logic** (`investlib/*.py`,
isolated from real data via `TempDataMixin`). Integration tests cover the HTTP layer
(`test_server.py`, `test_docs.py`) and the CLI process (`test_cli.py`). The sample-data
tests double as e2e: real calcs over the committed demo dataset catch both data drift
and math regressions.

## What's covered

### Core finance tracker

| Area | Type | File | Key cases |
|---|---|---|---|
| EMI / amortization | unit | `math.test.js` | standard formula, zero-interest, payoff to zero, diverging-loan guard |
| Loan prepayment | unit | `math.test.js` | tenure-mode shortens loan, emi-mode lowers EMI, overpay closes loan, elapsed-time balance |
| Income totals | unit | `math.test.js` | gross/ctc/in-hand split, variable earned-vs-eligible, bonus excluded from in-hand, empty year |
| Validation | unit | `math.test.js` | blanks→0, commas, reject junk/negatives/percent |
| Gold gain | unit | `math.test.js` | only priced entries count, zero/empty cases |
| Maturity info (goal age/year) | unit | `math.test.js` | target age + DOB, lock-in months, age-without-year, nothing-set |
| Lending ledgers (cashback/net/totals) | unit | `math.test.js` | fixed/% cashback, debit/credit/cancelled, per-year breakdown, net-negative direction flip |
| Gemini JSON parse | unit | `test_gemini.py` | markdown-fence strip, bare fence, invalid raises |
| Gemini price parse | unit | `test_gemini.py` | commas, decimals, null word, extract-from-noise |
| Gemini model fallback | unit | `test_gemini.py` | preference order, extras appended, empty→full list |
| Sample-data integrity | e2e | `sample.test.js` | every section present, all earners/loans price out, prepayment saves interest, goals/cards/expenses/ledgers/gold cover all variants |
| Port resolution | unit | `test_portlib.py` | explicit > environment > registry > default, invalid/conflicting values rejected |
| Zero-install import guard | integration | `test_no_extras.py` | every app module imports with optional live-sync extras forced absent |
| GET/PUT persistence | integration | `test_server.py` | roundtrip, full-document sections preserved |
| Concurrent writes (regression) | integration | `test_server.py` | 20 parallel PUTs, no `Errno 13`, file stays valid (the save-lock fix) |
| Static serving + shared theme | integration | `test_server.py` | index.html, finance-math.js, theme.js wired into both `/` and `/invest` (D11: one `ffa_theme` key) |
| File lifecycle | integration | `test_server.py` | upload→list→download→delete, dedupe, empty rejected |
| Security / errors | integration | `test_server.py` | path-traversal blocked, unknown route 404, invalid JSON 400, delete-missing 404 |
| Backup / restore | integration | `test_server.py` | snapshot→restore roundtrip, missing backup 404 |
| AI graceful-degrade | integration | `test_server.py` | no key → status `available:false`, extract/price → 503 |

### Investments module (`investlib/`, `invest_api.py`, `invest_cli.py`)

Business logic is unit-tested directly against `investlib/*.py` (isolated via
`TempDataMixin`, which repoints `config.DATA_DIR` at a `tempfile.TemporaryDirectory()`
and seeds a small **fake** account registry — a real install ships with none, see
D1 below); the HTTP route wiring in `invest_api.py` is separately exercised end-to-end
in `test_server.py`; the CLI process (`invest_cli.py`) is exercised end-to-end in
`test_cli.py`.

| Area | Type | File | Key cases |
|---|---|---|---|
| Broker CSV parser (Kite/Upstox/Coin header aliases) | unit | `test_investlib.py::TestPortfolioParser` | alias headers, unknown headers, unknown account |
| Live→Portfolio bridge | unit | `test_investlib.py::TestBridge` | inject/replace live rows, tamper-resistant, owner passthrough |
| IPO apply/skip recommendation + reminders | unit | `test_investlib.py::TestIpoRecommendation` | APPLY/NEUTRAL/SKIP/CAUTION thresholds, closing-soon reminders |
| IPO allotment matching + listing P&L | unit | `test_investlib.py::TestIpoAllotment` | matched by symbol, awaiting vs allotted, gain calc |
| Summary / signals / bonds analysis | unit | `test_investlib.py::TestAnalysis` | totals per account/asset class, buy/sell/keep heuristics |
| Wint Wealth xlsx import | unit | `test_investlib.py::TestWintWealthImport` | holding+cashflow combine, duplicate-ISIN lot split, master workbook (all 3 sheets), purchase-date enrichment |
| Past-IPO history (NSE/Chittorgarh/tracker) | unit | `test_investlib.py::TestIpoHistory`, `TestChittorgarhHistory` | dedupe by normalized name, CSV import |
| Upstox Analytics Token status/precedence | unit | `test_investlib.py::TestUpstoxAnalyticsToken` | long-lived token counts as fresh, headless-login skip, mocked `requests.get` sync |
| Unattended token refresh orchestration | unit | `test_investlib.py::TestTokenRefresh` | which accounts need manual login, unconfigured accounts skipped silently — `brokers.sync`/`headless_login`/`token_status` fully mocked |
| **Account registry CRUD** (D1/D2) | unit | `test_investlib.py::TestAccountRegistry` | fresh install = zero accounts, create/reject-duplicate, edit, delete blocked-by-holdings unless forced, old-record migration backfill |
| **Broker-capable accounts are data-driven** (D3) | unit | `test_investlib.py::TestBrokerDataDriven` | `api_accounts()` from the registry (not a hardcoded tuple), empty when no accounts, unknown-account sync/login rejected, `COIN_SOURCE_ACCOUNT` graceful-error vs valid |
| **Missing optional-extras degrade** (D6) | unit | `test_investlib.py::TestMissingExtras` | `kiteconnect`/`pyotp` absent → guided `RuntimeError` (not a raw `ModuleNotFoundError`); `daily_brief.py`/`refresh_tokens.py` push degrades without `requests` |
| Account registry over HTTP | integration | `test_server.py::AccountCrudTests` | full create→edit→delete lifecycle, `?force=1` holdings override, duplicate/bad-id/bad-type all 400 |
| Blank-slate boot (D1/D4/D8) | integration | `test_server.py::BlankSlateInvestTests` | zero accounts/persons/summary/tokens on a fresh data dir, onboarding hero HTML present, `imports/` isolated to the data dir (never the real repo `imports/`) |
| Demo mode is 100% fake (D5) | integration | `test_server.py::DemoModeTests` | seeded holdings/summary, live-row owners are demo persons, invest files stay in the demo sandbox |
| `GET /api/invest/tokens` over HTTP | integration | `test_server.py::InvestTokensHttpTests` | unconfigured / configured-but-no-fresh-token / Upstox long-lived-Analytics-Token branches |
| **OAuth login** `GET /auth/<kite\|upstox>/<account>/login` | integration | `test_server.py::OAuthAndSyncTests` | success (302 to a mocked broker URL), unknown account (guided error), missing `.env` keys (guided error naming the exact var), malformed/unknown auth path (404) |
| **OAuth callback** `GET /auth/<kite\|upstox>/<account>/callback` | integration | `test_server.py::OAuthAndSyncTests` | success kite + upstox (token saved for real, then `brokers.sync` invoked, then 302 to `/invest`), unknown broker kind (404), missing `request_token`/`code` (guided error), broker-rejected bad token (guided error), unknown account after a successful exchange (guided error from the follow-up sync call) |
| **Broker sync** `POST /api/invest/sync/<account>` | integration | `test_server.py::OAuthAndSyncTests` | success (mocked broker layer), unknown account (400), missing `kiteconnect` extras degrades to the guided `requirements-invest.txt` message (not a raw import crash) — forced via `sys.modules` so this is deterministic regardless of what's installed on the host |
| CLI `import` (broker CSV) | integration | `test_cli.py::TestImportSubcommand` | happy path writes `holdings.json` under the temp data dir, missing file → `FileNotFoundError`, unknown account → `ValueError`, nothing written outside the temp dir |
| CLI `wint` (Wint Wealth xlsx) | integration | `test_cli.py::TestWintSubcommand` | happy path (fixture built with the same minimal in-memory xlsx writer as `test_investlib.py`), missing file → `FileNotFoundError`, unknown account → `ValueError`, isolated writes |
| CLI `ipo add`/`list` | integration | `test_cli.py::TestIpoSubcommand` | happy path add-then-list, bad `close_date` → `ValueError` (the closest analogue to "missing file" — this subcommand takes no file input), isolated writes |
| `/docs/*.md` static route | integration | `test_docs.py::DocsRouteTests` | BROKER-SETUP.md / NOTIFICATIONS.md served as `text/plain`, content cross-checked against what's actually on disk, `/invest` links to the local path, `.md`-only extension allowlist, path traversal blocked (both encoded and literal `..`), missing doc → 404 |

## Deliberately NOT covered (and why)

- **Live Gemini calls** — costs quota, needs network, non-deterministic. Parse + fallback
  logic is unit-tested instead; the HTTP boundary is mocked out by testing helpers.
- **Live broker/NSE network calls** (Kite/Upstox OAuth token exchange, `kite.holdings()`,
  Upstox `long-term-holdings`/`ipos`, NSE/Chittorgarh IPO scraping) — every test that
  reaches this code path mocks `investlib.brokers`' network-calling functions
  (`kite_exchange`, `upstox_exchange`, `sync`) or `requests` directly; nothing in the
  suite ever dials out. Real broker/NSE sync is smoke-tested manually (see checklist)
  with a developer's own keys, never in CI.
- **`migrate()` (client schema upgrade)** — runs only in the browser against the `DB`
  global. Not reachable from Node/Python harness without invasive refactor. Covered
  manually (see checklist) by loading old exports.
- **DOM rendering / `PAGES.*` HTML, `/invest` dashboard rendering** — would need a
  headless browser. The data the views consume (both finance and investments) is fully
  unit/integration-tested up to the JSON/HTML-served boundary; rendering bugs are caught
  by the manual smoke pass.
- **CSS / visual layout, dark-mode appearance** — structurally verified (dark rules are
  scoped under `[data-theme="dark"]`, `theme.js` is served and referenced by both pages)
  but not visually screenshotted; manual only.
- **`POST /api/invest/sync/ipos`, `/sync/ipos-upstox`, `/sync/ipo-history`** — these three
  routes are pure network calls with no branching logic of their own to unit-test
  (they just call straight into `ipo_fetch`/`ipo_history`'s NSE/Chittorgarh/Upstox
  fetchers); the fetchers themselves are exercised with mocked `requests`
  (`TestChittorgarhHistory`, `TestUpstoxAnalyticsToken`), the route dispatch pattern is
  identical to the sibling `/sync/<account>` route which *is* tested at the HTTP layer.

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
10. **Investments (`/invest`) — fresh data dir**: `python server.py --data-dir <temp> --port <spare>`
    shows the onboarding hero (zero accounts), all three CTAs (connect a broker / import
    statements / track manually) work, "Add account" shows the computed env-var names +
    exact OAuth redirect URL + a working `/docs/BROKER-SETUP.md` link.
11. **Investments — broker login**: click a broker account's "Login & sync" button with
    real keys in `.env` (never committed) → OAuth round-trip lands back on `/invest` with
    fresh holdings. The `⟳ live` badge shows on synced rows; toggling dark mode here also
    flips the main app's theme (shared `ffa_theme` key) and vice versa.
12. **Investments — CLI**: `python invest_cli.py summary`, `ipo list`, and an `import`/`wint`
    against a real file in `imports/` all print sane output with no traceback.
13. `python server.py --demo` → `/invest` shows 3 fake accounts (Arjun/Priya/Rohan —
    never a real name) with holdings and one demo IPO.

## Coverage targets

- Pure financial/income/gold math: **100% of branches** — these are the money-correctness core.
- Server endpoints (finance + investments + docs): every route has at least one
  happy-path + one error-path case exercised over real HTTP.
- OAuth/broker-sync endpoints: every branch (success, unconfigured, unknown account,
  bad/missing broker response, missing optional extras) is covered with the network
  mocked — never a live call.
- `invest_cli.py` subcommands: happy path + at least one error path (missing file or,
  where no file applies, bad input) per subcommand covered by this plan.
- Sample dataset: every feature toggle/variant the UI can render is represented (asserted by `sample.test.js`).

## Adding tests (mandatory for every feature — see CLAUDE.md rule 6)

Every new feature ships with **both** test coverage and demo data:

- New pure calc → put it in `public/finance-math.js`, export it, add cases to `math.test.js`.
- New finance-tracker endpoint → add happy + error cases to `test_server.py`.
- New investments business logic (`investlib/*.py`) → add cases to `test_investlib.py`,
  using the `TempDataMixin` pattern (never real `data/`, never a real broker/NSE network call).
- New `/api/invest/*` or `/auth/*` route → add happy + error cases to `test_server.py`
  (mock `investlib.brokers`' network-calling functions for anything OAuth/sync-shaped).
- New `invest_cli.py` subcommand → add happy path + error path to `test_cli.py`
  (repoint `config.DATA_DIR`/`config.IMPORTS_DIR` at temp dirs, same isolation idea as
  `TempDataMixin`).
- New AI parse/logic → add cases to `test_gemini.py` (no network).
- New docs page served under `/docs/` → add a case to `test_docs.py`.
- **Demo data** → extend `samples/demo-finances.json` (finance tracker) or
  `server.py::ensure_demo_invest_data()` (investments — keep every seeded owner name in
  the `FAKE_DEMO_OWNERS` allowlist, never a real name), so the feature is visibly
  demoable via `python server.py --demo`, then assert its presence in `sample.test.js`
  or `test_server.py::DemoModeTests` respectively so the demo can't silently lose coverage.

A change is not "done" until `./test.sh` is green AND the feature shows up in the demo dataset.

## FINAL counts (from an actual run — 2026-08-10)

```
python -m unittest discover -s tests -p "test_*.py"
  Ran 210 tests ... OK

node --test tests/math.test.js tests/sample.test.js
  tests 47, pass 47, fail 0
```

Python breakdown (`python -m unittest tests.<module> -v`):

| File | Cases | Notes |
|---|---:|---|
| `test_cli.py` | 11 | `invest_cli.py` import/wint/ipo subcommands |
| `test_docs.py` | 14 | `/docs/*.md` static route |
| `test_gemini.py` | 13 | Gemini parsing and model fallback |
| `test_investlib.py` | 75 | investlib business logic and market-calendar behavior |
| `test_no_extras.py` | 1 | optional live-sync extras forced absent |
| `test_portlib.py` | 20 | shared port-resolution contract |
| `test_server.py` | 76 | finance and investments HTTP behavior |
| **Python total** | **210** | |

Node breakdown: `math.test.js` 33 + `sample.test.js` 14 = **47**.

**Grand total: 257 cases**, all green. No test in the suite touches real `data/`,
`imports/`, `.env`, or a live broker/NSE network — every network-shaped call is mocked
(`investlib.brokers` functions, `requests`) or, for OAuth/sync HTTP tests specifically,
exercised against an **in-process** server boot (`test_server.py::_boot_inprocess_server`)
so `unittest.mock.patch.object` can actually intercept the call — subprocess-booted
servers (used everywhere else in `test_server.py`) can't be mocked from the parent test
process.
