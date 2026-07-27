import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

# A fake account registry the store no longer ships (a real install starts with
# zero accounts — see investlib/store.accounts()). Generic ids + empty owners,
# NO personal data; tests that need accounts get this seeded in setUp.
FAKE_TEST_ACCOUNTS = [
    {"id": "kite-1", "broker": "Zerodha Kite", "broker_type": "kite",
     "asset_class": "stocks", "label": "Kite 1", "owner": ""},
    {"id": "kite-2", "broker": "Zerodha Kite", "broker_type": "kite",
     "asset_class": "stocks", "label": "Kite 2", "owner": ""},
    {"id": "upstox-1", "broker": "Upstox", "broker_type": "upstox",
     "asset_class": "stocks", "label": "Upstox", "owner": ""},
    {"id": "coin-1", "broker": "Coin (Zerodha)", "broker_type": "coin",
     "asset_class": "mutual_funds", "label": "Coin", "owner": ""},
    {"id": "wint-1", "broker": "Wint Wealth", "broker_type": "wint",
     "asset_class": "bonds", "label": "Bond account 1", "owner": ""},
    {"id": "wint-2", "broker": "Wint Wealth", "broker_type": "wint",
     "asset_class": "bonds", "label": "Bond account 2", "owner": ""},
    {"id": "smallcase-1", "broker": "smallcase", "broker_type": "smallcase",
     "asset_class": "managed_stocks", "label": "smallcase", "owner": "",
     "linked_accounts": ["kite-1", "kite-2"]},
]


