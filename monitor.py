#!/usr/bin/env python3
"""Tier-0 blackbox uptime monitor -> private Telegram channel.

State-change alerting only (alerts on UP<->DOWN transitions and cert-threshold
crossings, plus one daily digest). Python standard library only -- no pip
installs: urllib, ssl, socket, json, datetime.
"""

import datetime
from concurrent.futures import ThreadPoolExecutor
import html
import json
import os
import socket
import ssl
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
                         "digest_every_hours")
    for key in positive_settings:
        if not isinstance(settings.get(key), (int, float)) or settings[key] <= 0:
            raise ValueError(f"settings.{key} must be a positive number")
    if not isinstance(settings.get("cert_warn_days"), list):
        raise ValueError("settings.cert_warn_days must be a list")
    if not isinstance(settings.get("digest_every_hours", 24), (int, float)):
        raise ValueError("settings.digest_every_hours must be a number")

    names = set()
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            raise ValueError(f"targets[{index}] must be an object")
        name = target.get("name")
        url = target.get("url")
        parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError(f"targets[{index}].name must be non-empty and unique")
        if parsed is None or parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"targets[{index}].url must be an HTTPS URL with a hostname")
        if not isinstance(target.get("expect_status"), int) or not 100 <= target["expect_status"] <= 599:
            raise ValueError(f"targets[{index}].expect_status must be an HTTP status code")
        if "must_contain" in target and not isinstance(target["must_contain"], str):
            raise ValueError(f"targets[{index}].must_contain must be a string")
        if "expected_ip" in target and not isinstance(target["expected_ip"], str):
            raise ValueError(f"targets[{index}].expected_ip must be a string")
        names.add(name)


with open(os.path.join(ROOT, "targets.json"), encoding="utf-8") as targets_file:
    TARGETS = json.load(targets_file)
validate_config(TARGETS)
SETTINGS = TARGETS["settings"]

STATE_PATH = os.path.join(ROOT, "state.json")
HISTORY_PATH = os.path.join(ROOT, "history.json")
ROLLUP_PATH = os.path.join(ROOT, "uptime_daily.json")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# Iran Standard Time is a fixed UTC+03:30 (no DST since 2022). All human-facing
# timestamps (Telegram, status page) are shown in Tehran time; UTC is still used
# internally for any elapsed-time math.
TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))


def utcnow():
    """Timezone-aware current UTC time (utcnow() is deprecated in 3.12)."""
    return datetime.datetime.now(datetime.timezone.utc)


