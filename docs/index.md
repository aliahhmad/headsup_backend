# HeadsUp API

**Stay informed without having to keep checking.**

HeadsUp is an AI-powered personal monitoring assistant concept: users describe
what matters to them, and the product aims to follow relevant sources, remember
what has happened, and surface meaningful changes with supporting sources.

## Current backend

This repository provides the FastAPI backend foundation, including:

- User registration, login, email verification, and password recovery
- JWT authentication, refresh tokens, and API keys
- User management and an optional administration panel
- PostgreSQL persistence and Alembic migrations
- Optional caching, rate limiting, and Prometheus metrics
- A local development email inbox powered by Mailpit

Topic monitors, scheduled collection, AI analysis, and change notifications are
planned features. They are not implemented by the authentication foundation.

## Local development

Follow the **HeadsUp local development** section of the repository README for
the current setup. The API docs run at <http://localhost:8000/docs>, and local
email is captured at <http://localhost:8025>.

Docker containers are named `headsup-db` and `headsup-mailpit`; full Docker mode
also runs `headsup-api`. Both modes retain the `headsup-api-local_postgres-data`
volume.

## About these guides

The detailed guides document infrastructure inherited from
[FastAPI Template](https://github.com/seapagan/fastapi-template). Some examples
still use the original template's names and URLs. Use the repository README
and `.env.example` for HeadsUp's current names and configuration.

Public author, contact email, website, and repository URL are intentionally
unconfigured. Original template copyright notices remain in the MIT license.
