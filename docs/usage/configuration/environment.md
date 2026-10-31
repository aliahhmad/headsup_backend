# Configuration

The backend reads environment variables and the ignored `api/.env` file. Start
from `.env.example` for local development. Server deployment uses its own generated
`docker_support/deploy/server/.env`; see the [deployment runbook](../../deployment/deployment.md).
Keep secrets out of Git and frontend `VITE_*` variables. Do not source environment
files as shell scripts.

## Application and database

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | JWT signing secret; generate at least 32 characters |
| `I_READ_THE_DAMN_DOCS` | Existing startup acknowledgement; set `true` |
| `DB_USER`, `DB_PASSWORD` | PostgreSQL credentials; configure real, non-placeholder values |
| `DB_ADDRESS`, `DB_PORT` | Database host and port; local host API uses `127.0.0.1:5435` |
| `DB_NAME` | Application database, normally `headsup` |
| `TEST_DB_NAME` | Separate disposable test database, normally `headsup_test` |
| `BASE_URL` | Public backend URL used in account email links |
| `FRONTEND_URL` | Frontend origin used for redirects and application links |
| `CORS_ORIGINS` | Comma-separated exact browser origins, including scheme and port |
| `API_ROOT` | Route prefix; leave empty for the supported server deployment |
| `NO_ROOT_ROUTE` | Return 404 at the bare API root when enabled |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime; default 120 minutes |

Generate secrets with `openssl rand -hex 32`. Use the same configured database
credentials for local services, migrations, and the API. Changing `.env` does not
change a password in an already initialized PostgreSQL volume.

`BASE_URL` is the API origin, while `FRONTEND_URL` is the browser application
origin. For example, use `https://api.example.com` and `https://app.example.com`.
In the server deployment, `API_URL` in the deployment environment is mapped to
backend `BASE_URL`. Its routes are at the hostname root, with no `/api` suffix.

The frontend build setting is `VITE_API_URL=https://api.example.com`. With no value,
the local Vite development proxy uses `/api` and strips that prefix before forwarding.
Rebuild static frontend assets when changing `VITE_API_URL`.

## Research and alerts

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Server-side key for live research and composition |
| `OPENAI_MODEL` | Research/extraction/knowledge model; configured default `gpt-5.4-mini` |
| `OPENAI_ALERT_MODEL` | Conditional composition model; configured default `gpt-5.4-nano` |

These are application defaults, not a guarantee that every account has access to
the named models. The research model must support the tools and structured output
used by the provider. Missing model access is reported as a run failure.

Starting the API also starts the managed queue service, but new databases have
automatic processing off. Queue mode is stored in the database and survives
restarts. A saved enabled mode resumes processing; `worker --once` is an explicit
single-item override. See [queue controls](../../queue.md).

## Registration and administration

`REFERRAL_CODE_ENABLED` sets the initial invite-only policy, and `REFERRAL_CODE`
is the private code used to validate registrations. Administrators can change
the stored policy in Signup settings. Configure a nonempty private code before
enabling the gate. Existing accounts can still sign in when signup is restricted.
Never put the code in frontend configuration.

`ADMIN_PAGES_ENABLED` controls the optional SQLAdmin database panel. It is separate
from the React administrator pages and does not enable or disable those APIs.
When using SQLAdmin, configure a stable Fernet `ADMIN_PAGES_ENCRYPTION_KEY` and
review the route/title/timeout settings in `.env.example`. It is disabled in the
local example and the supported server deployment.

## Email

| Variable | Purpose |
| --- | --- |
| `MAIL_SERVER`, `MAIL_PORT` | SMTP endpoint |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | SMTP credentials when required |
| `MAIL_FROM`, `MAIL_FROM_NAME` | Sender address and displayed name |
| `MAIL_STARTTLS` | Upgrade a connection to TLS, commonly on port 587 |
| `MAIL_SSL_TLS` | Implicit TLS, commonly on port 465 |
| `MAIL_USE_CREDENTIALS` | Authenticate with the SMTP server |
| `MAIL_VALIDATE_CERTS` | Validate TLS certificates; keep enabled for real SMTP |

Enable STARTTLS or implicit TLS according to the SMTP service, not both. The local
Mailpit example disables TLS and authentication and captures mail without external
delivery. Inside the local API container, use `MAIL_SERVER=mailpit`; the host API
uses `127.0.0.1`. Do not carry local Mailpit settings into the server deployment.

Account verification, password reset, and opt-in alert delivery use these settings.
Alert email preferences default to off for every user; enabling SMTP alone does
not subscribe them. See [alert emails](../../alert-emails.md).

## Optional infrastructure

| Setting | Behavior |
| --- | --- |
| `CACHE_ENABLED` | Enables caching on the endpoints that opt into it; default false |
| `REDIS_ENABLED` | Selects Redis for enabled cache/rate-limit storage; default false |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB` | Redis connection settings |
| `CACHE_DEFAULT_TTL` | Default cache lifetime in seconds; default 300 |
| `RATE_LIMIT_ENABLED` | Enables configured authentication rate limits; default false |
| `METRICS_ENABLED` | Exposes Prometheus metrics at `/metrics`; default false |
| `LOG_PATH`, `LOG_LEVEL`, `LOG_FILENAME` | Log location, level, and filename |
| `LOG_ROTATION`, `LOG_RETENTION`, `LOG_COMPRESSION` | File rotation and retention |
| `LOG_CATEGORIES`, `LOG_CONSOLE_ENABLED` | Category selection and console output |

Without Redis, enabled caching/rate limiting use process-local storage, which is
not shared across replicas. Redis must be running and reachable when selected.
Monitor, notification, and administrator private reads use `Cache-Control: no-store`.
The authoritative settings and validators are in `app/config/settings.py`.

Restart host processes after changes. Containers need recreation to load updated
environment values; use the server update procedure for that deployment.

## Configured authentication rate limits

When `RATE_LIMIT_ENABLED=true`, `app/rate_limit/config.py` defines these limits:

| Operation | Limit |
| --- | --- |
| Registration | 3 per hour |
| Invitation-code check | 10 per minute |
| Login | 5 per 15 minutes |
| Forgot password | 3 per hour |
| Email verification | 10 per minute |
| Refresh token | 20 per minute |
| Reset form GET | 10 per minute |
| Reset password POST | 5 per hour |

These limits are independent of the monitor Check now cooldown and the queue's
single-dispatcher controls. They do not impose model observation or token budgets.
