#!/usr/bin/env python3
"""Tier-0 blackbox uptime monitor -> private Telegram channel.

State-change alerting only (alerts on UP<->DOWN transitions and cert-threshold
crossings, plus a configured digest). Python standard library only -- no pip
installs: urllib, ssl, socket, json, datetime.
"""

import datetime
from concurrent.futures import ThreadPoolExecutor
import html
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))


def validate_config(config):
    """Fail early with a useful error when the monitor configuration is invalid."""
    if not isinstance(config, dict):
        raise ValueError("targets.json must contain a JSON object")
    settings = config.get("settings")
    targets = config.get("targets")
    if not isinstance(settings, dict) or not isinstance(targets, list) or not targets:
        raise ValueError("targets.json must contain non-empty settings and targets")

    positive_settings = ("timeout_seconds", "latency_warn_ms", "failures_before_down",
                         "digest_every_hours", "expected_interval_seconds", "delayed_after_seconds",
                         "stale_after_seconds", "refresh_seconds", "retention_days",
                         "notification_max_count", "notification_max_age_hours", "cert_failure_threshold")
    allowed_settings = set(positive_settings) | {"cert_warn_days", "minimum_coverage"}
    if set(config) != {"settings", "targets"} or set(settings) != allowed_settings:
        raise ValueError(f"configuration keys must be settings/targets; settings missing={sorted(allowed_settings - set(settings))}, unsupported={sorted(set(settings) - allowed_settings)}")
    for key in positive_settings:
        if type(settings.get(key)) not in (int, float) or not math.isfinite(settings[key]) or settings[key] <= 0:
            raise ValueError(f"settings.{key} must be a positive number")
    for key in ("failures_before_down", "notification_max_count", "cert_failure_threshold", "retention_days"):
        if type(settings[key]) is not int:
            raise ValueError(f"settings.{key} must be an integer")
    thresholds = settings.get("cert_warn_days")
    if (not isinstance(thresholds, list) or not thresholds
            or any(type(t) is not int or t < 0 for t in thresholds)
            or thresholds != sorted(set(thresholds), reverse=True)):
        raise ValueError("settings.cert_warn_days must be unique nonnegative integers in descending order")
    if not settings["expected_interval_seconds"] <= settings["delayed_after_seconds"] < settings["stale_after_seconds"]:
        raise ValueError("expected interval <= delayed threshold < stale threshold is required")
    if type(settings["minimum_coverage"]) not in (int, float) or not 0 < settings["minimum_coverage"] <= 1:
        raise ValueError("settings.minimum_coverage must be in (0, 1]")
    if settings["retention_days"] < 30:
        raise ValueError("settings.retention_days must cover the 30-day availability window")

    names = set()
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise ValueError(f"targets[{index}] must be an object")
        name = target.get("name")
        url = target.get("url")
        allowed = {"name", "url", "expect_status", "must_contain", "check_cert", "expected_ips",
                   "method", "json_body", "json_equals", "allow_cross_host_redirects"}
        if set(target) - allowed:
            raise ValueError(f"targets[{index}] has unsupported keys: {sorted(set(target) - allowed)}")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"targets[{index}].name must be non-empty and unique")
        if not public_url(url):
            raise ValueError(f"targets[{index}].url must be a public HTTPS URL without credentials, query or fragment")
        if type(target.get("expect_status")) is not int or not 100 <= target["expect_status"] <= 599:
            raise ValueError(f"targets[{index}].expect_status must be an HTTP status code")
        if "must_contain" in target and (not isinstance(target["must_contain"], str) or not target["must_contain"]):
            raise ValueError(f"targets[{index}].must_contain must be a non-empty string")
        for key in ("check_cert", "allow_cross_host_redirects"):
            if key in target and type(target[key]) is not bool:
                raise ValueError(f"targets[{index}].{key} must be boolean")
        if "expected_ips" in target:
            ips = target["expected_ips"]
            if not isinstance(ips, list) or not ips or any(not isinstance(ip, str) for ip in ips):
                raise ValueError(f"targets[{index}].expected_ips must be a non-empty address list")
            try:
                if any(not ipaddress.ip_address(ip).is_global for ip in ips) or len({ipaddress.ip_address(ip) for ip in ips}) != len(ips):
                    raise ValueError()
            except ValueError:
                raise ValueError(f"targets[{index}].expected_ips must contain unique public IP addresses") from None
        if target.get("method", "GET") not in ("GET", "POST"):
            raise ValueError(f"targets[{index}].method must be GET or POST")
        if target.get("method", "GET") == "POST":
            if target.get("json_body") != {"query": "{ __typename }"}:
                raise ValueError(f"targets[{index}] POST only supports the harmless __typename query")
        elif "json_body" in target:
            raise ValueError(f"targets[{index}] GET cannot have json_body")
        rules = target.get("json_equals", {})
        if not isinstance(rules, dict) or ("json_equals" in target and not rules):
            raise ValueError(f"targets[{index}].json_equals must be a non-empty path/value object")
        if any(not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", path)
               or type(value) not in (str, bool, int, float, type(None))
               or (type(value) is float and not math.isfinite(value)) for path, value in rules.items()):
            raise ValueError(f"targets[{index}].json_equals requires dotted paths and scalar values")
        if not target.get("must_contain") and not rules:
            raise ValueError(f"targets[{index}] requires a body or JSON semantic assertion")
        names.add(name)


