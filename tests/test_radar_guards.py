import re
import unittest
from unittest.mock import patch

from modules.daily_briefing.collector import CollectedData, SourceResult
from modules.daily_briefing import radar


class _Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Values:
    def __init__(self, rows=None, corrupt_appended_column=None):
        self.rows = [list(row) for row in (rows or [])]
        self.corrupt_appended_column = corrupt_appended_column
        self.append_calls = 0
        self.update_calls = 0

    def get(self, *, spreadsheetId, range):
        if range.endswith("A2:J100000"):
            return _Request({"values": [list(row) for row in self.rows]})
        if range.endswith("A2:A100000"):
            return _Request({"values": [[row[0]] for row in self.rows if row]})
        match = re.search(r"!A(\d+):J\d+$", range)
        if match:
            index = int(match.group(1)) - 2
            values = [list(self.rows[index])] if 0 <= index < len(self.rows) else []
            if values and self.corrupt_appended_column is not None:
                values[0][self.corrupt_appended_column] = "CORRUPTED"
            return _Request({"values": values})
        raise AssertionError(f"unexpected range: {range}")

    def append(self, *, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.append_calls += 1
        self.rows.append(list(body["values"][0]))
        row_number = len(self.rows) + 1
        return _Request({"updates": {"updatedRange": f"'Радар — нові документи'!A{row_number}:J{row_number}"}})

    def update(self, **kwargs):
        self.update_calls += 1
        raise AssertionError("repeat detection must not overwrite an existing row")


class _Sheets:
    def __init__(self, rows=None, corrupt_appended_column=None):
        self.values_api = _Values(rows, corrupt_appended_column)

    def spreadsheets(self):
        return self

    def values(self):
        return self.values_api


def _record(url="https://moz.gov.ua/order/123?utm_source=test", change_status="NEW"):
    return {
        "source": "moz_ukraine",
        "order_no": "123",
        "doc_date": "2026-10-10",
        "url": url,
        "title": "Наказ №123",
        "document_type": "наказ",
        "change_status": change_status,
    }


def _collected(*records):
    result = SourceResult(
        source="researcher",
        status="success",
        checked_at="2026-10-10T10:00:00Z",
        window_start="2026-10-09T10:00:00Z",
        window_end="2026-10-10T10:00:00Z",
        records=list(records),
        connection_verified=True,
    )
    return CollectedData("run_test", "2026-10-10T10:00:00Z", {"researcher": result})


class RadarGuardTests(unittest.TestCase):
    def test_http_403_is_partial_unresolved_and_never_written(self):
        sheets = _Sheets()
        with patch.object(radar, "_verify_official_source", return_value=("HTTP 403", False)), \
             patch.object(radar, "_sheets_service", return_value=sheets):
            result = radar.process_radar(_collected(_record()))

        self.assertEqual("partial", result["radar_status"])
        self.assertEqual(0, result["radar_source_verified"])
        self.assertEqual(0, result["radar_write_verified"])
        self.assertEqual(1, result["radar_unresolved_count"])
        self.assertEqual(0, sheets.values_api.append_calls)
        self.assertEqual(0, sheets.values_api.update_calls)

    def test_verified_new_row_separates_source_and_write_verification(self):
        sheets = _Sheets()
        with patch.object(radar, "_verify_official_source", return_value=("verified", True)), \
             patch.object(radar, "_sheets_service", return_value=sheets):
            result = radar.process_radar(_collected(_record()))

        self.assertEqual("success", result["radar_status"])
        self.assertEqual(1, result["radar_source_verified"])
        self.assertEqual(1, result["radar_write_verified"])
        self.assertEqual(1, result["radar_persistence_verified"])
        self.assertEqual(1, sheets.values_api.append_calls)

    def test_repeat_preserves_existing_state_and_does_not_write(self):
        existing = radar._build_row(_record(change_status="ACTIVE"), "verified earlier", "original")
        sheets = _Sheets([existing])
        with patch.object(radar, "_verify_official_source", return_value=("verified", True)), \
             patch.object(radar, "_sheets_service", return_value=sheets):
            result = radar.process_radar(_collected(_record(change_status="NEW")))

        self.assertEqual("success", result["radar_status"])
        self.assertEqual(1, result["radar_rows_unchanged"])
        self.assertEqual(0, result["radar_write_verified"])
        self.assertEqual(1, result["radar_persistence_verified"])
        self.assertEqual("ACTIVE", sheets.values_api.rows[0][7])
        self.assertEqual(0, sheets.values_api.append_calls)
        self.assertEqual(0, sheets.values_api.update_calls)

    def test_tracking_variants_dedupe_by_document_and_canonical_url(self):
        sheets = _Sheets()
        first = _record("https://moz.gov.ua/order/123?utm_source=a")
        second = _record("https://moz.gov.ua/order/123?fbclid=b")
        with patch.object(radar, "_verify_official_source", return_value=("verified", True)), \
             patch.object(radar, "_sheets_service", return_value=sheets):
            result = radar.process_radar(_collected(first, second))

        self.assertEqual("success", result["radar_status"])
        self.assertEqual(1, result["radar_source_verified"])
        self.assertEqual(1, sheets.values_api.append_calls)

    def test_provenance_conflict_is_unresolved_not_overwritten_or_duplicated(self):
        existing = radar._build_row(
            _record("https://moz.gov.ua/order/123-old", change_status="ACTIVE"),
            "verified earlier",
            "original",
        )
        sheets = _Sheets([existing])
        with patch.object(radar, "_verify_official_source", return_value=("verified", True)), \
             patch.object(radar, "_sheets_service", return_value=sheets):
            result = radar.process_radar(
                _collected(_record("https://moz.gov.ua/order/123-new"))
            )

        self.assertEqual("partial", result["radar_status"])
        self.assertEqual(1, result["radar_unresolved_count"])
        self.assertEqual(0, sheets.values_api.append_calls)
        self.assertEqual(0, sheets.values_api.update_calls)
        self.assertEqual("ACTIVE", sheets.values_api.rows[0][7])

    def test_new_row_requires_exact_full_readback(self):
        sheets = _Sheets(corrupt_appended_column=7)
        with patch.object(radar, "_verify_official_source", return_value=("verified", True)), \
             patch.object(radar, "_sheets_service", return_value=sheets):
            result = radar.process_radar(_collected(_record()))

        self.assertEqual("failed", result["radar_status"])
        self.assertEqual(1, sheets.values_api.append_calls)
        self.assertEqual(0, result["radar_write_verified"])
        self.assertEqual(0, result["radar_persistence_verified"])
        self.assertFalse(result["readback_ok"])

    def test_duplicate_existing_provenance_is_unresolved(self):
        existing = radar._build_row(_record(change_status="ACTIVE"), "verified", "original")
        sheets = _Sheets([existing, existing])
        with patch.object(radar, "_verify_official_source", return_value=("verified", True)), \
             patch.object(radar, "_sheets_service", return_value=sheets):
            result = radar.process_radar(_collected(_record()))

        self.assertEqual("partial", result["radar_status"])
        self.assertEqual(1, result["radar_unresolved_count"])
        self.assertEqual(0, result["radar_persistence_verified"])
        self.assertEqual(0, sheets.values_api.append_calls)
        self.assertIn("duplicate provenance rows", result["radar_unresolved"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
