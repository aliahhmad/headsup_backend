# Deploy the development API to an existing Linux server

This setup adds HeadsUp alongside applications already running on a Linux server.
It assumes Docker Compose and an HTTPS tunnel/reverse proxy are already installed.
It does not install, replace, or stop those existing services.

The frontend stays on its current static host. Only the **backend repository** is
needed on the server; Node.js and the frontend repository are not needed there.

```text
Browser → hosted frontend
Browser → api.example.com → existing HTTPS tunnel/proxy
                              → 127.0.0.1:8081 → HeadsUp gateway → API → PostgreSQL
```

The gateway publishes only `127.0.0.1:8081`. API and database ports are not
published. There are no fixed container names. The default Compose project is
`headsup-demo`, with its own PostgreSQL volume, logs and networks. Keep another
application's database volumes, project names, tunnel routes and origin ports intact.

The image uses Python 3.13 and locked runtime dependencies. Source is built into
it, with one Uvicorn process and no development auto-reload. Updates require a
rebuild. Research and email workers run inside that API process as they do locally.
The local `docker-compose.yml` and its reload behavior remain available separately.

## 1. Transfer the backend checkout

Commit/push the intended backend files, then clone the backend repository to its
own directory using your actual repository URL:

```sh
mkdir -p ~/headsup
git clone YOUR_BACKEND_REPOSITORY_URL ~/headsup/api
cd ~/headsup/api
sudo docker compose version
```

If it is already cloned, review `git status --short` before pulling. Do not discard
server edits with `git reset --hard` or copy another application's environment file.
For a private repository use your normal Git SSH access. Do not put private keys,
passwords or tunnel tokens in chat, Git or frontend settings.

Alternatively transfer this backend directory while excluding `.env`, `.venv`,
`.git`, logs, caches and database dumps. Include `Dockerfile.server`, the lockfile,
`app/`, `static/`, `alembic.ini`, `pyproject.toml`, `LICENSE.txt`, `README.md`, and
`docker_support/deploy/server/`. The image needs the project metadata as well as application code.

## 2. Generate a separate environment file

Use your **actual public API hostname** and existing frontend origin:

```sh
python3 docker_support/deploy/server/init_env.py \
  --api-url https://api.example.com \
  --frontend-url https://app.example.com
nano docker_support/deploy/server/.env
```

The generator creates `docker_support/deploy/server/.env` with mode 600, separate random database,
signing and admin-encryption keys, and a private referral code. It refuses to
overwrite an existing file. Keep it outside Git; do not regenerate these secrets
on routine updates. Quote SMTP values containing `$` or `#` with single quotes
in the environment file; do not `source` it as a shell script. This is independent of the local development `api/.env`.

Set the real SMTP host, sender, credentials and TLS settings in that file. Copy only
the relevant **values** from your existing SMTP configuration; there are no Mailpit
overrides in this deployment. Enable STARTTLS for port 587 or implicit TLS for port
465 as required by your mail service, not both. Certificate validation stays on.
Verification, password reset and opt-in alert emails use this same SMTP service.

Set `OPENAI_API_KEY` server-side when ready to process real research. It can stay
empty for initial startup; the UI will report that live research is unavailable.
Automatic queue processing starts off in a fresh database. Email updates default
to off for each account. Neither migrating nor opening a dashboard starts paid work.

Important settings:

| Variable | Value |
| --- | --- |
| `API_URL` | Full public HTTPS API origin, without `/api` or a trailing slash |
| `FRONTEND_URL` | Canonical frontend HTTPS origin |
| `CORS_ORIGINS` | That exact frontend origin; add other trusted origins comma-separated |
| `ORIGIN_PORT` | An unused loopback port, default 8081 |
| `STACK_NAME` | Unique Compose project, default `headsup-demo`; keep stable after startup |
| `REFERRAL_CODE` | Private invitation code for demo registration |
| `MAIL_*` | This deployment's real SMTP settings |

`API_ROOT` is intentionally empty: routes are exposed at the API hostname root.
The browser admin dashboard remains available through the frontend and authenticated
API routes. The separate SQLAdmin HTML panel is disabled in this deployment.

Check that your chosen port is free with `sudo ss -ltnp`. If 8081 is occupied,
change `ORIGIN_PORT` and use the same port in the tunnel route. Do not reuse another
application's proxy port. Docker-published ports can bypass host firewall rules;
keep this binding on loopback. The existing tunnel supplies public HTTPS, so this
setup needs no additional public listener on port 80 or 443.

