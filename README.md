# mahansco-uptime

A free, **Tier-1** (blackbox / endpoint-only) uptime monitor for Mahansco's public web
surface. It runs on **GitHub Actions** (not on the monitored host — a monitor must never share
fate with what it watches), issues HTTPS `GET`s to the public endpoints every 15 minutes, and
posts to a **private Telegram channel** on state changes only. No paid SaaS, no servers, no cost.

> Pattern mirrors [`m3hr4nn/googleipmonitor`](https://github.com/m3hr4nn/googleipmonitor):
> GitHub Actions cron → Telegram, committed-back state for transition-only alerting,
> optional GitHub Pages status page.

## What it checks

Per run, for each target in `targets.json`:

- **HTTP status** equals the expected code.
- **Latency** in ms — flagged "slow" above `latency_warn_ms` (still counts as UP).
- **Body marker** — response contains the configured string and is non-empty.
- **DNS** resolves; when `expected_ip` is set, the resolved IP must match (DNS-change / hijack catch).
- **TLS cert days-to-expiry** for `check_cert` targets — warns at each `cert_warn_days` threshold.

### Monitored targets

| Endpoint | Expect | Body marker |
|---|---|---|
| `https://mahansco.ir/` | 200 | `System Online` (+ TLS, expected IP `185.208.173.17`) |
| `https://app.mahansco.ir/` | 200 | `<div id="root">` (+ TLS) |
| `https://app.mahansco.ir/api/health/` | 200 | — (proves the backend answers, not just static files) |
| `https://app.mahansco.ir/graphql/` | 200 | — |
| `https://mahansco.ir/api/` | 200 | — |
| `https://mahansco.ir/robots.txt` | 200 | — (cheap static-serving canary) |

## Alerting behaviour

- **2-strikes debounce** — a target is declared DOWN only after `failures_before_down` (default 2)
  consecutive failures, so one flaky probe from GitHub's network doesn't false-alarm.
- **State changes only** — never "still up" spam:
  - 🔴 **DOWN** — target newly confirmed down
  - 🟢 **RECOVERED** — previously-down target passing again
  - 🟡 **TLS cert** — once, when a cert crosses a warn threshold (silent until it un-crosses)
- 📊 **Daily digest** — once per day at `daily_digest_hour_utc`, a single summary with per-target
  status, latency, and an X/Y healthy count. The only heartbeat message.

## Files

| File | Purpose |
|---|---|
| `monitor.py` | The checker. Python 3.12, **standard library only** (no `pip install`). |
| `targets.json` | All targets + thresholds. Add/remove endpoints here — no code change. |
| `state.json` | Last-known state per target. **Auto-committed by the workflow** (don't hand-edit). |
| `history.json` | Rolling samples (~2880 ≈ 30 days at 15-min cadence) for digest + status page. |
| `.github/workflows/monitor.yml` | Cron `*/15 * * * *` + manual run; runs `monitor.py`, commits state back. |
| `docs/index.html` | Optional GitHub Pages status page rendered client-side from `history.json`. |

State is **committed back to the repo** after each run (commit message ends in `[skip ci]` so the
push doesn't retrigger the workflow). That's the free, zero-infra way to remember "was it up last
time?" and alert only on transitions.

## Setup

1. **Create the bot** — in Telegram message **@BotFather** → `/newbot` → name it (e.g.
   `Mahansco Uptime`) → save the **bot token** (`123456:ABC-…`).
2. **Create a PRIVATE Telegram channel** and add the team.
3. **Add the bot as an ADMIN** of the channel (channels only accept posts from admins).
4. **Get the channel chat id** (looks like `-100xxxxxxxxxx` — keep the `-100`): post a message,
   then either forward it to **@userinfobot**, or read `channel_post.chat.id` from
   `https://api.telegram.org/bot<TOKEN>/getUpdates`.
5. **Add GitHub Secrets** (repo → Settings → Secrets and variables → Actions):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

   Per project policy, also record both in **KeePass** (`IranSCM/Monitoring` group). Never commit
   the token.
6. **Test** — Actions tab → **Run workflow**. To confirm alerting, temporarily change a target's
   `must_contain` to something wrong, let two runs pass to see 🔴 **DOWN**, then revert to see
   🟢 **RECOVERED**.

### Optional: status page

Enable GitHub Pages (Settings → Pages → Branch `main`, folder `/docs`). The page reads
`history.json` and shows per-target status, recent uptime %, and a latency strip.

## Adding a target

Append an object to the `targets` array in `targets.json`:

```json
{ "name": "My service", "url": "https://example.com/health", "expect_status": 200, "must_contain": "ok", "check_cert": true, "expected_ip": "" }
```

`must_contain` and `expected_ip` may be empty strings to skip those checks; omit/`false`
`check_cert` to skip the TLS check.

## Limits (honest tradeoffs)

- **Resolution** ~15 min (plus possible GitHub cron delay) — not real-time. Fine for Tier 1.
- **Vantage point** — GitHub runners sit outside Iran, so this measures *world reachability* of the
  CDN-fronted site; the 2-strikes rule suppresses transient international-routing noise.
- **No internal visibility** — disk, DB, Redis, backups, containers are invisible by design. Those
  need an internal agent on the prod host (separate Tier-2/3 tool).

## Run locally

```bash
TELEGRAM_BOT_TOKEN=… TELEGRAM_CHAT_ID=… python monitor.py
```

Requires only Python 3.12+. No dependencies to install.
