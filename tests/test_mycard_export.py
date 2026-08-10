"""Synthetic-only tests for the explicit MyCard Benefits card handoff."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mycard_export


def synthetic_source(*, extra_card_field=False, network="VISA", status=""):
    card = {
        "id": "synthetic-card-01",
        "name": "SYNTHETIC-ONLY Card",
        "bank": "SYNTHETIC-ONLY Bank",
        "owner": "SYNTHETIC-ONLY Owner",
        "type": "Credit",
        "variant": network,
        "number": "SYNTHETIC-ONLY-PAN",
        "expiry": "2030-05-01",
        "cvv": "SYNTHETIC-ONLY-CVV",
        "pin": "SYNTHETIC-ONLY-PIN",
        "fees": 0,
        "benefits": "SYNTHETIC-ONLY benefit",
        "lounge": "",
        "loungeCriteria": "",
        "status": status,
    }
    if extra_card_field:
        card["unexpected"] = "SYNTHETIC-ONLY"
    return {
        "schemaVersion": 1,
        "settings": {"appName": "SYNTHETIC-ONLY"},
        "cards": [card],
        "ledgers": [{"transactions": [{"amount": 987654, "notes": "SYNTHETIC-ONLY ledger"}]}],
        "documents": [{"notes": "SYNTHETIC-ONLY document"}],
    }


class MyCardExportTests(unittest.TestCase):
    def test_build_export_retains_only_exact_schema_and_card_fields(self):
        payload = mycard_export.build_card_only_export(json.dumps(synthetic_source()))
        self.assertEqual(set(payload), {"schemaVersion", "cards"})
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(len(payload["cards"]), 1)
        self.assertEqual(set(payload["cards"][0]), mycard_export.CARD_FIELDS)
        encoded = json.dumps(payload)
        self.assertNotIn("ledgers", encoded)
        self.assertNotIn("transactions", encoded)
        self.assertNotIn("987654", encoded)
        self.assertNotIn("SYNTHETIC-ONLY ledger", encoded)

    def test_unknown_card_field_is_rejected_instead_of_silently_exported(self):
        with self.assertRaises(mycard_export.ExportRejected):
            mycard_export.build_card_only_export(json.dumps(synthetic_source(extra_card_field=True)))

    def test_adapter_incompatible_network_and_status_are_rejected(self):
        for kwargs in ({"network": "Priority Pass"}, {"status": "retired"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(mycard_export.ExportRejected):
                mycard_export.build_card_only_export(json.dumps(synthetic_source(**kwargs)))

    def test_atomic_cli_writes_new_file_and_never_prints_card_values(self):
        with tempfile.TemporaryDirectory(prefix="ffa-mycard-export-") as tmp:
            source = Path(tmp) / "source.json"
            output = Path(tmp) / "mycard-card-only.json"
            source.write_text(json.dumps(synthetic_source()), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = mycard_export.main(["--source", str(source), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            self.assertEqual(stderr.getvalue(), "")
            self.assertNotIn("SYNTHETIC-ONLY-PAN", stdout.getvalue())
            self.assertNotIn("SYNTHETIC-ONLY Owner", stdout.getvalue())
            self.assertEqual(mycard_export.build_card_only_export(output.read_text(encoding="utf-8"))["cards"],
                             json.loads(output.read_text(encoding="utf-8"))["cards"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(mycard_export.main(["--source", str(source), "--output", str(output)]), 2)

    def test_source_and_output_paths_are_explicit_and_output_does_not_exist(self):
        with tempfile.TemporaryDirectory(prefix="ffa-mycard-export-") as tmp:
            source = Path(tmp) / "source.json"
            source.write_text(json.dumps(synthetic_source()), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = mycard_export.main(["--source", str(source), "--output", str(source)])
            self.assertEqual(code, 2)
            self.assertTrue(os.path.isfile(source))
            self.assertNotIn("SYNTHETIC-ONLY-PAN", stderr.getvalue())

    def test_junction_or_reparse_source_is_rejected_before_opening(self):
        with tempfile.TemporaryDirectory(prefix="ffa-mycard-export-") as tmp:
            source = Path(tmp) / "source.json"
            source.write_text(json.dumps(synthetic_source()), encoding="utf-8")
            # This is synthetic rather than creating a real junction, so the
            # test remains portable and never needs administrator privileges.
            with mock.patch.object(Path, "is_junction", return_value=True, create=True):
                with self.assertRaises(mycard_export.ExportRejected):
                    mycard_export._read_source(source)

    def test_source_identity_swap_after_open_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ffa-mycard-export-") as tmp:
            source = Path(tmp) / "source.json"
            source.write_text(json.dumps(synthetic_source()), encoding="utf-8")
            real_fstat = os.fstat

            def swapped_identity(descriptor):
                info = real_fstat(descriptor)
                values = list(info)
                values[1] = values[1] + 1  # st_ino in os.stat_result's stable tuple layout
                return os.stat_result(values)

            with mock.patch("mycard_export.os.fstat", side_effect=swapped_identity):
                with self.assertRaises(mycard_export.ExportRejected):
                    mycard_export._read_source(source)

    def test_output_parent_swap_after_temp_creation_fails_closed_and_cleans_up(self):
        with tempfile.TemporaryDirectory(prefix="ffa-mycard-export-") as tmp:
            source = Path(tmp) / "source.json"
            output = Path(tmp) / "handoff.json"
            source.write_text(json.dumps(synthetic_source()), encoding="utf-8")
            # The first assertion follows the source descriptor open; the
            # second is the output-parent recheck immediately before linking.
            with mock.patch.object(
                mycard_export,
                "_assert_chain_unchanged",
                side_effect=[None, mycard_export.ExportRejected("local path changed while opening")],
            ):
                with self.assertRaises(mycard_export.ExportRejected):
                    mycard_export.write_card_only_export(source, output)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(tmp).glob(".handoff.json.*.tmp")), [])