## 3. Build, validate and start

Run from the backend repository root:

```sh
sudo docker_support/deploy/server/start.sh
```

The script validates Compose configuration, builds the API image, checks URLs and
SMTP configuration without sending mail, starts PostgreSQL, and waits for API and
gateway health. The API applies `alembic upgrade head` before it starts Uvicorn.
It never uses the destructive `api-admin db init` command. A failed migration
prevents the API becoming healthy.

Create an administrator interactively:

```sh
sudo docker_support/deploy/server/dc.sh exec api api-admin user create --admin
```

Keep passwords in the interactive prompt instead of command-line arguments.
CLI-created accounts are verified. The database starts fresh: this does not copy
local accounts, monitors, notifications or research history. An ordinary registration
needs the configured invite code and working verification email.

Verify locally before changing the public tunnel route:

```sh
sudo docker_support/deploy/server/dc.sh ps
sudo docker_support/deploy/server/dc.sh exec api alembic current
curl -fsS http://127.0.0.1:8081/heartbeat
```

Expect `{"status":"ok"}`. Use the chosen port if you changed it. The bare API root
returns 404 deliberately; use `/heartbeat` for health and `/docs` for API docs.
Health does not prove SMTP delivery or model availability.

## 4. Add or update only the HeadsUp tunnel route

Keep the existing tunnel connector and its other application routes running.
In a dashboard-managed tunnel, add/edit the published application for the HeadsUp
API hostname. Leave the path unrestricted and set its service to **HTTP** with
URL **`127.0.0.1:8081`**. Do not append `/api`. Preserve other hostname entries.

For a locally managed tunnel, merge this entry into its existing ingress list,
before the existing catch-all rule, then validate/reload using your normal workflow:

```yaml
ingress:
  # Keep all your existing application entries.
  - hostname: api.example.com
    service: http://127.0.0.1:8081
  # Keep the final catch-all last.
  - service: http_status:404
```

This is an **example fragment**, not a replacement for the entire tunnel config.
No tunnel token or credential JSON is needed in this repository. Do not create a
second laptop connector for the same tunnel unless you intend to operate replicas
with identical reachable origins.

If an existing local reverse proxy routes the hostname instead, add a separate
upstream pointing at `http://127.0.0.1:8081` and preserve the original `Host` and
`CF-Connecting-IP` headers. The private gateway normalizes forwarded HTTPS/client
headers before passing them to Uvicorn. Do not publish the API container directly
or expose the gateway on all interfaces while trusting those headers.

Then verify:

```sh
curl -fsS https://api.example.com/heartbeat
```

The API hostname should bypass any custom edge cache rules. Do not cache login,
verification, reset, authenticated JSON or notification responses. The gateway
sets `Cache-Control: no-store`. If your tunnel has an additional access/login gate,
make sure intended testers can reach it as well as the frontend.

### If the connector itself runs in Docker

`127.0.0.1` inside a bridge-networked connector means that container, not the host.
Either retain the existing host-networked/local-proxy arrangement, or connect the
connector to the new gateway's edge network:

```sh
sudo docker network connect headsup-demo-edge YOUR_TUNNEL_CONTAINER
```

Then use **`http://headsup-demo-origin:8080`** as this hostname's tunnel service.
Persist that external network attachment in the connector's existing Compose
configuration so it survives container recreation. Do not replace its existing
networks or environment, and do not attach it to the API/database networks.
Change the names if you chose a different `STACK_NAME`.

## 5. Connect the existing frontend

In the frontend hosting environment set:

```dotenv
VITE_API_URL=https://api.example.com
```

**HeadsUp uses `VITE_API_URL`, not `VITE_API_BASE_URL`.** There is no `/api` suffix
for this deployment. Rebuild/redeploy the frontend after changing the value; Vite
embeds it at build time. The local Vite `/api` development proxy is not available
on a static host. Keep the existing SPA fallback to `index.html`.

Set the backend `FRONTEND_URL` and `CORS_ORIGINS` to the same actual frontend
origin. Additional preview origins must be explicitly allowed. Keep SMTP/OpenAI
keys, database passwords and invitation codes out of all `VITE_*` settings.

