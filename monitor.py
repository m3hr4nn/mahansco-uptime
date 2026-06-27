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

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def utcnow():
    """Timezone-aware current UTC time (utcnow() is deprecated in 3.12)."""
    return datetime.datetime.now(datetime.timezone.utc)


def load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15).read()
    except Exception as e:  # noqa: BLE001 - never let a send failure crash a run
        print("Telegram send failed:", e)


def cert_days_left(host):
    """TLS certificate days-to-expiry for host:443, or None on failure."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=SETTINGS["timeout_seconds"]) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            not_after = ssock.getpeercert()["notAfter"]
    # OpenSSL format, e.g. "Sep  9 12:00:00 2026 GMT" (always UTC).
    expiry = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
    expiry = expiry.replace(tzinfo=datetime.timezone.utc)
    return (expiry - utcnow()).days


def resolve_ip(host):
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return None


def check(t):
    """Return (ok: bool, detail: str, latency_ms: int|None) for one target."""
    host = urllib.parse.urlparse(t["url"]).hostname

    # DNS / expected IP
    ip = resolve_ip(host)
    if ip is None:
        return False, "DNS resolution failed", None
    if t.get("expected_ip") and ip != t["expected_ip"]:
        return False, f"Resolves to {ip}, expected {t['expected_ip']} (possible DNS change)", None

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
        return False, f"Request failed: {type(e).__name__}", None
    latency_ms = int((time.monotonic() - start) * 1000)

    if status != t["expect_status"]:
        return False, f"HTTP {status} (expected {t['expect_status']})", latency_ms
    if t.get("must_contain") and t["must_contain"] not in body:
        return False, f"Body missing marker '{t['must_contain']}'", latency_ms
    if latency_ms > SETTINGS["latency_warn_ms"]:
        return True, f"OK but slow {latency_ms}ms", latency_ms
    return True, f"OK {latency_ms}ms", latency_ms


def main():
    state = load_json(STATE_PATH, {})
    history = load_json(HISTORY_PATH, [])
    now = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    sample = {"ts": now, "results": {}}

    for t in TARGETS["targets"]:
        name = t["name"]
        ok, detail, latency = check(t)
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
                     "last_detail": detail, "last_check": now}

        # Cert-expiry warnings: fire once per threshold crossing, reset when it un-crosses.
        if t.get("check_cert"):
            host = urllib.parse.urlparse(t["url"]).hostname
            try:
                days = cert_days_left(host)
                new_state["cert_days_left"] = days
                for thr in SETTINGS["cert_warn_days"]:
                    crossed_key = f"cert_warned_{thr}"
                    if days <= thr and not prev.get(crossed_key):
                        telegram(f"\U0001F7E1 <b>TLS cert</b> for {host} expires in <b>{days} days</b>")
                        new_state[crossed_key] = True
                    elif days <= thr:
                        new_state[crossed_key] = True  # still crossed, stay quiet
                    else:
                        new_state[crossed_key] = False
            except Exception as e:  # noqa: BLE001
                print(f"cert check failed for {host}:", e)

        state[name] = new_state
        sample["results"][name] = {"ok": ok, "detail": detail, "latency_ms": latency}

    # Rolling history: ~30 days at 15-min cadence (2880 samples).
    history.append(sample)
    history = history[-2880:]

    json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(history, open(HISTORY_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    # Daily digest -- the only "heartbeat" so the channel isn't silent for days.
    nowdt = utcnow()
    if nowdt.hour == SETTINGS["daily_digest_hour_utc"] and nowdt.minute < 15:
        up = sum(1 for r in sample["results"].values() if r["ok"])
        total = len(sample["results"])
        lines = [f"\U0001F4CA <b>Daily digest</b> — {now}", f"{up}/{total} endpoints healthy", ""]
        for n, r in sample["results"].items():
            mark = "\U0001F7E2" if r["ok"] else "\U0001F534"
            lat = f" · {r['latency_ms']}ms" if r["latency_ms"] else ""
            lines.append(f"{mark} {n}{lat}")
        telegram("\n".join(lines))


if __name__ == "__main__":
    main()
