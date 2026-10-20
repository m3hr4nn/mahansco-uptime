import copy
import datetime as dt
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor
import weekly_report


UTC = dt.timezone.utc


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.boundary = dt.datetime(2026, 10, 21, 5, 30, tzinfo=UTC)
        self.settings = copy.deepcopy(monitor.SETTINGS)
        self.settings.update(expected_interval_seconds=14400, minimum_coverage=.9)
        self.state = {"_meta": {"config": {"schema_version": 2, "settings": self.settings,
                                             "targets": [{"name": "Example", "hostname": "example.com", "check_kind": "semantic"}]}},
                      "Example": {"up": True, "fail_streak": 0}}

    def samples(self, start, count=42, latency=100, ok=True):
        return [{"ts": (start + dt.timedelta(hours=4 * i, minutes=1)).isoformat(),
                 "results": {"Example": {"ok": ok, "confirmed_up": ok, "latency_ms": latency,
                                           "status_code": 200, "detail": "OK"}}}
                for i in range(count)]

    def test_boundary_is_wednesday_0900_tehran(self):
        self.assertEqual(weekly_report.report_boundary(dt.datetime(2026, 10, 21, 9, 0, tzinfo=monitor.TEHRAN)), self.boundary)
        self.assertEqual(weekly_report.report_boundary(dt.datetime(2026, 10, 20, 23, 0, tzinfo=monitor.TEHRAN)),
                         self.boundary - dt.timedelta(days=7))

    def test_complete_windows_metrics_comparison_and_incident(self):
        previous_start = self.boundary - dt.timedelta(days=14)
        current_start = self.boundary - dt.timedelta(days=7)
        history = self.samples(previous_start, latency=80) + self.samples(current_start, latency=100)
        history[-40]["results"]["Example"].update(ok=False, confirmed_up=False,
            incident_started_utc=history[-40]["ts"], detail="HTTP 503")
        history[-39]["results"]["Example"].update(ok=False, confirmed_up=False,
            incident_started_utc=history[-40]["ts"], detail="HTTP 503")
        summary = weekly_report.generate_summary(history, self.state, self.boundary)
        self.assertEqual(summary["observed_slots"], 42)
        self.assertEqual(summary["missing_slots"], 0)
        self.assertAlmostEqual(summary["availability_percent"], 40 / 42 * 100)
        self.assertEqual(summary["incident_count"], 1)
        self.assertEqual(summary["longest_incident_seconds"], 8 * 3600)
        self.assertEqual(summary["median_ms"], 100)
        self.assertEqual(summary["p95_ms"], 100)
        self.assertEqual(summary["latency_samples"], 42)
        self.assertTrue(summary["comparison_available"])
        self.assertEqual(summary["median_change_percent"], 25)

    def test_missing_data_is_not_downtime_or_availability(self):
        history = self.samples(self.boundary - dt.timedelta(days=7), count=2)
        summary = weekly_report.generate_summary(history, self.state, self.boundary)
        self.assertIsNone(summary["availability_percent"])
        self.assertEqual(summary["incident_count"], 0)
        self.assertEqual(summary["confirmed_downtime_seconds"], 0)
        self.assertFalse(summary["comparison_available"])
        self.assertEqual(summary["health"], "UNKNOWN")

    def test_probe_definition_change_suppresses_incompatible_metrics(self):
        history = self.samples(self.boundary - dt.timedelta(days=14), latency=80) + self.samples(self.boundary - dt.timedelta(days=7), latency=100)
        history[-1]["results"]["Example"]["measurement_id"] = "new-definition"
        summary = weekly_report.generate_summary(history, self.state, self.boundary)
        self.assertIsNone(summary["availability_percent"])
        self.assertFalse(summary["comparison_available"])
        self.assertFalse(summary["measurement_consistent"])
        self.assertTrue(any("definition changed" in value for value in summary["observations"]))

    def test_nearest_rank_percentile_and_telegram_format(self):
        self.assertEqual(weekly_report.percentile(list(range(1, 101)), .95), 95)
        history = self.samples(self.boundary - dt.timedelta(days=14), latency=80) + self.samples(self.boundary - dt.timedelta(days=7), latency=100)
        summary = weekly_report.generate_summary(history, self.state, self.boundary)
        summary["observations"] = ["Unsafe <value> & evidence"]
        text = weekly_report.format_report(summary, test=True)
        self.assertIn("TEST — Weekly operations report", text)
        self.assertIn("Unsafe &lt;value&gt; &amp; evidence", text)
        self.assertIn("n=42", text)
        self.assertLessEqual(len(text), 4096)

    def test_delivery_failure_and_duplicate_prevention(self):
        history = self.samples(self.boundary - dt.timedelta(days=14), latency=80) + self.samples(self.boundary - dt.timedelta(days=7), latency=100)
        with tempfile.TemporaryDirectory() as folder:
            paths = {"STATE_PATH": str(Path(folder) / "state.json"),
                     "HISTORY_PATH": str(Path(folder) / "history.json"),
                     "ROLLUP_PATH": str(Path(folder) / "uptime_daily.json")}
            state = copy.deepcopy(self.state)
            state["_meta"].update(last_run_utc=history[-1]["ts"], latest_observation_utc=history[-1]["ts"])
            monitor.dump_json_atomic(paths["STATE_PATH"], state)
            monitor.dump_json_atomic(paths["HISTORY_PATH"], history)
            monitor.dump_json_atomic(paths["ROLLUP_PATH"], {})
            with patch.multiple(monitor, **paths), patch.object(monitor, "telegram", return_value=False) as sender, redirect_stdout(io.StringIO()):
                self.assertEqual(weekly_report.main(["--send", "--at", self.boundary.isoformat()]), 1)
                self.assertFalse((Path(folder) / "weekly_report.json").exists())
                sender.return_value = True
                self.assertEqual(weekly_report.main(["--send", "--at", self.boundary.isoformat()]), 0)
                self.assertEqual(weekly_report.main(["--send", "--at", self.boundary.isoformat()]), 0)
                self.assertEqual(sender.call_count, 2)
            tracking = json.loads((Path(folder) / "weekly_report.json").read_text())
            self.assertEqual(tracking["last_sent_boundary_utc"], self.boundary.isoformat())
            self.assertEqual(tracking["delivery_count"], 1)

    def test_controlled_resend_and_test_do_not_share_tracking(self):
        history = self.samples(self.boundary - dt.timedelta(days=14)) + self.samples(self.boundary - dt.timedelta(days=7))
        with tempfile.TemporaryDirectory() as folder:
            paths = {"STATE_PATH": str(Path(folder) / "state.json"), "HISTORY_PATH": str(Path(folder) / "history.json"),
                     "ROLLUP_PATH": str(Path(folder) / "uptime_daily.json")}
            state = copy.deepcopy(self.state)
            state["_meta"].update(last_run_utc=history[-1]["ts"], latest_observation_utc=history[-1]["ts"])
            for key, value in (("STATE_PATH", state), ("HISTORY_PATH", history), ("ROLLUP_PATH", {})):
                monitor.dump_json_atomic(paths[key], value)
            with patch.multiple(monitor, **paths), patch.object(monitor, "telegram", return_value=True) as sender, redirect_stdout(io.StringIO()):
                self.assertEqual(weekly_report.main(["--test-send", "--at", self.boundary.isoformat()]), 0)
                self.assertFalse((Path(folder) / "weekly_report.json").exists())
                self.assertEqual(weekly_report.main(["--send", "--at", self.boundary.isoformat()]), 0)
                self.assertEqual(weekly_report.main(["--send", "--resend", "--at", self.boundary.isoformat()]), 0)
                self.assertEqual(sender.call_count, 3)
            self.assertEqual(json.loads((Path(folder) / "weekly_report.json").read_text())["delivery_count"], 2)


if __name__ == "__main__":
    unittest.main()
