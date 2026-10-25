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

## SMTP verification emails

Registration already sends an email containing a verification link. The current
sender uses an authenticated Amazon SES Mail Manager SMTP endpoint on port 587
with STARTTLS. Set these values in `.env` (or the deployed backend environment):

```dotenv
MAIL_USERNAME=<your SMTP username>
MAIL_PASSWORD='<your SMTP password>'
MAIL_FROM=headsup@notify.aliahhmad.dev
MAIL_FROM_NAME="HeadsUp"
MAIL_SERVER=<your AWS SMTP endpoint>
MAIL_PORT=587
MAIL_STARTTLS=True
MAIL_SSL_TLS=False
MAIL_USE_CREDENTIALS=True
MAIL_VALIDATE_CERTS=True
```

Use the plaintext username and password fields from the SMTP credentials export;
the SMTP client handles Base64 encoding. Single-quote the password in `.env` to
preserve special characters. Keep credentials in the ignored `.env` file or the
hosting service's secrets. `BASE_URL` must be the reachable API URL because it is
used to build the verification link.

Mail Manager must have a rule that sends accepted messages to their recipients,
such as [Send to internet](https://docs.aws.amazon.com/ses/latest/dg/eb-rules.html),
and an execution role permitted to send using the verified SES sender identity.
Successful SMTP authentication alone does not confirm inbox delivery.

Restart the host API process after changing `.env`. For Docker, recreate the API
container from this directory so it loads the new environment:

```bash
docker compose up -d --no-deps --force-recreate api
docker compose logs -f --tail 100 api
```

Register a new account through the frontend, open the email in that account's
inbox (check spam too), and click the verification link within 10 minutes. CLI
accounts are already verified and do not send this email.

To return to Mailpit, use the local-development mail settings above. When the API
runs inside Docker, set `MAIL_SERVER=mailpit`; when it runs on the host, use
`MAIL_SERVER=127.0.0.1`. Docker reads the mail settings from `.env` without
overriding the selected SMTP server.

## Email templates

Emails use the website's navy, blue, teal, and amber palette, with a shared layout
in `app/templates/email/base.html`. Layout tables, inline styles, system fonts,
Outlook button fallbacks, and responsive spacing keep them readable without
external fonts or images. Every template has a plain-text alternative.

| Template | Purpose | Status |
| --- | --- | --- |
| `welcome.html` | Email verification, with a 10-minute link | Sent during registration; also used by the resend helper |
| `password_reset.html` | Password recovery, with a 30-minute link | Sent by forgot-password |
| `password_changed.html` | Security confirmation and recovery link | Ready to use; no automatic trigger |
| `notification.html` | General account updates with an optional action | Ready to use; no automatic trigger |

`EmailManager.template_send` adds the frontend URL and current year, and sends
HTML plus plain text when a matching `.txt` template exists. Existing custom
templates without a plain-text counterpart still work. Names and message text
are escaped in HTML.

Template messages use Python's standard MIME builder, with plain text first and
HTML last in `multipart/alternative`, so HTML-capable clients prefer the styled
version. Multipart containers are not base64-encoded.

For a general notification, supply `message` in the template body. Optionally
supply `name`, `heading`, `preview_text`, `category`, `introduction`, `action_url`,
and `action_label`. Use a trusted HTTPS URL for actions. Password-change emails
need the recipient's `name`; the recovery action uses `FRONTEND_URL`.

Preview all four designs with sample data, without sending any email:

```bash
uv run python scripts/preview_emails.py
```

Open the printed `index.html` path to switch between templates, HTML/plain text,
and desktop/mobile widths. Pass `--output <directory>` to choose the destination.
Preview links contain dummy tokens. Rendering in real email clients can differ
from a browser preview.

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