class TempDataMixin(unittest.TestCase):
    """Point the store at a throwaway data dir so tests never touch real data,
    and seed a generic fake account registry (the store ships none of its own)."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data_dir = config.DATA_DIR
        config.DATA_DIR = Path(self._tmp.name)
        from investlib import store
        store.save("accounts", [dict(a) for a in FAKE_TEST_ACCOUNTS])

    def tearDown(self):
        config.DATA_DIR = self._orig_data_dir
        self._tmp.cleanup()


class TestPortfolioParser(TempDataMixin):
    def _write_csv(self, text: str) -> Path:
        path = Path(self._tmp.name) / "holdings.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_parses_kite_console_style_headers(self):
        from investlib import portfolio
        csv_path = self._write_csv(
            "Symbol,ISIN,Quantity Available,Average Price,Previous Closing Price\n"
            "DEMOA,INE0DEMOA001,10,1400.50,1650.00\n"
            "DEMOB,INE0DEMOB002,5,3200,4100\n"
        )
        rows = portfolio.parse_holdings_csv(csv_path)
        self.assertEqual(len(rows), 2)
        infy = rows[0]
        self.assertEqual(infy["symbol"], "DEMOA")
        self.assertEqual(infy["value"], 16500.0)
        self.assertAlmostEqual(infy["pnl_pct"], 17.82, places=1)

    def test_parses_alias_headers_and_skips_zero_qty(self):
        from investlib import portfolio
        csv_path = self._write_csv(
            "Instrument,Qty.,Avg. cost,LTP\n"
            "RELIANCE,\"1,000\",2500,3000\n"
            "SOLDOUT,0,100,120\n"
        )
        rows = portfolio.parse_holdings_csv(csv_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 1000.0)  # comma-grouped number

    def test_unknown_headers_raise(self):
        from investlib import portfolio
        csv_path = self._write_csv("Foo,Bar\n1,2\n")
        with self.assertRaises(ValueError):
            portfolio.parse_holdings_csv(csv_path)

    def test_import_rejects_unknown_account(self):
        from investlib import portfolio
        csv_path = self._write_csv("Symbol,Qty\nDEMOA,1\n")
        with self.assertRaises(ValueError):
            portfolio.import_holdings("nope", csv_path)

    def test_manual_holdings_for_bonds(self):
        from investlib import portfolio
        snap = portfolio.set_manual_holdings("wint-1", [
            {"symbol": "ABC Bond 11%", "quantity": 10, "avg_price": 10000, "last_price": 10000},
        ])
        self.assertEqual(snap["rows"][0]["value"], 100000.0)
        self.assertEqual(snap["source"], "manual")


class TestBridge(TempDataMixin):
    def test_account_totals_sums_invested_and_falls_back_to_qty_times_last(self):
        from investlib import bridge
        holdings = {
            "kite-1": {"as_of": "2026-01-01", "source": "manual", "rows": [
                {"symbol": "A", "quantity": 10, "avg_price": 100, "last_price": 120, "value": 1200},
                {"symbol": "B", "quantity": 5, "avg_price": 50, "last_price": 60, "value": None},
            ]},
        }
        totals = bridge.account_totals(holdings)
        self.assertEqual(totals["kite-1"]["invested"], 1250.0)       # 10*100 + 5*50
        self.assertEqual(totals["kite-1"]["current"], 1200 + 5 * 60)  # value used, else qty*last_price

    def test_account_totals_uses_total_invested_for_wint_bond_rows(self):
        # Wint Wealth bond rows carry total_invested + value, no avg/last_price.
        from investlib import bridge
        holdings = {
            "wint-1": {"as_of": "2026-01-01", "source": "wint.xlsx", "rows": [
                {"symbol": "BOND-A", "quantity": 2, "total_invested": 20000, "value": 21000},
                {"symbol": "BOND-B", "quantity": 1, "total_invested": 10000, "value": 9800},
            ]},
        }
        totals = bridge.account_totals(holdings)
        self.assertEqual(totals["wint-1"]["invested"], 30000.0)
        self.assertEqual(totals["wint-1"]["current"], 30800.0)

    def test_live_rows_skips_accounts_with_no_owner(self):
        from investlib import bridge
        accounts = [{"id": "kite-1", "label": "Kite 1", "asset_class": "stocks", "owner": ""}]
        holdings = {"kite-1": {"rows": [
            {"symbol": "A", "quantity": 1, "avg_price": 10, "last_price": 10, "value": 10},
        ]}}
        self.assertEqual(bridge.live_rows(accounts, holdings, "2026-01-01T00:00:00"), [])

    def test_live_rows_skips_accounts_with_no_holdings(self):
        from investlib import bridge
        accounts = [{"id": "kite-1", "label": "Kite 1", "asset_class": "stocks", "owner": "Alex"}]
        self.assertEqual(bridge.live_rows(accounts, {}, "2026-01-01T00:00:00"), [])

    def test_live_rows_maps_asset_class_to_subcategory(self):
        from investlib import bridge
        accounts = [
            {"id": "kite-1", "label": "Kite", "asset_class": "stocks", "owner": "Alex"},
            {"id": "coin-1", "label": "Coin", "asset_class": "mutual_funds", "owner": "Alex"},
            {"id": "wint-1", "label": "Wint", "asset_class": "bonds", "owner": "Alex"},
        ]
        holdings = {aid: {"rows": [
            {"symbol": "X", "quantity": 1, "avg_price": 10, "last_price": 10, "value": 10},
        ]} for aid in ("kite-1", "coin-1", "wint-1")}
        rows = {r["investAccountId"]: r for r in bridge.live_rows(accounts, holdings, "now")}
        self.assertEqual(rows["kite-1"]["subCategory"], "Equity")
        self.assertEqual(rows["coin-1"]["subCategory"], "Mutual funds")
        self.assertEqual(rows["wint-1"]["subCategory"], "Debt")
        self.assertTrue(rows["kite-1"]["liveSync"])
        self.assertEqual(rows["kite-1"]["id"], "live-kite-1")

    def test_live_rows_carries_holdings_source_through(self):
        """The holdings snapshot's `source` (kite/upstox API, manual entry, or an
        imported CSV/xlsx filename) must reach the injected liveSync row — the main
        app's Portfolio badge (public/app.js liveSyncBadge()) uses it to tell a real
        broker-API sync apart from manual/CSV/xlsx entry so it doesn't call the
        latter "live" (which reads as broker-synced to a new user)."""
        from investlib import bridge
        accounts = [
            {"id": "kite-1", "label": "Kite", "asset_class": "stocks", "owner": "Alex"},
            {"id": "manual-1", "label": "Manual", "asset_class": "bonds", "owner": "Alex"},
            {"id": "csv-1", "label": "CSV", "asset_class": "stocks", "owner": "Alex"},
            {"id": "unknown-1", "label": "Unknown", "asset_class": "stocks", "owner": "Alex"},
        ]
        holdings = {
            "kite-1": {"source": "kite api", "rows": [
                {"symbol": "X", "quantity": 1, "avg_price": 10, "last_price": 10, "value": 10}]},
            "manual-1": {"source": "manual", "rows": [
                {"symbol": "Y", "quantity": 1, "avg_price": 10, "last_price": 10, "value": 10}]},
            "csv-1": {"source": "holdings.csv", "rows": [
                {"symbol": "Z", "quantity": 1, "avg_price": 10, "last_price": 10, "value": 10}]},
            "unknown-1": {"rows": [  # no "source" key at all — must default, never KeyError
                {"symbol": "W", "quantity": 1, "avg_price": 10, "last_price": 10, "value": 10}]},
        }
        rows = {r["investAccountId"]: r for r in bridge.live_rows(accounts, holdings, "now")}
        self.assertEqual(rows["kite-1"]["source"], "kite api")
        self.assertEqual(rows["manual-1"]["source"], "manual")
        self.assertEqual(rows["csv-1"]["source"], "holdings.csv")
        self.assertEqual(rows["unknown-1"]["source"], "")

    def test_live_rows_skips_smallcase_without_own_holdings_no_double_count(self):
        """smallcase-1's positions live inside kite-1/-2's own holdings — it must
        never get its own live row (that would double-count the same money)."""
        from investlib import bridge
        accounts = [
            {"id": "kite-1", "label": "Kite 1", "asset_class": "stocks", "owner": "Alex"},
            {"id": "smallcase-1", "label": "smallcase", "asset_class": "managed_stocks",
             "owner": "Alex", "linked_accounts": ["kite-1"]},
        ]
        holdings = {"kite-1": {"rows": [
            {"symbol": "A", "quantity": 1, "avg_price": 10, "last_price": 10, "value": 10},
        ]}}
        ids = [r["investAccountId"] for r in bridge.live_rows(accounts, holdings, "now")]
        self.assertEqual(ids, ["kite-1"])

    def test_inject_live_rows_replaces_stale_rows_and_adds_owner_to_persons(self):
        from investlib import bridge, portfolio, store
        accts = store.accounts()
        for a in accts:
            if a["id"] == "kite-1":
                a["owner"] = "Alex"
        store.save("accounts", accts)
        portfolio.set_manual_holdings("kite-1", [
            {"symbol": "A", "quantity": 1, "avg_price": 10, "last_price": 20},
        ])
        doc = {"portfolio": [{"id": "live-kite-1", "liveSync": True, "currentValue": 999999}],
               "settings": {"persons": []}}
        out = bridge.inject_live_rows(doc)
        live = [r for r in out["portfolio"] if r["id"] == "live-kite-1"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["currentValue"], 20.0)  # authoritative, not the tampered 999999
        self.assertIn("Alex", out["settings"]["persons"])

    def test_corrupt_holdings_file_error_names_the_file(self):
        from investlib import store
        path = Path(self._tmp.name) / "holdings.json"
        path.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            store.load("holdings")
        self.assertIn("data/invest/holdings.json", str(ctx.exception))
        self.assertIn(".bak backup may exist", str(ctx.exception))


class TestIpoRecommendation(TempDataMixin):
    def test_thresholds(self):
        from investlib import ipo
        self.assertEqual(ipo.recommend({"sub_total": 25.0})["call"], "APPLY")
        self.assertEqual(ipo.recommend({"sub_total": 3.0})["call"], "NEUTRAL")
        self.assertEqual(ipo.recommend({"sub_total": 0.4})["call"], "SKIP")

    def test_cold_qib_downgrades_to_caution(self):
        from investlib import ipo
        rec = ipo.recommend({"sub_total": 12.0, "sub_qib": 0.6})
        self.assertEqual(rec["call"], "CAUTION")

    def test_upsert_replaces_by_name_and_reminders_window(self):
        from datetime import date, timedelta
        from investlib import ipo
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        ipo.upsert("Acme IPO", tomorrow, sub_total=2.0)
        ipo.upsert("ACME ipo", tomorrow, sub_total=15.0)  # same IPO, updated numbers
        tracked = ipo.tracked()
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["sub_total"], 15.0)
        self.assertEqual(len(ipo.reminders()), 1)

    def test_cross_source_names_fold_to_one_row(self):
        from investlib import ipo
        # NSE spells it "... Limited", Upstox spells it "... IPO" / short — same IPO
        ipo.upsert("Laser Power & Infra Limited", "2026-07-20", sub_total=1.0)
        ipo.upsert("Laser Power & Infra IPO", "2026-07-20", sub_total=1.5, symbol="LASERPOWER")
        tracked = ipo.tracked(include_closed=True)
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["symbol"], "LASERPOWER")
        self.assertEqual(tracked[0]["sub_total"], 1.5)
        # and an application marked under one spelling is found under the other
        ipo.set_applied("Laser Power & Infra Limited", allotment_date="2026-07-22")
        self.assertTrue(ipo.tracked(include_closed=True)[0]["applied"])

    def test_closed_ipos_hidden_by_default(self):
        from investlib import ipo
        ipo.upsert("Old IPO", "2026-01-02", sub_total=50.0)
        self.assertEqual(ipo.tracked(), [])
        self.assertEqual(len(ipo.tracked(include_closed=True)), 1)


class TestIpoAllotment(TempDataMixin):
    def test_set_applied_requires_known_ipo(self):
        from investlib import ipo
        with self.assertRaises(ValueError):
            ipo.set_applied("Ghost IPO")

    def test_upsert_preserves_application_across_resync(self):
        from investlib import ipo
        ipo.upsert("Acme IPO", "2026-07-20", sub_total=2.0)
        ipo.set_applied("Acme IPO", symbol="acme", allotment_date="2026-07-22",
                        listing_date="2026-07-25", applied_lots=1)
        # a later NSE sync refreshes subscription numbers for the same IPO
        ipo.upsert("Acme IPO", "2026-07-20", sub_total=18.0, symbol="ACME")
        rec = ipo.tracked(include_closed=True)[0]
        self.assertEqual(rec["sub_total"], 18.0)       # numbers updated
        self.assertTrue(rec["applied"])                # application preserved
        self.assertEqual(rec["symbol"], "ACME")
        self.assertEqual(rec["allotment_date"], "2026-07-22")

    def test_allotment_status_allotted_from_holdings(self):
        from investlib import ipo, portfolio
        ipo.upsert("Acme IPO", "2026-07-20", sub_total=18.0)
        ipo.set_applied("Acme IPO", symbol="ACME", listing_date="2026-07-25")
        # allotted shares show up in a broker holdings sync (issue price 100, now 150)
        portfolio.set_manual_holdings("kite-1", [
            {"symbol": "ACME", "quantity": 10, "avg_price": 100, "last_price": 150},
        ])
        status = ipo.allotment_status()
        self.assertEqual(len(status), 1)
        row = status[0]
        self.assertEqual(row["status"], "ALLOTTED")
        self.assertEqual(row["account"], "kite-1")
        self.assertEqual(row["got_price"], 100)
        self.assertEqual(row["invested"], 1000.0)
        self.assertEqual(row["current_value"], 1500.0)
        self.assertEqual(row["gain_amount"], 500.0)
        self.assertAlmostEqual(row["gain_pct"], 50.0, places=1)

    def test_allotment_status_awaiting_then_not_allotted(self):
        from datetime import date, timedelta
        from investlib import ipo
        ipo.upsert("Acme IPO", "2026-07-20", sub_total=18.0)
        future = (date.today() + timedelta(days=3)).isoformat()
        ipo.set_applied("Acme IPO", symbol="ACME", allotment_date=future)
        self.assertEqual(ipo.allotment_status()[0]["status"], "AWAITING")
        # once the allotment date has passed with no matching holding → not allotted
        past = (date.today() - timedelta(days=1)).isoformat()
        ipo.set_applied("Acme IPO", allotment_date=past)
        self.assertEqual(ipo.allotment_status()[0]["status"], "NOT_ALLOTTED")

    def test_only_applied_ipos_appear_in_status(self):
        from investlib import ipo
        ipo.upsert("Unapplied IPO", "2026-07-20", sub_total=5.0)
        self.assertEqual(ipo.allotment_status(), [])

    def test_allotment_reminder_fires_on_allotment_day(self):
        from datetime import date
        from investlib import ipo
        ipo.upsert("Acme IPO", "2026-07-20", sub_total=18.0)
        ipo.set_applied("Acme IPO", allotment_date=date.today().isoformat())
        due = ipo.allotment_reminders()
        self.assertEqual([d["name"] for d in due], ["Acme IPO"])

    def test_refresh_from_upstox_populates_tracker_with_symbol(self):
        from investlib import ipo, ipo_fetch, brokers
        fake = {
            "open": [{"name": "Laser Power IPO", "symbol": "LASERPOWER",
                      "issue_type": "regular", "bidding_end_date": "2026-07-13",
                      "total_subscription": "1.5"}],
            "upcoming": [{"name": "SBI MF", "symbol": None, "issue_type": "regular",
                          "bidding_end_date": "2026-07-16", "total_subscription": "0.0"}],
        }
        orig = brokers.upstox_ipos
        brokers.upstox_ipos = lambda account_id, status="open": fake.get(status, [])
        try:
            result = ipo_fetch.refresh_from_upstox()
        finally:
            brokers.upstox_ipos = orig
        self.assertEqual(result["count"], 2)
        tracked = {i["name"]: i for i in ipo.tracked(include_closed=True)}
        self.assertEqual(tracked["Laser Power IPO"]["symbol"], "LASERPOWER")
        self.assertEqual(tracked["Laser Power IPO"]["close_date"], "2026-07-13")
        self.assertEqual(tracked["Laser Power IPO"]["sub_total"], 1.5)
        self.assertNotIn("symbol", tracked["SBI MF"])  # null symbol never clobbers

    def test_upstox_refresh_then_applied_matches_without_manual_symbol(self):
        """The whole point: Upstox gives the symbol, so allotment matches a
        holding even though the user never typed a symbol."""
        from investlib import ipo, ipo_fetch, brokers, portfolio
        fake = [{"name": "Laser Power IPO", "symbol": "LASERPOWER", "issue_type": "regular",
                 "bidding_end_date": "2026-07-13", "total_subscription": "1.5"}]
        orig = brokers.upstox_ipos
        brokers.upstox_ipos = lambda account_id, status="open": fake if status == "open" else []
        try:
            ipo_fetch.refresh_from_upstox()
        finally:
            brokers.upstox_ipos = orig
        ipo.set_applied("Laser Power IPO")  # no symbol passed — comes from Upstox
        portfolio.set_manual_holdings("upstox-1", [
            {"symbol": "LASERPOWER", "quantity": 20, "avg_price": 214, "last_price": 260},
        ])
        row = ipo.allotment_status()[0]
        self.assertEqual(row["status"], "ALLOTTED")
        self.assertEqual(row["got_price"], 214)


class TestAnalysis(TempDataMixin):
    def test_summary_and_concentration_signal(self):
        from investlib import analysis, portfolio
        portfolio.set_manual_holdings("kite-1", [
            {"symbol": "BIGPOS", "quantity": 100, "avg_price": 100, "last_price": 100},  # 10k
            {"symbol": "SMALL", "quantity": 10, "avg_price": 100, "last_price": 100},    # 1k
        ])
        s = analysis.summary()
        self.assertEqual(s["total"], 11000.0)
        self.assertIn("upstox-1", s["accounts_without_data"])
        flagged = {f["symbol"] for f in analysis.signals()}
        self.assertIn("BIGPOS", flagged)   # ~91% of portfolio
        self.assertNotIn("SMALL", flagged)

    def test_summary_per_account_carries_source(self):
        # JOB-5 fix: the per_account entry must surface the snapshot's `source`
        # (manual / kite api / a CSV filename) so invest.html's Accounts-table
        # "Source" column stops rendering blank for every account.
        from investlib import analysis, portfolio, store
        portfolio.set_manual_holdings("kite-1", [
            {"symbol": "SRCTEST", "quantity": 1, "avg_price": 100, "last_price": 120},
        ])
        # a CSV-imported snapshot keeps its filename as the source
        holdings = store.load("holdings", default={})
        holdings["kite-2"] = {"as_of": "2026-01-01", "source": "kite-console.csv",
                              "rows": [{"symbol": "CSVTEST", "quantity": 1, "avg_price": 10,
                                        "last_price": 12, "value": 12, "pnl_pct": 20.0}]}
        store.save("holdings", holdings)
        s = analysis.summary()
        self.assertEqual(s["per_account"]["kite-1"]["source"], "manual")
        self.assertEqual(s["per_account"]["kite-2"]["source"], "kite-console.csv")

    def test_drawdown_and_runup_signals(self):
        from investlib import analysis, portfolio
        portfolio.set_manual_holdings("kite-2", [
            {"symbol": "LOSER", "quantity": 10, "avg_price": 100, "last_price": 60},   # -40%
            {"symbol": "WINNER", "quantity": 10, "avg_price": 100, "last_price": 200}, # +100%
        ])
        reasons = {f["symbol"]: "; ".join(f["reasons"]) for f in analysis.signals()}
        self.assertIn("re-check the thesis", reasons["LOSER"])
        self.assertIn("profit booking", reasons["WINNER"])

    def test_bonds_lists_only_bond_accounts_sorted_by_maturity(self):
        from investlib import analysis, portfolio, wintwealth
        portfolio.set_manual_holdings("kite-1", [
            {"symbol": "NOTABOND", "quantity": 1, "avg_price": 100, "last_price": 100},
        ])
        header = [
            "Name Of Bond", "ISIN", "Maturity Date", "Units", "YTM", "Current Value",
            "Upcoming Sell Value", "Upcoming interest", "Upcoming Principal", "Total Invested",
            "Total Sold", "Principal Repaid till Date", "Interest Paid (Before TDS Deduction) till Date",
            "Interest Paid (After TDS Deduction) till Date", "TDS Deducted till Date",
            "Principal Repayment Type", "Interest Repayment Type",
        ]
        holding_path = Path(self._tmp.name) / "holding.xlsx"
        _make_xlsx(holding_path, wintwealth.HOLDING_SHEET, [
            header,
            ["Later Bond", "INE000000001", "01-01-2028", "1.0", "10.0", "10000.0",
             "", "1000.0", "10000.0", "10000.0", "", "", "0.0", "0.0", "0.0", "At Maturity", "Monthly"],
            ["Sooner Bond", "INE000000002", "01-01-2027", "1.0", "10.0", "10000.0",
             "", "1000.0", "10000.0", "10000.0", "", "", "0.0", "0.0", "0.0", "At Maturity", "Monthly"],
        ])
        portfolio.import_wint_statement("wint-1", holding_path)

        result = analysis.bonds()
        self.assertEqual([b["symbol"] for b in result], ["Sooner Bond", "Later Bond"])
        self.assertTrue(all(b["account"] == "wint-1" for b in result))

    def test_bonds_needing_refresh_flags_stale_accounts_only(self):
        from datetime import date, timedelta
        from investlib import analysis, portfolio, store

        holdings = store.load("holdings", default={})
        holdings["wint-1"] = {"as_of": (date.today() - timedelta(days=45)).isoformat(),
                                  "source": "manual", "rows": []}
        holdings["wint-2"] = {"as_of": date.today().isoformat(), "source": "manual", "rows": []}
        store.save("holdings", holdings)

        stale = analysis.bonds_needing_refresh()
        self.assertEqual([s["account"] for s in stale], ["wint-1"])
        self.assertGreaterEqual(stale[0]["days_stale"], 30)


def _make_xlsx(path: Path, sheet_name: str, rows: list) -> None:
    """Minimal single-sheet .xlsx (inline strings, no sharedStrings.xml) for testing xlsx_lite/wintwealth."""
    import zipfile

    def cell_xml(col_num, row_num, value):
        col_letter = ""
        n = col_num
        while n > 0:
            n, rem = divmod(n - 1, 26)
            col_letter = chr(65 + rem) + col_letter
        ref = f"{col_letter}{row_num}"
        if value == "":
            return ""
        try:
            float(value)
            return f'<c r="{ref}"><v>{value}</v></c>'
        except ValueError:
            escaped = str(value).replace("&", "&amp;").replace("<", "&lt;")
            return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'

    sheet_rows_xml = "".join(
        f'<row r="{i + 1}">' + "".join(cell_xml(j + 1, i + 1, v) for j, v in enumerate(row)) + "</row>"
        for i, row in enumerate(rows)
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{sheet_rows_xml}</sheetData></worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _make_xlsx_multi(path: Path, sheets: list) -> None:
    """Minimal multi-sheet .xlsx (inline strings). `sheets` is [(name, rows), ...],
    mirroring Wint's 'master' workbook (Holding + Cashflow + Investment Summary)."""
    import zipfile

    def col_letter(col_num):
        s, n = "", col_num
        while n > 0:
            n, rem = divmod(n - 1, 26)
            s = chr(65 + rem) + s
        return s

    def cell_xml(col_num, row_num, value):
        ref = f"{col_letter(col_num)}{row_num}"
        if value == "":
            return ""
        try:
            float(value)
            return f'<c r="{ref}"><v>{value}</v></c>'
        except ValueError:
            escaped = str(value).replace("&", "&amp;").replace("<", "&lt;")
            return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'

    def sheet_xml(rows):
        body = "".join(
            f'<row r="{i + 1}">' + "".join(cell_xml(j + 1, i + 1, v) for j, v in enumerate(row)) + "</row>"
            for i, row in enumerate(rows)
        )
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{body}</sheetData></worksheet>')

    sheet_tags = "".join(
        f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, (name, _) in enumerate(sheets))
    workbook_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    f'<sheets>{sheet_tags}</sheets></workbook>')
    rel_tags = "".join(
        f'<Relationship Id="rId{i + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     f'{rel_tags}</Relationships>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" '
                 'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                 'Target="xl/workbook.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        for i, (_, rows) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", sheet_xml(rows))


