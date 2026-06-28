#!/usr/bin/env python3
"""Tier-1 blackbox uptime monitor -> private Telegram channel.

State-change alerting only (alerts on UP<->DOWN transitions and cert-threshold
crossings, plus one daily digest). Python standard library only -- no pip
installs: urllib, ssl, socket, json, datetime.
"""

import datetime
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TARGETS = json.load(open(os.path.join(ROOT, "targets.json"), encoding="utf-8"))
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
        return json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


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
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15).read()
        return True
    except urllib.error.HTTPError as e:
        # Telegram returns a JSON body explaining the rejection (bad chat id,
        # bot not an admin, wrong token, etc.) -- surface it.
        print(f"Telegram send failed: HTTP {e.code} {e.read().decode('utf-8', 'ignore')}")
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
    """Return (ok, detail, latency_ms, status_code, dns_ms) for one target.
    latency_ms/status_code are None when the request never completed; dns_ms is
    the DNS-resolution time (measured even on failure so slow DNS is visible)."""
    host = urllib.parse.urlparse(t["url"]).hostname

    # DNS / expected IP -- timed separately so slow resolution shows up.
    dns_start = time.monotonic()
    ip = resolve_ip(host)
    dns_ms = int((time.monotonic() - dns_start) * 1000)
    if ip is None:
        return False, "DNS resolution failed", None, None, dns_ms
    if t.get("expected_ip") and ip != t["expected_ip"]:
        return False, f"Resolves to {ip}, expected {t['expected_ip']} (possible DNS change)", None, None, dns_ms

    # HTTP GET
    start = time.monotonic()
    try:
        req = urllib.request.Request(t["url"], headers={"User-Agent": "mahansco-uptime/1.0"})
        resp = urllib.request.urlopen(req, timeout=SETTINGS["timeout_seconds"])
        body = resp.read(200_000).decode("utf-8", "ignore")
        status = resp.getcode()
    except urllib.error.HTTPError as e:
        status, body = e.code, ""
    except Exception as e:  # noqa: BLE001
        return False, f"Request failed: {type(e).__name__}", None, None, dns_ms
    latency_ms = int((time.monotonic() - start) * 1000)

    if status != t["expect_status"]:
        return False, f"HTTP {status} (expected {t['expect_status']})", latency_ms, status, dns_ms
    if t.get("must_contain") and t["must_contain"] not in body:
        return False, f"Body missing marker '{t['must_contain']}'", latency_ms, status, dns_ms
    if latency_ms > SETTINGS["latency_warn_ms"]:
        return True, f"OK but slow {latency_ms}ms", latency_ms, status, dns_ms
    return True, f"OK {latency_ms}ms", latency_ms, status, dns_ms


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
    nowdt = utcnow()
    now = fmt_local(nowdt)
    today = nowdt.astimezone(TEHRAN).strftime("%Y-%m-%d")
    day_agg = rollup.setdefault(today, {})
    sample = {"ts": now, "results": {}}

    for t in TARGETS["targets"]:
        name = t["name"]
        ok, detail, latency, status_code, dns_ms = check(t)
        prev = state.get(name, {"up": True, "fail_streak": 0})

        # 2-strikes debounce: absorb a single flaky probe from GitHub's network.
        fail_streak = 0 if ok else prev.get("fail_streak", 0) + 1
        is_down = fail_streak >= SETTINGS["failures_before_down"]
        was_down = not prev.get("up", True)

        # Alert only on confirmed transitions -- never "still up".
        if is_down and not was_down:
            telegram(f"\U0001F534 <b>DOWN</b> — {name}\n{detail}\n<i>{now}</i>")
        elif was_down and ok:
            telegram(f"\U0001F7E2 <b>RECOVERED</b> — {name}\n{detail}\n<i>{now}</i>")

        new_state = {"up": not is_down, "fail_streak": fail_streak,
                     "last_detail": detail, "last_check": now,
                     "latency_ms": latency, "status_code": status_code, "dns_ms": dns_ms}

        # Cert-expiry warnings: fire once per threshold crossing, reset when it un-crosses.
        if t.get("check_cert"):
            host = urllib.parse.urlparse(t["url"]).hostname
            try:
                ci = cert_info(host)
                days = ci["days_left"]
                new_state["cert_days_left"] = days
                new_state["cert_not_after"] = ci["not_after"]
                new_state["cert_not_before"] = ci["not_before"]
                new_state["cert_issuer"] = ci["issuer"]
                for thr in SETTINGS["cert_warn_days"]:
                    crossed_key = f"cert_warned_{thr}"
                    if days <= thr and not prev.get(crossed_key):
                        telegram(f"\U0001F7E1 <b>TLS cert</b> for {host} expires in "
                                 f"<b>{days} days</b> (on {ci['not_after']}, issuer {ci['issuer']})")
                        new_state[crossed_key] = True
                    elif days <= thr:
                        new_state[crossed_key] = True  # still crossed, stay quiet
                    else:
                        new_state[crossed_key] = False
            except Exception as e:  # noqa: BLE001
                print(f"cert check failed for {host}:", e)

        state[name] = new_state
        sample["results"][name] = {"ok": ok, "detail": detail, "latency_ms": latency,
                                   "status_code": status_code, "dns_ms": dns_ms}

        # Daily uptime rollup: compact per-day up/total tallies back the 30-day
        # window on the status page without bloating history.json.
        agg = day_agg.setdefault(name, {"up": 0, "total": 0})
        agg["total"] += 1
        if ok:
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
    meta = state.get("_meta", {})
    last_digest = meta.get("last_digest_utc")
    due = True
    if last_digest:
        try:
            elapsed = (nowdt - datetime.datetime.fromisoformat(last_digest)).total_seconds()
            due = elapsed >= every * 3600 - 150   # 150s slack absorbs run-to-run jitter
        except ValueError:
            due = True
    force_digest = os.environ.get("FORCE_DIGEST", "").lower() == "true"
    if force_digest or due:
        up = sum(1 for r in sample["results"].values() if r["ok"])
        total = len(sample["results"])
        label = "Hourly digest" if every == 1 else "Status digest"
        lines = [f"\U0001F4CA <b>{label}</b> — {now}", f"{up}/{total} endpoints healthy", ""]
        for n, r in sample["results"].items():
            mark = "\U0001F7E2" if r["ok"] else "\U0001F534"
            lat = f" · {r['latency_ms']}ms" if r["latency_ms"] else ""
            lines.append(f"{mark} {n}{lat}")
        sent = telegram("\n".join(lines))
        # Only the scheduled (due) heartbeat advances the clock; an on-demand
        # FORCE_DIGEST send must not reset the hourly cadence.
        if due and sent:
            meta["last_digest_utc"] = nowdt.isoformat()
    state["_meta"] = meta

    json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(history, open(HISTORY_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(rollup, open(ROLLUP_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
