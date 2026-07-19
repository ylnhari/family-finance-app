"""CLI tests for invest_cli.py (import / ipo / wint subcommands). Zero
dependencies (stdlib unittest).

Run from the project root:
    python -m unittest discover -s tests -p "test_*.py" -v

invest_cli.py has no --data-dir flag of its own (unlike server.py) — it
always reads/writes through the module-level config.DATA_DIR / config.
IMPORTS_DIR globals. So the only way to keep it off the real data/ and
imports/ folders is to repoint those globals at throwaway temp dirs for the
duration of each test and restore them afterwards — the same isolation idea
as tests/test_investlib.py's TempDataMixin, just covering both dirs and
driving the actual CLI (invest_cli.main()) end-to-end instead of calling
investlib functions directly.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile  # noqa: F401  (imported for parity/clarity with _make_xlsx's use)
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config       # noqa: E402
import invest_cli   # noqa: E402

REAL_INVEST_DIR = (Path(ROOT) / "data" / "invest").resolve()
REAL_IMPORTS_DIR = (Path(ROOT) / "imports").resolve()


def _make_xlsx(path: Path, sheet_name: str, rows: list) -> None:
    """Minimal single-sheet .xlsx (inline strings, no sharedStrings.xml) —
    just enough for investlib/xlsx_lite.py to read back. Mirrors the helper
    of the same name in tests/test_investlib.py (kept local here so this
    file has no cross-test-file dependency)."""

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


_WINT_HOLDING_HEADER = [
    "Name Of Bond", "ISIN", "Maturity Date", "Units", "YTM", "Current Value",
    "Upcoming Sell Value", "Upcoming interest", "Upcoming Principal", "Total Invested",
    "Total Sold", "Principal Repaid till Date", "Interest Paid (Before TDS Deduction) till Date",
    "Interest Paid (After TDS Deduction) till Date", "TDS Deducted till Date",
    "Principal Repayment Type", "Interest Repayment Type",
]


class CliTestCase(unittest.TestCase):
    """Repoints config.DATA_DIR / config.IMPORTS_DIR at throwaway temp dirs
    and seeds a small fake account registry. Never touches real data/ or
    imports/ — no test in this file reads or lists either real directory."""

    def setUp(self):
        self._data_tmp = tempfile.TemporaryDirectory(prefix="fft-cli-data-")
        self._imports_tmp = tempfile.TemporaryDirectory(prefix="fft-cli-imports-")
        self._orig_data_dir = config.DATA_DIR
        self._orig_imports_dir = config.IMPORTS_DIR
        config.DATA_DIR = Path(self._data_tmp.name)
        config.IMPORTS_DIR = Path(self._imports_tmp.name)
        from investlib import store
        store.save("accounts", [
            {"id": "kite-cli", "broker": "Zerodha Kite", "broker_type": "kite",
             "asset_class": "stocks", "label": "CLI Kite", "owner": ""},
            {"id": "wint-cli", "broker": "Wint Wealth", "broker_type": "wint",
             "asset_class": "bonds", "label": "CLI Wint", "owner": ""},
        ])

    def tearDown(self):
        config.DATA_DIR = self._orig_data_dir
        config.IMPORTS_DIR = self._orig_imports_dir
        self._data_tmp.cleanup()
        self._imports_tmp.cleanup()

    def _import_path(self, name: str) -> Path:
        return Path(self._imports_tmp.name) / name

    def _run_cli(self, *argv) -> str:
        """Run invest_cli.main() with argv, capturing and returning stdout."""
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["invest_cli.py", *argv]), \
                contextlib.redirect_stdout(out):
            invest_cli.main()
        return out.getvalue()

    def _assert_writes_stay_in_temp_data_dir(self):
        """Every file the CLI could have written lives under our temp data
        dir, and that dir is provably not the real data/invest/ path (path
        comparison only — the real dir's contents are never read)."""
        written = list(Path(self._data_tmp.name).rglob("*"))
        self.assertTrue(written, "expected the CLI to have written something")
        for p in written:
            self.assertTrue(str(p.resolve()).startswith(str(Path(self._data_tmp.name).resolve())))
        self.assertNotEqual(Path(self._data_tmp.name).resolve(), REAL_INVEST_DIR)
        self.assertNotEqual(Path(self._imports_tmp.name).resolve(), REAL_IMPORTS_DIR)


class TestImportSubcommand(CliTestCase):
    """python invest_cli.py import <account> <csv-in-imports/>"""

    def _write_csv(self, name, symbol="FAKECO", isin="INE000FAKE01",
                   qty="10", avg="100", ltp="120"):
        path = self._import_path(name)
        path.write_text(
            "Symbol,ISIN,Quantity Available,Average Price,Previous Closing Price\n"
            f"{symbol},{isin},{qty},{avg},{ltp}\n",
            encoding="utf-8",
        )
        return path

    def test_happy_path_imports_csv_into_temp_data_dir(self):
        self._write_csv("holdings.csv")
        out = self._run_cli("import", "kite-cli", "holdings.csv")
        self.assertIn("imported 1 positions into kite-cli", out)

        holdings_file = Path(self._data_tmp.name) / "holdings.json"
        self.assertTrue(holdings_file.is_file())
        data = json.loads(holdings_file.read_text(encoding="utf-8"))
        self.assertEqual(data["kite-cli"]["rows"][0]["symbol"], "FAKECO")
        self.assertEqual(data["kite-cli"]["rows"][0]["quantity"], 10)

    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self._run_cli("import", "kite-cli", "does-not-exist.csv")
        # a failed import must not create a partial holdings.json
        self.assertFalse((Path(self._data_tmp.name) / "holdings.json").exists())

    def test_unknown_account_raises_value_error(self):
        self._write_csv("holdings.csv")
        with self.assertRaises(ValueError):
            self._run_cli("import", "not-a-real-account", "holdings.csv")

    def test_writes_land_only_under_temp_data_dir(self):
        self._write_csv("holdings.csv")
        self._run_cli("import", "kite-cli", "holdings.csv")
        self._assert_writes_stay_in_temp_data_dir()


class TestWintSubcommand(CliTestCase):
    """python invest_cli.py wint <account> <holding-xlsx> [--cashflow] [--summary]"""

    def _write_holding_xlsx(self, name="holding.xlsx", bond_name="Fake Bond Co",
                            isin="INE000FAKEBD1"):
        path = self._import_path(name)
        _make_xlsx(path, "Holding Statement", [
            _WINT_HOLDING_HEADER,
            [bond_name, isin, "29-08-2030", "1.0", "10.0", "10100.0",
             "", "500.0", "10000.0", "10000.0", "", "", "200.0", "180.0", "20.0",
             "At Maturity", "Quarterly"],
        ])
        return path

    def test_happy_path_imports_bond_xlsx(self):
        self._write_holding_xlsx()
        out = self._run_cli("wint", "wint-cli", "holding.xlsx")
        self.assertIn("imported 1 bonds into wint-cli", out)

        holdings_file = Path(self._data_tmp.name) / "holdings.json"
        self.assertTrue(holdings_file.is_file())
        data = json.loads(holdings_file.read_text(encoding="utf-8"))
        self.assertEqual(data["wint-cli"]["rows"][0]["symbol"], "Fake Bond Co")
        self.assertEqual(data["wint-cli"]["rows"][0]["isin"], "INE000FAKEBD1")

    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self._run_cli("wint", "wint-cli", "does-not-exist.xlsx")
        self.assertFalse((Path(self._data_tmp.name) / "holdings.json").exists())

    def test_unknown_account_raises_value_error(self):
        self._write_holding_xlsx()
        with self.assertRaises(ValueError):
            self._run_cli("wint", "not-a-real-account", "holding.xlsx")

    def test_writes_land_only_under_temp_data_dir(self):
        self._write_holding_xlsx()
        self._run_cli("wint", "wint-cli", "holding.xlsx")
        self._assert_writes_stay_in_temp_data_dir()


class TestIpoSubcommand(CliTestCase):
    """python invest_cli.py ipo add|list ... (no file input — 'missing file'
    doesn't apply, so the error-path case here is the closest analogue: a
    bad close_date, invest_cli's own input-validation failure)."""

    def test_happy_path_add_then_list(self):
        out = self._run_cli("ipo", "add", "Fake Foods IPO", "2099-01-02",
                            "--total", "12.4", "--retail", "3.1", "--gmp", "25")
        self.assertIn('"name": "Fake Foods IPO"', out)

        ipos_file = Path(self._data_tmp.name) / "ipos.json"
        self.assertTrue(ipos_file.is_file())
        data = json.loads(ipos_file.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "Fake Foods IPO")
        self.assertEqual(data[0]["sub_total"], 12.4)

        out_list = self._run_cli("ipo", "list")
        self.assertIn("Fake Foods IPO", out_list)

    def test_add_bad_close_date_raises(self):
        with self.assertRaises(ValueError):
            self._run_cli("ipo", "add", "Bad Date IPO", "not-a-date")
        # a rejected add must not create ipos.json at all
        self.assertFalse((Path(self._data_tmp.name) / "ipos.json").exists())

    def test_writes_land_only_under_temp_data_dir(self):
        self._run_cli("ipo", "add", "Fake Retail IPO", "2099-01-02")
        self._assert_writes_stay_in_temp_data_dir()


if __name__ == "__main__":
    unittest.main(verbosity=2)