def public_url(url):
    if not isinstance(url, str) or any(c.isspace() for c in url):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return (bool(re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", host))
                    and not host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")))
    except ValueError:
        return False


with open(os.path.join(ROOT, "targets.json"), encoding="utf-8") as targets_file:
    TARGETS = json.load(targets_file)
validate_config(TARGETS)
SETTINGS = TARGETS["settings"]

STATE_PATH = os.path.join(ROOT, "docs", "state.json")
HISTORY_PATH = os.path.join(ROOT, "docs", "history.json")
ROLLUP_PATH = os.path.join(ROOT, "docs", "uptime_daily.json")


# Iran Standard Time is a fixed UTC+03:30 (no DST since 2022). All human-facing
# timestamps (Telegram, status page) are shown in Tehran time; UTC is still used
# internally for any elapsed-time math.
TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))


def utcnow():
    """Timezone-aware current UTC time (utcnow() is deprecated in 3.12)."""
    return datetime.datetime.now(datetime.timezone.utc)


def fmt_local(dt):
    """Format a timezone-aware datetime as a Tehran-time display string."""
    return dt.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M") + " UTC+03:30"


def parse_timestamp(value):
    value = value.replace(" IRST", "+03:30").replace(" UTC+03:30", "+03:30").replace(" UTC", "+00:00")
    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must have an explicit offset")
    return dt


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as data_file:
            return json.load(data_file)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        raise ValueError(f"Corrupt {os.path.basename(path)}; restore the last valid version from Git before running") from None


