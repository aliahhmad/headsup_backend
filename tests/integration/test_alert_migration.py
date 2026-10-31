"""Exercise real migration SQL and legacy backfill in a rollback-only schema."""

# SQL fixtures keep column lists readable.
# ruff: noqa: E501, PLR2004

from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def migrate_and_backfill(connection) -> None:
    """Build the historical schema, preserve an alert, and guard data loss."""
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    context = MigrationContext.configure(connection)
    digest = scripts.get_revision("add_alert_digests").module
    with Operations.context(context):
        for revision in reversed(
            list(scripts.walk_revisions("base", "add_alerts"))
        ):
            revision.module.upgrade()
        connection.execute(
            text("""INSERT INTO users
            (id, email, password, first_name, last_name, banned, verified)
            VALUES (1, 'migration@example.test', 'unused', 'Migration', 'Owner', false, true)""")
        )
        monitor, run, observation, alert = (uuid4() for _ in range(4))
        values = {
            "monitor": monitor,
            "run": run,
            "observation": observation,
            "alert": alert,
        }
        connection.execute(
            text("""INSERT INTO monitors
            (id, user_id, name, objective, status, version, check_interval_seconds, next_run_at)
            VALUES (:monitor, 1, 'Migration', 'Track developments', 'active', 1, 86400, now())"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO monitor_runs
            (id, monitor_id, status, trigger, idempotency_key, monitor_version, objective_snapshot, context_snapshot, attempt_count)
            VALUES (:run, :monitor, 'succeeded', 'manual', 'legacy', 1, 'Objective', '{}', 1)"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO observations
            (id, monitor_run_id, item_key, summary) VALUES (:observation, :run, 'legacy', 'Finding')"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO alerts
            (id, monitor_id, monitor_run_id, observation_id, title, body, deduplication_key)
            VALUES (:alert, :monitor, :run, :observation, 'Original title', 'Original body', 'legacy-event')"""),
            values,
        )
        digest.upgrade()
        assert (
            connection.scalar(text("SELECT title FROM alerts"))
            == "Original title"
        )
        assert (
            connection.scalar(
                text("SELECT observation_id FROM alert_observations")
            )
            == observation
        )
        assert (
            connection.scalar(
                text("SELECT development_key FROM alert_observations")
            )
            == "legacy-event"
        )
        digest.downgrade()
        assert (
            connection.scalar(text("SELECT observation_id FROM alerts"))
            == observation
        )
        assert (
            connection.scalar(text("SELECT deduplication_key FROM alerts"))
            == "legacy-event"
        )
        digest.upgrade()
        other = uuid4()
        values["other"] = other
        connection.execute(
            text("""INSERT INTO observations
            (id, monitor_run_id, item_key, summary) VALUES (:other, :run, 'second', 'Second finding')"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO alert_observations
            (id, alert_id, monitor_id, monitor_run_id, observation_id, development_key)
            VALUES (gen_random_uuid(), :alert, :monitor, :run, :other, 'second-event')"""),
            values,
        )
        savepoint = connection.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="cannot be downgraded"):
                digest.downgrade()
        finally:
            savepoint.rollback()
        assert (
            connection.scalar(text("SELECT count(*) FROM alert_observations"))
            == 2
        )


async def test_digest_migration_preserves_legacy_alert(test_engine) -> None:
    """Check migration SQL in an isolated, rollback-only schema."""
    schema = f"alert_migration_{uuid4().hex}"
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(
                text(f'SET LOCAL search_path TO "{schema}"')
            )
            await connection.run_sync(migrate_and_backfill)
        finally:
            await transaction.rollback()
