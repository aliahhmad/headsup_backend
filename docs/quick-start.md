# Local quick start

Use Python 3.13, `uv`, Docker with Compose, and a Node.js version supported by the
locked frontend dependencies. Run backend commands from `api/`; frontend commands
from the neighboring `web/` directory. This guide creates a local development setup.
For an existing server, use the [deployment runbook](deployment/deployment.md).

## 1. Configure the backend

For a fresh checkout only:

```sh
cp .env.example .env
openssl rand -hex 24
openssl rand -hex 32
```

Put the first generated value in `DB_PASSWORD` and the second in `SECRET_KEY`.
Keep an existing `.env` rather than overwriting its credentials. The example uses
local defaults, including PostgreSQL on port 5435 and Mailpit on port 1025:

```dotenv
DB_USER=headsup
DB_PASSWORD=<your-generated-database-password>
DB_ADDRESS=127.0.0.1
DB_PORT=5435
DB_NAME=headsup
TEST_DB_NAME=headsup_test
SECRET_KEY=<your-generated-signing-secret>
I_READ_THE_DAMN_DOCS=true
BASE_URL=http://localhost:8000
FRONTEND_URL=http://localhost:5173
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
MAIL_SERVER=127.0.0.1
MAIL_PORT=1025
MAIL_FROM=noreply@example.com
MAIL_STARTTLS=false
MAIL_SSL_TLS=false
MAIL_USE_CREDENTIALS=false
MAIL_VALIDATE_CERTS=false
```

`I_READ_THE_DAMN_DOCS` is the existing startup acknowledgement setting. The literal
angle-bracket values above are placeholders. See [configuration](usage/configuration/environment.md)
for live SMTP, invitation codes, and optional model access.

## 2. Start backing services and the API

```sh
uv sync --locked --python 3.13
docker compose -f compose.local.yml up -d --wait
uv run --locked alembic upgrade head
uv run --locked api-admin serve
```

Keep the API running in this terminal. Migrations apply additive schema changes;
use `alembic upgrade head` for setup and upgrades. The database initialization CLI
can reset data and is not the migration procedure.

| Service | Local address |
| --- | --- |
| API documentation | <http://localhost:8000/docs> |
| API heartbeat | <http://localhost:8000/heartbeat> |
| Email inbox | <http://localhost:8025> |
| PostgreSQL | `127.0.0.1:5435` |

The database volume contains `headsup` and a separate `headsup_test` database.
The initialization script creates the test database only when the volume is new.

## 3. Create an administrator and start the frontend

In another backend terminal:

```sh
uv run --locked api-admin user create --admin
```

CLI-created users are verified. API registrations must follow the verification
link delivered to Mailpit. Enter credentials in the interactive CLI prompt.

From `web/`:

```sh
npm ci
npm run dev
```

Open <http://localhost:5173/login>. The Vite development proxy forwards browser
`/api` requests to `http://localhost:8000`; no frontend environment file is required
for this arrangement.

## 4. Run a monitor

Create a monitor in the frontend. Creation queues an initial check. Configure
`OPENAI_API_KEY` in the backend environment and restart the API before live processing.
As an administrator, open **Administration → Queue**, keep automatic mode off,
and select **Run next item**. This can make paid model requests.

Alternatively, run one item from `api/`:

```sh
uv run --locked api-admin worker --once
```

The [offline fixture workflow](monitor-worker.md#offline-dry-run) makes no model
requests but writes synthetic findings to the configured database. Use a disposable
monitor. The run dashboard shows results; an alert is conditional on worthwhile,
new developments. No alert is a valid successful outcome.

## Stop and restart

Stop host API/frontend processes with Ctrl+C. Stop backing services with:

```sh
docker compose -f compose.local.yml down
```

The named database volume persists. Do not add `-v` if you want to keep accounts,
monitors, and history. For all-container local development, see
[development](development/local.md#run-the-api-in-docker).
