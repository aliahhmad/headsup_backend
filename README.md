# HeadsUp API

**Stay informed without having to keep checking.**

HeadsUp is an AI-powered personal monitoring assistant concept. Users describe
what they care about; HeadsUp aims to follow relevant sources, remember past
developments, and notify them only when something meaningful changes.

This repository contains the FastAPI backend foundation. Authentication, user
management, API keys, password recovery, administration, and local development
services are available. Topic monitors, scheduled collection, AI analysis, and
change notifications are planned product features.

## HeadsUp local development

Run the API with Python 3.13 and `uv`, with PostgreSQL and a local email inbox
provided by Docker. Use `compose.local.yml` for this setup.

The local `.env` contains generated secrets and is ignored by Git. For a fresh
checkout, copy `.env.example` to `.env` and configure these values first:

```dotenv
DB_USER=headsup
DB_PASSWORD=<generate with openssl rand -hex 24>
DB_ADDRESS=127.0.0.1
DB_PORT=5435
DB_NAME=headsup
TEST_DB_NAME=headsup_test
SECRET_KEY=<generate with openssl rand -hex 32>
I_READ_THE_DAMN_DOCS=true
BASE_URL=http://localhost:8000
FRONTEND_URL=http://localhost:5173
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_FROM=noreply@example.com
MAIL_SERVER=127.0.0.1
MAIL_PORT=1025
MAIL_STARTTLS=False
MAIL_SSL_TLS=False
MAIL_USE_CREDENTIALS=False
MAIL_VALIDATE_CERTS=False
```

The acknowledgement setting is required by the template; review
[breaking changes](docs/important.md) before upgrading it.

```bash
uv sync --locked --python 3.13
docker compose -f compose.local.yml up -d --wait
uv run --locked alembic upgrade head
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  --reload --reload-dir app
```

- API docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/heartbeat>
- Local email inbox: <http://localhost:8025> (Mailpit captures outgoing email)
- Database: `127.0.0.1:5435`, with separate `headsup` and `headsup_test` databases

Create your first admin account interactively:

```bash
uv run --locked api-admin user create --admin
```

Choose your own email and password, then sign in at <http://localhost:8000/admin>.
Accounts created through the CLI are already verified. Accounts registered
through the API receive a verification link in the local email inbox.

Run the existing test suite against the dedicated test database:

```bash
uv run --locked pytest -n 2
```

Tests recreate tables in `TEST_DB_NAME`; keep it distinct from `DB_NAME`.
Redis is optional and disabled for this setup.

Stop the API with Ctrl+C and stop backing services with
`docker compose -f compose.local.yml down`. Database data persists in the
project's Docker volume across restarts. Changing database credentials in `.env`
after the volume has been initialized also requires updating the PostgreSQL role.

## Hosted frontend connection

Set these environment variables on the service hosting the deployed backend:

```dotenv
BASE_URL=https://headsup-api.aliahhmad.dev
FRONTEND_URL=https://headsup.aliahhmad.dev
CORS_ORIGINS=https://headsup.aliahhmad.dev
```

`BASE_URL` controls verification and password-reset email links. `FRONTEND_URL`
redirects the API's password-reset page to the frontend. `CORS_ORIGINS` allows
browser requests from the deployed frontend; use comma-separated exact origins
without trailing slashes. If connecting a local frontend to this API, append
`http://localhost:5173,http://127.0.0.1:5173` to the allowed origins.

Restart or redeploy the backend after changing these settings. The local `.env`
is ignored by Git, and `.env.example` is a reference; neither automatically
updates environment variables on a hosted service. Keep production database,
SMTP, and secret settings in the backend host's environment.

In Vercel, the frontend uses `VITE_API_URL=https://headsup-api.aliahhmad.dev`.
Redeploy the frontend after setting that build-time variable.

## Docker resources

| Resource | Name |
| --- | --- |
| Compose project | `headsup-api-local` |
| PostgreSQL container | `headsup-db` |
| Local email container | `headsup-mailpit` |
| API container (full Docker mode) | `headsup-api` |
| Optional cache container | `headsup-cache` |
| PostgreSQL data volume | `headsup-api-local_postgres-data` |

The database volume is shared by both development modes. Container renaming does
not change the stored databases or credentials.

To run the API in Docker instead, stop the API running on the host, then run:

```bash
docker compose up -d --build --wait
```

The API still uses port 8000. Both Compose files use the same backing services
and database volume. Use `docker compose down` to stop the full stack. Add
`--profile cache` when starting it to include Redis, and set `REDIS_ENABLED=true`
and `CACHE_ENABLED=true` in `.env` to use it.

## Project details

- API title: **HeadsUp API**
- Python package: `headsup-api`
- Admin title: **HeadsUp Administration**
- Email sender name: **HeadsUp**
- Author, public contact email, website, and repository URL: not configured

`MAIL_FROM=noreply@example.com` is a local testing sender captured by Mailpit;
it is not a public contact address. Configure a real sender and SMTP service
before using email outside development.

The inherited `api-admin` command remains available for administration.
Application metadata lives in `app/config/metadata.py`; Python package metadata
lives in `pyproject.toml`.

## Documentation and attribution

The [documentation](docs/index.md) covers the backend and its infrastructure.
Some detailed guides retain examples from the original template and describe
inherited functionality rather than implemented HeadsUp monitoring features.

This project is based on
[Grant Ramsay's FastAPI Template](https://github.com/seapagan/fastapi-template).
The original copyright and MIT license are preserved in [LICENSE.txt](LICENSE.txt).
