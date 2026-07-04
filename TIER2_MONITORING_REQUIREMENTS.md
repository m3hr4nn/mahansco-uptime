# Tier 2 Monitoring Requirements for Mahansco Application Stack

**Audience:** Artificial intelligence coding agent working inside the private Mahansco application repository.

**Purpose:** Add internal monitoring metrics and alerts to the currently running Docker Compose solution that serves:

- Frontend/public web: `https://app.mahansco.ir/`
- Frontend/marketing web: `https://mahansco.ir/`
- Backend: Django / Python
- Database: PostgreSQL
- Outbound mail: Postal
- Inbound mail: Maddy, integrated into Django for internal UI workflows
- Runtime: Docker images orchestrated with Docker Compose

The public Tier 1 uptime monitor already checks external reachability from GitHub Actions. This Tier 2 system must monitor the application from inside the production environment and report whether the stack is actually healthy, not merely reachable from the internet.

## Operating Assumptions

- The private application repository is not available to this requirements document.
- The agent implementing this must first inspect the private repo before editing.
- The solution currently runs with Docker Compose.
- The monitoring system should be implemented with minimal operational complexity.
- Prefer existing stack patterns, naming, environment variables, logging conventions, and deployment scripts.
- Do not require a paid SaaS dependency.
- Do not commit secrets.
- Do not expose sensitive internal metrics publicly.

## Tier Definitions

### Tier 1, Already Existing

External blackbox checks from GitHub Actions:

- Public URL reachability
- HTTP status
- TLS expiry
- DNS sanity
- Body marker checks
- Telegram state-change alerts

### Tier 2, To Implement

Internal service health from inside the production Docker environment:

- Django application health
- PostgreSQL health and capacity
- Mail subsystem health, both outbound and inbound
- Docker container health
- Disk, memory, CPU, and process-level risk signals
- Internal synthetic checks that prove the app can use its dependencies
- Alerting on state changes and high-risk thresholds

Tier 2 should answer:

> Is the application stack internally healthy enough to keep serving users?

## Required Deliverable

Create an internal monitoring solution in the private application repo. A good default shape is:

```text
monitoring/
  README.md
  monitor.py
  checks/
    django.py
    postgres.py
    mail.py
    docker.py
    system.py
  state.json
  config.example.json
docker-compose.monitoring.yml
```

This is only a suggested layout. Follow the private repo's existing structure if it has a better convention.

## Core Design Requirements

### 1. Run Inside the Production Environment

The monitor should run where it can see internal services:

- As a dedicated Docker Compose service, or
- As a cron/systemd timer on the Docker host, or
- As a management command triggered by a scheduled container

Recommended default:

```yaml
monitor:
  image: <existing-python-image-or-small-python-runtime>
  command: python monitoring/monitor.py
  env_file: .env
  volumes:
    - ./monitoring:/app/monitoring
    - /var/run/docker.sock:/var/run/docker.sock:ro
  depends_on:
    - backend
    - postgres
```

Only mount the Docker socket if container-level inspection is required and acceptable in the deployment security model.

### 2. Alert on State Changes, Not Noise

Use the same alerting philosophy as Tier 1:

- Send alerts only when a check changes state: OK to WARN, OK to CRITICAL, WARN to OK, CRITICAL to OK.
- Debounce transient failures.
- Do not send "still OK" messages every run.
- Send one periodic digest, configurable as hourly or daily.
- Send alerts to a private Telegram channel or the existing internal alert channel.

Suggested states:

- `OK`
- `WARN`
- `CRITICAL`
- `UNKNOWN`

Suggested debounce:

- Critical only after 2 consecutive failed checks unless the failure is clearly deterministic.
- Recovery after 1 or 2 consecutive passing checks, configurable per check.

### 3. Keep Secrets Out of Git

Required secrets must come from environment variables, Docker secrets, or the existing private deployment secret mechanism:

