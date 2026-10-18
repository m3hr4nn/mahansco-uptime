# Operational dashboard and portability report

Date: 2026-10-18

## Investigation

The existing checker already had the critical operational behavior this change
needed to preserve: two-observation outage confirmation, recovery on the first
successful probe, time-based history retention with incident boundary anchors,
coverage-aware observed availability, snapshot completion metadata, atomic writes,
bounded Telegram retries/queues, certificate threshold warnings, and non-force Git
publication. The previous page calculated these semantics correctly but placed most
evidence in a collapsed section and dedicated substantial space to decorative status
art.

The REA skill was read and its browser-runtime workflow selected. Its MCP browser
tools were not connected in this session, so reference inspection used isolated
headless Chrome captures and public source inspection instead. No REA runtime claim
is made. `gatus.io` is the product/marketing site; `status.twin.sh` is the operational
dashboard. Useful observed dashboard patterns were instant endpoint search, simple
status filtering, compact endpoint cards, status text alongside color, dense recent
history marks, response-time context, and mobile stacking. The Mahansco design uses
those interaction ideas with its own visual language and the repository's stricter
freshness, confirmation, coverage, certificate, and semantic-validation concepts.

## Focused plan and result

1. Keep the monitor state machine, Telegram delivery functions, production command,
   targets, workflow schedule, and publication path unchanged.
2. Put runtime config/data selection around the existing checker. Custom runs get an
   isolated data directory and a notification-disabled execution boundary.
3. Replace the static presentation with an at-a-glance summary, actionable warning
   queue, searchable/filterable service rows, in-page evidence, recorded latency
   trends, and selectable history windows.
4. Add a small Python preview server, Docker/Compose workflow, example configuration,
   and serialized Git-backed Jenkins/GitLab examples.
5. Test calculations and state distinctions with offline fixtures, then inspect real
   checked-in data in desktop and mobile headless browsers.

The implementation follows that plan. URL-only onboarding is explicitly marked as
generic HTTPS reachability. Existing configured Mahansco probes remain semantic
checks. The dashboard never turns absent slots into successes, never derives a live
status from stale data, and continues to withhold availability percentages below the
configured coverage threshold.

## Preservation notes

- `python3 monitor.py` still uses `targets.json`, writes `docs/`, enables Telegram,
  and retains the existing production behavior.
- Custom `--url`, `--config`, or `--data-dir` runs default to Telegram disabled and
  use their own state/history/rollup files. They neither load nor flush the production
  notification queue.
- No production observation file, target, assertion, secret, workflow schedule,
  deployment setting, or Git history was rewritten by implementation or testing.
- Container and CI examples perform one cycle per invocation. Scheduling and durable
  storage remain explicit runner responsibilities.

## Validation record

- `python3 -m unittest discover -s tests`: passed (34 tests at implementation time).
- `node --test tests/status.test.js`: passed.
- Desktop 1440×1200 and mobile 390×844 renders were inspected with the checked-in
  dataset. The page correctly presented it as stale rather than healthy.
- Headless-browser smoke checks exercised service/hostname search, status filtering,
  empty/reset behavior, the 7-day range, and in-page detail disclosure with no
  JavaScript exceptions.
- `docker compose config` passed. A local image built and its production config,
  URL-only config/data path, and preview entry point validated using the host's
  cached `python:3.11-slim` image. The Dockerfile still defaults to Python 3.12.
  Pulling `python:3.12-slim` was not verified because this host references a missing
  `docker-credential-pass` helper.
- `actionlint` and `shellcheck` were unavailable; Python compilation, JavaScript
  syntax, Bash syntax, and `git diff --check` passed.

## Unrelated observed condition

The checked-in production snapshot reports its last completed cycle at
`2026-09-28T05:33:27.102035+00:00`. On the implementation date, 2026-10-18, that is
stale under the configured eight-hour threshold. This change displays the condition
prominently but does not trigger monitoring, alter scheduling, repair production
data, or send notifications.
