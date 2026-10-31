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
        trace_migration = scripts.get_revision("add_run_model_calls").module
        trace_migration.upgrade()
        connection.execute(
            text("""INSERT INTO run_model_calls
            (id, monitor_run_id, stage, attempt_number, model, status, request_payload)
            VALUES (gen_random_uuid(), :run, 'research', 1, 'test-model', 'running', '{}')"""),
            values,
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM run_model_calls")) == 1
        )
        queue_migration = scripts.get_revision("add_queue_controls").module
        queue_migration.upgrade()
        assert (
            connection.scalar(
                text("SELECT automatic_enabled FROM queue_control")
            )
            is False
        )
        connection.execute(
            text(
                "INSERT INTO queue_executions (id, mode, status, monitor_run_id) VALUES (gen_random_uuid(), 'manual', 'pending', :run)"
            ),
            values,
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM queue_executions"))
            == 1
        )
        queue_migration.downgrade()
        trace_migration.downgrade()
        assert (
            connection.scalar(text("SELECT title FROM alerts"))
            == "Original title"
        )
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
