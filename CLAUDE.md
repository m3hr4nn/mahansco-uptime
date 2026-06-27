# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A free, **Tier-1** (blackbox / endpoint-only) uptime monitor for Mahansco's public web surface.
It runs entirely on **GitHub Actions** — never on the monitored host — issues HTTPS `GET`s to the
public endpoints every 15 minutes, and posts to a **private Telegram channel** on state changes
only. The authoritative design and rationale live in `20260627-TELEGRAM_BOT_MONITORING.md`; read
it before making non-trivial changes.

## Hard constraints (do not break these)

- **Standard library only.** `monitor.py` must import nothing outside the Python 3.12 stdlib
  (`urllib`, `ssl`, `socket`, `json`, `datetime`, `time`, `os`). There is no `requirements.txt`
  and no `pip install` step in the workflow — keep it that way.
- **No secrets in the repo.** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` come from GitHub Secrets
  via env vars only. Never hardcode or commit them.
- **State-change alerting only.** The channel must never receive "still up" messages. The only
  proactive heartbeat is the once-daily digest.

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
   `latency_warn_ms`, `failures_before_down`, `cert_warn_days`, `daily_digest_hour_utc`) and a
   `targets` array. **Adding/removing endpoints or changing thresholds is a config edit only — never
   a code change.** Each target: `name`, `url`, `expect_status`, `must_contain` (empty = skip),
   `check_cert`, optional `expected_ip` (empty = skip DNS-match check).

2. **`monitor.py`** — `main()` loops every target, calls `check()` (DNS/expected-IP → HTTP
   GET+timing → status → body marker → latency), applies the debounce, sends transition alerts,
   then writes state + history. `check_cert` targets also get a `cert_days_left()` probe.

3. **`state.json`** — last-known state per target, keyed by target `name`:
   `{up, fail_streak, last_detail, last_check, cert_days_left, cert_warned_<thr>}`. **Machine-owned:
   the workflow commits it back each run — do not hand-edit.** This committed-back state is what
   makes transition-only alerting possible with zero infrastructure.

4. **`history.json`** — append-only rolling samples, capped to the last 2880 (~30 days at 15-min
   cadence). Feeds the daily digest and the `docs/index.html` status page. Also machine-owned.

### Two behaviours that are easy to get subtly wrong

- **2-strikes debounce.** A target is only DOWN once `fail_streak >= failures_before_down`. Alerts
  fire on the *transition* (`is_down and not was_down` → DOWN; `was_down and ok` → RECOVERED), not
  on each failing check. Keep `fail_streak` accumulation and the transition comparison in sync if
  you touch this loop.
- **Cert threshold latching.** Each `cert_warn_days` threshold warns **once** via a
  `cert_warned_<thr>` flag in state, and only un-latches when `days > thr` again. Don't make it
  re-warn every run.

### Workflow (`.github/workflows/monitor.yml`)

Cron `*/15 * * * *` + `workflow_dispatch`. Needs `permissions: contents: write` to push state back.
The commit-back step's message ends in `[skip ci]` so the push does **not** retrigger the workflow —
preserve that. `concurrency` prevents overlapping runs.

### Status page (`docs/index.html`)

Optional GitHub Pages page. Pure static HTML+JS, no build; fetches `../history.json` at runtime and
renders per-target status, recent uptime %, and a latency strip. Served from `/docs` on `main`.

## Conventions specific to this repo

- Timestamps are UTC throughout; use the `utcnow()` helper (timezone-aware — `datetime.utcnow()` is
  deprecated in 3.12), and format display strings as `"%Y-%m-%d %H:%M UTC"`.
- Telegram messages use `parse_mode=HTML` and always carry a UTC timestamp. Emojis are written as
  `\U0001F…` escapes to keep the source ASCII-clean.
- JSON is written with `indent=2, ensure_ascii=False` so committed-back state stays diff-friendly
  and readable (Persian/Unicode preserved).
- This is **Tier 1 only** — anything requiring access inside the prod network (disk, DB, Redis,
  backups, containers) is explicitly out of scope and belongs in a separate internal agent.
