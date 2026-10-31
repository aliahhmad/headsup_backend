# HeadsUp API

Stay informed without having to keep checking.

HeadsUp turns a monitoring objective into sourced findings, accumulated knowledge,
and meaningful updates. This repository contains the FastAPI backend; the React
application lives in the neighboring `web/` repository.

## Start here

- [Local quick start](docs/quick-start.md): PostgreSQL, email inbox, API, and frontend.
- [Server deployment](docker_support/deploy/server/README.md): an isolated Docker
  stack behind an existing HTTPS tunnel or reverse proxy, including updates and backups.
- [Configuration](docs/usage/configuration/environment.md): local settings,
  SMTP, model access, and optional infrastructure.
- [Documentation index](docs/index.md): feature guides and administration.

## Available features

- Registration, verification, login, password recovery, profile management, and API keys.
- Account-owned monitors, lifecycle controls, queued checks, sourced observations,
  and versioned monitor knowledge.
- Alert decisions within knowledge updates, followed by an optional smaller-model
  composition request that publishes at most one alert per run. Empty, all-false,
  or already-published decisions skip composition.
- An Updates history with expandable alerts, a notification inbox for alerts and
  monitor activation/pause events, and opt-in alert emails controlled in the profile.
- Administrator user/signup management, automatic or single-step queue controls,
  and run inspection with evidence, memory snapshots, and model request traces.

The API starts the managed queue service. Automatic processing defaults to off in
new databases; saved settings survive restarts. A separate CLI worker is optional.
Recurring schedule enqueueing, conversational chat processing, push notifications,
and configurable model context budgets remain unimplemented. A monitor's cadence
setting does not yet schedule checks.

## Common local commands

Run these from this backend directory after completing the quick start:

```sh
docker compose -f compose.local.yml up -d --wait
uv run --locked alembic upgrade head
uv run --locked api-admin serve
```

Create a verified administrator:

```sh
uv run --locked api-admin user create --admin
```

Process one queue item explicitly, including when automatic mode is off:

```sh
uv run --locked api-admin worker --once
```

This can make paid model requests. For the synthetic offline option, see the
[worker guide](docs/monitor-worker.md#offline-dry-run).

Explore the running API at <http://localhost:8000/docs>. The local email inbox is
<http://localhost:8025>, and the frontend runs at <http://localhost:5173>.
Server deployments use the separate `docker_support/deploy/server/dc.sh` wrapper;
local Compose commands are not the server deployment procedure.

## Development and documentation

See [development](docs/development/local.md), [project structure](docs/project-organization.md),
and [contributing](CONTRIBUTING.md). Build the documentation with:

```sh
uv run --locked poe docs:build
uv run --locked poe docs:serve
```

## License and attribution

Licensed under the [MIT license](LICENSE.txt). HeadsUp's backend originated from
Grant Ramsay's [FastAPI Template](https://github.com/seapagan/fastapi-template).
The original copyright and license notices are retained.
