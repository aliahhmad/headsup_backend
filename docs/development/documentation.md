# Maintaining documentation

The documentation site uses MkDocs with Material. Markdown sources live in `docs/`,
and `mkdocs.yml` defines navigation. Run commands from `api/` after installing the
locked development dependencies:

```sh
uv run --locked poe docs:serve
uv run --locked mkdocs build --strict
```

The local documentation server uses <http://127.0.0.1:9000>. The build writes static
files to ignored `site/`. Publishing is a separate operator action; building does
not publish or deploy anything.

## Sources of truth

- Feature guides describe current behavior verified against models, schemas,
  managers, providers, and routes.
- `.env.example` documents local defaults; `docker_support/deploy/server/.env.example`
  documents server settings. Keep real secrets in ignored environment files.
- The canonical server runbook is `docker_support/deploy/server/README.md`; the
  site's deployment page includes that file instead of maintaining a second copy.
- The live API serves `/docs`, `/redoc`, and `/openapi.json`. Static docs link to
  these runtime resources instead of bundling a potentially stale schema.
- Product concept and design documents live in the parent `docs/` directory.
  Distinguish accepted design proposals from implemented behavior.

For a standalone schema export, when needed:

```sh
uv run --locked api-admin docs openapi --prefix=docs/reference
```

Exporting imports application configuration and needs valid backend settings.
It is optional for the static documentation build; schema generation is not
implicitly run by MkDocs or the build task.

## Editing guidance

Add retained guides to navigation and check relative links. Keep commands scoped
to the intended environment: host development, local Docker, or server deployment.
Use generic domains and synthetic examples, preserve license notices, and avoid
copying credentials, request traces, or private data into examples. Mark future
work explicitly and update conflicting status claims when a feature ships.

MkDocs renders Mermaid diagrams through the configured fenced-code integration.
Alert email previews in `docs/examples/` are synthetic artifacts, not sent messages.
