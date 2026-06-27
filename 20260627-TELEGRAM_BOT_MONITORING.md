# Telegram Bot — Endpoint Uptime Monitor (Tier 1)

**Status:** Spec + reference implementation (ready to build)
**Type:** Monitoring tool (free, external, blackbox HTTP)
**Owner:** IT team (private Telegram channel)
**Created:** 2026-06-27
**Pattern source:** Mirrors `github.com/m3hr4nn/googleipmonitor` (GitHub Actions cron → Telegram, free, state-change alerting, GitHub Pages status page).

---

## 1. Purpose

A standalone, free, **external** uptime monitor for Mahansco's public web surface. It runs on
GitHub's infrastructure (NOT on the prod host — a monitor must never share fate with what it
watches), hits the public endpoints on a schedule, and posts to a **private** Telegram channel
for you and the team. No public channel, no third-party SaaS, no cost.

**Goal in one line:** at any moment you (and the team) have phone-visible proof that
`https://mahansco.ir/` is up, fast, trusted (valid TLS), and serving the real landing page.

This is **Tier 1** only (availability from the endpoint's point of view). Internal health
(disk, DB, Redis, backups, containers) is explicitly out of scope here — that needs an agent
*inside* the network and is tracked separately.

---

## 2. What can be monitored from an endpoint (blackbox) point of view

You do not need access to the server to learn a surprising amount. Everything below is
measurable with a plain HTTPS `GET` from outside.

### 2.1 Per-endpoint signals (the core of a GET check)

| Signal | What it proves | How |
|---|---|---|
| **HTTP status code** | Endpoint is serving / not 5xx / not redirect-looping | `GET`, compare to expected (200, or 401/403 for protected APIs that should *answer*) |
| **Reachability (up/down)** | The most basic "is it alive" | Did the request complete at all (no connection refused / timeout)? |
| **Response latency** | Performance degradation *before* it becomes an outage | Measure total request time; alert when above a threshold (e.g. >3s) |
| **Body content marker** | The page is the *real* page, not an error/parking/maintenance page | Assert response body contains an expected string (e.g. `System Online`, app `<div id="root">`) |
| **Response size** | Catches blank/truncated pages even when status is 200 | Compare `content-length` to an expected floor |
| **Redirect behavior** | HTTP→HTTPS works; no unexpected redirect to a wrong host | Inspect redirect chain / `Location` |
| **Expected final IP** | DNS hijack / misconfigured A record detection | Resolve hostname, compare to known-good IP set (you already do this in googleipmonitor) |

### 2.2 Transport / TLS signals

| Signal | What it proves | Alert rule |
|---|---|---|
| **TLS certificate validity** | Cert chain trusted, not expired/revoked | Fail = critical |
| **Cert days-to-expiry** | Renew before it bites | Warn at 21 / 7 / 1 days |
| **Cert subject / issuer** | Right cert for the right host (no swap) | Mismatch = critical |
| **HTTPS handshake success** | Port 443 open and negotiating | Fail = down |
| **HTTP/2 availability** | Protocol regressions | Informational |

### 2.3 DNS signals

| Signal | What it proves |
|---|---|
| **Resolves at all** | Domain not lapsed / nameserver not broken |
| **Resolves to expected IP(s)** | No hijack, no stale record after a migration |
| **Resolution latency** | DNS provider health |

### 2.4 CDN / freshness signals (Mahansco-specific, high value)

Mahansco sits behind **Parspack WCDN**. The CDN exposes useful headers and the app ships
content-hashed asset bundles. Both let you catch problems unique to a cached SPA:

| Signal | What it proves | How |
|---|---|---|
| **CDN cache status** | Whether you're seeing edge cache or origin | `wcdn-status: Hit/Miss` header |
| **CDN edge / rayid** | Which edge served you (debugging) | `wcdn-edge`, `wcdn-rayid` headers |
| **Stale-deploy detection** | The CDN is still serving the *old* build after a deploy | Parse the hashed bundle name from the SPA HTML (e.g. `main.<hash>.js`) and compare to the hash you expect from the latest release. This automates the "purge the CDN after every deploy" pain. |
| **`Last-Modified` / `ETag`** | Content actually changed when it should have | Compare across runs |

### 2.5 What is NOT visible from outside (and needs a different tool)

For honesty: a blackbox monitor **cannot** see disk usage, DB/Redis health, container restarts,
queue depth, backup freshness, or memory. Those require an internal agent on the prod host. Keep
those out of this repo — this is Tier 1.

---

## 3. The actual targets (probed 2026-06-27, live values)

These are real, confirmed responses — use them as the monitor's expected baseline.

| # | Endpoint | Method | Expected | Body marker | Notes |
|---|---|---|---|---|---|
| 1 | `https://mahansco.ir/` | GET | 200 | `System Online` | Landing page. Title: `Mahansco - IranSCM \| سامانه مدیریت زنجیره تأمین`. This is your "proof" page. |
| 2 | `https://app.mahansco.ir/` | GET | 200 | `<div id="root">` | React SPA shell. `theme-color #0E4D92`. Bundle: `main.<hash>.js` (freshness check). |
| 3 | `https://app.mahansco.ir/api/health/` | GET | 200 | — | **Real health endpoint.** Proves the app backend (not just static files) answers. |
| 4 | `https://app.mahansco.ir/graphql/` | GET | 200 | — | GraphQL endpoint reachable. |
| 5 | `https://mahansco.ir/api/` | GET | 200 | — | API surface on the marketing host. |
| 6 | `https://mahansco.ir/robots.txt` | GET | 200 | — | Cheap "static serving works" canary. |

**TLS:** `mahansco.ir` cert valid until **2026-09-09** (set a cert-expiry warn).
**CDN:** `server: WCDN 3.8.6`, `wcdn-cache-policy: SMART`.
**DNS:** both hosts currently resolve to edge `185.208.173.17`.

> Note: external probes run from GitHub's runners (US/EU). For an Iran-hosted, CDN-fronted site
> this measures *world reachability* — exactly what a public uptime monitor should measure — but
> can be slightly noisier than a probe from inside Iran. Treat single missed checks with a
> "2-strikes" rule (see §6) to avoid false alarms.

---

## 4. How the GitHub Actions monitor works

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions (cron: every 15 min)                         │
│                                                              │
│  1. Checkout repo (incl. committed state.json)               │
│  2. python monitor.py:                                       │
│       - for each target: GET, time it, check status+marker   │
│       - check TLS cert days-to-expiry                        │
│       - check DNS resolves to expected IP                    │
│       - compare result to previous state (state.json)        │
│       - on STATE CHANGE only → send Telegram message         │
│       - (optional) once/day → send digest                    │
│  3. Write updated state.json + history → commit back         │
│  4. (optional) regenerate docs/index.html status page        │
└─────────────────────────────────────────────────────────────┘
                          │  Telegram Bot API (HTTPS POST)
                          ▼
            ┌──────────────────────────────┐
            │  PRIVATE Telegram channel     │
            │  (you + team, bot is admin)   │
            └──────────────────────────────┘
```

**Why state.json committed back to the repo:** it's the free, zero-infra way to remember
"was it up last time?" so you alert only on **transitions** (UP→DOWN, DOWN→UP, cert crossing a
threshold) instead of spamming "still up ✅" every 15 minutes. Same trick googleipmonitor uses.

**Cadence:** `*/15 * * * *` = 4×/hour (matches googleipmonitor). GitHub cron can be delayed a few
minutes under load — fine for a free tier. Don't go below ~5 min; GitHub will throttle.

---

## 5. Repository layout

```
mahansco-uptime/
├── .github/
│   └── workflows/
│       └── monitor.yml          # cron trigger + run + commit-back
├── monitor.py                   # the checker (stdlib only, no pip)
├── targets.json                 # what to monitor (editable, no code change)
├── state.json                   # last-known state (auto-committed by the bot)
├── history.json                 # rolling uptime samples (for the digest/status page)
├── docs/
│   └── index.html               # optional GitHub Pages status page
└── README.md
```

---

## 6. Reference implementation

### 6.1 `targets.json`

```json
{
  "settings": {
    "timeout_seconds": 20,
    "latency_warn_ms": 3000,
    "failures_before_down": 2,
    "cert_warn_days": [21, 7, 1],
    "daily_digest_hour_utc": 5
  },
  "targets": [
    { "name": "Landing (mahansco.ir)",      "url": "https://mahansco.ir/",                 "expect_status": 200, "must_contain": "System Online", "check_cert": true,  "expected_ip": "185.208.173.17" },
    { "name": "App SPA (app.mahansco.ir)",   "url": "https://app.mahansco.ir/",             "expect_status": 200, "must_contain": "<div id=\"root\">", "check_cert": true },
    { "name": "App health API",              "url": "https://app.mahansco.ir/api/health/",  "expect_status": 200, "must_contain": "", "check_cert": false },
    { "name": "GraphQL endpoint",            "url": "https://app.mahansco.ir/graphql/",     "expect_status": 200, "must_contain": "", "check_cert": false },
    { "name": "Marketing API",               "url": "https://mahansco.ir/api/",             "expect_status": 200, "must_contain": "", "check_cert": false },
    { "name": "robots.txt canary",           "url": "https://mahansco.ir/robots.txt",       "expect_status": 200, "must_contain": "", "check_cert": false }
  ]
}
```

### 6.2 `monitor.py` (Python 3, standard library only — no `pip install`)

```python
#!/usr/bin/env python3
"""Tier-1 blackbox uptime monitor → private Telegram channel.
State-change alerting only. Stdlib only (urllib, ssl, socket)."""

import json, os, ssl, socket, time, datetime, urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
TARGETS = json.load(open(os.path.join(ROOT, "targets.json")))
SETTINGS = TARGETS["settings"]

STATE_PATH = os.path.join(ROOT, "state.json")
HISTORY_PATH = os.path.join(ROOT, "history.json")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def load_json(path, default):
    try:
        return json.load(open(path))
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
    except Exception as e:
        print("Telegram send failed:", e)


def cert_days_left(host):
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=SETTINGS["timeout_seconds"]) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            not_after = ssock.getpeercert()["notAfter"]
    expiry = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
    return (expiry - datetime.datetime.utcnow()).days