class TestWintWealthImport(TempDataMixin):
    def test_build_bond_rows_combines_holding_and_cashflow(self):
        from investlib import wintwealth

        holding_path = Path(self._tmp.name) / "holding.xlsx"
        _make_xlsx(holding_path, wintwealth.HOLDING_SHEET, [
            ["Name Of Bond", "ISIN", "Maturity Date", "Units", "YTM", "Current Value",
             "Upcoming Sell Value", "Upcoming interest", "Upcoming Principal", "Total Invested",
             "Total Sold", "Principal Repaid till Date", "Interest Paid (Before TDS Deduction) till Date",
             "Interest Paid (After TDS Deduction) till Date", "TDS Deducted till Date",
             "Principal Repayment Type", "Interest Repayment Type"],
            ["Demo Bond Beta", "INE0BETA0002", "15-11-2028", "1.0", "11.5", "9950.00",
             "", "500.0", "10000.0", "9000.00", "", "", "400.0", "360.0", "40.0",
             "Staggered", "Quarterly"],
            ["Total", "", "", "", "11.5", "9950.00", "", "500.0", "10000.0", "9000.00"],
        ])
        cashflow_path = Path(self._tmp.name) / "cashflow.xlsx"
        _make_xlsx(cashflow_path, wintwealth.CASHFLOW_SHEET, [
            ["Name Of Bond", "ISIN", "Date", "Amount in Bank", "Principal Repaid",
             "Interest Paid (After TDS deduction)", "Interest Paid (Before TDS deduction)", "TDS",
             "Sell Value", "Transactiontype"],
            ["Demo Bond Beta", "INE0BETA0002", "15-11-2027", "297.0", "", "297.0", "330.0", "33.0", "", "INTEREST"],
            ["Demo Bond Beta", "INE0BETA0002", "15-11-2028", "4162.0", "4000.0", "162.0", "180.0", "18.0", "",
             "PRINCIPAL AND INTEREST"],
        ])

        rows = wintwealth.build_bond_rows(holding_path, cashflow_path)
        self.assertEqual(len(rows), 1)
        bond = rows[0]
        self.assertEqual(bond["symbol"], "Demo Bond Beta")
        self.assertEqual(bond["maturity_date"], "2028-11-15")
        self.assertEqual(bond["value"], 9950.00)
        self.assertAlmostEqual(bond["pnl_pct"], (9950.00 - 9000.00) / 9000.00 * 100, places=2)
        self.assertEqual(bond["upcoming_interest_after_tds"], 459.0)  # 297.0 + 162.0
        self.assertEqual(bond["next_cashflow_date"], "2027-11-15")
        self.assertEqual(bond["next_cashflow_amount"], 297.0)
        self.assertEqual(bond["expected_total_remaining"], round(459.0 + 10000.0, 2))

    def test_import_wint_statement_stores_snapshot(self):
        from investlib import portfolio, wintwealth

        holding_path = Path(self._tmp.name) / "holding.xlsx"
        _make_xlsx(holding_path, wintwealth.HOLDING_SHEET, [
            ["Name Of Bond", "ISIN", "Maturity Date", "Units", "YTM", "Current Value",
             "Upcoming Sell Value", "Upcoming interest", "Upcoming Principal", "Total Invested",
             "Total Sold", "Principal Repaid till Date", "Interest Paid (Before TDS Deduction) till Date",
             "Interest Paid (After TDS Deduction) till Date", "TDS Deducted till Date",
             "Principal Repayment Type", "Interest Repayment Type"],
            ["Demo Bond Alpha", "INE0ALPHA001", "20-12-2028", "1.0", "11.5", "8900.00",
             "", "600.0", "10000.0", "8450.00", "", "", "300.0", "270.0", "30.0",
             "At Maturity", "Monthly"],
        ])

        snap = portfolio.import_wint_statement("wint-1", holding_path)
        self.assertEqual(snap["rows"][0]["symbol"], "Demo Bond Alpha")
        self.assertEqual(snap["rows"][0]["value"], 8900.00)

    def test_duplicate_isin_lots_split_cashflow_instead_of_collapsing(self):
        """A monthly bond SIP can buy the same ISIN in different months —
        each Holding Statement row is a separate lot, not a duplicate, and
        the shared ISIN's cashflow rows must be split across both lots."""
        from investlib import wintwealth

        header = [
            "Name Of Bond", "ISIN", "Maturity Date", "Units", "YTM", "Current Value",
            "Upcoming Sell Value", "Upcoming interest", "Upcoming Principal", "Total Invested",
            "Total Sold", "Principal Repaid till Date", "Interest Paid (Before TDS Deduction) till Date",
            "Interest Paid (After TDS Deduction) till Date", "TDS Deducted till Date",
            "Principal Repayment Type", "Interest Repayment Type",
        ]
        holding_path = Path(self._tmp.name) / "holding.xlsx"
        _make_xlsx(holding_path, wintwealth.HOLDING_SHEET, [
            header,
            ["Demo Bond Gamma", "INE0GAMMA003", "18-09-2028", "1.0", "11.15", "9500.00",
             "", "900.0", "10000.0", "8700.00", "", "", "200.0", "180.0", "20.0",
             "At Maturity", "Monthly"],
            ["Demo Bond Gamma", "INE0GAMMA003", "18-09-2028", "1.0", "11.25", "9400.00",
             "", "900.0", "10000.0", "8600.00", "", "", "250.0", "225.0", "25.0",
             "At Maturity", "Monthly"],
        ])
        cashflow_path = Path(self._tmp.name) / "cashflow.xlsx"
        _make_xlsx(cashflow_path, wintwealth.CASHFLOW_SHEET, [
            ["Name Of Bond", "ISIN", "Date", "Amount in Bank", "Principal Repaid",
             "Interest Paid (After TDS deduction)", "Interest Paid (Before TDS deduction)", "TDS",
             "Sell Value", "Transactiontype"],
            ["Demo Bond Gamma", "INE0GAMMA003", "12-09-2026", "120.0", "", "120.0", "132.0", "12.0", "",
             "INTEREST"],
            ["Demo Bond Gamma", "INE0GAMMA003", "12-09-2026", "120.0", "", "120.0", "132.0", "12.0", "",
             "INTEREST"],
        ])

        rows = wintwealth.build_bond_rows(holding_path, cashflow_path)
        self.assertEqual(len(rows), 2)  # both lots kept, not collapsed to one
        # equal upcoming_interest_before_tds (900.0 each) -> even 50/50 split of the combined 240.0
        for row in rows:
            self.assertAlmostEqual(row["upcoming_interest_after_tds"], 120.0, places=2)
            self.assertEqual(row["next_cashflow_date"], "2026-09-12")
            self.assertAlmostEqual(row["next_cashflow_amount"], 120.0, places=2)

    _HOLDING_HEADER_ROW = [
        "Name Of Bond", "ISIN", "Maturity Date", "Units", "YTM", "Current Value",
        "Upcoming Sell Value", "Upcoming interest", "Upcoming Principal", "Total Invested",
        "Total Sold", "Principal Repaid till Date", "Interest Paid (Before TDS Deduction) till Date",
        "Interest Paid (After TDS Deduction) till Date", "TDS Deducted till Date",
        "Principal Repayment Type", "Interest Repayment Type",
    ]
    _INVEST_HEADER_ROW = [
        "Bond Name", "ISIN", "No. Of Units", "Invested Amount", "Face Value", "Acquisition Cost*",
        "Date of Investment", "Maturity Date", "XIRR", "Frequency of Interest Payment",
        "Frequency Of Principal Repayment",
    ]

    def test_parse_investment_summary_reads_repeated_fy_blocks(self):
        from investlib import wintwealth
        path = Path(self._tmp.name) / "master.xlsx"
        _make_xlsx_multi(path, [(wintwealth.INVEST_SHEET, [
            [], ["", "", "", "", "CONSOLIDATED INVESTMENT REPORT"],
            ["Name", "Alex Kumar"], [],
            ["", "", "", "", "INVESTMENT SUMMARY (1/04/2025 to 31/03/2026)"],
            self._INVEST_HEADER_ROW,
            ["Demo Bond Alpha", "INE0ALPHA001", "1.0", "8450.00", "10000.0", "-",
             "18/03/2026", "20/12/2028", "11.5%", "Monthly", "At Maturity"],
            ["", "Total: ", "1.0", "8450.00", "10000.0", "-"],
            [],
            ["", "", "", "", "INVESTMENT SUMMARY (1/04/2026 to 31/03/2027)"],
            self._INVEST_HEADER_ROW,
            ["Demo Bond Beta", "INE0BETA0002", "1.0", "9000.00", "10000.0", "50.00",
             "10/02/2026", "15/11/2028", "11.5%", "Quarterly", "Staggered"],
            ["", "Total: ", "1.0", "9000.00", "10000.0", "50.00"],
            [], ["Date of report generation:", "08/07/2026"],
        ])])
        entries = wintwealth.parse_investment_summary(path)
        self.assertEqual(len(entries), 2)  # both FY blocks, preamble/Total/report rows skipped
        by_isin = {e["isin"]: e for e in entries}
        self.assertEqual(by_isin["INE0ALPHA001"]["purchase_date"], "2026-03-18")
        self.assertEqual(by_isin["INE0ALPHA001"]["xirr_pct"], 11.5)
        self.assertEqual(by_isin["INE0BETA0002"]["purchase_date"], "2026-02-10")
        self.assertEqual(by_isin["INE0BETA0002"]["principal_frequency"], "Staggered")

    def test_master_workbook_enriches_each_lot_with_its_own_purchase_date(self):
        """The master workbook carries Holding + Cashflow + Investment Summary;
        passing it alone must auto-derive cashflows and match each SIP lot to its
        own buy date by ISIN + invested amount."""
        from investlib import wintwealth
        holding = [
            self._HOLDING_HEADER_ROW,
            ["Demo Bond Gamma", "INE0GAMMA003", "18-09-2028", "1.0", "11.15", "9500.00",
             "", "900.0", "10000.0", "8700.00", "", "", "200.0", "180.0", "20.0",
             "At Maturity", "Monthly"],
            ["Demo Bond Gamma", "INE0GAMMA003", "18-09-2028", "1.0", "11.25", "9400.00",
             "", "900.0", "10000.0", "8600.00", "", "", "250.0", "225.0", "25.0",
             "At Maturity", "Monthly"],
        ]
        cashflow = [
            ["Name Of Bond", "ISIN", "Date", "Amount in Bank", "Principal Repaid",
             "Interest Paid (After TDS deduction)", "Interest Paid (Before TDS deduction)", "TDS",
             "Sell Value", "Transactiontype"],
            ["Demo Bond Gamma", "INE0GAMMA003", "12-09-2026", "120.0", "", "120.0", "132.0", "12.0", "", "INTEREST"],
            ["Demo Bond Gamma", "INE0GAMMA003", "12-09-2026", "120.0", "", "120.0", "132.0", "12.0", "", "INTEREST"],
        ]
        invest = [
            ["", "", "", "", "INVESTMENT SUMMARY (1/04/2025 to 31/03/2026)"],
            self._INVEST_HEADER_ROW,
            ["Demo Bond Gamma", "INE0GAMMA003", "1.0", "8600.00", "10000.0", "-",
             "22/01/2026", "18-09-2028".replace("-", "/"), "11.25%", "Monthly", "At Maturity"],
            ["Demo Bond Gamma", "INE0GAMMA003", "1.0", "8700.00", "10000.0", "-",
             "14/03/2026", "18/09/2028", "11.15%", "Monthly", "At Maturity"],
            ["", "Total: ", "2.0", "17300.00", "20000.0", "-"],
        ]
        master = Path(self._tmp.name) / "master.xlsx"
        _make_xlsx_multi(master, [
            (wintwealth.HOLDING_SHEET, holding),
            (wintwealth.CASHFLOW_SHEET, cashflow),
            (wintwealth.INVEST_SHEET, invest),
        ])

        rows = wintwealth.build_bond_rows(master)  # master alone → all three sheets used
        self.assertEqual(len(rows), 2)
        by_invested = {round(r["total_invested"], 2): r for r in rows}
        # lot matched to its own buy date + XIRR by invested amount, not collapsed to earliest
        self.assertEqual(by_invested[8700.00]["purchase_date"], "2026-03-14")
        self.assertEqual(by_invested[8700.00]["xirr_pct"], 11.15)
        self.assertEqual(by_invested[8600.00]["purchase_date"], "2026-01-22")
        self.assertEqual(by_invested[8600.00]["xirr_pct"], 11.25)
        # cashflow was auto-derived from the same workbook
        for r in rows:
            self.assertEqual(r["next_cashflow_date"], "2026-09-12")

    def test_plain_holding_statement_has_no_purchase_date(self):
        from investlib import wintwealth
        path = Path(self._tmp.name) / "holding.xlsx"
        _make_xlsx(path, wintwealth.HOLDING_SHEET, [
            self._HOLDING_HEADER_ROW,
            ["Demo Bond Alpha", "INE0ALPHA001", "20-12-2028", "1.0", "11.5", "8900.00",
             "", "600.0", "10000.0", "8450.00", "", "", "300.0", "270.0", "30.0",
             "At Maturity", "Monthly"],
        ])
        rows = wintwealth.build_bond_rows(path)
        self.assertNotIn("purchase_date", rows[0])  # no summary sheet → nothing invented