def fmt_local(dt):
    """Format a timezone-aware datetime as a Tehran-time display string."""
    return dt.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M") + " IRST"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as data_file:
            return json.load(data_file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def dump_json_atomic(path, value):
    """Write JSON without leaving a partially-written state file behind."""
    directory = os.path.dirname(path) or "."
    fd, temporary_path = tempfile.mkstemp(prefix=".monitor-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as data_file:
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
    if not isinstance(pending, list):
        pending = []
        meta["pending_notifications"] = pending
    pending.append({
        "kind": kind,
        "text": text,
        "advance_digest_clock": advance_digest_clock,
        "queued_at": utcnow().isoformat(),
    })


def flush_notifications(meta):
    """Deliver queued messages in order, retaining anything Telegram rejects."""
    pending = meta.setdefault("pending_notifications", [])
    if not isinstance(pending, list):
        pending = []
        meta["pending_notifications"] = pending
    remaining = []
    for index, event in enumerate(pending):
        if isinstance(event, str):  # tolerate an older/manual state format
            event = {"kind": "alert", "text": event}
        if not isinstance(event, dict) or not event.get("text"):
            continue
        if not telegram(event["text"]):
            remaining.extend(pending[index:])
            break
        if event.get("kind") == "digest" and event.get("advance_digest_clock"):
            meta["last_digest_utc"] = event.get("digest_utc", utcnow().isoformat())
    meta["pending_notifications"] = remaining


def telegram(text):
    """Send a message. Returns True on success. Never raises (a send failure
    must not crash a monitoring run), but prints enough to debug from CI logs."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as response:
            response.read()
        return True
    except urllib.error.HTTPError as e:
        # Telegram returns a JSON body explaining the rejection (bad chat id,
        # bot not an admin, wrong token, etc.) -- surface it.
        print(f"Telegram send failed: HTTP {e.code} {e.read().decode('utf-8', 'ignore')}")
        e.close()
    except Exception as e:  # noqa: BLE001
        print("Telegram send failed:", e)
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


def resolve_ip(host):
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return None


def check(t):
    """Return (ok, detail, latency_ms, status_code, dns_ms, dns_warning).
    latency_ms/status_code are None when the request never completed; dns_ms is
    the DNS-resolution time (measured even on failure so slow DNS is visible).
    An expected-IP mismatch is advisory: public reachability remains the primary
    Tier-0 signal, and legitimate DNS migrations must not look like outages."""
    host = urllib.parse.urlparse(t["url"]).hostname

    # DNS / expected IP -- timed separately so slow resolution shows up.
    dns_start = time.monotonic()
    ip = resolve_ip(host)
    dns_ms = int((time.monotonic() - dns_start) * 1000)
    if ip is None:
        return False, "DNS resolution failed", None, None, dns_ms, None
    dns_warning = None
    if t.get("expected_ip") and ip != t["expected_ip"]:
        dns_warning = f"Resolves to {ip}, expected {t['expected_ip']}"

    # HTTP GET
    start = time.monotonic()
    try:
        req = urllib.request.Request(t["url"], headers={"User-Agent": "mahansco-uptime/1.0"})
        with urllib.request.urlopen(req, timeout=SETTINGS["timeout_seconds"]) as resp:
            body = resp.read(200_000).decode("utf-8", "ignore")
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        status, body = e.code, ""
        e.close()
    except Exception as e:  # noqa: BLE001
        return False, f"Request failed: {type(e).__name__}", None, None, dns_ms, dns_warning
    latency_ms = int((time.monotonic() - start) * 1000)

    if status != t["expect_status"]:
        return False, f"HTTP {status} (expected {t['expect_status']})", latency_ms, status, dns_ms, dns_warning
    if t.get("must_contain") and t["must_contain"] not in body:
        return False, f"Body missing marker '{t['must_contain']}'", latency_ms, status, dns_ms, dns_warning
    if latency_ms > SETTINGS["latency_warn_ms"]:
        return True, f"OK but slow {latency_ms}ms", latency_ms, status, dns_ms, dns_warning
    return True, f"OK {latency_ms}ms", latency_ms, status, dns_ms, dns_warning


def probe_target(t):
    """Run the external HTTP and optional TLS probes for one target."""
    result = check(t)
    cert = None
    cert_error = None
    if t.get("check_cert"):
        host = urllib.parse.urlparse(t["url"]).hostname
        try:
            cert = cert_info(host)
        except Exception as e:  # noqa: BLE001
            cert_error = e
    return result, cert, cert_error


def derive_state(prev, ok):
    """Apply the configured consecutive-failure debounce to one probe result."""
    fail_streak = 0 if ok else prev.get("fail_streak", 0) + 1
    is_down = fail_streak >= SETTINGS["failures_before_down"]
    was_down = not prev.get("up", True)
    return fail_streak, is_down, was_down


def main():
    # On-demand delivery test (workflow_dispatch input). Confirms the bot token,
    # chat id, and bot-admin status end-to-end without needing a real outage.
    if os.environ.get("TEST_PING", "").lower() == "true":
        now = fmt_local(utcnow())
        ok = telegram(f"✅ <b>Test ping</b> — mahansco-uptime is wired up correctly.\n<i>{now}</i>")
        print("Test ping delivered." if ok else "Test ping FAILED — see error above.")

    state = load_json(STATE_PATH, {})
    history = load_json(HISTORY_PATH, [])
    rollup = load_json(ROLLUP_PATH, {})
    if not isinstance(state, dict):
        state = {}
    if not isinstance(history, list):
        history = []
    if not isinstance(rollup, dict):
        rollup = {}
    meta = state.get("_meta", {})
    if not isinstance(meta, dict):
        meta = {}
    state["_meta"] = meta
    flush_notifications(meta)
    nowdt = utcnow()
    now = fmt_local(nowdt)
    today = nowdt.astimezone(TEHRAN).strftime("%Y-%m-%d")
    day_agg = rollup.setdefault(today, {})
    sample = {"ts": now, "results": {}}

    targets = TARGETS["targets"]
    # Network probes are independent, so run them concurrently. State updates and
    # notifications remain ordered below for deterministic transition handling.
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as executor:
        probes = list(executor.map(probe_target, targets))

    for t, (probe, ci, cert_error) in zip(targets, probes):
        name = t["name"]
        ok, detail, latency, status_code, dns_ms, dns_warning = probe
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

        dns_mismatch = bool(dns_warning)
        previous_dns_mismatch = bool(prev.get("dns_mismatch"))
        if dns_mismatch and not previous_dns_mismatch:
            queue_notification(meta, f"\U0001F7E1 <b>DNS warning</b> — {telegram_escape(name)}\n"
                                      f"{telegram_escape(dns_warning)}\n<i>{telegram_escape(now)}</i>")
        elif previous_dns_mismatch and not dns_mismatch:
            queue_notification(meta, f"\U0001F7E2 <b>DNS recovered</b> — {telegram_escape(name)}\n"
                                      f"DNS now matches the configured address.\n"
                                      f"<i>{telegram_escape(now)}</i>")

        new_state = {"up": confirmed_up, "fail_streak": fail_streak,
                     "last_detail": detail, "last_check": now,
                     "latency_ms": latency, "status_code": status_code, "dns_ms": dns_ms,
                     "dns_warning": dns_warning, "dns_mismatch": dns_mismatch}

        # Cert-expiry warnings: fire once per threshold crossing, reset when it un-crosses.
        if t.get("check_cert"):
            host = urllib.parse.urlparse(t["url"]).hostname
            if ci:
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
                print(f"cert check failed for {host}:", cert_error)

        state[name] = new_state
        sample["results"][name] = {"ok": ok, "confirmed_up": confirmed_up,
                                   "detail": detail, "latency_ms": latency,
                                   "status_code": status_code, "dns_ms": dns_ms,
                                   "dns_warning": dns_warning}

        # Daily uptime rollup: compact per-day up/total tallies back the 30-day
        # window on the status page without bloating history.json.
        agg = day_agg.setdefault(name, {"up": 0, "total": 0})
        agg["total"] += 1
        if confirmed_up:
            agg["up"] += 1

    # Rolling history: ~10 days at the 5-min cadence (2880 samples) -- enough for the
    # 24h/7d windows and incident log; longer windows come from the daily rollup.
    history.append(sample)
    history = history[-2880:]
    # Keep ~5 weeks of daily rollups so the 30-day window always has full coverage.
    for old in sorted(rollup)[:-35]:
        del rollup[old]

    # Heartbeat digest so the channel isn't silent between incidents. Fires once
    # `digest_every_hours` have ELAPSED since the previous one (tracked in state),
    # rather than at a fixed minute-of-hour -- GitHub's cron is throttled and jittery,
    # so an elapsed-time gate is the only reliable way to get a roughly-hourly cadence
    # off the 5-min run schedule. Also sendable on demand via the FORCE_DIGEST input.
    every = SETTINGS.get("digest_every_hours", 24)
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
        lines = [f"\U0001F4CA <b>{label}</b> — {now}", f"{up}/{total} endpoints healthy", ""]
        for n, r in sample["results"].items():
            mark = "\U0001F7E2" if r["confirmed_up"] else "\U0001F534"
            lat = f" · {r['latency_ms']}ms" if r["latency_ms"] else ""
            lines.append(f"{mark} {telegram_escape(n)}{lat}")
        queue_notification(meta, "\n".join(lines), kind="digest", advance_digest_clock=due)
        meta["pending_notifications"][-1]["digest_utc"] = nowdt.isoformat()

    # New transition, DNS, certificate, and digest events are retried before state
    # is persisted. Failed events remain in _meta.pending_notifications for the next run.
    flush_notifications(meta)

    dump_json_atomic(STATE_PATH, state)
    dump_json_atomic(HISTORY_PATH, history)
    dump_json_atomic(ROLLUP_PATH, rollup)


if __name__ == "__main__":
    main()