def resolve_ip(host):
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return None


def check(t):
    """Return (ok: bool, detail: str, latency_ms: int|None)."""
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
    except Exception as e:
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
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    sample = {"ts": now, "results": {}}

    for t in TARGETS["targets"]:
        name = t["name"]
        ok, detail, latency = check(t)
        prev = state.get(name, {"up": True, "fail_streak": 0})

        # 2-strikes debounce to absorb a single flaky probe
        fail_streak = 0 if ok else prev.get("fail_streak", 0) + 1
        is_down = fail_streak >= SETTINGS["failures_before_down"]
        was_down = not prev.get("up", True)

        # alert only on confirmed transitions
        if is_down and not was_down:
            telegram(f"🔴 <b>DOWN</b> — {name}\n{detail}\n<i>{now}</i>")
        elif was_down and ok:
            telegram(f"🟢 <b>RECOVERED</b> — {name}\n{detail}\n<i>{now}</i>")

        state[name] = {"up": not is_down, "fail_streak": fail_streak,
                       "last_detail": detail, "last_check": now}
        sample["results"][name] = {"ok": ok, "detail": detail, "latency_ms": latency}

        # cert expiry warnings (separate cadence: only when crossing a threshold)
        if t.get("check_cert"):
            host = urllib.parse.urlparse(t["url"]).hostname
            try:
                days = cert_days_left(host)
                for thr in SETTINGS["cert_warn_days"]:
                    crossed_key = f"cert_warned_{thr}"
                    if days <= thr and not prev.get(crossed_key):
                        telegram(f"🟡 <b>TLS cert</b> for {host} expires in <b>{days} days</b>")
                        state[name][crossed_key] = True
                    elif days > thr:
                        state[name][crossed_key] = False
            except Exception as e:
                print(f"cert check failed for {host}:", e)

    # rolling history (keep ~30 days at 15-min cadence ≈ 2880 samples)
    history.append(sample)
    history = history[-2880:]

    json.dump(state, open(STATE_PATH, "w"), indent=2, ensure_ascii=False)
    json.dump(history, open(HISTORY_PATH, "w"), indent=2, ensure_ascii=False)

    # optional daily digest
    if datetime.datetime.utcnow().hour == SETTINGS["daily_digest_hour_utc"] and datetime.datetime.utcnow().minute < 15:
        up = sum(1 for r in sample["results"].values() if r["ok"])
        total = len(sample["results"])
        lines = [f"📊 <b>Daily digest</b> — {now}", f"{up}/{total} endpoints healthy", ""]
        for n, r in sample["results"].items():
            mark = "🟢" if r["ok"] else "🔴"
            lat = f" · {r['latency_ms']}ms" if r["latency_ms"] else ""
            lines.append(f"{mark} {n}{lat}")
        telegram("\n".join(lines))


