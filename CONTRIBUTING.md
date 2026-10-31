# Contributing to HeadsUp

Start with the [quick start](docs/quick-start.md) and
[development guide](docs/development/local.md). Backend and frontend live in
separate repositories; make changes and review diffs in the repository concerned.

Keep changes focused on the intended behavior, preserve ownership checks and
transaction boundaries, and add meaningful tests when behavior changes. Use a
separate test database and synthetic fixtures. Keep credentials, personal request
traces, and production data out of commits.

Run the relevant tests/builds for a change. Backend quality and documentation tasks
are defined in `pyproject.toml`; frontend commands are in `package.json`. The
application test suite runs locally, while the existing workflows cover code quality
and security checks. Do not add successful-check or coverage claims without running
the checks concerned.

Update affected feature guides and navigation when behavior changes. Distinguish
implemented functionality from proposals and use generic example hosts. See
[documentation maintenance](docs/development/documentation.md).

Pull requests should describe the resulting behavior and relevant validation.
Report security issues privately to this repository's maintainers as described in
[the security policy](SECURITY.md). Follow the [code of conduct](CODE_OF_CONDUCT.md).
