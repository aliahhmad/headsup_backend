# API reference

Use the running API's `/docs` for exact request/response schemas, validation,
query parameters, and status codes. `/openapi.json` exposes its machine-readable
schema. This overview groups the current API; feature guides explain behavior
beyond an individual endpoint.

## Authentication

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/registration/settings/` | Read the public invitation requirement |
| POST | `/registration/referral-code/` | Validate an invitation code |
| POST | `/register/` | Register and request verification email |
| POST | `/login/` | Obtain access and refresh tokens |
| POST | `/refresh/` | Exchange a refresh token for an access token |
| GET | `/verify/` | Follow an email verification link |
| POST | `/forgot-password/` | Request recovery email |
| GET / POST | `/reset-password/` | Display/redirect to reset UI or apply a reset |

Registration and login return `token` and `refresh`. Send protected requests with
`Authorization: Bearer <token>` or, on routes accepting it, `X-API-Key: <key>`.
Accounts must be verified and not banned. See [accounts](../accounts.md).

## Feature groups

| Paths | Guide |
| --- | --- |
| `/users/me`, `/users/`, `/users/keys` | [Accounts and access](../accounts.md) |
| `/monitors`, `/monitors/{id}/checks`, `/monitors/{id}/runs` | [Monitors](../monitors.md) |
| `/monitors/{id}/alerts` | [Alerts](../alerts.md) |
| `/notifications`, `/notifications/count` | [In-app notifications](../notifications.md) |
| `/users/me/notification-preferences` | [Alert email preferences](../alert-emails.md) |
| `/admin/registration/settings/` | [Invitation settings](../accounts.md#invitation-settings) |
| `/admin/queue` | [Queue management](../queue.md) |
| `/admin/monitor-runs` | [Run administration](../run-dashboard.md) |

The feature-group paths are entry points, not an exhaustive endpoint list.
Administrator routes require the administrator role. Ordinary monitor routes
remain scoped to their owner even for administrators. Unknown, foreign, and
soft-deleted monitor resources return 404 rather than exposing another account's data.

## Health, errors, and prefixes

`GET /heartbeat` returns `{"status":"ok"}` when the API is reachable.
`GET /metrics` is available only when `METRICS_ENABLED=true`. A heartbeat does not
validate SMTP delivery, model access, or queue progress.

Common responses include 401 for missing/invalid authentication, 403 for insufficient
permissions, 404 for unavailable resources, 409 for version/state conflicts, and
422 for request validation. Configured authentication rate limits may return 429.
Error response details depend on the endpoint; use the runtime schema and response.

Paths here assume the default empty `API_ROOT`. The supported server deployment
exposes routes at the API hostname root. The Vite `/api` prefix is a local browser
proxy convention and is stripped before forwarding to this API.
