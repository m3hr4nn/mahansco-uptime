import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")

import monitor


class FakeResponse:
    def __init__(self, body="ok", status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body.encode()

    def getcode(self):
        return self.status


class MonitorTests(unittest.TestCase):
    def test_single_failure_is_debounced(self):
        streak, is_down, was_down = monitor.derive_state({"up": True, "fail_streak": 0}, False)
        self.assertEqual(streak, 1)
        self.assertFalse(is_down)
        self.assertFalse(was_down)

    def test_second_failure_is_confirmed_down(self):
        streak, is_down, _ = monitor.derive_state({"up": True, "fail_streak": 1}, False)
        self.assertEqual(streak, 2)
        self.assertTrue(is_down)

    def test_telegram_html_is_escaped(self):
        self.assertEqual(
            monitor.telegram_escape('<div id="root">'),
            "&lt;div id=&quot;root&quot;&gt;",
        )

    def test_dns_mismatch_is_advisory(self):
        target = {
            "name": "Landing",
            "url": "https://example.com/",
            "expect_status": 200,
            "expected_ip": "192.0.2.1",
        }
        with mock.patch.object(monitor, "resolve_ip", return_value="198.51.100.2"), \
                mock.patch.object(monitor.urllib.request, "urlopen", return_value=FakeResponse()):
            result = monitor.check(target)
        self.assertTrue(result[0])
        self.assertIn("198.51.100.2", result[5])

    def test_failed_notification_is_retained_and_retried(self):
        meta = {}
        monitor.queue_notification(meta, "alert")
        with mock.patch.object(monitor, "telegram", return_value=False):
            monitor.flush_notifications(meta)
        self.assertEqual(len(meta["pending_notifications"]), 1)
        with mock.patch.object(monitor, "telegram", return_value=True):
            monitor.flush_notifications(meta)
        self.assertEqual(meta["pending_notifications"], [])

    def test_state_write_is_atomic_and_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            monitor.dump_json_atomic(str(path), {"ok": True})
            self.assertEqual(path.read_text(), '{\n  "ok": true\n}\n')
            self.assertEqual(list(Path(directory).glob(".monitor-*.tmp")), [])

    def test_main_persists_debounced_status_and_dns_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            history_path = Path(directory) / "history.json"
            rollup_path = Path(directory) / "uptime_daily.json"
            target = {
                "name": "Example",
                "url": "https://example.com/",
                "expect_status": 200,
                "check_cert": False,
            }
            fake_result = (True, "OK 12ms", 12, 200, 3, "Resolves to 198.51.100.2, expected 192.0.2.1")
            with mock.patch.object(monitor, "STATE_PATH", str(state_path)), \
                    mock.patch.object(monitor, "HISTORY_PATH", str(history_path)), \
                    mock.patch.object(monitor, "ROLLUP_PATH", str(rollup_path)), \
                    mock.patch.object(monitor, "TARGETS", {"settings": monitor.SETTINGS, "targets": [target]}), \
                    mock.patch.object(monitor, "check", return_value=fake_result), \
                    mock.patch.object(monitor, "telegram", return_value=True), \
                    mock.patch.dict(os.environ, {"FORCE_DIGEST": "false"}, clear=False):
                monitor.main()

            state = monitor.load_json(str(state_path), {})
            history = monitor.load_json(str(history_path), [])
            self.assertTrue(state["Example"]["up"])
            self.assertTrue(state["Example"]["dns_mismatch"])
            self.assertTrue(history[-1]["results"]["Example"]["confirmed_up"])


if __name__ == "__main__":
    unittest.main()
