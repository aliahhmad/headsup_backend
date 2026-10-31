# Troubleshooting

## API does not start

Check required backend settings and logs. `SECRET_KEY` needs at least 32 characters,
database credentials must be configured, and `I_READ_THE_DAMN_DOCS=true` satisfies
the existing startup acknowledgement. Apply `alembic upgrade head` against the
same database the API uses. Do not reset a populated database to fix migration state.

For local backing services:

```sh
docker compose -f compose.local.yml ps
docker compose -f compose.local.yml logs --tail=100 db
```

For the deployed server, use the wrapper and [server runbook](deployment/deployment.md):

```sh
sudo docker_support/deploy/server/dc.sh ps
sudo docker_support/deploy/server/dc.sh logs --tail=100 api db gateway
```

## Database connection fails

A host API uses `127.0.0.1` and the published local port, normally 5435. The local
API container uses Docker service `db` on 5432. Editing the password in `.env`
does not alter an existing PostgreSQL role. Keep the original credentials or
change the role deliberately; do not remove the data volume as a workaround.
See [database access](operations.md#access-postgresql-and-list-tables).

## Test database is missing

`headsup_test` is created only during initialization of a new local volume.
On an older volume, inspect `\l` in `psql` and create the separate test database
with the configured role if needed. Keep `TEST_DB_NAME` different from `DB_NAME`;
tests recreate its tables. No valuable data belongs in the test database.

## Browser calls the wrong API or CORS fails

The frontend setting is `VITE_API_URL`, without `/api` for the server deployment.
Rebuild/redeploy after changing it. The local Vite proxy is unavailable on a static
host. Set backend `CORS_ORIGINS` to the exact browser origin, including scheme and
port. Set `FRONTEND_URL` consistently and recreate the server API through its update
procedure after changing environment values.

## Login fails or no verification email arrives

New registrations require verification. Inspect Mailpit in local development;
real deployments require valid SMTP. CLI-created accounts are already verified.
Check whether the account is banned or an invitation code is required. Keep the
API `BASE_URL` and frontend URL correct before generating another recovery email.
Inside Docker, local Mailpit is `mailpit:1025`, not `127.0.0.1:1025`.

## A check remains queued

Monitor creation and Check now enqueue work; processing is administrator-controlled.
Check **Administration → Queue**, worker liveness, configured model key, and
pending composition for the monitor. Automatic mode defaults to off. Use a single
step or `api-admin worker --once` for explicit processing. Scheduled checks require
the updated API to be running and an active, undeleted monitor owned by a verified,
non-banned account. Existing queued/running work is coalesced instead of duplicated.
See [scheduling](scheduling.md) and [queue behavior](queue.md).

## Research succeeded but no alert appeared

That can be expected: no observations, empty/all-false decisions, already-published
developments, or a final composer declining an update all produce no alert. Inspect
`alert_processing` separately from research status. Pending/failed composition has
its own durable job and retry policy; see [alerts](alerts.md).

## A worker stopped during a request

The queue heartbeat measures service liveness, not progress within a model call.
After a hard kill, a research run can remain running and needs reconciliation.
Do not requeue a request still executing. Composition leases can recover independently;
SMTP delivery with an uncertain outcome is not automatically replayed.

## No alert email arrived

Email is opt-in in **My profile**, applies only to future alerts, and requires a
verified eligible recipient. There is no replay when opting in later. Inspect
`notification_deliveries` and the [delivery states](alert-emails.md). A `sent` state
means SMTP accepted the message, not that it reached the inbox. Pausing the research
queue does not stop the independent email loop.

## Server API root returns 404

That is intentional in the server defaults. Test `/heartbeat` for reachability and
`/docs` for the schema UI. If local health works but the public hostname fails,
check the existing tunnel's origin port and whether its connector runs on the
host or inside Docker. Preserve other application routes.
