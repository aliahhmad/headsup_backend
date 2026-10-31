# Development and testing

Use the [quick start](../quick-start.md) for the host API with Docker-backed
PostgreSQL and Mailpit. The checked-in Python version is 3.13, dependencies are
managed by `uv.lock`, and the server image also uses Python 3.13.

## Run the API on the host

From `api/`:

```sh
uv sync --locked --python 3.13
uv run --locked alembic upgrade head
uv run --locked api-admin serve
```

For explicit Uvicorn options:

```sh
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app
```

Restart after changing environment settings. The API hosts both the managed
recurring scheduler, research queue service, and independent alert-email delivery loop. You do not
need an additional worker process for administrator queue controls.

## Run the API in Docker

The root `docker-compose.yml` is for local development and mounts source code.
It is separate from the immutable server image and deployment Compose file.
For local email capture, set `MAIL_SERVER=mailpit` in the environment consumed by
the container; keep the Mailpit port/TLS/auth values from the quick start.

```sh
docker compose up -d --build --wait
docker compose logs -f --tail=100 api
docker compose exec api uv run --locked api-admin user create --admin
```

The local image currently uses the Python version declared in `Dockerfile`
(3.14), while host/server development targets 3.13. The API binds loopback port
8000. Host and all-container modes use the same local project and database volume;
stop the host API before starting the container API on that port. For optional
Redis, enable the `cache` profile and corresponding settings explicitly.

Stop containers with `docker compose down`; retain the database volume. Switching
the API back to the host requires `MAIL_SERVER=127.0.0.1` again when using Mailpit.

## Run tests

Configure `TEST_DB_NAME=headsup_test` with local database credentials and keep it
different from `DB_NAME`. The local Compose initialization creates this database
for a new volume. The test suite recreates tables there and must never target
valuable data. Database connection credentials/host/port come from `DB_*`, with
only the name selected by `TEST_DB_NAME`.

```sh
uv run --locked pytest -n 2
uv run --locked pytest tests/unit --no-cov
```

Use a test file or `-k` expression for a focused change. The offline worker fixtures
exercise persistence without model calls but write to the configured application
database, so they are a different workflow from tests.

## Frontend and checks

From `web/`, use `npm ci`, `npm run dev`, and `npm run build`. See its README for
routing, API configuration, and static hosting. Source changes should be checked
with the relevant test/build commands and the repository's configured quality
checks; do not interpret a documentation build as application validation.

GitHub Actions currently cover Ruff, mypy, prek hooks, Zizmor, dependency review,
and CodeQL. There is no hosted application-test or coverage-upload workflow.
The available backend Poe tasks are defined in `pyproject.toml`.