- Telegram bot token
- Telegram chat ID
- Database credentials
- Mail admin credentials or API tokens
- Any internal synthetic user credentials

Provide `.env.example` or `config.example.json`, but never commit real values.

### 4. Use Structured Output

Each check should produce a structured result:

```json
{
  "name": "postgres_connection",
  "status": "OK",
  "severity": "critical",
  "detail": "PostgreSQL responded in 12ms",
  "latency_ms": 12,
  "metrics": {
    "connections_used": 24,
    "connections_max": 200
  },
  "checked_at": "2026-07-04T11:30:00Z"
}
```

Persist current state locally so transitions can be detected across runs.

## Required Checks

### A. Django Backend

Implement internal Django health checks that do not depend on public DNS, CDN, or public TLS.

Required checks:

- Backend container is running.
- Django process is responding on the internal Docker network.
- Internal health endpoint returns success.
- Django can connect to PostgreSQL.
- Django can access required cache/session backend if present.
- Django can write to required media/upload/temp directories if applicable.
- Django migrations are not pending, if this can be checked safely.
- Application error rate is not spiking, if logs are available.

Recommended internal endpoint:

```text
GET http://backend:<port>/api/health/
```

The endpoint should verify dependencies, not just return a static `200`.

Suggested response:

```json
{
  "status": "ok",
  "django": "ok",
  "database": "ok",
  "mail_outbound": "ok",
  "mail_inbound": "ok",
  "timestamp": "2026-07-04T11:30:00Z"
}
```

Avoid exposing sensitive dependency details on public endpoints. If the existing health endpoint is public, create an internal-only deeper health endpoint or management command.

### B. PostgreSQL

Required checks:

- PostgreSQL container is running and healthy.
- TCP connection succeeds from the backend or monitor container.
- Simple query succeeds:

```sql
SELECT 1;
```

- Database latency is below threshold.
- Connection usage is below threshold.
- Database size is tracked.
- Disk space for PostgreSQL data volume is below threshold.
- Long-running queries are detected.
- Locks or blocked queries are detected.
- Replication status is checked if replication exists.
- Backup freshness is checked.

Suggested SQL metrics:

```sql
SELECT count(*) FROM pg_stat_activity;
SELECT setting::int FROM pg_settings WHERE name = 'max_connections';
SELECT pg_database_size(current_database());
SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND now() - query_start > interval '5 minutes';
SELECT count(*) FROM pg_locks WHERE NOT granted;
```

Suggested thresholds:

- `WARN` if connection usage > 75%.
- `CRITICAL` if connection usage > 90%.
- `WARN` if query latency > 500ms.
- `CRITICAL` if simple query fails.
- `WARN` if latest backup is older than 26 hours.
- `CRITICAL` if latest backup is older than 48 hours.

### C. Outbound Mail, Postal

Required checks:

- Postal container or service is running.
- Postal HTTP/admin/API endpoint is reachable internally, if available.
- SMTP submission path is reachable from Django.
- Outbound queue depth is below threshold.
- Failed delivery count is below threshold.
- DKIM/SPF/DMARC configuration should be checked periodically if feasible.
- Test mail path exists, but should be safe and rate-limited.

Suggested checks:

- Connect to Postal SMTP host and port.
- Verify Django can enqueue an email.
- Verify queue depth through Postal API, database, or CLI if available.
- Verify no sustained growth in deferred/failed queue.

Do not send real test emails every minute. If a synthetic email test is implemented, run it at low cadence and send only to a controlled internal mailbox.

Suggested thresholds:

- `CRITICAL` if Postal is unreachable.
- `WARN` if outbound queue is growing for more than 15 minutes.
- `CRITICAL` if queue age exceeds business-defined threshold.
- `WARN` if failed deliveries exceed normal baseline.

### D. Inbound Mail, Maddy

Maddy is used as the inbound mail application and is served internally in the Django UI.

