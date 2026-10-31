# Queued research worker

The worker polls queued research and due alert-composition work every five seconds.
Research saves sourced observations and merges `monitor_knowledge`, recording a
revision only when its content changes. The same merge call returns explicit alert
decisions. Empty/all-false decisions, or only duplicate developments, stop alert
processing without calling the smaller model. Eligible true decisions create a
durable composition job that can publish zero or one sourced digest per run.

Research success and alert-processing state are separate. Composition retries do
not repeat research or knowledge updates. Pending composition blocks new research
claims for that monitor while other monitors can proceed. Monitor context and
personal memory remain outside the update flow. External delivery and recurring
schedule enqueueing are unimplemented. API startup does not start this worker.

See the [alert guide](alerts.md) for schemas, endpoints, the monitor Updates panel,
recovery, and [future cost controls](alerts.md#observation-and-request-size-budgets).
Existing observation limits are unchanged and no new token caps have been applied.
The queue poll is not a monitor's research cadence.

The [administrator run dashboard](run-dashboard.md) shows work across accounts.
Apply `add_run_model_calls` and restart the worker to capture future request
stages, exact application JSON bodies, timing and reported usage. Request traces
exclude authentication headers and raw response bodies. Old runs and offline
fixtures are not backfilled. Trace storage is best effort and does not change
the research/alert transaction boundaries or make additional model requests.

## Start the worker

Run from `api/`, using the same environment and database as the API:

```bash
uv run --locked alembic upgrade head
uv run --locked api-admin worker
```

Set `OPENAI_API_KEY` in `.env`. The existing `chat-gpt-api-key` spelling is also
accepted; prefer the standard name for new setups. `OPENAI_MODEL` defaults to
`gpt-5.4-mini` and must support web search and Structured Outputs. API startup
does not start a worker or spend API credits.

For one queue check that processes at most one run:

```bash
uv run --locked api-admin worker --once
```

Live research with findings normally makes three Responses API requests: web
search with citations, structured extraction that groups the cited answer into
developments, then a structured knowledge merge. The extractor receives
backend-assigned citation IDs and may only reference those IDs. It does not get to invent source URLs. Empty research
without citations produces zero observations. The merge is skipped when extraction
returns no observations, and an empty queue makes no API calls. Eligible alert candidates add a separate
composition request with `OPENAI_ALERT_MODEL` (default `gpt-5.4-nano`); this fourth
provider call is skipped when decisions are empty/all false or deduplicate away.

## Knowledge updates

The merge receives the objective, frozen previous `monitor_knowledge`, and validated
observations with their supporting sources. Its prompt requests the complete
updated knowledge, preserving still-relevant facts, adding supported findings,
avoiding duplicates, and keeping unresolved conflicts and uncertainty explicit.
Missing information in the latest run does not justify forgetting earlier facts.
Live responses require `content` (1–20,000 characters), `change_reason`
(1–1,000 characters), and `alert_decisions` (possibly empty). Each decision uses a
backend-assigned observation reference, a strict boolean, factual change/relevance
reasons, and a development key for positive decisions. Unknown/repeated references,
missing decisions, inconsistent keys, and invalid output are rejected. The merge
also receives owned observation/publication history and accepted monitor context.
Structured output validates shape, not the factual correctness of the merge.

When trimmed content changes, the worker increments only that monitor's knowledge
version and inserts a full `MemoryRevision` with `source_kind=run` and
`source_run_id`. Identical trimmed content creates no revision or timestamp change.
Zero-observation runs retain knowledge and skip the merge request. Detailed evidence
remains in observations and sources. Next checks read the current knowledge, while
the completed run retains its original input snapshot. Context edits and personal
memory are outside this flow. Apply `add_alert_digests` before running the updated
worker so durable candidates, composition jobs, and digest links are available.

## Offline dry run

Queue a run by creating a monitor or using its authenticated Check now endpoint.
Then execute:

```bash
uv run --locked api-admin worker --once --fixture docs/examples/research-result.json
```

This makes no external requests and requires no OpenAI key. It **writes the
synthetic fixture to the real configured database** and finishes the oldest
eligible run. Use a disposable development monitor. This is an offline pipeline
exercise, not a transaction rollback. A malformed fixture is rejected before any
run is claimed. Live mode is selected by omitting `--fixture`.

Fixtures must include `knowledge_update` containing the complete replacement
`content` and `change_reason` when observations exist, or `null` for zero
observations. Fixture mode does not merge with existing knowledge: it writes that
explicit snapshot. Use only a disposable monitor, as the fixture can replace its
previous knowledge. Old fixtures omit decisions and produce no alerts. To exercise
positive alerts, use `docs/examples/alert-result.json`, which includes decisions
and an explicit `alert_composition`. Fixture execution does not resume unrelated
pending live composition jobs.

## Inspect the results

Read the existing authenticated endpoint:

```text
GET /monitors/{monitor_id}/runs/{run_id}
```

The response includes `status`, `error_code`, and an `observations` array. Each
observation contains its summary and a `sources` array with all evidence fields.
Run detail additionally returns `alert_processing` and an optional published
`alert`. Research can succeed while composition is pending/failed. Read a digest
with evidence through `GET /monitors/{monitor_id}/alerts/{alert_id}`. Read current
knowledge and its version through `GET /monitors/{monitor_id}`.
Run-list responses remain lightweight. Access is checked through the owning,
non-deleted monitor, and responses use `Cache-Control: no-store`.

Evidence is stored in relational tables, not in run input snapshots or memory.
Publication dates, page excerpts, and content hashes remain null because this
integration does not capture full source pages. `retrieved_at` records when the
worker accepts the completed research result, not OpenAI's upstream fetch time.
API citations do not prove a claim is true; summaries retain uncertainty. Show
saved source links as visible, clickable citations in any results UI.

## Execution and failure behavior

- Claims lock the monitor before its run and skip locked monitors, so multiple
  PostgreSQL workers cannot claim the same queued run.
- First execution refreshes objective and monitor-memory snapshots consistently.
  Retries must preserve those inputs; changed inputs cancel the old logical run.
- External requests run outside database transactions. Before saving, the worker
  checks lifecycle, configuration, memory versions/content, and attempt identity.
  Stale results are discarded and the run is cancelled with `superseded_inputs`.
  Paused monitors permit manual runs; archived/deleted ones permit none.
- Every saved observation has at least one source. All observations, sources,
  accepted knowledge changes, revisions, decisions, job state, and successful
  research completion commit together. Digests and their included evidence/ledger
  links publish atomically afterward.
  A merge or revision failure publishes no outputs or memory changes.
  Duplicate output entries are merged; unique run/item and observation/source keys provide database protection.
- Provider failures, refusals, malformed results, unknown citation IDs, and timeouts
  fail the run without publishing evidence. Safe error codes are stored instead
  of provider bodies, credentials, or personal input. Research has a five-minute
  overall timeout including knowledge merging. Failed research runs are not automatically
  retried. Composition alone retries up to three total attempts with 5/15-second
  delays, a 180-second lease, a 150-second timeout, and fenced completion. Expired
  composition claims can be recovered; exhausted work remains visibly failed.
- Graceful cancellation records `worker_interrupted`. A hard process kill or
  database outage during completion can leave a run `running`. Automatic lease
  recovery is deferred: stop the affected worker and reconcile that run before
  requeueing it. Do not requeue a run that is still executing.

## Request and storage contract

The search request contains the run's `objective_snapshot` and `context_snapshot`,
plus fixed research instructions. IDs, scheduling metadata, credentials, and
unrelated user-profile data are not research inputs.

The extraction request uses a strict schema with this shape:

```json
{
  "observations": [
    {"summary": "One supported development", "source_ids": ["s1"]}
  ]
}
```

The backend resolves those IDs against search-answer citation metadata, validates
HTTP(S) URLs and nonempty source lists, and creates the observation and evidence
rows. Identical normalized summaries get deterministic item keys; source URLs
get deterministic source keys within each observation. Keys deduplicate identical
outputs, not semantically different summaries. Semantic consolidation is the
extractor's responsibility. Alert development keys are matched against published
identities and deduplicated across runs before composing and before publishing.
Semantic identity matching still depends on model behavior; see the alert guide.
