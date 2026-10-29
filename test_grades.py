import copy
import csv
import http.client
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from grades import SheetError, parse_sheet
from tracker import Store


HEADERS = ["N° Alumno", "AC1", "AC2", "Nota Cátedra", "I1", "I2", "Examen", "Nota\nE. Escritas", "Nota\nPresentación", "Bonificación\nECA", "Nota\nFinal", "Nota\nBanner"]


def fixture(rows=None, headers=None):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["", "Cátedra"])
    writer.writerow(HEADERS if headers is None else headers)
    writer.writerows(rows if rows is not None else [
        ["26663279", "5,9", "0", "5,41", "6,2", "0", "0", "1,86", "3,28", "0", "3,28", "3,3"],
        ["26666237J", "0", "0", "1", "3,8", "0", "0", "1,14", "1,2", "0", "1,2", "1,2"],
    ])
    return stream.getvalue()


class ParsingTests(unittest.TestCase):
    def test_decimal_comma_multiline_headers_and_string_ids(self):
        parsed = parse_sheet(fixture())
        self.assertEqual(parsed["students"][0]["grades"]["catedra"], 5.41)
        self.assertEqual(parsed["students"][1]["id"], "26666237J")

    def test_inactive_columns_not_in_mean_but_individual_zero_is(self):
        parsed = parse_sheet(fixture())
        by_key = {m["key"]: m for m in parsed["metrics"]}
        self.assertFalse(by_key["ac2"]["available"])
        self.assertFalse(by_key["i2"]["available"])
        self.assertEqual(parsed["students"][0]["grades"]["activities"], 5.9)
        self.assertEqual(parsed["students"][1]["grades"]["activities"], 0)

    def test_missing_grade_is_not_zero(self):
        text = fixture().replace("5,9", "")
        parsed = parse_sheet(text)
        self.assertIsNone(parsed["students"][0]["grades"]["ac1"])
        self.assertIsNone(parsed["students"][0]["grades"]["activities"])

    def test_partial_mean_is_unavailable(self):
        rows = [["26663279", "5,9", "", "5", "6", "0", "0", "1", "3", "0", "3", "3"], ["26666237J", "2", "3", "4", "4", "0", "0", "1", "2", "0", "2", "2"]]
        parsed = parse_sheet(fixture(rows))
        self.assertIsNone(parsed["students"][0]["grades"]["activities"])
        self.assertEqual(parsed["students"][1]["grades"]["activities"], 2.5)

    def test_new_activity_becomes_available_when_results_arrive(self):
        before = parse_sheet(fixture())
        rows = [["26663279", "5,9", "4", "5,41", "6,2", "0", "0", "1,86", "3,28", "0", "3,28", "3,3"], ["26666237J", "0", "0", "1", "3,8", "0", "0", "1,14", "1,2", "0", "1,2", "1,2"]]
        after = parse_sheet(fixture(rows))
        self.assertFalse(next(m for m in before["metrics"] if m["key"] == "ac2")["available"])
        self.assertTrue(next(m for m in after["metrics"] if m["key"] == "ac2")["available"])
        self.assertEqual(after["students"][0]["grades"]["activities"], 4.95)
        self.assertEqual(after["students"][1]["grades"]["activities"], 0)

    def test_all_zero_and_empty_columns(self):
        rows = [["26663279"] + ["0"] * 11]
        parsed = parse_sheet(fixture(rows))
        self.assertFalse(any(m["available"] for m in parsed["metrics"]))
        self.assertIsNone(parsed["students"][0]["grades"]["activities"])

    def test_reject_bad_values_and_structure(self):
        valid = fixture()
        broken = ["<html>login</html>", "not a sheet", valid.replace('"5,9"', '"NaN"'), valid.replace('"5,9"', '"-1"'), valid.replace('"5,9"', '"#REF!"'), valid.replace("26666237J", "26663279"), valid.replace("Nota Cátedra", "Different"), valid + "12345678,1\n"]
        for text in broken:
            with self.subTest(text=text[:40]), self.assertRaises(SheetError):
                parse_sheet(text)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.data = parse_sheet(fixture())
        self.store = Store(loader=lambda: copy.deepcopy(self.data))

    def refresh(self):
        self.store.last_attempt = 0
        return self.store.get(refresh=True)

    def test_cache_delta_and_unchanged_refresh(self):
        first = self.store.get()
        self.assertIsNone(first["previous_at"])
        self.data["students"][0]["grades"]["final"] = 4
        changed = self.refresh()
        self.assertEqual(changed["students"][0]["changes"]["final"], 0.72)
        self.assertEqual(changed["previous_at"], first["fetched_at"])
        unchanged = self.refresh()
        self.assertEqual(unchanged["students"][0]["changes"], changed["students"][0]["changes"])
        self.assertEqual(unchanged["changed_at"], changed["changed_at"])

    def test_failed_fetch_keeps_stale_copy(self):
        self.store.get()
        def fail():
            raise urllib.error.URLError("offline")
        self.store.loader = fail
        result = self.refresh()
        self.assertTrue(result["stale"])
        self.assertTrue(result["error"])
        self.assertEqual(len(result["students"]), 2)

    def test_first_failure_is_explicit(self):
        def fail():
            raise SheetError("Invalid CSV")
        with self.assertRaises(SheetError):
            Store(loader=fail).get()

    def test_returned_data_cannot_mutate_cache(self):
        result = self.store.get()
        result["students"].clear()
        self.assertEqual(len(self.store.get()["students"]), 2)

    def test_disk_cache_is_marked_stale_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grades.json"
            Store(path, loader=lambda: copy.deepcopy(self.data)).get()
            loaded = Store(path).get()
            self.assertTrue(loaded["stale"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_incompatible_or_incomplete_cache_is_discarded(self):
        good = self.store.get()
        malformed = [[], {}, {"version": 9, "data": good}, {"version": 1, "data": {"students": [], "metrics": [1], "fetched_at": "yesterday"}}]
        for field in ("changed_at", "grades", "id"):
            broken = copy.deepcopy(good)
            if field == "changed_at":
                del broken[field]
            else:
                del broken["students"][0][field]
            malformed.append({"version": 1, "data": broken})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grades.json"
            for data in malformed:
                with self.subTest(data=data):
                    path.write_text(json.dumps(data), encoding="utf-8")
                    store = Store(path, loader=lambda: copy.deepcopy(self.data))
                    self.assertIsNone(store.payload)
                    self.assertFalse(store.get()["stale"])

    def test_interrupted_download_returns_stale_or_explicit_error(self):
        def fail():
            raise http.client.IncompleteRead(b"partial", 100)
        self.store.get()
        self.store.loader = fail
        self.assertTrue(self.refresh()["stale"])
        with self.assertRaises(SheetError):
            Store(loader=fail).get()

    def test_failed_save_does_not_create_false_changes(self):
        self.store.get()
        self.data["students"][0]["grades"]["final"] = 4
        with patch.object(self.store, "save", side_effect=OSError("read-only")):
            changed = self.refresh()
            unchanged = self.refresh()
        self.assertIsNotNone(unchanged["storage_warning"])
        self.assertEqual(unchanged["changed_at"], changed["changed_at"])
        self.assertEqual(unchanged["students"][0]["changes"], {"final": 0.72})
        self.assertEqual(unchanged["warnings"], self.data["warnings"])


if __name__ == "__main__":
    unittest.main()