Required checks:

- Maddy container/service is running.
- SMTP inbound port is listening internally and externally if applicable.
- Django can access inbound mail data or integration endpoint.
- Mail ingestion pipeline is not stalled.
- Last inbound mail processing timestamp is recent enough.
- Failed inbound parsing/import count is below threshold.
- Storage for inbound mail is below disk threshold.

Suggested synthetic check:

- At low cadence, send a controlled test message to a monitoring mailbox.
- Verify that Django can see or process that message within an expected SLA.
- Clean up or mark synthetic test messages to avoid polluting production UI.

Use this only if safe for the business workflow. Otherwise, monitor queue age, last processed timestamp, and error logs.

Suggested thresholds:

- `CRITICAL` if Maddy is unreachable.
- `WARN` if no inbound processing has happened within expected business hours and mail volume is expected.
- `CRITICAL` if inbound queue age exceeds SLA.
- `WARN` if repeated parse/import failures appear.

### E. Docker Compose / Container Health

Required checks:

- Expected containers are running.
- Containers with Docker healthchecks are `healthy`.
- Restart count has not increased unexpectedly.
- No container is in `restarting`, `exited`, or `dead` state.
- Image tags or digests are reported in digest output.
- Critical containers have expected exposed/internal ports.

Expected service classes:

- Frontend
- Django backend
- PostgreSQL
- Postal
- Maddy
- Reverse proxy, if present
- Worker containers, if present
- Scheduler/beat containers, if present

Suggested thresholds:

- `CRITICAL` if a required container is stopped.
- `WARN` if restart count changes.
- `CRITICAL` if restart count repeatedly increases within a short window.

### F. Host and Filesystem

Required checks:

- Disk usage for root filesystem.
- Disk usage for Docker data directory.
- Disk usage for PostgreSQL volume.
- Disk usage for media/upload/mail storage volumes.
- Inode usage.
- Memory usage.
- Swap usage.
- CPU load average.
- System clock sanity.

Suggested thresholds:

- Disk `WARN` > 80%, `CRITICAL` > 90%.
- Inodes `WARN` > 80%, `CRITICAL` > 90%.
- Memory `WARN` > 85%, `CRITICAL` > 95%.
- Swap `WARN` if sustained non-trivial usage appears.
- Load `WARN` if 15-minute load exceeds CPU count for a sustained period.

### G. Logs and Error Signals

Required checks where feasible:

- Django 5xx errors increased.
- Django uncaught exceptions increased.
- Postal delivery failures increased.
- Maddy inbound processing errors increased.
- PostgreSQL logs show repeated connection or disk errors.
- Docker logs show crash loops.

Implementation can start simple:

- Inspect recent logs for known critical patterns.
- Count occurrences since last run.
- Alert only on meaningful spikes.

Avoid noisy raw-log forwarding. Alerts should summarize the symptom and point to where logs can be inspected.

### H. Backup Freshness

Required checks:

- Latest PostgreSQL backup exists.
- Latest backup age is within policy.
- Latest backup size is plausible.
- Optional checksum/manifest exists.
- Optional restore-test status is checked if a restore validation process exists.

Suggested thresholds:

- `WARN` if backup age > 26 hours.
- `CRITICAL` if backup age > 48 hours.
- `WARN` if latest backup size is much smaller than recent baseline.

## Metrics to Store

Store recent check results locally as JSON or SQLite.

Minimum:

- Current state per check.
- Last transition time.
- Consecutive failure count.
- Last OK time.
- Last alert time.
- Last detail.
- Recent metrics for digest.

Suggested files if using JSON:

```text
monitoring/state.json
monitoring/history.json
```

Do not let history grow without bounds. Keep either:

- Rolling 7-30 days of samples, or
- SQLite with retention cleanup.

## Alert Message Requirements

Telegram messages should be short and actionable.

Example critical:

