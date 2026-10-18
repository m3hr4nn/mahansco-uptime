# Mahansco Uptime

Tier 0 is an outside-in monitor of public HTTPS services, running in GitHub Actions
and publishing public-safe observations through GitHub Pages. Tier 1 owns host,
container, database and application telemetry. Tier 0 must remain outside the
production failure domain. Runtime Python uses only the standard library; there
are no paid monitoring dependencies.

## Main files

- `monitor.py` — monitoring and alerting logic
- `targets.json` — monitoring configuration
- `.github/workflows/monitor.yml` — scheduled execution
- `docs/index.html`, `docs/status.css`, `docs/status.js` — accessible public status page and calculations
- `docs/state.json`, `docs/history.json`, `docs/uptime_daily.json` — generated data,
  owned by the scheduled workflow; never regenerate these during development tests
- `scripts/persist-state.sh` — bounded, non-force publication retries

`docs/` is the sole data location. Root copies were removed after byte-for-byte
verification against the latest remote state. Existing published history was left
untouched. The first completed cycle publishes metadata and prunes by time. Before
that cycle, the upgraded page reports UNKNOWN because legacy state has no completion
heartbeat. No public Git history is rewritten.

## Local checks

```sh
python3 monitor.py --validate
python3 -m unittest discover -s tests
PYTHONPYCACHEPREFIX=/tmp/mahansco-uptime-pycache python3 -m py_compile monitor.py
node --test tests/status.test.js
actionlint .github/workflows/monitor.yml
bash -n scripts/persist-state.sh
```

Node 18+ is required for the offline page tests, which also run from the Python
suite. Tests mock network calls and use temporary data directories. Configuration
validation and tests need no Telegram credentials. A normal `python3 monitor.py`
performs real probes, notifications and generated-file writes; do not use it as a test.

## Freshness and availability

`targets.json` is the configuration source. Every completed cycle embeds its
public settings and target names in `state._meta.config`; the page derives
all operational thresholds and its refresh cadence from that data.

The workflow is scheduled about every four hours (`17 */4 * * *`), following
GitHub Actions scheduling policies. GitHub may delay or drop scheduled runs, so
this is an expected cadence, not a guaranteed deadline. Data is marked delayed
after five hours and stale after eight hours; equality stays in the preceding
state. Missing metadata, invalid timestamps, failed fetches or mismatched
state/history snapshots produce UNKNOWN. Stale data shows historical endpoint
states, never an operational overall status. The page updates freshness locally
between refreshes. Tehran display includes UTC+03:30; new stored timestamps use UTC.

Availability windows are trailing 24 hours, 7 days and 30 days, calculated from
timestamped observations. Each window is divided into expected four-hour slots
ending at the current time. A slot is observed if it has at least one check; all
checks within that slot must succeed for it to count as successful. Extra manual
runs cannot fill other missing slots. Raw failed observations count as failures
for observed availability even when alert debounce absorbs them.

The public page presents a compact single-page operations dashboard. The summary,
freshness, active incidents and certificate/DNS warnings stay above the service
list. Search matches public service names and hostnames; status filters and a reset
work instantly in the browser. Each service row exposes its latest HTTP result,
response time, check age, certificate state, observed availability and coverage.
Expanding a row reveals recorded response-time history, probe evidence and 24-hour,
7-day and 30-day coverage without navigating away.
Status data omits exact checked URLs. The public source repository still contains
the probe configuration.
Gray tiles show historical or unknown status; delayed checks never appear as live
green tiles. The 24-hour history strip divides time into expected monitoring
windows (about four hours each), green only when every configured service meets
the coverage threshold, amber when any recorded check failed, and gray otherwise.
It does not interpolate missing observations. Failed page fetches
clear old status and retry automatically. Open diagnostics stay open across updates.

The details report observed availability plus observed/expected slots and coverage.
Below 90% coverage it displays insufficient data and suppresses the percentage.
Unobserved time is never credited as successful or treated as an endpoint outage.
Gaps longer than two expected intervals appear separately; exact missing-slot
counts are shown for every window. Legacy daily sample tallies are retained for
continuity but are not used to claim 30-day coverage or availability.

