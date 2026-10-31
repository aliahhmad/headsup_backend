# Email updates for alerts

Alert email updates are optional. Open **My profile → Email updates**, turn
**Email me new alert updates** on or off, and save. The preference belongs only
to the signed-in account. It defaults to **off** for both existing and new accounts.
No historical alerts are emailed when migrating or opting in.

Only a newly published `alert.published` event can enqueue an email. Monitor
activation/pause, observations, negative decisions and composition retries do not
create additional emails. In-app notifications and their read history remain
available regardless of this preference. No extra AI calls are made.

## Email content and presentation

The HTML template extends the existing HeadsUp email design: a navy header, clear
update headline, readable paragraphs, a teal monitor-information card, source
links and a prominent **Read update & sources** button. Inline styles and table
layout support common email clients, with a fluid 600px shell, mobile adjustments
and an Outlook-compatible button. Essential content also has a plain-text MIME
alternative, ordered before HTML. No remote images, fonts or tracking pixels are
required.

Each message includes:

- The published alert title and complete message.
- The monitor name and an objective excerpt, captured when the alert is published.
- The alert publication timestamp explicitly labeled UTC.
- Deduplicated HTTP(S) source links from the observations selected for this alert.
- A deep link to that exact update and its evidence dialog.
- A link to My profile to change the email preference.

Monitor objective excerpts are limited to 360 display characters; this does not
change model input, observation limits or research cost controls. Private memories,
raw prompts, account credentials and internal model traces are never included.
User/model text is HTML-escaped, subjects are normalized to prevent header
injection, and links are generated from configured application URLs. Opening the
email does not mark the alert or inbox notification read.

Review the synthetic [HTML preview](examples/alert-email-preview.html) or open the
[MIME email preview](examples/alert-email-preview.eml) in a mail client. These use
fictional accounts, IDs and example links. No email was sent to generate them.
Actual rendering in individual email clients remains a separate verification step.

## Preference API and recipient checks

| Method | Path | Result |
| --- | --- | --- |
| GET | `/users/me/notification-preferences` | Current `email_alerts_enabled` and last saved time |
| PATCH | `/users/me/notification-preferences` | Save `{"email_alerts_enabled": true}` or `false` |

Both endpoints require authentication and return `Cache-Control: no-store`.
They do not use the cached profile response. Other-account IDs, custom recipients,
unknown fields, nulls and string booleans cannot override the setting.

Recipients must be verified, not banned, and still own the accessible monitor and
alert. The queued recipient address must match the account at send time. Changing
the account email through the profile API disables the preference and cancels
waiting mail until the user explicitly enables it again.

Turning the preference off immediately cancels `pending` and `processing` deliveries.
The final transition to `sending` serializes with preference changes. An email
already admitted to SMTP may still arrive after opting out; it is not recalled.
Turning the preference on later does not replay skipped or historical mail.

## Persistence and worker behavior

Migration `add_alert_email_delivery` creates two tables:

| Table | Purpose |
| --- | --- |
| `notification_preferences` | One account's explicit opt-in and update timestamp; missing rows mean off |
| `notification_deliveries` | Durable email work, associated notification, recipient, monitor snapshot, stable Message-ID, attempts, claim lease, safe outcomes and timing |

The inbox notification and its eligible delivery row commit with final alert
publication. `UNIQUE(notification_id, channel)` prevents duplicate delivery work.
Retrying the same published event cannot add another email or retroactively email
an event created while opted out. A failed outbox insert rolls back publication;
the existing composition retry retains successful research. SMTP failures happen
after publication and do not roll back alerts, knowledge or evidence.

The API lifespan runs a separate email worker, polling every five seconds. Email
work is independent of the automatic **research** queue switch and the OpenAI key.
No extra CLI worker is required. A standalone research CLI can enqueue durable
email work while the API is offline; delivery resumes when the updated API starts.
SMTP calls run outside database transactions. Claims use PostgreSQL row locks and
attempt-specific lease tokens so multiple API processes cannot normally send the
same item concurrently.

Delivery states:

| State | Meaning |
| --- | --- |
| `pending` | Waiting for a first attempt or known-safe retry |
| `processing` | Claimed and preparing/checking eligibility; SMTP has not started |
| `sending` | Final preference check passed; SMTP outcome may become uncertain |
| `sent` | SMTP accepted the message; this does not prove inbox placement or reading |
| `skipped` | Opted out, recipient changed/ineligible, or target unavailable |
| `failed` | Permanent refusal, rendering/configuration failure, or exhausted safe retries |
| `uncertain` | Acceptance cannot be confirmed; automatic replay is stopped |

Known pre-DATA connection failures and explicit temporary SMTP refusals retry at
most three total attempts, with 60/300-second delays. Permanent refusals do not
retry. The same Message-ID is reused across attempts, but SMTP does not guarantee
exactly-once inbox delivery. If confirmation is lost after sending begins, or the
worker dies with an expired `sending` lease, the delivery becomes `uncertain` and
is not automatically resent. This favors avoiding duplicate email. Review the
SMTP provider logs/Message-ID before any manual recovery. No public force-resend
endpoint is provided.

A crashed preparation can be reclaimed after its 120-second lease. Graceful
cancellation before SMTP schedules a safe retry; cancellation after SMTP begins
records an uncertain outcome. Safe error codes are stored without SMTP error
bodies, passwords, or provider credentials. `sent_at` denotes SMTP acceptance.

Deleting an inbox event cascades its delivery record. Soft monitor deletion removes
its notifications/deliveries, and hard monitor/account purges cascade them. An
already sending message cannot be recalled from SMTP. Email content already received
by a mailbox is outside the database deletion lifecycle.

## Configuration and startup

Reuse the existing `MAIL_*` configuration; no new global email feature flag is
needed. Set `FRONTEND_URL` to the browser application's public URL so the update
and profile links work for recipients. A missing/invalid frontend URL fails the
delivery before SMTP. The backend/API URL alone does not provide these UI routes.

Apply migrations and restart the API:

```bash
docker compose exec api uv run --locked alembic upgrade head
docker compose restart api
```

Restart separately running research workers so new publications enqueue delivery
work. Existing queue mode and SMTP credentials remain unchanged. Enable the
preference in My profile, then publish a new alert to exercise delivery.

For local capture instead of an external mailbox, explicitly configure the API's
SMTP host as `mailpit` inside Docker (or `127.0.0.1` for a host-run API), port 1025,
with credentials/STARTTLS/SSL disabled. View captured mail at
<http://localhost:8025>. Production uses the configured external SMTP service and
appropriate TLS/authentication settings. The implementation does not replace or
print configured SMTP secrets.

Inspect delivery outcomes in PostgreSQL:

```sql
SELECT id, notification_id, status, attempt_count, reason_code, error_code,
       next_attempt_at, started_at, sent_at, completed_at
FROM notification_deliveries
ORDER BY created_at DESC;
```

Tests use the dedicated test database with mocked/suppressed SMTP. They verify
opt-in isolation, defaults, profile email changes, atomic publication, duplicate
prevention, exact deep links, monitor context/sources, HTML escaping, MIME order,
read-state independence, opt-out races, lease recovery, bounded retries, uncertain
outcomes, and migration upgrade/downgrade. No real email or model request is needed.