if __name__ == "__main__":
    main()
```

### 6.3 `.github/workflows/monitor.yml`

```yaml
name: Uptime Monitor

on:
  schedule:
    - cron: "*/15 * * * *"   # every 15 minutes (4×/hour)
  workflow_dispatch: {}       # manual run button

permissions:
  contents: write             # needed to commit state.json back

concurrency:
  group: uptime-monitor
  cancel-in-progress: false

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run monitor
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID:   ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python monitor.py

      - name: Persist state
        run: |
          git config user.name  "uptime-bot"
          git config user.email "uptime-bot@users.noreply.github.com"
          git add state.json history.json docs/ 2>/dev/null || true
          git diff --staged --quiet || git commit -m "chore: update monitor state [skip ci]"
          git push
```

---

## 7. Setting up the PRIVATE Telegram channel + bot

1. **Create the bot:** in Telegram, message **@BotFather** → `/newbot` → name it (e.g.
   `Mahansco Uptime`) → receive the **bot token** (`123456:ABC-...`).
2. **Create a PRIVATE channel:** Telegram → New Channel → set **Private**. Add your team members.
3. **Add the bot as an admin** of that channel (channels only accept posts from admins).
4. **Get the channel chat id:**
   - Post any message in the channel.
   - Temporarily forward it to **@userinfobot**, OR
   - Call `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `channel_post.chat.id`.
   - Private channel ids look like **`-100xxxxxxxxxx`** (keep the `-100`).
