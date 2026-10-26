# Monitor backend

This is the first persistence and management step for HeadsUp monitors.
It provides authenticated configuration, initialized workspace records, and
durable check requests. It does not run searches, call an AI, send alerts,
process chat messages, or periodically enqueue scheduled work.

## Setup and authorization

Apply `alembic upgrade head` before running the updated API. The
`add_monitor_foundation` migration adds `monitors`, `chats`, `messages`,
`memories`, `memory_revisions`, and `monitor_runs`. Existing user and API-key
tables are unchanged. User ownership uses the existing integer `users.id`;
monitoring records use UUIDs.

All routes use the existing verified, non-banned user authentication through a
bearer token or API key. Records are scoped to that account, including for
administrators. Foreign, unknown, and soft-deleted monitors return `404`.
Read responses use `Cache-Control: no-store`.

Paths below are relative to the configured API root.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/monitors` | Create an active monitor and workspace; `201` |
| GET | `/monitors` | Paginate owned monitors; optional `status` filter |
| GET | `/monitors/{id}` | Read configuration, chat, current memories, and latest run |
| PATCH | `/monitors/{id}` | Update configuration with an expected version |
| DELETE | `/monitors/{id}?expected_version=1` | Soft-delete and cancel queued checks; `204` |
| POST | `/monitors/{id}/checks` | Queue or coalesce a check; `202` |
| GET | `/monitors/{id}/runs` | Paginate check history |
| GET | `/monitors/{id}/runs/{run_id}` | Read a run belonging to this monitor |

## Create a monitor

Send `POST /monitors`:

```json
{
  "name": "Summer internships",
  "objective": "Track summer 2027 backend internship openings in New York.",
  "monitor_context": "I graduate in May 2027 and have Python experience.",
  "check_interval_seconds": 86400
}
```

Only `name` and `objective` are required. Both are trimmed and must be nonblank;
name allows 120 characters and objective allows 10,000. Context defaults to an
empty string and allows 20,000 characters. The interval is an integer from 1 to
31,536,000 seconds and defaults to 86,400. Unknown fields are rejected, including
client-supplied ownership, knowledge, status, or version fields.

One transaction creates:

1. An active monitor at configuration version 1, with a future `next_run_at`.
2. Its single persistent chat, initially without a summary.
3. `monitor_context` and `monitor_knowledge` memories at version 1. The supplied
   context is saved as context; knowledge starts empty.
4. A complete initialization revision for each memory.
5. One queued initial run with attempt count 0 and enqueue-time input snapshots.

The response includes monitor configuration plus `chat`, `memories`, and
`latest_run`. Memory revision history is not loaded for ordinary reads. Shared
user-profile memory is not created or changed by monitor creation.

## List and change configuration

`GET /monitors?page=1&size=20&status=active` returns the existing pagination
format: `items`, `total`, `page`, `size`, and `pages`. Page size is bounded to 100.
Omit the status filter to include active, paused, and archived monitors. Deleted
monitors are always excluded. Lists use creation time and UUID for stable order.

Send `PATCH /monitors/{id}` using the version from the most recent response:

```json
{
  "expected_version": 1,
  "objective": "Track paid summer 2027 backend internships in New York.",
  "check_interval_seconds": 43200
}
```

Editable fields are `name`, `objective`, `status`, and
`check_interval_seconds`. Empty edits, explicit nulls, invalid states, and
missing expected versions return `422`. A stale version returns `409`.
Accepted changes increment the configuration version; an edit that repeats the
current values does not increment it. Updates lock the monitor row before
checking the version. Deletion uses the same version protection.

Changing the objective does not itself create another run. Earlier run snapshots
remain historical enqueue-time inputs. Changing the interval on an active
monitor sets its next due time from the current time plus the new interval.

Task-memory edits and conversation APIs are not exposed in this step.

## Lifecycle

| Change | Scheduling and queued work |
| --- | --- |
| Pause | Clear the due time and cancel queued initial/scheduled checks. Manual checks remain allowed. |
| Archive | Clear the due time, cancel all queued checks, and reject Check now. |
| Reactivate | Set a future due time and enqueue one current scheduled check, coalescing with any in-flight run. |
| Soft-delete | Clear the due time, cancel all queued checks, and make the monitor and its children inaccessible through these routes. |

Chats, memories, and history stay attached across status changes. Soft deletion
retains them for a future retention/purge workflow; it does not permanently erase
them or modify shared user-profile memory. Automatic purging is not implemented.

Running checks are not marked successful or silently rewritten by configuration
changes. A future worker must recheck monitor state, ownership, configuration,
and memory versions before starting and before committing outputs.

## Check now and history

Send `POST /monitors/{id}/checks`:

```json
{
  "client_request_id": "8d9d009c-319c-4c56-b6c3-d4d57fe89861"
}
```

Generate an identifier per intentional action and reuse it for retries. When a
new manual run is created, this identifier becomes its idempotency key. Repeating
that request returns the same run, even after it has finished. If another check
is already queued or running, the endpoint returns that check without creating
a separate manual run or storing another request identifier.

Monitor row locking and a partial unique database index permit at most one
queued/running run per monitor. A 60-second cooldown limits distinct newly
created manual checks; `429` responses include `Retry-After: 60`. Coalescing and
known request retries are checked before the cooldown. Manual checks do not
reset the monitor's automatic schedule.

Runs snapshot the objective and current monitor memory IDs, versions, and
contents. Chat summaries and raw messages are not run inputs. The future worker
must refresh relevant inputs consistently at first execution, then freeze them
for that logical run. Relevant shared-profile selection and evidence retrieval
belong to that execution step.

Queued status means the request is saved, not that external research has
happened. No run-start or completion endpoint is publicly exposed. Observation,
source, alert, and delivery models remain a subsequent implementation step.