```text
CRITICAL - PostgreSQL connection usage
Connections: 184/200 (92%)
Host: production
Time: 2026-07-04 15:00 IRST
```

Example recovery:

```text
RECOVERED - PostgreSQL connection usage
Connections: 91/200 (45%)
Time: 2026-07-04 15:10 IRST
```

Example digest:

```text
Mahansco Tier 2 digest - 2026-07-04 15:00 IRST
OK: Django internal health
OK: PostgreSQL query 14ms, connections 24/200
OK: Postal reachable, queue 3
OK: Maddy reachable, inbound age 2m
WARN: Disk /var/lib/docker 82%
```

## Configuration Requirements

All thresholds and service names should be configurable.

Example:

```json
{
  "settings": {
    "interval_seconds": 300,
    "failures_before_critical": 2,
    "digest_every_hours": 1,
    "timezone": "Asia/Tehran"
  },
  "services": {
    "django_internal_url": "http://backend:8000/api/health/",
    "postgres_host": "postgres",
    "postal_smtp_host": "postal",
    "maddy_smtp_host": "maddy"
  },
  "thresholds": {
    "disk_warn_pct": 80,
    "disk_critical_pct": 90,
    "postgres_connections_warn_pct": 75,
    "postgres_connections_critical_pct": 90,
    "backup_warn_hours": 26,
    "backup_critical_hours": 48
  }
}
```

## Security Requirements

- Do not expose internal monitoring endpoints publicly.
- Do not print secrets in logs.
- Do not include database passwords, mail credentials, tokens, or user data in Telegram messages.
- Use a least-privilege PostgreSQL monitoring user where possible.
- If mounting Docker socket, document the security tradeoff.
- If synthetic login or mail tests require credentials, use a dedicated monitoring account.

## Implementation Guidance for the Coding Agent

Before coding:

1. Read the Docker Compose files.
2. Identify service names, networks, volumes, ports, healthchecks, and env files.
3. Identify the Django settings module and existing health endpoints.
4. Identify whether Celery, Redis, workers, schedulers, or reverse proxies are present.
5. Identify PostgreSQL backup location and naming convention.
6. Identify Postal and Maddy integration points.
7. Identify existing logging conventions.

Then implement in small phases:

1. Add internal monitor runner and config.
2. Add Django internal health check.
3. Add PostgreSQL checks.
4. Add Docker/container checks.
5. Add Postal and Maddy checks.
6. Add filesystem/resource checks.
7. Add backup freshness check.
8. Add Telegram state-change alerting.
9. Add digest.
10. Document local and production operation.

## Acceptance Criteria

- The monitor can run inside the Docker Compose environment without public internet dependencies except Telegram alert delivery.
- A healthy stack produces no incident alerts, only the configured digest.
- Stopping the Django backend produces a debounced critical alert.
- Restarting the Django backend produces a recovery alert.
- Stopping PostgreSQL produces a critical alert.
- Filling a test filesystem past threshold produces a warning or critical alert.
- Postal outage is detected.
- Maddy outage or stalled inbound processing is detected.
- Backup freshness problems are detected.
- Secrets are not committed.
- Alert state persists across monitor runs.
- Documentation explains how to run, configure, and test the monitor.

## Non-Goals

- Replacing the public Tier 1 GitHub Actions uptime monitor.
- Building a public status page for internal metrics.
- Sending raw logs to Telegram.
- Monitoring private customer content.
- Adding paid SaaS dependencies.
- Implementing full APM/tracing unless the project already has that stack.

## Suggested Future Tier 3

Tier 3 can be added later for deeper observability:

- Prometheus and Grafana
- PostgreSQL exporter
- cAdvisor or node exporter
- Loki for logs
- Sentry for Django exceptions
- OpenTelemetry traces
- Automated restore testing

Tier 2 should stay simpler: internal health, dependency checks, resource thresholds, backup freshness, and actionable alerts.