5. **Store both as GitHub Secrets** (repo → Settings → Secrets and variables → Actions):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   Per project policy, **also record both in KeePass** (`IranSCM/Monitoring` group) — never commit
   the token to the repo.
6. **Test:** run the workflow via **"Run workflow"** (workflow_dispatch). Temporarily break a
   target marker to confirm a 🔴 fires, then revert to confirm 🟢 recovery.

---

## 8. Cost, limits, honest tradeoffs

- **Cost:** free. GitHub Actions free tier easily covers a 15-min cron; Telegram Bot API is free.
- **Resolution:** not real-time — up to ~15 min blind between checks (plus possible GitHub cron
  delay). Acceptable for Tier 1.
- **Vantage point:** GitHub runners are outside Iran → measures world reachability; debounced with
  a 2-strikes rule to suppress transient international-routing noise.
- **No internal visibility:** disk, DB, Redis, backups, containers are invisible here by design.
- **Graduation path (if needed later):** self-hosted **Uptime Kuma** (MIT, free, OSS-policy
  compliant) adds sub-minute checks + native Telegram, but needs a box to run on. Build-your-own
  (this) wins when you want the GitHub-Pages public status page and full control, like googleipmonitor.

---

## 9. The complete build prompt (copy-paste into a fresh repo's Claude session)

> Paste the **entire** block below into Claude Code (or any agent) **inside a brand-new empty
> GitHub repo**. It is fully self-contained — it does not depend on the rest of this document —
> and will scaffold every file, the workflow, and the README.