Probe observations and daily rollups are retained for 35 elapsed days, plus one
predecessor per target for incident boundary context. Availability is calculated
over 24-hour, 7-day and 30-day windows. Failed Telegram notifications are retried
from a queue capped at 50 messages and 48 hours. History is serialized as one
compact observation per line to reduce generated file size and Git diff churn.
Removed endpoints leave active state on the next cycle but remain in retained
history and the incident view. Git commit history is retained independently and
is not pruned by the 35-day data retention window.

## Targets and alerts

Checks cover the landing page, SPA, canonical API health, application access,
WordPress API and robots canary. The obsolete address pin is removed.
Optional `expected_ips` accepts validated public IPv4/IPv6 sets;
all DNS A/AAAA answers are considered, and unexpected addresses are advisory.
Nonpublic DNS answers fail the probe without publishing the addresses.

Targets require a body marker or exact JSON path assertions. The application
access check uses only a harmless metadata query and verifies that unauthenticated
access is denied; it does not test authenticated resolver health.

Cross-host redirects default to denied and can be explicitly enabled per target.
HTTPS, public destination checks and semantic assertions still apply; POST redirects
are rejected to avoid silently converting the query to GET. Successful final URLs
are recorded. Configuration forbids credentials, URL queries/fragments, internal
hosts, arbitrary POST bodies, unsupported keys and malformed thresholds.

One failed observation creates a pending failure. Two consecutive failures confirm
DOWN and produce one transition notification. Further failures are quiet; the first
successful observation closes the incident at its observation time and queues one
recovery. The page does not debounce confirmed states again. Incident duration is
elapsed time between observations and may include monitoring gaps; open incidents
are described as open at the last observation. Legacy raw-only history is not
retroactively promoted into confirmed incidents.

Certificate details include the expiry date and are checked on each monitoring
cycle. Telegram warnings are sent once when a certificate crosses 21, under 10,
7 or 1 whole days remaining. Transient metadata inspection failures preserve
previous certificates and warning flags; three failed inspections queue one
warning, and successful inspection queues one recovery. HTTPS request certificate
validation remains independent and mandatory. Renew certificates before expiry;
the under-10-day warning may arrive on the next scheduled check.

Telegram credentials come only from runtime environment variables
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Errors log categories/status codes, never
tokens, chat identifiers, URLs or API response bodies. Messages escape interpolated
HTML. Failed delivery retries up to three times per attempt and is retained for
later runs, bounded to 50 queued messages and 48 hours. Oldest messages are evicted
with a public count and a log warning. At most five queued messages are processed
per flush; two flushes run per cycle. A digest is sent when due, and its clock
advances only on successful delivery. A manual test ping exits unsuccessfully if
delivery fails. Network ambiguity or a crash before persistence can still cause a
duplicate delivery; Telegram and Git do not provide a shared transaction.

## Scheduling, publication and recovery