class TestIpoHistory(TempDataMixin):
    def _write_csv(self, text: str) -> Path:
        path = Path(self._tmp.name) / "ipohist.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_parse_nse_issues_segment_dates_and_price_band(self):
        from investlib import ipo_history
        recs = ipo_history.parse_nse_issues([
            {"companyName": "Alpha Ltd", "symbol": "ALPHA", "securityType": "EQ",
             "ipoStartDate": "01-MAR-2024", "ipoEndDate": "05-MAR-2024",
             "listingDate": "12-MAR-2024", "issuePrice": "100"},
            {"company": "Beta SME", "symbol": "BETA", "securityType": "SME",
             "ipoEndDate": "12-JAN-2023", "listingDate": "-", "priceRange": "90-95"},
            # real NSE shape: issuePrice "-" and an "Rs." price band. The "Rs."
            # dot must not be read as a decimal (regression: "Rs.99" -> 0.99).
            {"companyName": "Gamma Ltd", "symbol": "GAMMA", "securityType": "SME",
             "ipoEndDate": "07-JUL-2026", "listingDate": "10-JUL-2026",
             "issuePrice": "-", "priceRange": "Rs.94 to Rs.99"},
            {"foo": "bar"},  # no name -> skipped
        ])
        self.assertEqual(len(recs), 3)
        alpha, beta, gamma = recs
        self.assertEqual(alpha["segment"], "Mainboard")
        self.assertEqual(alpha["issue_price"], 100.0)
        self.assertEqual(alpha["listing_date"], "2024-03-12")
        self.assertEqual(beta["segment"], "SME")
        self.assertEqual(beta["issue_price"], 95.0)   # top of the band
        self.assertIsNone(beta["listing_date"])       # "-" -> None
        self.assertEqual(gamma["issue_price"], 99.0)  # top of "Rs.94 to Rs.99", not 0.99

    def test_csv_import_computes_listing_gain(self):
        from investlib import ipo_history
        self._run_import(ipo_history)
        rec = {r["name"]: r for r in ipo_history.records()}["Gamma Technologies Limited"]
        self.assertEqual(rec["issue_price"], 200.0)
        self.assertEqual(rec["listing_price"], 250.0)
        self.assertEqual(rec["listing_gain_pct"], 25.0)  # (250-200)/200
        self.assertEqual(rec["segment"], "SME")
        self.assertEqual(rec["sub_total"], 42.5)
        self.assertEqual(rec["year"], 2025)

    def _run_import(self, ipo_history):
        csv_path = self._write_csv(
            "IPO Name,Type,Issue Price,Listing Price,Listing Date,Subscription,QIB\n"
            "Gamma Technologies Limited,SME,\"₹200\",250,15-05-2025,42.5x,80\n"
        )
        return ipo_history.import_csv(csv_path)

    def test_explicit_gain_column_wins_over_computed(self):
        from investlib import ipo_history
        csv_path = self._write_csv(
            "Company,Issue Price,Listing Price,Listing Gains\n"
            "Delta Corp,100,130,12%\n"  # explicit 12% overrides the 30% the prices imply
        )
        ipo_history.import_csv(csv_path)
        self.assertEqual(ipo_history.records()[0]["listing_gain_pct"], 12.0)

    def test_nse_then_csv_merge_by_normalized_name(self):
        """NSE (symbol, dates, issue price) and a CSV (name only, listing price)
        for the same IPO must fold into one row despite Ltd vs Limited."""
        from investlib import ipo_history
        ipo_history._upsert_all(ipo_history.parse_nse_issues([
            {"companyName": "Epsilon Ltd", "symbol": "EPS", "securityType": "EQ",
             "ipoEndDate": "10-06-2025", "listingDate": "18-06-2025", "issuePrice": "300"},
        ]), source="nse")
        csv_path = self._write_csv(
            "Company,Listing Price,Subscription\n"
            "Epsilon Limited,360,15\n"
        )
        ipo_history.import_csv(csv_path)

        recs = ipo_history.records()
        self.assertEqual(len(recs), 1)              # merged, not duplicated
        rec = recs[0]
        self.assertEqual(rec["symbol"], "EPS")      # kept from NSE
        self.assertEqual(rec["issue_price"], 300.0)  # kept from NSE
        self.assertEqual(rec["listing_price"], 360.0)  # filled from CSV
        self.assertEqual(rec["listing_gain_pct"], 20.0)  # (360-300)/300
        self.assertEqual(sorted(rec["sources"]), ["csv", "nse"])

    def test_carry_from_tracker_folds_in_closed_ipo_subscription(self):
        from datetime import date, timedelta
        from investlib import ipo, ipo_history
        ipo.upsert("Zeta Ltd", (date.today() - timedelta(days=5)).isoformat(), sub_total=33.0, sub_qib=50.0)
        ipo.upsert("Future Ltd", (date.today() + timedelta(days=5)).isoformat(), sub_total=9.0)
        ipo_history.carry_from_tracker()
        names = {r["name"]: r for r in ipo_history.records()}
        self.assertIn("Zeta Ltd", names)             # closed -> carried in
        self.assertNotIn("Future Ltd", names)         # still open -> not yet
        self.assertEqual(names["Zeta Ltd"]["sub_total"], 33.0)


