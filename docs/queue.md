# Administrator queue management

Open **Administration → Queue** (`/admin/queue`) as an administrator. This page
controls existing queued work across all accounts. It does not enqueue recurring
checks or change monitor schedules. Opening or refreshing it makes no model calls.

## Controls and behavior

- **Turn automatic on:** save a shared switch that lets the managed worker process
  one executable item at a time. It checks for work every five seconds. The switch
  survives API restarts and defaults to **off** after migration.
- **Turn automatic off:** stop admitting further automatic work. An item already
  admitted finishes its research and any immediate alert composition. Pause does
  not cancel requests, delete checks, or pause individual monitors.
- **Run next item:** while automatic processing is off and no request is outstanding,
  accept one durable request and return immediately. The background worker processes
  at most one item, then remains paused. Repeated/concurrent clicks are rejected
  while a request is pending or processing.

One item is **one due alert-composition attempt**, or, if none can be claimed,
**one queued research check and its immediate alert-composition attempt**. Existing
composition jobs have priority. Research can involve search, extraction and a
knowledge/decision update; eligible positive decisions add the smaller-model
composition. Empty/all-false or duplicate decisions still skip composition. A
single step does not drain every composition retry; a delayed retry needs another
step or automatic mode.

The next executable item may differ from the oldest displayed research row:
monitors may be busy, cancelled or blocked by composition work. An attempt that
finds nothing executable ends as **Empty**. The research table shows the oldest
20 queued runs, with a link to the filtered run directory and a separate link to
pending alert jobs.

The page shows research backlog, due/delayed/active alert work, the managed API
worker heartbeat, the current request and the latest 20 nonempty automatic or
manual attempts. A heartbeat within 20 seconds is considered online. This is
service liveness, not proof that a particular provider request is progressing.
The heartbeat continues during provider requests. A missing key or a service error
is shown separately from the saved processing mode.

**Completed** means the controller finished its attempt. Research may have failed,
and successful research may still have pending or failed alert composition. The
history shows those outcomes separately and links to the full run dashboard.
Empty automatic polls are discarded so they do not flood history. Actor IDs are
recorded for mode changes and administrator single-step requests. CLI steps have
no web account actor. These records do not hold prompts, credentials, or user data
beyond identifiers; private request traces remain in the run dashboard.

## Worker operation

The API lifespan now starts a lightweight managed queue service. It makes no paid
requests until an administrator enables automatic processing, submits a manual
step, or an operator explicitly invokes the CLI one-shot command. `OPENAI_API_KEY`
must be configured before enabling processing through the admin API.

API processes and updated CLI workers share a PostgreSQL session advisory lock,
so only one managed dispatcher may process an item at a time across the database.
The lock uses a dedicated connection in autocommit mode; no business transaction
or monitor row lock is held through a provider request. Settings/manual admission
use a short singleton-row transaction to serialize competing control changes.
Existing per-monitor claims, input checks and fenced composition leases remain.

An extra CLI process is no longer required for the frontend controls. If one is
used, its continuous loop obeys the same saved automatic switch:

```bash
uv run --locked api-admin worker
```

For an explicit one-shot operation, even when automatic mode is off:

```bash
uv run --locked api-admin worker --once
```

`--once` still shares the dispatcher lock. If the lock is busy, it exits without
processing an additional item. Fixture mode remains an explicit write of synthetic
outputs to the configured database; it also uses managed dispatch. See the
[worker guide](monitor-worker.md#offline-dry-run) before using fixtures.

Restart **all old API/worker processes** after upgrading. Already-running old
CLI processes do not know about the new control switch. The global controls apply
to the updated managed entry points, not ad hoc scripts calling low-level claim
functions directly.

Graceful shutdown interrupts the current attempt using existing research and
composition cleanup, and records the controller attempt as interrupted. After a
hard kill, the next dispatcher to obtain the advisory lock marks leftover
controller attempts interrupted. It does **not** replay paid research. A research
run may still be `running`; inspect and reconcile it before retrying. Existing
expired composition leases retain their recoverability. There is no destructive
queue clear, force-cancel, or automatic failed-research retry action.

## API

All endpoints require a current administrator and return `Cache-Control: no-store`.
Ordinary users receive 403; unauthenticated requests receive 401.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/admin/queue` | Policy, liveness, work counts, current request, recent attempts |
| PATCH | `/admin/queue/settings` | Save `{"automatic_enabled": true}` or `false` |
| POST | `/admin/queue/run-next` | Accept one durable request; return 202 with its ID and pending state |

A single-step request conflicts (409) with automatic mode or another outstanding
request. Enabling automatic mode conflicts with an outstanding manual request.
Missing provider configuration rejects new starts with 503; turning automatic off
is always allowed. Validation uses a strict boolean and rejects unknown settings.
No processing happens inside the HTTP request. Poll GET to follow completion;
network retries cannot silently queue a second step while the first is outstanding.

## Setup and verification

From `api/`, apply the migration and restart the API:

```bash
docker compose exec api uv run --locked alembic upgrade head
docker compose restart api
```

Migration `add_queue_controls` creates `queue_control` and `queue_executions` and
initializes automatic mode to off. It does not modify research, observations,
memories, alerts, or monitor schedules. Restart any separately running workers.
Open `/admin/queue` to process existing work explicitly.

Tests use mocked research against the dedicated test database. They cover access
control, defaults, persisted settings, duplicate admission, exact single stepping,
automatic draining/pause, real advisory-lock exclusion, pause during active work,
interrupt cleanup, safe errors, alert retry priority, heartbeat age and migration
upgrade/downgrade. No live research or paid provider requests are required.

See the [run dashboard guide](run-dashboard.md) for inspecting inputs, evidence,
memories and model request traces behind each attempt.
