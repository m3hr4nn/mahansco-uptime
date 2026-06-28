# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A free, **Tier-1** (blackbox / endpoint-only) uptime monitor for Mahansco's public web surface.
It runs entirely on **GitHub Actions** — never on the monitored host — issues HTTPS `GET`s to the
public endpoints every 5 minutes, and posts to a **private Telegram channel** on state changes
only. The authoritative design and rationale live in `20260627-TELEGRAM_BOT_MONITORING.md`; read
it before making non-trivial changes.

## Hard constraints (do not break these)

- **Standard library only.** `monitor.py` must import nothing outside the Python 3.12 stdlib
  (`urllib`, `ssl`, `socket`, `json`, `datetime`, `time`, `os`). There is no `requirements.txt`
  and no `pip install` step in the workflow — keep it that way.
- **No secrets in the repo.** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` come from GitHub Secrets
  via env vars only. Never hardcode or commit them.
- **State-change alerting only.** The channel must never receive per-check "still up" spam. The
  only proactive heartbeat is the recurring digest, whose cadence is set by `digest_every_hours`
  in `targets.json` (`1` = hourly, `24` = once daily). The digest fires once that many hours have
  **elapsed** since the last one (tracked in `state["_meta"]["last_digest_utc"]`) rather than at a
  fixed clock time — GitHub's cron is jittery, so an elapsed-time gate is the only reliable cadence.

## Commands

```bash
# Run the monitor locally (needs the two secrets in the environment)
TELEGRAM_BOT_TOKEN=… TELEGRAM_CHAT_ID=… python monitor.py

# Syntax / smoke check without sending Telegram messages or hitting the network:
#   set dummy secrets so the os.environ[...] lookups succeed; real network calls
#   will run, so use a throwaway chat id or expect sends to fail gracefully.
python -m py_compile monitor.py        # parse check, no execution
```

There is no test suite, linter config, or build step. Validation is manual: trigger the workflow
(Actions → **Run workflow**), or temporarily break a target's `must_contain` to confirm a 🔴 fires
after two runs and 🟢 on revert.

## Architecture

Single-process Python script driven by a cron workflow. The whole system is four moving parts:

1. **`targets.json`** — the entire configuration surface: a `settings` block (`timeout_seconds`,
   `latency_warn_ms`, `failures_before_down`, `cert_warn_days`, `digest_every_hours`) and a
   `targets` array. **Adding/removing endpoints or changing thresholds is a config edit only — never
   a code change.** Each target: `name`, `url`, `expect_status`, `must_contain` (empty = skip),
   `check_cert`, optional `expected_ip` (empty = skip DNS-match check).

2. **`monitor.py`** — `main()` loops every target, calls `check()` (DNS/expected-IP → HTTP
   GET+timing → status → body marker → latency; returns `ok, detail, latency_ms, status_code,
   dns_ms`), applies the debounce, sends transition alerts, then writes state + history + rollup.
   `check_cert` targets also get a `cert_info()` probe (days-left, valid-from/to, issuer).

3. **`state.json`** — last-known state per target, keyed by target `name`:
   `{up, fail_streak, last_detail, last_check, latency_ms, status_code, dns_ms,
   cert_days_left, cert_not_after, cert_not_before, cert_issuer, cert_warned_<thr>}`.
   **Machine-owned: the workflow commits it back each run — do not hand-edit.** This committed-back
   state is what makes transition-only alerting possible with zero infrastructure.

4. **`history.json`** — append-only rolling samples, capped to the last 2880 (~10 days at the 5-min
   cadence). Each per-target result is `{ok, detail, latency_ms, status_code, dns_ms}`. Feeds the
   digest and the `docs/index.html` status page (sparkline, 24h/7d uptime, incident log). Machine-owned.

5. **`uptime_daily.json`** — compact per-day `{up, total}` tallies per target, kept ~35 days. Backs
   the status page's **30-day** uptime number without bloating `history.json`. Machine-owned.

### Two behaviours that are easy to get subtly wrong

- **2-strikes debounce.** A target is only DOWN once `fail_streak >= failures_before_down`. Alerts
  fire on the *transition* (`is_down and not was_down` → DOWN; `was_down and ok` → RECOVERED), not
  on each failing check. Keep `fail_streak` accumulation and the transition comparison in sync if
  you touch this loop.
- **Cert threshold latching.** Each `cert_warn_days` threshold warns **once** via a
  `cert_warned_<thr>` flag in state, and only un-latches when `days > thr` again. Don't make it
  re-warn every run.

### Workflow (`.github/workflows/monitor.yml`)

Cron `*/5 * * * *` + `workflow_dispatch`. Needs `permissions: contents: write` to push state back.
The commit-back step's message ends in `[skip ci]` so the push does **not** retrigger the workflow —
preserve that. `concurrency` prevents overlapping runs.

### Status page (`docs/index.html`)

Optional GitHub Pages page. Pure static HTML+JS, no build. Served from `/docs` on `main`; the
workflow `cp`s `history.json`, `state.json`, and `uptime_daily.json` into `docs/` each run so the
page can fetch them. Renders per-target status, a latency sparkline (bar height = latency, colour =
ok/slow/down), 24h/7d/30d uptime, TLS cert panel, an incident log, and "time since last outage".
The page degrades gracefully if `state.json`/`uptime_daily.json` are missing (cert/30d show `—`).

## Conventions specific to this repo

- Time math is UTC internally (`utcnow()` helper — timezone-aware, since `datetime.utcnow()` is
  deprecated in 3.12), but every **human-facing** timestamp is shown in **Tehran time** (Iran
  Standard Time, fixed UTC+03:30, no DST). Use `fmt_local()` → `"%Y-%m-%d %H:%M IRST"`. The status
  page's `parseTs()` still accepts the old `... UTC` label so retained history samples parse correctly.
- Telegram messages use `parse_mode=HTML` and always carry a Tehran (IRST) timestamp. Emojis are written as
  `\U0001F…` escapes to keep the source ASCII-clean.
- JSON is written with `indent=2, ensure_ascii=False` so committed-back state stays diff-friendly
  and readable (Persian/Unicode preserved).
- This is **Tier 1 only** — anything requiring access inside the prod network (disk, DB, Redis,
  backups, containers) is explicitly out of scope and belongs in a separate internal agent.
