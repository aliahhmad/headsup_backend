# Project structure

The workspace has separate `api/` and `web/` Git repositories and shared product
documents in the parent `docs/` directory.

## Backend

| Path | Responsibility |
| --- | --- |
| `app/main.py` | FastAPI application, middleware, and service lifespan |
| `app/config/` | Environment settings, validation, and metadata |
| `app/database/` | Database engines and session dependencies |
| `app/models/` | SQLAlchemy tables, ownership, and constraints |
| `app/schemas/` | Request/response schemas and model-output contracts |
| `app/resources/` | HTTP endpoints and authorization dependencies |
| `app/managers/` | Domain transactions, queue dispatch, model integration/prompts, research, alerts, and delivery |
| `app/commands/` | `api-admin` CLI, including users, worker, and schema export |
| `app/migrations/` | Alembic schema history |
| `app/templates/` | Account pages and HTML/plain-text email templates |
| `tests/` | Unit and integration tests |
| `scripts/` | Developer utilities, including account-email previews |
| `docs/` | Current feature, setup, and operations guides |
| `compose.local.yml` | PostgreSQL and Mailpit for a host development API |
| `docker-compose.yml`, `Dockerfile` | All-container local development |
| `Dockerfile.server` | Non-root runtime image without development dependencies |
| `docker_support/deploy/server/` | Server environment generator, Compose, gateway, backup/update tools |

## Domain records

A user owns monitors. Each monitor owns check runs, observations and source evidence,
monitor memories/revisions, and published alerts. Observations belong to runs;
several observations can support one alert. Development keys identify published
changes across runs independently of per-response `finding_1` references.

`alert_candidates` retains knowledge-stage decisions, `alert_jobs` stores durable
composition work, and `alert_observations` links published evidence and provides
the development deduplication ledger. Research completion and composition completion
are separate transactions and states.

`run_model_calls` captures administrator-visible request attempts. `queue_control`
stores the shared automatic switch and heartbeat; `queue_executions` audits manual
and automatic dispatch attempts. Notifications use typed JSON payloads and optional
monitor scope. `notification_preferences` stores email opt-in, and
`notification_deliveries` is the email outbox. See each feature guide for fields
and constraints rather than inferring behavior from table names.

Chat/message and personal-memory structures exist, but conversational processing
is deferred. Persisted monitor cadence is not yet a recurring scheduler.

## Frontend

`web/src/App.jsx` connects routes, session state, and page components. `src/lib/api.js`
handles API requests and authentication, `src/lib/routes.js` describes navigation,
`src/pages/` provides screens, and `src/components/` provides shared and feature UI.
The React admin pages use authenticated APIs; they are separate from SQLAdmin.
