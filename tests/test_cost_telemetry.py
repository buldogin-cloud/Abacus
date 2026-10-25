import json
import tempfile
import unittest
from pathlib import Path
from modules.telemetry.cost_telemetry import estimated_usd, record_usage, summarize

class CostTelemetryTests(unittest.TestCase):
    def test_known_cost(self):
        self.assertEqual(estimated_usd(1_000_000, 2_000_000, "0.10", "0.50"), "1.10")
    def test_unknown_stays_unknown(self):
        self.assertIsNone(estimated_usd(100, 20, None, None))
    def test_negative_tokens_rejected(self):
        with self.assertRaises(ValueError):
            estimated_usd(-1, 1, 1, 1)
    def test_jsonl_and_aggregate(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "usage.jsonl"
            record_usage(p, run_id="r1", task_id="email", provider="test",
                         model="stub", status="success", input_tokens=100,
                         output_tokens=20, price_input_per_m=1,
                         price_output_per_m=2, price_source="test price fixture")
            record_usage(p, run_id="r2", task_id="email", provider="test",
                         model="stub", status="failed")
            rows = [json.loads(s) for s in p.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(summarize(p)["unpriced_events"], 1)
            self.assertIsNone(summarize(p)["total_cost_usd"])
            self.assertNotIn("prompt", rows[0])
if __name__ == "__main__":
    unittest.main()
