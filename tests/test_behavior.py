import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

import monitor
from test_monitor import FakeResponse


class ConfigTests(unittest.TestCase):
    def test_real_config_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            monitor.validate_config(monitor.TARGETS)
            subprocess.run(["python3", "monitor.py", "--validate"], check=True, capture_output=True)

    def test_rejects_malformed_and_unsafe_definitions(self):
        mutations = [
            lambda c: c.update(extra=True),
            lambda c: c["settings"].update(timeout_seconds=True),
            lambda c: c["settings"].update(failures_before_down=1.5),
            lambda c: c["settings"].update(cert_warn_days=[1, 7]),
            lambda c: c["settings"].update(cert_warn_days=[7, 7]),
            lambda c: c["settings"].update(cert_warn_days=[False]),
            lambda c: c["settings"].update(stale_after_seconds=100),
            lambda c: c["settings"].update(minimum_coverage=0),
            lambda c: c["settings"].update(timeout_seconds=float("nan")),
            lambda c: c["targets"].append(c["targets"][0]),
        ]
        for key, value in [("url", "http://example.com"), ("url", "https://localhost/"),
                           ("url", "https://127.0.0.1/"), ("url", "https://[::1]/"),
                           ("url", "https://user:pass@example.com/"), ("url", "https://example.com/?token=x"),
                           ("url", "https://example.com:bad/"), ("check_cert", "false"),
                           ("allow_cross_host_redirects", 1), ("expected_ips", ["bad"]),
                           ("expected_ips", ["10.0.0.1"]), ("method", "DELETE"),
                           ("json_body", {}), ("must_contain", ""), ("json_equals", []),
                           ("json_equals", {"bad..path": "x"}), ("unknown", True)]:
            mutations.append(lambda c, k=key, v=value: c["targets"][0].update({k: v}))
        for mutate in mutations:
            config = copy.deepcopy(monitor.TARGETS)
            mutate(config)
            with self.subTest(config=config), self.assertRaises(ValueError):
                monitor.validate_config(config)

    def test_post_rejects_mutation(self):
        config = copy.deepcopy(monitor.TARGETS)
        config["targets"][0].update(method="POST", json_body={"query": "mutation { deleteAll }"})
        with self.assertRaises(ValueError):
            monitor.validate_config(config)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.target = {"name": "Example", "url": "https://example.com/", "expect_status": 200,
                       "json_equals": {"status": "healthy"}}

    def check_with(self, response=None, error=None):
        with patch.object(monitor, "resolve_ips", return_value=["8.8.8.8"]), \
                patch.object(monitor.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = response or FakeResponse('{"status":"healthy"}')
            opener.return_value.open.side_effect = error
            result = monitor.check(self.target)
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], monitor.SETTINGS["timeout_seconds"])
            return result

    def test_json_schema_rejects_fallback_html_and_wrong_types(self):
        for body in ('<html>healthy</html>', '{}', '{"status":true}', '{"status":"bad"}'):
            self.assertFalse(self.check_with(FakeResponse(body))[0])
        result = self.check_with()
        self.assertTrue(result[0])
        self.assertEqual(result[-1], self.target["url"])

    def test_graphql_auth_boundary(self):
        target = monitor.TARGETS["targets"][3]
        good = {"data": None, "errors": [{"extensions": {"code": "authentication_required"}, "path": ["__typename"]}]}
        self.assertTrue(monitor.semantic_match(json.dumps(good), target))
        good["errors"][0]["extensions"]["code"] = "internal_error"
        self.assertFalse(monitor.semantic_match(json.dumps(good), target))
        with patch.object(monitor, "resolve_ips", return_value=["8.8.8.8"]), \
                patch.object(monitor.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = FakeResponse(json.dumps(good))
            monitor.check(target)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(json.loads(request.data), {"query": "{ __typename }"})

    def test_timeouts_tls_and_http_errors(self):
        for error in (TimeoutError(), ssl.SSLCertVerificationError(), urllib.error.URLError("sensitive URL")):
            result = self.check_with(error=error)
            self.assertFalse(result[0])
            self.assertNotIn("sensitive", result[1])
        error = urllib.error.HTTPError(self.target["url"], 503, "unavailable", {}, io.BytesIO(b"bad"))
        result = self.check_with(error=error)
        self.assertFalse(result[0])
        self.assertEqual(result[3], 503)
        error = urllib.error.HTTPError(self.target["url"], 503, "unavailable", {}, io.BytesIO())
        with patch.object(error, "read", side_effect=TimeoutError()):
            self.assertFalse(self.check_with(error=error)[0])

    def test_all_dns_answers_and_address_subset(self):
        addresses = [(socket.AF_INET, 1, 6, '', ('8.8.8.8', 443)),
                     (socket.AF_INET6, 1, 6, '', ('2001:4860:4860::8888', 443, 0, 0))]
        with patch.object(socket, "getaddrinfo", return_value=addresses):
            self.assertEqual(set(monitor.resolve_ips("example.com")), {'8.8.8.8', '2001:4860:4860::8888'})
        self.target["expected_ips"] = ["8.8.8.8", "2001:4860:4860::8888"]
        self.assertIsNone(self.check_with()[5])
        for addresses in ([], ["127.0.0.1"]):
            self.target["check_cert"] = True
            with patch.object(monitor, "resolve_ips", return_value=addresses), patch.object(monitor, "cert_info") as cert:
                self.assertFalse(monitor.probe_target(self.target)[0][0])
                cert.assert_not_called()

    def test_redirect_policy(self):
        req = urllib.request.Request(self.target["url"])
        handler = monitor.PublicRedirect(self.target)
        with patch.object(monitor, "resolve_ips", return_value=["8.8.8.8"]):
            self.assertIsNotNone(handler.redirect_request(req, None, 302, "", {}, "https://example.com/health/"))
            for url in ("https://other.example/", "http://example.com/", "https://localhost/"):
                with self.assertRaises(ValueError):
                    handler.redirect_request(req, None, 302, "", {}, url)
            self.target["allow_cross_host_redirects"] = True
            self.assertIsNotNone(handler.redirect_request(req, None, 302, "", {}, "https://other.example/"))
            with self.assertRaises(ValueError):
                handler.redirect_request(urllib.request.Request(self.target["url"], data=b'{}'), None, 302, "", {}, self.target["url"])


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.paths = {}
        for name in ("STATE_PATH", "HISTORY_PATH", "ROLLUP_PATH"):
            self.paths[name] = str(folder / (name + ".json"))
            self.stack.enter_context(patch.object(monitor, name, self.paths[name]))
        self.target = {"name": "Example <escaped>", "url": "https://example.com/", "check_cert": True}
        self.stack.enter_context(patch.object(monitor, "TARGETS", {"settings": monitor.SETTINGS, "targets": [self.target]}))
        self.stack.enter_context(patch.dict(os.environ, {"TEST_PING": "false", "FORCE_DIGEST": "false"}))
        self.sender = self.stack.enter_context(patch.object(monitor, "telegram", return_value=True))
        self.probe = self.stack.enter_context(patch.object(monitor, "probe_target"))
        self.now = dt.datetime(2026, 9, 19, tzinfo=dt.timezone.utc)
        self.stack.enter_context(patch.object(monitor, "utcnow", side_effect=lambda: self.now))

    def cycle(self, ok, cert=None, error=None):
        self.probe.return_value = ((ok, "safe detail", 1, 200 if ok else 503, 1, None, "https://example.com/"), cert, error)
        monitor.main()
        self.now += dt.timedelta(minutes=5)
        return monitor.load_json(self.paths["STATE_PATH"], {})

    def test_debounce_transitions_recovery_and_retention(self):
        states = [self.cycle(ok) for ok in (True, False, False, False, True)]
        self.assertEqual([s[self.target["name"]]["up"] for s in states], [True, True, False, False, True])
        messages = [call.args[0] for call in self.sender.call_args_list]
        self.assertEqual(sum("<b>DOWN</b>" in m for m in messages), 1)
        self.assertEqual(sum("<b>RECOVERED</b>" in m for m in messages), 1)
        self.assertIn("Example &lt;escaped&gt;", messages[-1])
        history = monitor.load_json(self.paths["HISTORY_PATH"], [])
        self.assertEqual(history[2]["results"][self.target["name"]]["incident_started_utc"], history[2]["ts"])
        self.assertEqual(states[-1]["_meta"]["latest_observation_utc"], history[-1]["ts"])

    def test_tls_metadata_survives_transient_and_sustained_failures(self):
        cert = {"days_left": 6, "not_after": "2026-09-25", "not_before": "2026-06-25", "issuer": "Example CA"}
        self.cycle(True, cert)
        for _ in range(4):
            state = self.cycle(True, error=TimeoutError())
            self.assertTrue(state[self.target["name"]]["cert_warned_7"])
            self.assertEqual(state[self.target["name"]]["cert_issuer"], "Example CA")
        self.cycle(True, cert)
        messages = [c.args[0] for c in self.sender.call_args_list]
        self.assertEqual(sum("TLS cert" in m for m in messages), 2)  # thresholds 21 and 7
        self.assertEqual(sum("TLS inspection unavailable" in m for m in messages), 1)
        self.assertEqual(sum("TLS inspection recovered" in m for m in messages), 1)

    def test_removed_target_state_pruned_history_preserved(self):
        self.cycle(False)
        self.cycle(False)
        old_name = self.target["name"]
        self.target["name"] = "Replacement"
        state = self.cycle(True)
        self.assertNotIn(old_name, state)
        self.assertIn(old_name, monitor.load_json(self.paths["HISTORY_PATH"], [])[0]["results"])

    def test_corrupt_data_never_overwritten(self):
        monitor.dump_json_atomic(self.paths["STATE_PATH"], {"broken": []})
        original = Path(self.paths["STATE_PATH"]).read_bytes()
        with self.assertRaisesRegex(ValueError, "Corrupt"):
            self.cycle(True)
        self.assertEqual(Path(self.paths["STATE_PATH"]).read_bytes(), original)
        self.sender.assert_not_called()
        self.probe.assert_not_called()

    def test_completion_is_not_persisted_when_history_write_fails(self):
        self.cycle(True)
        original = Path(self.paths["STATE_PATH"]).read_bytes()
        with patch.object(monitor, "dump_json_atomic", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.cycle(True)
        self.assertEqual(Path(self.paths["STATE_PATH"]).read_bytes(), original)


class PersistenceAndNotificationTests(unittest.TestCase):
    def test_queue_bounded_and_expired(self):
        meta = {}
        with patch.dict(monitor.SETTINGS, notification_max_count=3):
            for i in range(6):
                monitor.queue_notification(meta, str(i))
        self.assertEqual(len(meta["pending_notifications"]), 3)
        self.assertEqual(meta["notifications_dropped"], 3)
        meta["pending_notifications"][0]["queued_at"] = "2000-01-01T00:00:00Z"
        with patch.object(monitor, "telegram", return_value=False):
            monitor.flush_notifications(meta)
        self.assertEqual(len(meta["pending_notifications"]), 2)

    def test_delivery_errors_redacted_and_retried(self):
        output = io.StringIO()
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN="synthetic", TELEGRAM_CHAT_ID="synthetic"), \
                patch.object(monitor.urllib.request, "urlopen", side_effect=urllib.error.URLError("sensitive address")) as send, \
                patch.object(monitor.time, "sleep"), redirect_stdout(output):
            self.assertFalse(monitor.telegram("hello"))
        self.assertEqual(send.call_count, 3)
        self.assertNotIn("sensitive", output.getvalue())
        self.assertNotIn("synthetic", output.getvalue())

    def test_telegram_api_rejection_and_manual_ping_exit(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN="synthetic", TELEGRAM_CHAT_ID="synthetic"), \
                patch.object(monitor.urllib.request, "urlopen", return_value=FakeResponse('{"ok":false}')), \
                patch.object(monitor.time, "sleep"):
            self.assertFalse(monitor.telegram("hello"))
        with patch.dict(os.environ, TEST_PING="true"), patch.object(monitor, "telegram", return_value=False):
            with self.assertRaises(SystemExit) as result:
                monitor.main()
            self.assertEqual(result.exception.code, 1)

    def test_atomic_write_failure_and_corrupt_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            monitor.dump_json_atomic(path, {"original": True})
            with patch.object(monitor.os, "replace", side_effect=OSError()), self.assertRaises(OSError):
                monitor.dump_json_atomic(path, {})
            self.assertEqual(monitor.load_json(path, {}), {"original": True})
            self.assertEqual(list(Path(folder).glob(".monitor-*")), [])
            with patch("builtins.open", return_value=io.StringIO("broken")), self.assertRaisesRegex(ValueError, "Corrupt"):
                monitor.load_json(path, {})

    def test_time_based_retention_keeps_boundary_context(self):
        now = monitor.utcnow()
        history = [{"ts": (now - dt.timedelta(days=days)).isoformat(),
                    "results": {"Example": {"ok": False, "confirmed_up": False, "incident_started_utc": "2020-01-01T00:00:00Z"}}}
                   for days in (60, 50, 40, 30, 1)]
        retained = monitor.retain_history(history, now)
        self.assertEqual(len(retained), 3)
        self.assertTrue(retained[0]["boundary_anchor"])
        self.assertEqual(retained[0]["results"]["Example"]["incident_started_utc"], "2020-01-01T00:00:00Z")

    def test_only_one_generated_location(self):
        root = Path(monitor.ROOT)
        for filename in ("state.json", "history.json", "uptime_daily.json"):
            self.assertFalse((root / filename).exists(), "Root duplicates must never return")
            self.assertTrue((root / "docs" / filename).exists())

    def test_browser_logic(self):
        subprocess.run(["node", "--test", "tests/status.test.js"], check=True)


if __name__ == "__main__":
    unittest.main()
