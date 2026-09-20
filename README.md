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
public settings and target names/URLs in `state._meta.config`; the page derives
all operational thresholds and its refresh cadence from that data.

The nominal interval is five minutes, delayed means older than ten minutes, and
stale means older than twenty minutes. Equality stays in the preceding state.
This tolerates ordinary schedule jitter without allowing hours-old data to appear
healthy. Missing metadata, invalid timestamps, failed fetches or mismatched
state/history snapshots produce UNKNOWN. Stale data shows historical endpoint
states, never an operational overall status. The page updates freshness locally
between refreshes. Tehran display includes UTC+03:30; new stored timestamps use UTC.

Availability windows are trailing 24 hours, 7 days and 30 days, calculated from
timestamped observations. Each window is divided into expected five-minute slots
ending at the current time. A slot is observed if it has at least one check; all
checks within that slot must succeed for it to count as successful. Extra manual
runs cannot fill other missing slots. Raw failed observations count as failures
for observed availability even when alert debounce absorbs them.

The public page presents a compact dark service-tile dashboard, with plain-language
status, an hourly check-history strip and recent confirmed incidents. Technical
information is collapsed under **Monitoring details**: observed availability,
observed/expected slots, coverage, certificate information and monitoring gaps.
Tile sizes emphasize the website and web app; they do not encode traffic or uptime.
The sign-in tile checks public discovery configuration, not a complete login flow.
Gray tiles show historical or unknown status; delayed checks never appear as live
green tiles. The history strip shows an hour as green only when every configured
service meets the coverage threshold, amber when any recorded check failed, and
gray otherwise. It does not interpolate missing observations. Failed page fetches
clear old status and retry automatically. Open diagnostics stay open across updates.

The details report observed availability plus observed/expected slots and coverage.
Below 90% coverage it displays insufficient data and suppresses the percentage.
Unobserved time is never credited as successful or treated as an endpoint outage.
Gaps longer than two expected intervals appear separately; exact missing-slot
counts are shown for every window. Legacy daily sample tallies are retained for
continuity but are not used to claim 30-day coverage or availability.

Retention is 35 elapsed days, plus one predecessor per target for incident boundary
context. History is serialized as one compact observation per line to reduce
generated file size and Git diff churn. Removed endpoints leave active state on
the next cycle but remain in retained history and the incident view. Git storage
still grows with generated commits; changing publication/storage architecture is
a separate decision, and the repository history is not rewritten.

## Targets and alerts

Checks cover the landing page, SPA, canonical API health, GraphQL, WordPress API,
robots canary and the enabled public SSO discovery document. The obsolete address
pin is removed. Optional `expected_ips` accepts validated public IPv4/IPv6 sets;
all DNS A/AAAA answers are considered, and unexpected addresses are advisory.
Nonpublic DNS answers fail the probe without publishing the addresses.

Targets require a body marker or exact JSON path assertions. GraphQL uses only
the harmless POST `{ __typename }`. This service currently requires authentication
even for that query, so its expected public result is the GraphQL
`authentication_required` error for `__typename`, with null data. This verifies
the public GraphQL authentication boundary, not authenticated resolver health.
SSO validates the public realm discovery issuer and key URL without logging in.

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

Certificate warnings are once per configured threshold crossing. Transient metadata
inspection failures preserve previous certificates and warning flags; three failed
inspections queue one warning, and successful inspection queues one recovery.
HTTPS request certificate validation remains independent and mandatory.

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
five-minute cron is not a five-minute detection guarantee. See
[GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
The cron is staggered away from minute zero. Runs have a ten-minute timeout,
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