class TestChittorgarhHistory(TempDataMixin):
    def test_parse_chittorgarh_rows_maps_price_and_subscription(self):
        from investlib import ipo_history
        recs = ipo_history.parse_chittorgarh_rows([
            {"Company": "Indo Farm Equipment Ltd.", "Issue Category": "Mainboard",
             "Issue Price (Rs.)": 215, "Listing Date": "07-Jan-2025",
             "Open Price on Listing (Rs.)": 256, "Close Price on Listing (Rs.)": 273.69,
             "QIB (x)": 97.11, "NII (x)": 498.49, "Retail (x)": 102.57, "Total (x)": 159.23,
             "~id": 1934},
            {"Issue Category": "SME"},  # no company -> skipped
        ])
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["segment"], "Mainboard")
        self.assertEqual(r["issue_price"], 215.0)
        self.assertEqual(r["listing_price"], 273.69)  # listing-day CLOSE, unadjusted
        self.assertEqual(r["listing_date"], "2025-01-07")
        self.assertEqual(r["sub_total"], 159.23)
        self.assertEqual(r["sub_qib"], 97.11)

    def test_key_folds_company_and_co_variants(self):
        from investlib import ipo_history
        # NSE "Company Limited" vs Chittorgarh "Co.Ltd." must key identically
        self.assertEqual(ipo_history._key("IC Electricals Company Limited"),
                         ipo_history._key("IC Electricals Co.Ltd."))
        self.assertEqual(ipo_history._key("Tata Capital Ltd."),
                         ipo_history._key("Tata Capital Limited"))
        # a real trailing word that isn't a corporate form is preserved
        self.assertEqual(ipo_history._key("Reliance Industries Ltd"), "reliance industries")

    def test_chittorgarh_fills_price_and_sub_onto_nse_row(self):
        """A Chittorgarh row folds onto the NSE-sourced row by name, filling the
        listing price + subscription NSE never had, and the gain is computed."""
        from investlib import ipo_history
        ipo_history._upsert_all(ipo_history.parse_nse_issues([
            {"companyName": "Indo Farm Equipment Limited", "symbol": "INDOFARM",
             "securityType": "EQ", "ipoEndDate": "02-01-2025",
             "listingDate": "07-01-2025", "issuePrice": "215"},
        ]), source="nse")
        ipo_history._upsert_all(ipo_history.parse_chittorgarh_rows([
            {"Company": "Indo Farm Equipment Ltd.", "Issue Category": "Mainboard",
             "Issue Price (Rs.)": 215, "Listing Date": "07-Jan-2025",
             "Close Price on Listing (Rs.)": 273.69, "Total (x)": 159.23, "QIB (x)": 97.11,
             "~id": 1934},
        ]), source="chittorgarh")

        recs = ipo_history.records()
        self.assertEqual(len(recs), 1)                       # merged, not duplicated
        r = recs[0]
        self.assertEqual(r["symbol"], "INDOFARM")            # kept from NSE
        self.assertEqual(r["listing_price"], 273.69)         # filled from Chittorgarh (close)
        self.assertEqual(r["sub_total"], 159.23)             # filled from Chittorgarh
        self.assertEqual(r["listing_gain_pct"], 27.3)        # (273.69-215)/215
        self.assertEqual(sorted(r["sources"]), ["chittorgarh", "nse"])