```
ROLE
You are setting up a brand-new standalone GitHub repository called "mahansco-uptime". Build a
free, Tier-1 (blackbox / endpoint-only) website uptime monitor from scratch in this empty repo.

STACK (hard constraints)
- GitHub Actions cron as the runner (runs externally on GitHub's infrastructure — NOT on the
  monitored server).
- Python 3.12, STANDARD LIBRARY ONLY (urllib, ssl, socket, json, datetime). No pip installs,
  no requirements.txt.
- Telegram Bot API for delivery, to a PRIVATE Telegram channel (just an owner + small team; no
  public audience).
- Everything free. No paid SaaS. No external services besides GitHub and Telegram.

WHAT IT MONITORS — TARGETS (issue an HTTPS GET to each; these are real, confirmed live values)
  1. https://mahansco.ir/                → expect 200; body MUST contain the string: System Online
                                           check TLS cert; expected DNS IP: 185.208.173.17
  2. https://app.mahansco.ir/            → expect 200; body MUST contain: <div id="root">
                                           check TLS cert
  3. https://app.mahansco.ir/api/health/ → expect 200  (proves the app BACKEND answers, not just static files)
  4. https://app.mahansco.ir/graphql/    → expect 200
  5. https://mahansco.ir/api/            → expect 200
  6. https://mahansco.ir/robots.txt      → expect 200

PER-TARGET CHECKS (measure/verify all of these on every run)
- HTTP status code equals the expected status.
- Response latency in ms; flag "slow" when above latency_warn_ms (default 3000) but still treat
  as UP.
- Response body contains the configured marker string (when one is set) and is not empty.
- DNS resolves; when an expected_ip is configured, the resolved IP must match it (else report a
  possible DNS change / hijack).
- For cert-checked targets: compute TLS certificate days-to-expiry; emit a warning when it
  crosses each threshold in cert_warn_days (default [21, 7, 1]).

ALERTING RULES (this is the core behaviour — get it exactly right)
- 2-STRIKES DEBOUNCE: only declare a target DOWN after 2 consecutive failed checks, so a single
  flaky probe from GitHub's network does not false-alarm. Make the threshold configurable
  (failures_before_down).
- ALERT ON STATE CHANGES ONLY — never send "still up" messages:
    * 🔴 DOWN     — when a target becomes newly confirmed down (failure streak hits the threshold)
    * 🟢 RECOVERED — when a previously-down target passes again
    * 🟡 TLS cert  — once, when a cert crosses a warn threshold (don't repeat until it un-crosses)
- DAILY DIGEST: once per day at a configurable UTC hour, send one 📊 summary listing every
  target with 🟢/🔴 and its latency, plus an X/Y healthy count. This is the only "heartbeat"
  message so the channel isn't silent for days.
- All messages use parse_mode=HTML and include a UTC timestamp.

STATE PERSISTENCE (free, no database)
- Keep last-known state per target in state.json: {up, fail_streak, last_detail, last_check, and
  per-threshold cert_warned_* flags}.
- Keep rolling samples in history.json (cap to the most recent ~2880 entries ≈ 30 days at 15-min
  cadence) for the digest and optional status page.
- The workflow commits state.json + history.json back to the repo after each run so the next run
  remembers prior state. Use a commit message ending in "[skip ci]" and grant the job
  contents:write permission.

CONFIG (no code change to add/remove endpoints)
- Put all targets and all thresholds in targets.json (settings + targets array). The monitor
  reads everything from there.
- Read TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment variables (provided via GitHub
  Secrets). NEVER hardcode or commit them.

FILES TO CREATE
- monitor.py            — the checker, stdlib-only, structured as: load config+state, loop
                          targets, run checks, apply debounce, send transition alerts, update
                          state/history, write files, optional daily digest. Use a small Telegram
                          sendMessage helper that POSTs to
                          https://api.telegram.org/bot<TOKEN>/sendMessage.
- targets.json          — settings { timeout_seconds:20, latency_warn_ms:3000,
                          failures_before_down:2, cert_warn_days:[21,7,1],
                          daily_digest_hour_utc:5 } + the 6 targets above with fields
                          name/url/expect_status/must_contain/check_cert/expected_ip.
- state.json            — initialise to {}
- history.json          — initialise to []
- .github/workflows/monitor.yml — cron "*/15 * * * *" (4×/hour) + workflow_dispatch button;
                          permissions: contents: write; concurrency group so runs don't overlap;
                          steps: checkout, setup-python 3.12, run monitor.py with the two secrets
                          in env, then a commit-back step that adds state/history (and docs/ if
                          present), commits only when changed, and pushes.
- README.md             — setup guide (see SETUP DOC below) + what each file does + how to add a
                          target.
- docs/index.html       — OPTIONAL GitHub-Pages status page rendered from history.json (per-target
                          status + last latency + uptime %). Nice-to-have, not required for v1.

SETUP DOC to put in README.md
  1. Create the bot: message @BotFather → /newbot → save the bot token.
  2. Create a PRIVATE Telegram channel; add the team.
  3. Add the bot as an ADMIN of the channel (channels only accept posts from admins).
  4. Get the channel chat id (looks like -100xxxxxxxxxx): post a message, forward it to
     @userinfobot, or read channel_post.chat.id from
     https://api.telegram.org/bot<TOKEN>/getUpdates.
  5. Add repo GitHub Secrets: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
  6. Test via the Actions "Run workflow" button; temporarily break a marker to confirm 🔴 then
     revert to confirm 🟢.

ACCEPTANCE CRITERIA
- A manual run with valid secrets posts nothing when all 6 targets are healthy (state-change only)
  EXCEPT a digest if run during the digest hour.
- Breaking one target (e.g. wrong expected marker) makes the SECOND consecutive run post a single
  🔴 DOWN; restoring it makes the next run post a single 🟢 RECOVERED.
- state.json and history.json are committed back automatically and the workflow does not retrigger
  itself (because of [skip ci]).
- monitor.py imports nothing outside the Python standard library.

Generate all files now with complete, runnable contents.
```

---

## 10. Registered as a monitoring tool

| Tool | Scope | Infra | Repo | Channel |
|---|---|---|---|---|
| **googleipmonitor** | Google VM IP watch, 4×/hr | GitHub Actions | `m3hr4nn/googleipmonitor` | Telegram (existing) |
| **mahansco-uptime** (this) | Mahansco web Tier-1 uptime | GitHub Actions | `mahansco-uptime` (to create) | Private Telegram (you + team) |

Future Tier-2/Tier-3 (internal: disk on `/opt/iranscm/data`, DB/Redis, backup-age, containers)
will be a separate internal agent — see `email-notification-vision` / launch-tracker for context.