Test login, monitor creation, the admin queue/run views, notifications, and profile
email preferences. Enable automatic research or request a manual queue item only
when ready for live model requests. Test a verification/reset email and an opted-in
new alert; SMTP settings being syntactically valid does not verify delivery.

## 6. Update the backend later

Keep the project name, server `.env`, database volume and tunnel route unchanged.
Source is not bind-mounted, so pulling code alone does not affect the running API.

1. In the admin queue page, turn automatic processing off and wait for current
   work to finish. Wait for email delivery as well; opting out in the profile can
   cancel mail waiting to be sent. Do not interrupt an active SMTP send casually.
2. Review server edits and pull the intended backend revision:

   ```sh
   cd ~/headsup/api
   git status --short
   git pull --ff-only origin main
   sudo docker_support/deploy/server/update.sh
   ```

3. The update script builds/validates first, checks that work is idle, closes only
   this gateway, rechecks for last-second work, and takes a PostgreSQL backup. It
   then stops/recreates the API, applies migrations, and reopens the gateway only
   after health passes. Other applications, databases and the tunnel keep running.
4. Confirm the expected revision, local/public health, login and changed behavior.
   Restore the queue mode manually when ready. Frontend updates remain separate.

If a migration/startup fails, inspect `dc.sh logs --tail=100 api` and the backup.
Do not blindly run old code against a changed schema or run `down -v`. The script
leaves ingress closed on migration failure so an unhealthy API is not presented
as ready. A plain container restart does not load changed environment values;
use the update procedure to recreate the API after changing settings.

## Backups and operations

```sh
sudo docker_support/deploy/server/backup.sh
sudo docker_support/deploy/server/dc.sh logs -f --tail=50 api gateway
sudo docker_support/deploy/server/dc.sh exec api alembic current
sudo docker_support/deploy/server/dc.sh stop gateway api
sudo docker_support/deploy/server/dc.sh up -d --wait
```

Backups are custom-format `pg_dump` archives in `docker_support/deploy/server/backups/`, protected
by the creating user's permissions and checked with `pg_restore --list`. Copy
backups and the server configuration securely off-server; a disk-local backup
alone does not survive losing the server. Test restoring to a separate disposable
database using `pg_restore --no-owner --no-acl`; do not overwrite a running database.
Do not import a laptop database casually: durable queue/email rows can resume
work. Reconcile pending requests, claims and recipient preferences before starting
a restored copy. This deployment initializes fresh data by default.

Use `dc.sh stop` to retain containers/data. **Never use `dc.sh down -v` during an
update**: it deletes this project's database volume. Do not run global Docker
prune/cleanup commands on a server shared with other applications.

The API runs as a non-root user with a read-only root filesystem, a writable
private logs volume and temporary storage. Only the private gateway publishes a
host port. In-memory rate limiting is intended for this single API process. The API base and gateway use named runtime tracks; PostgreSQL 18.1 matches local
development. Review/pin image digests for stricter release reproducibility. Rebuild
application images deliberately for updates; database-image upgrades need their
own backup and compatibility plan.

## Troubleshooting

- Local heartbeat fails: inspect this project's `dc.sh ps` and `logs api db gateway`;
  check migrations, environment validation and whether the selected port is occupied.
- Local works but public fails: check the existing tunnel's route/connector and
  origin port. Preserve other application routes and verify host-vs-container localhost.
- Browser calls localhost or the old API: correct `VITE_API_URL` and redeploy the
  frontend. The server cannot change a previously built frontend bundle.
- CORS fails: allow the exact browser origin and recreate the API through the
  update procedure. Do not work around it with wildcard origins.
- Email links use the wrong host: correct `API_URL`/`FRONTEND_URL`, recreate, and
  generate a new email. Review `docs/alert-emails.md` for delivery statuses.
- Research stays queued: configure the server-side OpenAI key and explicitly
  enable the managed queue. Periodic monitor enqueueing is still not implemented.
- Image CLI fails only on sample seeding: sample data uses development-only Faker;
  use a local development checkout for that optional operation.

References: [Compose startup health](https://docs.docker.com/compose/how-tos/startup-order/),
[Docker published ports and firewall behavior](https://docs.docker.com/engine/install/ubuntu/),
[Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/),
[Cloudflare request headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/),
[Docker networking](https://docs.docker.com/engine/network/),
[uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/),
and [Vite environment variables](https://vite.dev/guide/env-and-mode).