class TestUpstoxAnalyticsToken(TempDataMixin):
    def test_status_marks_analytics_token_long_lived_and_fresh(self):
        from unittest import mock
        from investlib import brokers
        with mock.patch.dict("os.environ", {"UPSTOX_1_API_KEY": "k", "UPSTOX_1_ANALYTICS_TOKEN": "AT"}):
            st = brokers.token_status()["upstox-1"]
        self.assertTrue(st["configured"])
        self.assertTrue(st["token_fresh"])   # no daily login needed
        self.assertTrue(st["long_lived"])

    def test_token_prefers_analytics_then_falls_back_to_daily(self):
        from unittest import mock
        from investlib import brokers
        brokers.save_token("upstox-1", "DAILY")
        with mock.patch.dict("os.environ", {"UPSTOX_1_ANALYTICS_TOKEN": "ANALYTICS"}):
            self.assertEqual(brokers.upstox_token("upstox-1"), "ANALYTICS")
        import os
        with mock.patch.dict(os.environ):          # restored on exit
            os.environ.pop("UPSTOX_1_ANALYTICS_TOKEN", None)
            self.assertEqual(brokers.upstox_token("upstox-1"), "DAILY")

    def test_headless_login_noops_with_token_raises_without(self):
        from unittest import mock
        import os
        from investlib import brokers
        with mock.patch.dict("os.environ", {"UPSTOX_1_ANALYTICS_TOKEN": "AT"}):
            self.assertIsNone(brokers.headless_login("upstox-1"))  # nothing to do
        with mock.patch.dict(os.environ):
            os.environ.pop("UPSTOX_1_ANALYTICS_TOKEN", None)
            with self.assertRaises(NotImplementedError):
                brokers.headless_login("upstox-1")

    def test_sync_sends_analytics_token_as_bearer(self):
        from unittest import mock
        from investlib import brokers

        class FakeResp:
            def raise_for_status(self): pass
            def json(self):
                return {"data": [{"trading_symbol": "DEMOA", "isin": "INE0",
                                  "quantity": 2, "average_price": 100, "last_price": 150}]}

        # Patch the whole `requests` module attribute on `brokers`, not an
        # attribute *on* it (e.g. brokers.requests.get) — with no extras
        # installed, brokers.requests is the _MissingRequests shim, whose
        # __getattr__ raises RuntimeError, and mock.patch.object(brokers.requests,
        # "get", ...) needs to getattr("get") on it to save the original before
        # patching. Patching the module-level name itself is a dict lookup on
        # `brokers.__dict__`, never touching the shim's __getattr__ — so this
        # test exercises real coverage in both the with- and without-extras envs.
        fake_requests = mock.MagicMock()
        fake_requests.get.return_value = FakeResp()

        with mock.patch.dict("os.environ", {"UPSTOX_1_ANALYTICS_TOKEN": "AT123"}), \
                mock.patch.object(brokers, "requests", fake_requests):
            result = brokers.upstox_sync("upstox-1")
        _, kwargs = fake_requests.get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer AT123")
        self.assertEqual(result["upstox-1"], 1)


