# Database and service operations

Choose the commands for the environment you are using. Run backend commands from
`api/`. The server wrapper supplies the deployment's Compose file, project name,
and private environment; plain local Compose commands do not select that stack.

## Inspect containers

On any Docker host:

```sh
docker ps
docker ps -a
docker stats --no-stream
```

Use `sudo` if your server account requires it. To inspect only the deployed HeadsUp
stack:

```sh
sudo docker_support/deploy/server/dc.sh ps
sudo docker_support/deploy/server/dc.sh logs -f --tail=100 api gateway
```

For local backing services:

```sh
docker compose -f compose.local.yml ps
docker compose -f compose.local.yml logs --tail=100 db
```

## Access PostgreSQL and list tables

Local database:

```sh
docker compose -f compose.local.yml exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Server database:

```sh
sudo docker_support/deploy/server/dc.sh exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Within `psql`:

```text
\dt public.*
\d monitors
\d monitor_runs
\d alerts
\d notifications
\q
```

Use `\d table_name` to inspect a table's columns, constraints, and indexes. The server
database is private to its Docker network; a database port need not be published
to use this container console. Local host clients connect to `127.0.0.1:5435` with
the credentials from the local environment.

## Migrations

For a host development API:

```sh
uv run --locked alembic current
uv run --locked alembic upgrade head
```

The server startup/update procedure applies `alembic upgrade head` before the API
starts. Inspect its current revision with:

```sh
sudo docker_support/deploy/server/dc.sh exec api alembic current
```

Do not use `api-admin db init` to upgrade a populated database. It is a reset-oriented
command. Do not delete volumes to fix an unapplied migration.

## Queue and delivery

Use [queue administration](queue.md) for automatic mode, single-step requests,
liveness, and controller history. Use the [run dashboard](run-dashboard.md) for
execution details and [alert emails](alert-emails.md) for delivery states.
Disabling research processing does not stop the independent email delivery loop.
A controller attempt marked Completed may contain failed research or composition;
inspect its linked run.

Research left running after a hard kill requires inspection and reconciliation;
it is not automatically retried. Composition and email have their own retry/lease
rules. Do not requeue work that may still be executing.

## Server backups and updates

```sh
sudo docker_support/deploy/server/backup.sh
sudo docker_support/deploy/server/update.sh
```

Follow the [complete server runbook](deployment/deployment.md) before updating:
turn automatic mode off, wait for active research and email work, preserve the
server environment and project name, and verify health afterward. The update
script checks idleness, closes this gateway, backs up the database, and recreates
the API with migrations. It leaves ingress closed on migration/startup failure.

Backups contain private account and monitoring data. Store copies off-server and
test restores into a separate database. Never use `down -v` during updates or run
global Docker pruning on a host shared with other applications.
