# Tier 0 optimization acceptance report — 2026-09-19

Implemented on `optimize-tier0`, based on `origin/main` at `fa2d43a`. The original
local branch was behind generated-state commits only. Those commits were preserved
by branching from the refreshed remote. The owner's untracked `optimization.md`
was left unchanged and excluded from commits.

## Findings addressed

- Completion heartbeat and producing run/commit metadata; explicit fresh, delayed,
  stale and unknown UI states, observation age and UTC+03:30 display.
- Shared published configuration, coverage-qualified observed availability, missing
  intervals, 35-day retention and boundary context. No inferred success during gaps.
- Single backend debounce, one incident on confirmed DOWN, recovery-time closure,
  and incident start persistence across retention boundaries.
- Canonical API routes, semantic assertions for all seven public targets, harmless
  GraphQL POST, enabled SSO discovery, redirect policy and final URL recording.
- Obsolete IP pin removal, optional validated A/AAAA address sets and advisory
  mismatch policy; nonpublic DNS answers fail without exposing their addresses.
- Strict configuration checks, credential-free validation/tests, corrupt/partial
  dataset rejection, individually atomic writes and completion marker written last.
- Preserved TLS metadata and warning flags on inspection errors; one sustained-error
  warning and recovery. Bounded, redacted, escaped Telegram retries and queue; a
  failed manual test returns a failing process status.
- Removed-target state pruning with recent incident continuity; a single published
  data location and compact per-observation serialization on future runs.
- Staggered cron, bounded execution, pre-probe tests, approved action major versions,
  scoped permissions, safe publication retry and failure recovery artifact.
- Accessible textual status, announced summary changes, labeled observation bars,
  responsive wrapping, escaped interpolations and operator recovery documentation.

The duplicate root data files were byte-identical to `docs/` immediately before
removal. All three canonical published files remain byte-identical to the branch
base; tests and read-only probes did not regenerate operational data. Root copies
are recoverable from earlier Git commits. The first deployed completed cycle will
publish schema-v2 metadata and apply time-based pruning; the new page displays
UNKNOWN until that metadata exists.

## Scheduler investigation

The workflow was active, the repository unarchived, and `main` was its default
branch with recent generated commits. The latest 20 monitor-only runs, from
2026-09-16 16:54:28 UTC through 2026-09-19 11:29:23 UTC, all succeeded. Their 19
start-to-start intervals had minimum **109.15 minutes**, median **214.77 minutes**,
and maximum **298.50 minutes**. These results exclude the separate Pages jobs.

The newest [monitor run](https://github.com/m3hr4nn/mahansco-uptime/actions/runs/35440242325)
was created at 11:29:23 UTC; its job started at 11:29:26 and completed at 11:29:37.
The sampled successful runs provide no evidence of push failures or long-running
monitor jobs explaining the multi-hour trigger gaps. Serialization was already
enabled. The API does not establish the exact cause of missing cron invocations.

The cron now uses `2-59/5`, avoiding minute zero. This is a contention mitigation,
not a guarantee. GitHub documents that scheduled events can be delayed or dropped:
[schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

**Post-change production cadence is not yet measured:** changes are local and no
live workflow was dispatched. Reassess actual intervals and published coverage
after deployment; do not claim five-minute detection or an availability SLO.

## Acceptance evidence

| Requirement | Automated evidence |
| --- | --- |
| Valid and malformed configuration | `ConfigTests`: real config, unsafe URLs, booleans, numeric limits/order, unknown keys, duplicate names, methods and body rules |
| Debounce and exactly one transition per incident | `CycleTests.test_debounce_transitions_recovery_and_retention` plus page incident tests |
| Fresh/delayed/stale boundaries | Page freshness tests, including invalid/missing/future timestamps |
| Missing observations and honest coverage | Page slot-coverage tests: sparse data, duplicates, raw failures, insufficient data |
| Recovery duration and retained boundary | Page incident tests and Python time-retention test |
| Probe semantics and failure modes | `ProbeTests`: JSON/HTML, GraphQL auth boundary, redirects, A/AAAA, DNS failures, timeout, HTTP and invalid TLS; `CycleTests` TLS metadata preservation |
| Notification failures | Queue eviction/expiry, retry/redaction, API rejection, failing manual ping |
| Safe persistence and pruning | Atomic replacement failure, corrupt-data preservation, completion write failure, removed target handling |
| Safe page rendering | Escaping tests for names/details/URLs/certificates; stale/unknown/inconsistent snapshots cannot render healthy |
| One generated-data location | `test_only_one_generated_location` rejects reintroduced root copies |
| Publication races | `PublicationTests`: local bare repositories exercise clean/no-op pushes, concurrent source edits and conflicting data without losing either version |

Validation completed:

```text
python3 monitor.py --validate                                  PASS
python3 -m unittest discover -s tests                          PASS (30 Python tests, including the page runner)
node tests/status.test.js                                     PASS (7 page cases)
PYTHONPYCACHEPREFIX=/tmp/mahansco-uptime-pycache \
  python3 -m py_compile monitor.py                            PASS
actionlint v1.7.12 .github/workflows/monitor.yml                PASS
bash -n scripts/persist-state.sh                              PASS
git diff --check                                              PASS
Existing published state/history/rollup structural validation  PASS
```

Read-only live checks using the implemented probes passed for all seven targets.
HTTP response times in that sample were 632–982 ms. Requested TLS metadata checks
also passed. These are individual observations, not measured availability or a
detection guarantee. No probe result was written to public state, and no Telegram
message was sent.

GraphQL's anonymous `__typename` query returns HTTP 200 with null data and the
structured `authentication_required` error. The probe explicitly validates that
public authentication boundary; it does not claim authenticated resolver health.
The public SSO realm discovery document was reachable and is already referenced
by the main project's enabled monitoring configuration.

## Remaining operational decisions and limits

- The Tier 1 dead-man contract is documented in README. Implementing it in the
  separate production stack was outside scope. It still cannot alert during a
  total outage of its own production host; stronger external heartbeats require
  owner approval.
- Git history continues to grow with generated commits. Removing duplicates and
  compacting future observations reduces churn without rewriting public history.
- Telegram delivery and Git persistence cannot be atomically committed together;
  crashes or ambiguous network responses may duplicate a delivery. Normal state
  transitions queue once, and retry storage/time is bounded.
- No DNS, GitHub secrets, Pages settings, Telegram configuration or Tier 1 services
  were modified. No code was pushed or deployed. A real `workflow_dispatch` test
  requires the owner's explicit confirmation because it sends messages and commits
  live state, as required by the implementation brief.