class TestTokenRefresh(TempDataMixin):
    """refresh_tokens.refresh_all decides which accounts still need a manual
    login. All broker calls are stubbed — the orchestration never touches the
    network or real credentials."""

    def _run(self, *, configured, login):
        """configured: {account_id: bool}; login: account_id -> None | raises."""
        from unittest import mock
        import refresh_tokens
        from investlib import brokers

        status = {aid: {"configured": configured.get(aid, True)}
                  for aid, _ in brokers.api_accounts()}
        with mock.patch.object(brokers, "token_status", return_value=status), \
                mock.patch.object(brokers, "headless_login", side_effect=login), \
                mock.patch.object(brokers, "sync", return_value={}):
            return refresh_tokens.refresh_all()

    def test_upstox_and_failed_kite_need_manual_login(self):
        def login(account_id):
            if account_id == "upstox-1":
                raise NotImplementedError  # no headless path
            if account_id == "kite-2":
                raise RuntimeError("wrong TOTP")  # auto-login flaked
            # kite-1 succeeds

        self.assertEqual(sorted(self._run(configured={}, login=login)),
                         ["kite-2", "upstox-1"])

    def test_all_kite_ok_only_upstox_manual(self):
        def login(account_id):
            if account_id == "upstox-1":
                raise NotImplementedError

        self.assertEqual(self._run(configured={}, login=login), ["upstox-1"])

    def test_unconfigured_accounts_are_skipped_silently(self):
        calls = []

        def login(account_id):
            calls.append(account_id)  # only configured accounts should reach here

        manual = self._run(configured={"kite-2": False, "upstox-1": False}, login=login)
        self.assertEqual(calls, ["kite-1"])
        self.assertEqual(manual, [])  # unconfigured != needs-manual


