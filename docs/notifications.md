# In-app notifications

The inbox at `/notifications` and the topbar bell show account-owned activity.
Three exact event types are implemented. Notifications are plain text, retained
when read, and presented without automatic dialogs or sounds. Alert email
delivery is a separate opt-in; push is not implemented.

## Events

| Type | Trigger | Payload |
| --- | --- | --- |
| `alert.published` | A final sourced alert digest is published | `alert_id` |
| `monitor.activated` | A monitor is created active or transitions from paused/archived to active | `monitor_id`, `monitor_version`, `previous_status` |
| `monitor.paused` | A monitor transitions from active/archived to paused | `monitor_id`, `monitor_version`, `previous_status` |

An initial activation has `previous_status=new` and version 1. Saving the same
status, editing a name/objective/interval, archiving, or deleting a monitor does
not create a lifecycle notification. Subsequent real activations/pauses each
create their own event. Optimistic version checks reject stale monitor edits.

Alert decisions, individual observations, research completion and composition
retries do not themselves notify. A final published alert produces one notification,
regardless of how many findings it includes. Empty/all-false/deduplicated decisions
and a negative composition produce none. Existing monitors and alerts are **not
backfilled** when migrating, avoiding a burst of historical activity.

Example event:

```json
{
  "type": "alert.published",
  "payload": {
    "alert_id": "f91c7d04-c21e-4ebe-aa6a-d6d55d122a9a"
  }
}
```

Each exact event name selects a Pydantic payload schema. Missing IDs, invalid UUIDs,
unknown types, extra payload fields and invalid lifecycle transitions are rejected.
The namespace prefix groups related events; arbitrary substring matching is not
used. IDs are payload data, never embedded in the event type itself.

## Persistence and atomicity

Migration `add_notifications` creates `notifications`:

| Column | Purpose |
| --- | --- |
| `id` | Notification UUID |
| `user_id` | Recipient account; foreign key with deletion cascade |
| `type` | Supported event name |
| `payload` | Typed JSONB event data; there is no fixed alert ID column |
| `deduplication_key` | Unique event identity per recipient |
| `created_at` | Timestamp with timezone |
| `read_at` | Nullable acknowledgement timestamp |
| `seen_at` | Nullable exposure timestamp |
| `scope_monitor_id` | Nullable internal ownership/deletion scope, set for all current events |

The optional scope is independent of payload shape. A composite foreign key from
`(scope_monitor_id, user_id)` to `(monitors.id, monitors.user_id)` prevents a scoped
notification being assigned to a different account and cascades monitor purges.
Future account-only types can leave this scope null. Supporting a new event still
requires defining its payload, renderer, producer, visibility rules and database
allowed-type migration; the current API intentionally supports only these three.

`UNIQUE(user_id, deduplication_key)` prevents duplicates. Publication uses
`alert.published:<alert_id>`. Lifecycle keys include event type, monitor ID and
monitor version, so later genuine transitions can notify again. Indexes cover
account/time pagination, unread counts and monitor-scope deletion.

`save_composition()` inserts the alert notification in the same transaction as the
alert, selected evidence and publication state. A notification-write failure rolls
back publication; the existing composition retry can publish it later without
repeating successful research. Monitor creation/status changes and their notices
also commit or roll back together. There is no additional model request.

Alert references inside JSON are checked against the scoped monitor before insert.
They do not have a direct alert foreign key. The shared visibility query excludes
missing or mismatched alerts from history, counts and state mutation. There is no
public alert-deletion API. Ad hoc alert purges should also erase their JSON-linked
notification rows; hidden orphans otherwise persist until the monitor/account is
purged. Soft-deleting a monitor explicitly removes all its scoped notifications
in the same transaction. Hard monitor/account deletion cascades them.

Display titles, previews and internal links are derived from currently accessible
owned monitors/alerts. Full digest text, source evidence and historical input data
are not duplicated into inbox payloads. Monitor labels reflect current names;
payload version/previous status identify the historical transition.

## Reading and presentation

The bell polls the global unread count every ten seconds, on focus/visibility
return and after local notification-affecting actions. Count failures are indicated;
a missing response is not treated as zero. It links to the inbox and never opens
it automatically. Notification history polls every ten seconds, supports event-type
and unread filters, and paginates 20 items at a time.

Reading a list/count endpoint does not mark anything read or seen. Once inbox rows
are rendered in a visible document, the frontend acknowledges only those IDs as
seen. Merely receiving a notification or polling the bell does not acknowledge it.
Users may mark items read/unread without deleting history. There is no dismissal
or bulk clear action.

Alert notification read state and `Alert.read_at` synchronize in both directions,
including marking unread. Clicking Open update opens that specific alert in the
existing evidence dialog, even when it is outside the current Updates page.
Opening it from Updates also clears the corresponding unread inbox event. Read
acknowledgement implies seen; marking unread preserves the earlier seen timestamp.
Monitor lifecycle acknowledgement changes only the inbox event, never monitor
status. Monitor pages now expose version-protected Pause/Activate actions.

State changes use monitor → alert → notification lock order, matching existing
alert mutation/publication ordering. Notification content/recipient/type cannot
be edited through public endpoints. Even administrators see only their own inbox;
cross-account execution inspection remains a separate admin feature.

## API

All routes require authentication, use the authenticated account as recipient,
and return `Cache-Control: no-store`.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/notifications` | Paginated newest-first history; optional `type` and `unread` filters |
| GET | `/notifications/count` | Global `total`, `unread` and `unseen` counts |
| PATCH | `/notifications/{id}` | Set `read=true/false` and/or `seen=true` |
| POST | `/notifications/seen` | Acknowledge 1–50 rendered IDs in `{"ids": [...]}` |

Explicit nulls, string booleans, empty state changes and `seen=false` are rejected.
Batch seen requests ignore unknown/other-account IDs and report only the number
of owned visible rows changed. Foreign/missing notification mutations return 404.
There is no client-facing event-creation endpoint or arbitrary navigation URL.

## Setup and verification

From `api/`:

```bash
docker compose exec api uv run --locked alembic upgrade head
docker compose restart api
```

Restart separately running workers to enable publication notifications. Existing
queue mode is preserved; this migration does not start research or replay events.
Open Notifications after creating a monitor, pausing/reactivating it, or publishing
a future alert. Notification history starts empty for pre-migration events.

Tests cover exact payload validation, account isolation, initialization and genuine
status transitions, no-op/stale edits, transactional rollback, deduplication, quiet
research, retry publication, read synchronization, seen semantics, pagination and
filters, deletion/privacy scope, and migration upgrade/downgrade. Provider calls
are mocked. Optional [alert emails](alert-emails.md) now use account preferences
and a durable delivery outbox. Push and delivery batching remain future work.

See [alert email updates](alert-emails.md) for the profile switch, SMTP configuration, template preview and delivery recovery semantics.
