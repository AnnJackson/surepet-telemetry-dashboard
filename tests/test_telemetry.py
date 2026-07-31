import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from surepet_telemetry import attribution_for_context, load_local_dotenv, normalize_csv, report_events, write_json


class TelemetryTests(unittest.TestCase):
    def test_demo_csv_normalizes_all_event_concepts(self):
        payload = normalize_csv(ROOT / "data" / "demo_events.csv")
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertGreaterEqual(len(payload["events"]), 800)
        self.assertEqual({event["category"] for event in payload["events"]}, {"feeding", "hydration", "access", "ambient"})
        ambient = next(event for event in payload["events"] if event["category"] == "ambient")
        self.assertIsNone(ambient["subject"])
        self.assertEqual(ambient["attribution"], "ambient")

    def test_json_output_is_portable(self):
        payload = normalize_csv(ROOT / "data" / "demo_events.csv")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "telemetry.json"
            write_json(output, payload)
            loaded = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(loaded["events"][0]["category"], "feeding")

    def test_checked_in_demo_json_covers_the_same_event_categories(self):
        payload = json.loads((ROOT / "data" / "demo_telemetry.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertGreaterEqual(len(payload["events"]), 800)
        self.assertEqual({event["category"] for event in payload["events"]}, {"feeding", "hydration", "access", "ambient"})

    def test_known_contexts_have_documented_attribution_labels(self):
        self.assertEqual(attribution_for_context(1), "pet")
        self.assertEqual(attribution_for_context("3"), "owner")
        self.assertEqual(attribution_for_context(5), "food_addition")
        self.assertEqual(attribution_for_context(6), "system_event")
        self.assertEqual(attribution_for_context(99), "unknown")

    def test_report_contexts_keep_their_numbers_and_receive_labels(self):
        report = {
            "feeding": {"datapoints": [
                {"context": 3, "to": "2026-07-31T12:00:00Z", "weights": [{"change": -8}]},
                {"context": 5, "to": "2026-07-31T12:01:00Z", "weights": [{"change": 12}]},
                {"context": 6, "to": "2026-07-31T12:02:00Z", "weights": [{"change": -2}]},
            ]}
        }
        events = report_events(report, {"id": "cat-1", "name": "Test cat"}, "/report/demo")
        self.assertEqual([event["context"] for event in events], [3, 5, 6])
        self.assertEqual([event["attribution"] for event in events], ["owner", "food_addition", "system_event"])

    def test_local_dotenv_reads_quoted_values_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text('SUREPET_EMAIL=cat@example.com\nSUREPET_PASSWORD="a password with spaces"\nFROM_SHELL=from-file\n', encoding="utf-8")
            environment = {"FROM_SHELL": "from-shell"}
            load_local_dotenv(dotenv, environment)
        self.assertEqual(environment["SUREPET_EMAIL"], "cat@example.com")
        self.assertEqual(environment["SUREPET_PASSWORD"], "a password with spaces")
        self.assertEqual(environment["FROM_SHELL"], "from-shell")


if __name__ == "__main__":
    unittest.main()