class TestAccountRegistry(TempDataMixin):
    """D1/D2: a fresh install has zero accounts; full create/edit/delete CRUD;
    old records migrate forward."""

    def setUp(self):
        super().setUp()
        from investlib import store
        store.save("accounts", [])  # start from a blank registry

    def test_missing_registry_file_returns_empty(self):
        from investlib import store
        (Path(self._tmp.name) / "accounts.json").unlink()
        self.assertEqual(store.accounts(), [])  # no seeded example/personal accounts

    def test_create_account_and_reject_duplicate(self):
        from investlib import store
        acct = store.create_account("kite-1", "kite", label="My Kite", owner="Jordan")
        self.assertEqual(acct["broker_type"], "kite")
        self.assertEqual(acct["asset_class"], "stocks")
        self.assertEqual([a["id"] for a in store.accounts()], ["kite-1"])
        with self.assertRaises(ValueError):
            store.create_account("kite-1", "kite")  # duplicate id

    def test_create_rejects_bad_id_and_type(self):
        from investlib import store
        with self.assertRaises(ValueError):
            store.create_account("Bad Id", "kite")       # spaces/uppercase
        with self.assertRaises(ValueError):
            store.create_account("x-1", "notabroker")     # unknown broker type
        with self.assertRaises(ValueError):
            store.create_account("", "kite")              # empty id

    def test_create_rejects_overlong_id_but_accepts_max_length(self):
        # JOB-5 fix: cap the id length so a giant id can't blow out the Accounts
        # table width. The existing char-set/dupe/empty checks stay intact (above).
        from investlib import store
        ok_id = "k" + "1" * (store.ID_MAX_LEN - 1)        # exactly 40 chars — allowed
        self.assertEqual(len(ok_id), store.ID_MAX_LEN)
        store.create_account(ok_id, "kite")
        with self.assertRaises(ValueError) as ctx:
            store.create_account("k" + "1" * store.ID_MAX_LEN, "kite")  # 41 chars — rejected
        self.assertIn("40", str(ctx.exception))

    def test_update_label_and_owner(self):
        from investlib import store
        store.create_account("kite-1", "kite")
        store.update_account("kite-1", label="Renamed", owner="Sam")
        a = store.accounts()[0]
        self.assertEqual(a["label"], "Renamed")
        self.assertEqual(a["owner"], "Sam")
        with self.assertRaises(ValueError):
            store.update_account("ghost", label="x")

    def test_delete_blocks_with_holdings_unless_forced(self):
        from investlib import portfolio, store
        store.create_account("kite-1", "kite")
        portfolio.set_manual_holdings("kite-1", [
            {"symbol": "A", "quantity": 1, "avg_price": 10, "last_price": 10}])
        with self.assertRaises(ValueError):
            store.delete_account("kite-1")               # blocked: has holdings
        store.delete_account("kite-1", force=True)        # force drops it + holdings
        self.assertEqual(store.accounts(), [])
        self.assertNotIn("kite-1", store.load("holdings", default={}))

    def test_delete_empty_account_ok(self):
        from investlib import store
        store.create_account("manual-1", "manual")
        store.delete_account("manual-1")
        self.assertEqual(store.accounts(), [])

    def test_migration_backfills_broker_type_and_owner(self):
        from investlib import store
        # a record saved before the broker_type/owner fields existed
        store.save("accounts", [{"id": "kite-9", "broker": "Zerodha Kite",
                                 "asset_class": "stocks", "label": "old"}])
        a = store.accounts()[0]
        self.assertEqual(a["broker_type"], "kite")  # inferred from the id prefix
        self.assertEqual(a["owner"], "")


class TestBrokerDataDriven(TempDataMixin):
    """D3: broker-capable accounts come from the store, not a hardcoded tuple."""

    def test_api_accounts_from_registry(self):
        from investlib import brokers
        self.assertEqual(set(brokers.api_accounts()),
                         {("kite-1", "kite"), ("kite-2", "kite"), ("upstox-1", "upstox")})

    def test_api_accounts_empty_when_no_accounts(self):
        from investlib import brokers, store
        store.save("accounts", [])
        self.assertEqual(brokers.api_accounts(), [])

    def test_sync_and_login_reject_unknown_account(self):
        from investlib import brokers, store
        store.save("accounts", [])
        with self.assertRaises(ValueError):
            brokers.sync("kite-1")       # not in registry -> no API sync
        with self.assertRaises(ValueError):
            brokers.login_url("kite-1")  # not in registry -> no API login

    def test_coin_source_issue_flags_bad_source_but_not_valid(self):
        from unittest import mock
        from investlib import brokers
        # auto-seed includes a coin account, so a source is required
        with mock.patch.object(brokers, "COIN_SOURCE_ACCOUNT", "kite-1"):
            self.assertIsNone(brokers.coin_source_issue())     # valid kite source
        with mock.patch.object(brokers, "COIN_SOURCE_ACCOUNT", "ghost-1"):
            msg = brokers.coin_source_issue()
            self.assertIsNotNone(msg)
            self.assertIn("ghost-1", msg)

    def test_coin_source_issue_none_without_coin_account(self):
        from unittest import mock
        from investlib import brokers, store
        store.save("accounts", [a for a in store.accounts() if a["broker_type"] != "coin"])
        with mock.patch.object(brokers, "COIN_SOURCE_ACCOUNT", "ghost-1"):
            self.assertIsNone(brokers.coin_source_issue())  # no coin acct -> nothing to warn


class TestMissingExtras(unittest.TestCase):
    """D6: optional broker extras degrade to a clear, guided error — never a raw
    ModuleNotFoundError or a crash."""

    def test_kite_missing_kiteconnect_gives_guided_error(self):
        import sys
        from unittest import mock
        from investlib import brokers
        with mock.patch.dict(sys.modules, {"kiteconnect": None}):
            with self.assertRaises(RuntimeError) as ctx:
                brokers._kite("kite-1")
        self.assertIn("requirements-invest.txt", str(ctx.exception))

    def test_kite_headless_missing_pyotp_gives_guided_error(self):
        import sys
        from unittest import mock
        from investlib import brokers
        with mock.patch.dict(sys.modules, {"pyotp": None}):
            with self.assertRaises(RuntimeError) as ctx:
                brokers.kite_headless_login("kite-1")
        self.assertIn("requirements-invest.txt", str(ctx.exception))

    def test_daily_brief_push_without_requests_prints_only(self):
        from unittest import mock
        import daily_brief
        with mock.patch.object(daily_brief, "requests", None), \
                mock.patch.dict("os.environ", {"NTFY_TOPIC": "topic"}):
            daily_brief.push("hello")  # must not raise

    def test_refresh_tokens_push_without_requests_prints_only(self):
        from unittest import mock
        import refresh_tokens
        with mock.patch.object(refresh_tokens, "requests", None), \
                mock.patch.dict("os.environ", {"NTFY_TOPIC": "topic"}):
            refresh_tokens._push("title", "msg")  # must not raise


if __name__ == "__main__":
    unittest.main()