GitHub's scheduled workflows are best effort and may be delayed or dropped; a
four-hour cron is an expected check cadence, not a four-hour detection guarantee. See
[GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
The cron is staggered to minute 17 to avoid the busiest minute. Runs have a ten-minute timeout,
serialized concurrency, validation before probing, and job-scoped write permissions.
Action major versions match the main project's approved checkout v7/setup-python v6.

Publication retries a rejected push up to three times and rebases clean concurrent
changes. A generated-data conflict aborts the rebase and fails visibly; it never
force-pushes or chooses one side destructively. Failure uploads a seven-day recovery
artifact. File replacement is individually atomic; history and rollup are written
before the state completion marker. A Git commit publishes the set together. The
page rejects mismatched snapshots fetched across publication boundaries.

If input JSON is corrupt or structurally invalid, the monitor stops before probing
and leaves the original files intact. To recover, pause scheduled execution using
the owner's usual workflow, save the failed-run recovery artifact, inspect Git
history, and restore all three data files from the same last known-good commit on
a review branch. Review any subsequent observations before replaying them; never
substitute empty state or resolve data conflicts by force. Missing files are accepted
only for an entirely new installation; a partial existing dataset fails validation.
Re-enable the workflow after reviewing the repaired dataset. Review pending alerts
because recovered state may retry delivery.

Do not change DNS, secrets, Pages settings or Tier 1 as part of this repository's
maintenance. A live `workflow_dispatch` test sends real notifications and commits
state; it requires explicit owner confirmation. Offline tests never fabricate a
public outage.

## Portable local and CI use

The no-argument production command is unchanged: `python3 monitor.py` reads
`targets.json`, writes `docs/`, uses the existing Telegram queue, and is the command
used by GitHub Actions. Any run with `--url`, `--config`, or `--data-dir` is treated
as an independent run: Telegram is disabled by default and observations are written
only under the selected data directory. Custom runs do not read or flush the
production notification queue. `--notifications` is an explicit opt-in for an
independent installation with its own credentials.

Preview the checked-in dashboard and current public dataset:

```sh
docker compose up --build dashboard
# open http://localhost:8080
```

Start a separate URL-only monitor and preview its persistent dataset:

```sh
cp .env.example .env
mkdir -p runtime/data
docker compose run --rm monitor --url "https://example.com/" --data-dir /data
docker compose --profile project up --build project-dashboard
# open http://localhost:8081
```

The `runtime/data` bind mount survives container replacement. Every invocation is
one monitoring cycle; use cron, a Jenkins timer, or a GitLab pipeline schedule to
set cadence. URL-only mode expects HTTP 200, validates public HTTPS and TLS, and is
explicitly labeled as generic reachability. It does not claim application health.
For content or JSON assertions and multiple services, copy
`examples/targets.example.json` outside the production configuration and run:

```sh
docker compose run --rm monitor --config /app/examples/targets.example.json --data-dir /data
```

`examples/Jenkinsfile` schedules one cycle about every four hours with
`disableConcurrentBuilds()`. `examples/gitlab-ci.yml` runs only for scheduled or
manual pipelines and uses a `resource_group` to serialize writers. Both examples
restore a dedicated state repository before the cycle, archive the resulting JSON,
and call `scripts/persist-external-state.sh`. Configure the runner-only
`STATE_REPO_URL` credential to an initialized, independent repository (an empty
first-run dataset is allowed; subsequent runs contain the three JSON files) and set
the desired schedule in Jenkins or GitLab. The Jenkins timer is embedded in the
example. For GitLab, create a pipeline schedule in **Build > Pipeline schedules**;
`17 */4 * * *` matches the example configuration's expected four-hour interval.
The helper
commits only those files, retries a normal fetch/rebase/push three times, aborts on
conflict, and never force-pushes.

Do not point these examples at this repository's `docs/` dataset or schedule a
second writer against the production branch. An ephemeral CI workspace does not
retain history by itself; the separate state repository is the durable source and
the archived JSON is only a recovery copy. Runtime Git credentials should be
provided by the CI credential store. Local containers remain outside-in development
or independent monitors and should not be moved onto the production application
host.

## Tier 0 / Tier 1 contract

Tier 1 should independently fetch the public Pages `state.json` and alert when
`_meta.last_run_utc` is absent, invalid, in the future or older than
`_meta.config.settings.stale_after_seconds`, or the fetch repeatedly fails.
`_meta.latest_observation_utc` identifies the last history entry; `_meta.run_id`
and `_meta.commit` identify the producing workflow/code. A future schema other
than `_meta.config.schema_version = 2` should be treated as an integration error.
The last completed run is a monitor heartbeat even when endpoints are down.

This dead-man check is specified here but requires implementation in the Tier 1
repository by its owner. It can warn about scheduler/publication failures while
production is running; it cannot alert during a total outage of that same production
host. A stronger independent external heartbeat requires owner approval. Tier 0
cannot reliably alert about its own missing scheduler. No monitoring SLO is claimed.
