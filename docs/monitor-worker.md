# Queued research worker

This increment polls existing queued monitor runs every five seconds, researches
their objectives with OpenAI web search, and saves `Observation` and
`ObservationSource` records. Runs with findings merge current `monitor_knowledge`
and save a revision when its content changes. Monitor context and personal memory
are not changed. Threshold evaluation, alerts, deliveries, and periodic scheduling
remain deferred.
The five-second interval is queue polling, not a request to repeat every monitor's
research every five seconds. One worker processes one run at a time.

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
returns no observations, and an empty queue makes no API calls.

## Knowledge updates

The merge receives the objective, frozen previous `monitor_knowledge`, and validated
observations with their supporting sources. Its prompt requests the complete
updated knowledge, preserving still-relevant facts, adding supported findings,
avoiding duplicates, and keeping unresolved conflicts and uncertainty explicit.
Missing information in the latest run does not justify forgetting earlier facts.
The response contains only `content` (1–20,000 characters) and `change_reason`
(1–1,000 characters); blank, oversized, malformed, or extra fields are rejected.
Structured output validates shape, not the factual correctness of the merge.

When trimmed content changes, the worker increments only that monitor's knowledge
version and inserts a full `MemoryRevision` with `source_kind=run` and
`source_run_id`. Identical trimmed content creates no revision or timestamp change.
Zero-observation runs retain knowledge and skip the merge request. Detailed evidence
remains in observations and sources. Next checks read the current knowledge, while
the completed run retains its original input snapshot. Context edits and personal
memory are outside this flow. No migration is needed for this update.

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
previous knowledge.

## Inspect the results

Read the existing authenticated endpoint:

```text
GET /monitors/{monitor_id}/runs/{run_id}
```

The response includes `status`, `error_code`, and an `observations` array. Each
observation contains its summary and a `sources` array with all evidence fields.
Read current knowledge and its version through `GET /monitors/{monitor_id}`.
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
  accepted knowledge changes, revisions, and successful completion commit together.
  A merge or revision failure publishes no outputs or memory changes.
  Duplicate output entries are merged; unique run/item and observation/source keys provide database protection.
- Provider failures, refusals, malformed results, unknown citation IDs, and timeouts
  fail the run without publishing evidence. Safe error codes are stored instead
  of provider bodies, credentials, or personal input. Research has a five-minute
  overall timeout including knowledge merging. Failed runs are not automatically
  retried.
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
extractor's responsibility. Cross-run alert deduplication remains deferred.
