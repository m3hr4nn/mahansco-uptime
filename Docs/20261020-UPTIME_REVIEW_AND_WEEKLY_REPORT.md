# Uptime review, latency investigation and weekly reporting

Date: 2026-10-20 (Asia/Tehran)

## Baseline and release evidence

- Original branch: `feat/compact-navy-dashboard`; commit `57a23396815eda1a99e363eb1f257f1e1ecb0077`.
- Original working tree: one unrelated untracked file, `optimization.md`; it was preserved and excluded.
- Baseline checks: configuration validation, 34 Python tests, browser tests, Python byte-compilation and persistence-script syntax all passed.
- Annotated checkpoint: `baseline-weekly-report-20261020`, pushed to `origin` at the original commit without replacing any tag.
- Production source before implementation: GitHub Pages used `main:/docs`; latest deployed Pages build reported commit `bad5593d06c32025f7dd2992bb40e5eecb45a6e6`, built 2026-10-20 04:50 UTC.
- Implementation branch: `feat/weekly-telegram-report`, created from that deployed `origin/main` revision.

## Findings by priority

### High — verified false landing outage

The landing document reported `Last-Modified: Fri, 16 Oct 2026 13:19:26 GMT` and now
contains `<title>Mahansco — Smart seals. Connected supply chains.</title>`. The monitor
required the old exact `<title>Mahansco</title>` string. The last passing observation
was 2026-10-16 13:14 UTC; every landing observation from 19:33 UTC onward returned
HTTP 200 with a semantic-assertion failure, becoming confirmed DOWN at 22:50 UTC.
This is a content-contract defect and false outage, not evidence of unreachable HTTP.
The assertion now accepts the stable `<title>Mahansco` prefix while retaining status,
HTTPS and content validation. The normal first successful probe will use the unchanged
recovery path and send the existing recovery notification once.

### Medium — reported latency increase is not supported for the landing endpoint

The seven days before the server-reported landing update contained 26 samples:
median 1,474.5 ms and nearest-rank p95 2,419 ms. The 15 samples after the update through
2026-10-20 04:49 UTC had median 1,096 ms and p95 2,466 ms. The median improved; the
47 ms p95 difference with only 15 post-change samples does not establish degradation.
The same-host Marketing API median increased from 1,687 ms to 2,054 ms, while other
services varied in both directions, so GitHub-runner/network variability and response
size remain plausible. Current direct checks showed no redirects on the landing page.

Historical `latency_ms` is HTTP request start through full response-body read. It
excludes the explicit DNS preflight, though the HTTP library may resolve independently.
Historical connect, TLS, TTFB, redirect-count and response-size phases were never
stored, so no claim is made about them. New observations add redirect count and bounded
response bytes and a public-safe probe-definition fingerprint without changing the
latency definition. A custom phase-timing HTTP
client was intentionally not added; use Tier 1 telemetry or a purpose-built probe if
phase attribution becomes operationally necessary.

### Medium — monitoring coverage is partial

The scheduler targets a four-hour cadence (42 slots per endpoint per week), but the
most recent complete preview window had 57% coverage. Recent Actions start times were
irregular, including gaps longer than eight hours. GitHub explicitly treats scheduled
workflows as best effort. Missing slots therefore remain separate from failed probes,
availability is suppressed below 90% coverage, and weekly comparisons are labeled
unavailable unless both windows meet that threshold.

### Low — security and delivery review

Telegram secrets remain environment-only and are not printed. Public data omits target
URLs and final redirect URLs. Incident, recovery, certificate, DNS and digest message
construction and triggers are unchanged. Weekly delivery reuses the existing escaped
HTML sender, three bounded retries, API `ok` verification and redacted error logging.

## Weekly metric definitions

- Boundary: Wednesday 09:00 Asia/Tehran; window `[boundary - 7 days, boundary)`.
- Expected coverage: configured four-hour slots multiplied by configured endpoints.
  Multiple runs in one slot cannot fill another slot.
- Availability: endpoint-slots where every recorded probe passed divided by observed
  endpoint-slots; hidden when coverage is below `minimum_coverage` (90%).
- Failures: raw `ok == false` checks, including pending failures; gaps are not failures.
- Incidents: confirmed-down spans overlapping the window. Open incidents end at their
  latest confirmed observation, not at the report boundary. Downtime sums endpoint
  spans, so simultaneous endpoint incidents are intentionally not a fleet-wide union.
- Latency: median and nearest-rank p95 over individual finite, nonnegative HTTP-total
  samples. Completed HTTP responses that fail content semantics still provide valid
  timing. Percentiles are never averaged.
- Comparison: the immediately preceding equivalent seven-day window, shown only when
  both windows meet minimum coverage, use one matching probe-definition fingerprint,
  and prior valid latency exists. A definition change also suppresses window availability.
- Freshness: time from the latest observation before the boundary; stale observations
  make overall health UNKNOWN.

## Scheduling, operation and duplicate behavior

The existing `Uptime Monitor` workflow has a second cron, `30 5 * * 3`, equivalent to
Wednesday 09:00 in fixed UTC+03:30. The normal monitor job and weekly job are mutually
exclusive for that event and share repository-wide concurrency group `uptime-monitor`.
The weekly job validates, tests and prints a preview before delivery. It persists the
successful boundary in `docs/weekly_report.json`, which also powers the dashboard card.

Manual workflow modes are `preview`, `test`, `send`, and `resend`. Test delivery is
clearly labeled and never advances tracking. Normal send is idempotent for a boundary;
resend is an explicit override. Failed Telegram delivery does not advance tracking.
The unavoidable ambiguity is a crash or Git publication failure after Telegram accepts
the message but before the new boundary is committed; retrying may duplicate that report.

## Rollback

1. Disable the weekly cron or revert the release commit through the normal reviewed
   branch workflow; do not force-push `main`.
2. The verified pre-change code checkpoint is `baseline-weekly-report-20261020`.
3. Preserve `docs/state.json`, `docs/history.json`, `docs/uptime_daily.json`, and
   `docs/weekly_report.json` before reverting. Weekly tracking can remain harmlessly
   present if only the schedule is disabled.
4. Re-run configuration, Python and browser tests, then verify the public Pages build.
5. If reverting the landing assertion separately, first confirm the live title; the old
   exact assertion is known to create a false outage against the current page.

No force-push, tag replacement, secret rotation, DNS change, Tier 1 change, or unrelated
refactor is part of this release.