def dump_json_atomic(path, value):
    """Write JSON without leaving a partially-written state file behind."""
    directory = os.path.dirname(path) or "."
    fd, temporary_path = tempfile.mkstemp(prefix=".monitor-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as data_file:
            if isinstance(value, list):
                # One compact observation per line keeps generated Git diffs small.
                data_file.write("[\n")
                for index, item in enumerate(value):
                    if index:
                        data_file.write(",\n")
                    data_file.write(json.dumps(item, separators=(",", ":"), ensure_ascii=False))
                data_file.write("\n]")
            else:
                json.dump(value, data_file, indent=2, ensure_ascii=False)
            data_file.write("\n")
            data_file.flush()
            os.fsync(data_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def telegram_escape(value):
    """Escape data interpolated into Telegram HTML messages."""
    return html.escape(str(value), quote=True)


def queue_notification(meta, text, kind="alert", advance_digest_clock=False):
    pending = meta.setdefault("pending_notifications", [])
    pending.append({
        "kind": kind,
        "text": text,
        "advance_digest_clock": advance_digest_clock,
        "queued_at": utcnow().isoformat(),
    })
    bound_notifications(meta)


def bound_notifications(meta):
    pending = meta.setdefault("pending_notifications", [])
    cutoff = utcnow() - datetime.timedelta(hours=SETTINGS["notification_max_age_hours"])
    retained = [event for event in pending if parse_timestamp(event["queued_at"]) >= cutoff]
    retained = retained[-SETTINGS["notification_max_count"]:]
    dropped = len(pending) - len(retained)
    if dropped:
        meta["notifications_dropped"] = meta.get("notifications_dropped", 0) + dropped
        print(f"Notification queue expired or evicted {dropped} events")
    meta["pending_notifications"] = retained


def flush_notifications(meta):
    """Deliver queued messages in order, retaining anything Telegram rejects."""
    bound_notifications(meta)
    pending = meta.setdefault("pending_notifications", [])
    remaining = []
    for index, event in enumerate(pending):
        if index >= 5:  # bound delivery time as well as storage
            remaining.extend(pending[index:])
            break
        if not telegram(event["text"]):
            remaining.extend(pending[index:])
            break
        if event.get("kind") == "digest" and event.get("advance_digest_clock"):
            meta["last_digest_utc"] = event.get("digest_utc", utcnow().isoformat())
    meta["pending_notifications"] = remaining


def telegram(text):
    """Send a message. Returns True on success. Never raises (a send failure
    must not crash a monitoring run), but prints enough to debug from CI logs."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram delivery unavailable: missing runtime credentials")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10) as response:
                if json.loads(response.read(100_000)).get("ok") is True:
                    return True
                print("Telegram delivery rejected")
        except urllib.error.HTTPError as e:
            print(f"Telegram send failed: HTTP {e.code}")
            e.close()
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:  # never log URLs, response bodies or credentials
            print(f"Telegram send failed: {type(e).__name__}")
        if attempt < 2:
            time.sleep(attempt + 1)
    return False


def _parse_cert_time(value):
    # OpenSSL format, e.g. "Sep  9 12:00:00 2026 GMT" (always UTC).
    return datetime.datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=datetime.timezone.utc)


def cert_info(host):
    """TLS certificate summary for host:443, or None on failure:
    {days_left, not_before, not_after, issuer}."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=SETTINGS["timeout_seconds"]) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
    not_after = _parse_cert_time(cert["notAfter"])
    not_before = _parse_cert_time(cert["notBefore"])
    # issuer is a tuple of RDN tuples; flatten and prefer the org / CN name.
    issuer = dict(x[0] for x in cert.get("issuer", []))
    return {
        "days_left": (not_after - utcnow()).days,
        "not_before": not_before.strftime("%Y-%m-%d"),
        "not_after": not_after.strftime("%Y-%m-%d"),
        "issuer": issuer.get("organizationName") or issuer.get("commonName") or "unknown",
    }


def resolve_ips(host):
    try:
        return sorted({str(ipaddress.ip_address(answer[4][0]))
                       for answer in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    except socket.gaierror:
        return []


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, target):
        self.target = target

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (not public_url(newurl) or
                (not self.target.get("allow_cross_host_redirects", False)
                 and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(self.target["url"]).hostname)):
            raise ValueError("Unexpected redirect")
        if any(not ipaddress.ip_address(ip).is_global for ip in resolve_ips(urllib.parse.urlsplit(newurl).hostname)):
            raise ValueError("Nonpublic redirect")
        if req.get_method() == "POST":
            raise ValueError("POST redirects cannot preserve semantic checks safely")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def semantic_match(body, target):
    if target.get("must_contain") and target["must_contain"] not in body:
        return False
    if target.get("json_equals"):
        try:
            document = json.loads(body)
            for path, expected in target["json_equals"].items():
                value = document
                for key in path.split("."):
                    value = value[int(key)] if isinstance(value, list) else value[key]
                if type(value) is not type(expected) or value != expected:
                    return False
        except (ValueError, KeyError, IndexError, TypeError):
            return False
    return True


def check(t):
    """Return (ok, detail, latency_ms, status_code, dns_ms, dns_warning, final_url).
    latency_ms/status_code are None when the request never completed; dns_ms is
    the DNS-resolution time (measured even on failure so slow DNS is visible).
    An expected-IP mismatch is advisory: public reachability remains the primary
    Tier-0 signal, and legitimate DNS migrations must not look like outages."""
    host = urllib.parse.urlparse(t["url"]).hostname

    # DNS / expected IP -- timed separately so slow resolution shows up.
    dns_start = time.monotonic()
    ips = resolve_ips(host)
    dns_ms = int((time.monotonic() - dns_start) * 1000)
    if not ips:
        return False, "DNS resolution failed", None, None, dns_ms, None, None
    if any(not ipaddress.ip_address(ip).is_global for ip in ips):
        return False, "DNS returned a nonpublic address", None, None, dns_ms, None, None
    dns_warning = None
    if t.get("expected_ips") and not set(ips) <= {str(ipaddress.ip_address(ip)) for ip in t["expected_ips"]}:
        dns_warning = "DNS addresses differ from the configured address set"

    # Bounded public GET or the configured harmless GraphQL POST.
    start = time.monotonic()
    final_url = None
    try:
        data = json.dumps(t["json_body"]).encode() if "json_body" in t else None
        req = urllib.request.Request(t["url"], data=data, method=t.get("method", "GET"),
                                     headers={"User-Agent": "mahansco-uptime/2.0", "Content-Type": "application/json"})
        try:
            response = urllib.request.build_opener(PublicRedirect(t)).open(req, timeout=SETTINGS["timeout_seconds"])
        except urllib.error.HTTPError as error:
            response = error  # HTTP errors are still readable responses; reads may also time out.
        with response as resp:
            body = resp.read(2_000_000).decode("utf-8", "ignore")
            status = resp.getcode()
            final_url = resp.geturl()
    except Exception as e:  # noqa: BLE001
        return False, f"Request failed: {type(e).__name__}", None, None, dns_ms, dns_warning, None
    latency_ms = int((time.monotonic() - start) * 1000)

    if status != t["expect_status"]:
        return False, f"HTTP {status} (expected {t['expect_status']})", latency_ms, status, dns_ms, dns_warning, final_url
    if not semantic_match(body, t):
        return False, "Response failed semantic assertion", latency_ms, status, dns_ms, dns_warning, final_url
    if latency_ms > SETTINGS["latency_warn_ms"]:
        return True, f"OK but slow {latency_ms}ms", latency_ms, status, dns_ms, dns_warning, final_url
    return True, f"OK {latency_ms}ms", latency_ms, status, dns_ms, dns_warning, final_url


def probe_target(t):
    """Run the external HTTP and optional TLS probes for one target."""
    result = check(t)
    cert = None
    cert_error = None
    if t.get("check_cert") and not result[1].startswith("DNS "):
        host = urllib.parse.urlparse(t["url"]).hostname
        try:
            cert = cert_info(host)
        except Exception as e:  # noqa: BLE001
            cert_error = e
    return result, cert, cert_error


def derive_state(prev, ok):
    """Apply the configured consecutive-failure debounce to one probe result."""
    fail_streak = 0 if ok else prev.get("fail_streak", 0) + 1
    was_down = not prev.get("up", True)
    is_down = not ok and (was_down or fail_streak >= SETTINGS["failures_before_down"])
    return fail_streak, is_down, was_down


def validate_data(state, history, rollup):
    """Refuse damaged operational data before sending alerts or writing anything."""
    try:
        if not isinstance(state, dict) or not isinstance(history, list) or not isinstance(rollup, dict):
            raise ValueError()
        for name, value in state.items():
            if not isinstance(value, dict):
                raise ValueError()
            if name != "_meta":
                if type(value.get("up")) is not bool or type(value.get("fail_streak")) is not int or value["fail_streak"] < 0:
                    raise ValueError()
                for key in ("incident_started_utc", "last_check_utc"):
                    if value.get(key):
                        parse_timestamp(value[key])
        meta = state.get("_meta", {})
        if meta.get("latest_observation_utc") and (not history or history[-1]["ts"] != meta["latest_observation_utc"]):
            raise ValueError()
        for key in ("last_run_utc", "last_digest_utc"):
            if meta.get(key):
                parse_timestamp(meta[key])
        pending = meta.get("pending_notifications", [])
        if not isinstance(pending, list):
            raise ValueError()
        for event in pending:
            if not isinstance(event, dict) or not isinstance(event.get("text"), str):
                raise ValueError()
            parse_timestamp(event["queued_at"])
        previous = None
        for sample in history:
            stamp = parse_timestamp(sample["ts"])
            if previous and stamp < previous:
                raise ValueError()
            previous = stamp
            if not isinstance(sample["results"], dict):
                raise ValueError()
            for result in sample["results"].values():
                if type(result.get("ok")) is not bool or ("confirmed_up" in result and type(result["confirmed_up"]) is not bool):
                    raise ValueError()
                if result.get("incident_started_utc"):
                    parse_timestamp(result["incident_started_utc"])
        for day, targets in rollup.items():
            datetime.date.fromisoformat(day)
            for aggregate in targets.values():
                if any(type(aggregate.get(key)) is not int for key in ("up", "total")) or not 0 <= aggregate["up"] <= aggregate["total"]:
                    raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Corrupt operational data; restore state/history/rollup from the same valid Git commit before running") from None


def retain_history(history, now):
    cutoff = now - datetime.timedelta(days=SETTINGS["retention_days"])
    # Keep one predecessor for each target, so older ongoing incidents retain context.
    anchors = {}
    kept = []
    for sample in history:
        if parse_timestamp(sample["ts"]) < cutoff:
            for name, result in sample["results"].items():
                anchors[name] = {"ts": sample["ts"], "result": result}
        else:
            kept.append(sample)
    grouped = {}
    relevant_names = {t["name"] for t in TARGETS["targets"]} | {name for sample in kept for name in sample["results"]}
    for name, anchor in anchors.items():
        if name in relevant_names:
            grouped.setdefault(anchor["ts"], {})[name] = anchor["result"]
    return [{"ts": ts, "results": results, "boundary_anchor": True}
            for ts, results in sorted(grouped.items(), key=lambda item: parse_timestamp(item[0]))] + kept


def public_config():
    return {"schema_version": 2, "settings": SETTINGS,
            "targets": [{"name": t["name"], "url": t["url"]} for t in TARGETS["targets"]]}


def main():
    paths = (STATE_PATH, HISTORY_PATH, ROLLUP_PATH)
    if sum(os.path.exists(path) for path in paths) not in (0, len(paths)):
        raise ValueError("Corrupt/incomplete dataset; restore state/history/rollup from the same valid Git commit")
    state = load_json(STATE_PATH, {})
    history = load_json(HISTORY_PATH, [])
    rollup = load_json(ROLLUP_PATH, {})
    validate_data(state, history, rollup)

    # On-demand delivery test (workflow_dispatch input). Confirms the bot token,
    # chat id, and bot-admin status end-to-end without needing a real outage.
    if os.environ.get("TEST_PING", "").lower() == "true":
        now = fmt_local(utcnow())
        ok = telegram(f"✅ <b>Test ping</b> — mahansco-uptime is wired up correctly.\n<i>{now}</i>")
        print("Test ping delivered." if ok else "Test ping FAILED — see error above.")
        if not ok:
            raise SystemExit(1)

    meta = state.get("_meta", {})
    active_names = {t["name"] for t in TARGETS["targets"]}
    state = {key: value for key, value in state.items() if key in active_names or key == "_meta"}
    state["_meta"] = meta
    flush_notifications(meta)
    nowdt = utcnow()
    now = fmt_local(nowdt)
    today = nowdt.astimezone(TEHRAN).strftime("%Y-%m-%d")
    day_agg = rollup.setdefault(today, {})
    sample = {"ts": nowdt.isoformat(), "results": {}}

    targets = TARGETS["targets"]
    # Network probes are independent, so run them concurrently. State updates and
    # notifications remain ordered below for deterministic transition handling.
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as executor:
        probes = list(executor.map(probe_target, targets))

    for t, (probe, ci, cert_error) in zip(targets, probes):
        name = t["name"]
        ok, detail, latency, status_code, dns_ms, dns_warning, final_url = probe
        prev = state.get(name, {"up": True, "fail_streak": 0})

        # 2-strikes debounce: absorb a single flaky probe from GitHub's network.
        fail_streak, is_down, was_down = derive_state(prev, ok)
        confirmed_up = not is_down

        # Alert only on confirmed transitions -- never "still up".
        if is_down and not was_down:
            queue_notification(meta, f"\U0001F534 <b>DOWN</b> — {telegram_escape(name)}\n"
                                      f"{telegram_escape(detail)}\n<i>{telegram_escape(now)}</i>")
        elif was_down and ok:
            queue_notification(meta, f"\U0001F7E2 <b>RECOVERED</b> — {telegram_escape(name)}\n"
                                      f"{telegram_escape(detail)}\n<i>{telegram_escape(now)}</i>")

        if t.get("expected_ips") and detail.startswith("DNS "):
            dns_warning = prev.get("dns_warning")
        dns_mismatch = bool(dns_warning)
        previous_dns_mismatch = bool(prev.get("dns_mismatch"))
        if dns_mismatch and not previous_dns_mismatch:
            queue_notification(meta, f"\U0001F7E1 <b>DNS warning</b> — {telegram_escape(name)}\n"
                                      f"{telegram_escape(dns_warning)}\n<i>{telegram_escape(now)}</i>")
        elif t.get("expected_ips") and previous_dns_mismatch and not dns_mismatch:
            queue_notification(meta, f"\U0001F7E2 <b>DNS recovered</b> — {telegram_escape(name)}\n"
                                      f"DNS now matches the configured address.\n"
                                      f"<i>{telegram_escape(now)}</i>")

        new_state = {"up": confirmed_up, "fail_streak": fail_streak,
                     "last_detail": detail, "last_check": now,
                     "last_check_utc": nowdt.isoformat(), "final_url": final_url,
                     "latency_ms": latency, "status_code": status_code, "dns_ms": dns_ms,
                     "dns_warning": dns_warning, "dns_mismatch": dns_mismatch}
        if is_down:
            new_state["incident_started_utc"] = prev.get("incident_started_utc", nowdt.isoformat())

        # Cert-expiry warnings: fire once per threshold crossing, reset when it un-crosses.
        if t.get("check_cert"):
            new_state.update({key: value for key, value in prev.items() if key.startswith("cert_")})
            host = urllib.parse.urlparse(t["url"]).hostname
            if ci:
                if prev.get("cert_failure_streak", 0) >= SETTINGS["cert_failure_threshold"]:
                    queue_notification(meta, f"🟢 <b>TLS inspection recovered</b> — {telegram_escape(name)}")
                new_state["cert_failure_streak"] = 0
                new_state["cert_inspected_utc"] = nowdt.isoformat()
                days = ci["days_left"]
                new_state["cert_days_left"] = days
                new_state["cert_not_after"] = ci["not_after"]
                new_state["cert_not_before"] = ci["not_before"]
                new_state["cert_issuer"] = ci["issuer"]
                for thr in SETTINGS["cert_warn_days"]:
                    crossed_key = f"cert_warned_{thr}"
                    if days <= thr and not prev.get(crossed_key):
                        queue_notification(meta, f"\U0001F7E1 <b>TLS cert</b> for "
                                             f"{telegram_escape(host)} expires in "
                                             f"<b>{days} days</b> (on {telegram_escape(ci['not_after'])}, "
                                             f"issuer {telegram_escape(ci['issuer'])})")
                        new_state[crossed_key] = True
                    elif days <= thr:
                        new_state[crossed_key] = True  # still crossed, stay quiet
                    else:
                        new_state[crossed_key] = False
            elif cert_error:
                streak = prev.get("cert_failure_streak", 0) + 1
                new_state["cert_failure_streak"] = streak
                if streak == SETTINGS["cert_failure_threshold"]:
                    queue_notification(meta, f"🟡 <b>TLS inspection unavailable</b> — {telegram_escape(name)}")
                print(f"TLS inspection failed: {type(cert_error).__name__}")

        state[name] = new_state
        sample["results"][name] = {"ok": ok, "confirmed_up": confirmed_up,
                                   "detail": detail, "latency_ms": latency,
                                   "status_code": status_code, "dns_ms": dns_ms,
                                   "dns_warning": dns_warning, "final_url": final_url}
        if is_down:
            sample["results"][name]["incident_started_utc"] = new_state["incident_started_utc"]

        # Retain legacy daily tallies for continuity, not coverage calculations.
        agg = day_agg.setdefault(name, {"up": 0, "total": 0})
        agg["total"] += 1
        if confirmed_up:
            agg["up"] += 1

    # Retain elapsed days regardless of the actual scheduler cadence.
    history.append(sample)
    history = retain_history(history, nowdt)
    # Expire legacy rollups by date too; they cannot establish coverage.
    for old in list(rollup):
        if datetime.date.fromisoformat(old) < nowdt.astimezone(TEHRAN).date() - datetime.timedelta(days=SETTINGS["retention_days"]):
            del rollup[old]

    # Heartbeat digest so the channel isn't silent between incidents. Fires once
    # `digest_every_hours` have ELAPSED since the previous one (tracked in state),
    # rather than at a fixed minute-of-hour -- GitHub's cron is throttled and jittery,
    # so an elapsed-time gate is the only reliable way to get a roughly-hourly cadence
    # off the 5-min run schedule. Also sendable on demand via the FORCE_DIGEST input.
    every = SETTINGS["digest_every_hours"]
    last_digest = meta.get("last_digest_utc")
    due = True
    if last_digest:
        try:
            elapsed = (nowdt - datetime.datetime.fromisoformat(last_digest)).total_seconds()
            due = elapsed >= every * 3600 - 150   # 150s slack absorbs run-to-run jitter
        except ValueError:
            due = True
    force_digest = os.environ.get("FORCE_DIGEST", "").lower() == "true"
    digest_pending = any(isinstance(event, dict) and event.get("kind") == "digest"
                         for event in meta.get("pending_notifications", []))
    if (force_digest or due) and not digest_pending:
        up = sum(1 for r in sample["results"].values() if r["confirmed_up"])
        total = len(sample["results"])
        label = "Hourly digest" if every == 1 else "Status digest"
        lines = [f"\U0001F4CA <b>{label}</b> — {now}", f"{up}/{total} endpoints confirmed UP", ""]
        for n, r in sample["results"].items():
            mark = "\U0001F7E1" if not r["ok"] and r["confirmed_up"] else "\U0001F7E2" if r["confirmed_up"] else "\U0001F534"
            lat = f" · {r['latency_ms']}ms" if r["latency_ms"] else ""
            lines.append(f"{mark} {telegram_escape(n)}{lat}")
        queue_notification(meta, "\n".join(lines), kind="digest", advance_digest_clock=due)
        meta["pending_notifications"][-1]["digest_utc"] = nowdt.isoformat()

    # New transition, DNS, certificate, and digest events are retried before state
    # is persisted. Failed events remain in _meta.pending_notifications for the next run.
    flush_notifications(meta)

    meta.update({"last_run_utc": utcnow().isoformat(), "config": public_config(),
                 "run_id": os.environ.get("GITHUB_RUN_ID"), "commit": os.environ.get("GITHUB_SHA"),
                 "latest_observation_utc": sample["ts"]})
    # Publish completion last: partial writes cannot advance the heartbeat.
    dump_json_atomic(HISTORY_PATH, history)
    dump_json_atomic(ROLLUP_PATH, rollup)
    dump_json_atomic(STATE_PATH, state)


if __name__ == "__main__":
    if sys.argv[1:] == ["--validate"]:
        validate_config(TARGETS)
        print("Configuration valid")
    elif sys.argv[1:]:
        raise SystemExit("Usage: monitor.py [--validate]")
    else:
        main()
