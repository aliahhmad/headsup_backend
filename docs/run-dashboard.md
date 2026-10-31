# Monitor run administration

The frontend's **Administration → Monitor runs** page (`/admin/runs`) is a
read-only operations dashboard centered on executions across accounts. It uses
administrator-only APIs; ordinary users cannot access cross-account inputs or
traces. Opening a run does not queue work, call a model, or mark the owner's alerts
as read. Five-second refresh can be paused.

## Directory and drill-down

The directory shows the run ID, parent monitor/current account, trigger, research
attempts, research/alert states, latest recorded model request, evidence counts,
queue wait, execution duration and safe error codes. Search matches account names,
email, monitor names, objective snapshots, and exact run/monitor UUIDs. Filters
include account/monitor IDs, research status, trigger, alert state, timezone-aware
creation window and sort order. Summary cards use the same filters as the list.
Dates in the UI are entered/displayed in the browser's timezone.

Soft-deleted monitors remain visible for historical inspection by administrators.
Hard-purged runs are no longer available. Account names/emails and monitor names
are current parent data; they are not reconstructed historical identity records.

Run inspection separates:

- **Execution & requests:** saved lifecycle times, composition outcome, and actual
  captured request attempts with model, stage, duration, HTTP/provider status,
  usage, and safe response identifiers.
- **Inputs & context:** the run's objective/configuration version, complete input
  snapshot and selected history, plus the current worker's context policy.
- **Memories:** exact input memory content/versions from the run snapshot, revisions
  linked to this run, accepted post-merge state used to validate composition, and
  explicitly separate current monitor memories.
- **Observations & sources:** all saved findings, source links and captured metadata.
  Missing publication dates, excerpts or hashes are labeled as uncaptured.
- **Alert decisions:** true and false decisions, reasons, development keys,
  observation references, dispositions, job retries and outcome codes.
- **Published alerts:** the digest and its selected evidence, including read/legacy
  dismissed timestamps. Admin inspection leaves owner state unchanged.

Queued runs are labeled as having enqueue-time snapshots. Once first claimed,
the worker captures execution inputs. Current memory must never be substituted
for missing historical input or presented as the value used by an older run.
No linked revision means no recorded accepted change; it does not by itself prove
that the merge request was skipped.

## Actual requests versus context policy

Migration `add_run_model_calls` creates `run_model_calls`. For future live worker
requests, the observer records the exact application JSON body separately from
business-output transactions. It captures the stage (`research`, `extraction`,
`knowledge`, `composition`), phase-local attempt number, requested model, start/end
times, duration, HTTP outcome and safe response metadata. Usage is shown when
reported; missing usage is not treated as zero cost. Retries are separate rows.

The request view separates exact input, instructions, the complete body (including
tools and output schema), and response metadata. Authentication headers/API keys,
raw provider errors, and raw provider response text are not captured. Traces do
contain private monitor inputs and evidence, so they use the same protected run
lifecycle and are exposed only through admin endpoints, with `Cache-Control:
no-store`. Deleting a run/monitor cascades to its traces. Explicit privacy erasure
must include retained input copies as well as memories/revisions.

Task-local context binds calls to the correct run and attempt, including concurrent
workers and composition retries. Trace storage is best effort: a telemetry-write
failure does not turn an otherwise valid research request into a failure. A hard
worker interruption can leave a request without a completion record; the UI does
not treat that alone as proof that the provider is still running.

Provider request success is not the same as successful parsing, factual validation,
research publication or alert publication. A completed response may subsequently
fail schema/reference validation, and research may succeed while composition fails.
The dashboard preserves those separate states rather than inventing a completion
percentage.

Historical runs before instrumentation and offline fixtures have no captured
provider requests. They remain inspectable using their saved snapshots and outputs,
but traces are not backfilled or synthesized. The context policy table describes
the current implementation, not an audit of historical omissions. Its profile/chat
presence counts describe current storage. Inspect a captured request for exact
fields sent in that request; information may also be reflected indirectly in
research text. Provider-internal context/reasoning is not available here.

The observer does not add model requests or change the existing observation/cost
limits. It also does not retain full fetched source pages. Exact monetary cost
cannot be inferred from token totals without the applicable model/tool pricing.

## API

| Method | Path | Result |
| --- | --- | --- |
| GET | `/admin/monitor-runs` | Paginated enriched run directory |
| GET | `/admin/monitor-runs/summary` | Counts and average completed duration under the same filters |
| GET | `/admin/monitor-runs/{run_id}` | Snapshots, memories/revisions, sources, decisions, alerts and request traces |

Query filters are `q`, `status`, `trigger`, `alert_status`, `user_id`, `monitor_id`,
`created_from`, `created_to`, and `order=newest|oldest`. `alert_status=not_evaluated`
selects runs without a saved alert job. Creation bounds must include a timezone;
an inverted range is rejected. Standard `page`/`size` parameters paginate the list.
Search wildcard characters are treated literally.

Existing owner-scoped `/monitors` endpoints retain their authorization rules. The
admin dashboard has no mutation or retry endpoint and exposes an explicit account
projection, never password hashes, access tokens, API keys or SMTP credentials.

## Setup and verification

Apply migrations and restart the API so it loads the new routes:

```bash
docker compose exec api uv run --locked alembic upgrade head
docker compose restart api
```

Restart any separately running worker to enable traces for its future requests.
Existing runs are not rerun. Navigate to `/admin/runs` while signed in as an admin.
Use the separate [queue administration page](queue.md) to enable automatic
processing or request one item. Opening the run dashboard only observes work.

Tests use mocked HTTP responses and the dedicated test database to verify admin
authorization, credential exclusion, filters/counts, historical fidelity, exact
input capture, evidence links, task isolation, telemetry failure tolerance and
all-false composition skipping. Live research quality remains a separate concern.

## Useful next additions

1. **Claim ownership and stalled-run detection:** the queue page now records a
   managed service heartbeat. Per-run ownership and recovery would help identify
   abandoned research; a long queue wait alone is not proof of a worker outage.
2. **Stage latency percentiles and alerts:** establish expected durations, then
   highlight regressions or repeated provider failures against measured thresholds.
3. **Prompt/model versions and experiment comparisons:** store explicit versions
   so changes in alert quality can be compared across runs. Request instructions
   and requested model are already captured for new calls.
4. **Cost accounting:** retain applicable pricing/version and tool usage alongside
   reported usage, rather than showing an invented currency estimate.
5. **Source and decision evaluation:** review duplicate alerts, missed important
   updates, unsupported claims, and citation quality against human-reviewed cases.
6. **Controlled recovery actions:** add audited, authorized retry/reconcile actions
   with fencing and idempotency, rather than resubmitting paid research casually.
