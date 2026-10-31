# Security policy

HeadsUp is under active development. There is no separate long-term support or
upstream-template version support policy. Use the revision maintained for your
installation and review application, dependency, migration, and infrastructure
changes before updating.

## Reporting a vulnerability

Report security issues privately to the maintainers of this HeadsUp repository.
Use the repository's private vulnerability reporting facility if enabled, or an
existing private maintainer contact. A dedicated public security email address is
not configured here. Do not send HeadsUp reports to the original template author.

Provide the affected revision, steps to reproduce, expected and actual behavior,
and a synthetic example when possible. Do not post credentials, tokens, private
monitor content, or exploit details in a public issue. Ordinary non-sensitive bugs
can use the repository's normal issue tracker.

## Operating an installation

Use separate secrets and databases per environment, preserve ownership checks,
keep the server database private, and follow the backup/update procedure. Run
schema migrations with the application revision they accompany. Private model
request traces and backups contain account data and require restricted access.
See the [deployment runbook](docker_support/deploy/server/README.md).

This policy does not constitute a security audit or a guarantee of production readiness.
