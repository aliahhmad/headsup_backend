"""Validate alert provenance, deduplication, and deletion in PostgreSQL."""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monitor import (
    Alert,
    AlertObservation,
    Monitor,
    MonitorRun,
    Observation,
    ObservationSource,
    utc_now,
)
from app.models.user import User

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def observations(test_db: AsyncSession) -> list[Observation]:
    """Create two runs of one monitor and a run owned by another user."""
    findings = []
    monitor = None
    for index in range(3):
        if index != 1:
            owner = User(
                email=f"{uuid4().hex}@example.test",
                password="unused-alert-test-password",  # noqa: S106
                first_name="Alert",
                last_name="Owner",
                verified=True,
            )
            test_db.add(owner)
            await test_db.flush()
            monitor = Monitor(
                user_id=owner.id,
                name="Internships",
                objective="Track application deadlines",
                next_run_at=utc_now(),
            )
            test_db.add(monitor)
            await test_db.flush()
        assert monitor is not None
        run = MonitorRun(
            monitor_id=monitor.id,
            status="succeeded",
            trigger="manual",
            idempotency_key=uuid4().hex,
            monitor_version=1,
            objective_snapshot=monitor.objective,
            context_snapshot={},
        )
        test_db.add(run)
        await test_db.flush()
        finding = Observation(
            monitor_run_id=run.id,
            item_key="deadline-changed",
            summary="The application deadline changed.",
        )
        test_db.add(finding)
        await test_db.flush()
        test_db.add(
            ObservationSource(
                observation_id=finding.id,
                source_key="official-deadline",
                source_url="https://example.com/applications",
                retrieved_at=utc_now(),
            )
        )
        findings.append(finding)
    await test_db.flush()
    return findings


async def publish(
    session: AsyncSession, finding: Observation, key: str
) -> Alert:
    """Persist a digest and one valid provenance link."""
    run = await session.get(MonitorRun, finding.monitor_run_id)
    alert = Alert(
        monitor_id=run.monitor_id,
        monitor_run_id=run.id,
        title="Deadline changed",
        body="Review the official deadline.",
    )
    session.add(alert)
    await session.flush()
    session.add(
        AlertObservation(
            alert_id=alert.id,
            monitor_id=run.monitor_id,
            monitor_run_id=run.id,
            observation_id=finding.id,
            development_key=key,
        )
    )
    await session.flush()
    return alert


async def test_digest_state_and_independent_monitor_scope(
    test_db, observations
) -> None:
    """Independent owners can receive the same event and update read state."""
    alert = await publish(test_db, observations[0], "event-a")
    await publish(test_db, observations[2], "event-a")
    assert alert.read_at is None
    assert alert.dismissed_at is None
    alert.read_at = utc_now()
    alert.dismissed_at = utc_now()
    await test_db.flush()
    await test_db.refresh(alert)
    assert alert.read_at is not None
    assert alert.dismissed_at is not None


@pytest.mark.parametrize("duplicate", ["run", "development"])
async def test_duplicate_publication_is_rejected(
    test_db, observations, duplicate
) -> None:
    """Both run publication and cross-run development identity are unique."""
    original = await publish(test_db, observations[0], "event-a")
    with pytest.raises(IntegrityError):
        await duplicate_publication(test_db, observations, original, duplicate)


async def duplicate_publication(
    session, observations, original, duplicate
) -> None:
    """Keep expected failures isolated from the fixture transaction."""
    async with session.begin_nested():
        if duplicate == "run":
            session.add(
                Alert(
                    monitor_id=original.monitor_id,
                    monitor_run_id=original.monitor_run_id,
                    title="Again",
                    body="Repeated",
                )
            )
            await session.flush()
        else:
            await publish(session, observations[1], "event-a")


@pytest.mark.parametrize("mismatch", ["monitor", "run"])
async def test_link_provenance_is_rejected(
    test_db, observations, mismatch
) -> None:
    """A digest cannot cite another run's or another user's evidence."""
    alert = await publish(test_db, observations[0], "event-a")
    link = AlertObservation(
        alert_id=alert.id,
        monitor_id=alert.monitor_id,
        monitor_run_id=alert.monitor_run_id,
        observation_id=observations[1].id,
        development_key="event-b",
    )
    if mismatch == "monitor":
        other = await publish(test_db, observations[2], "event-c")
        link.monitor_id = other.monitor_id
    with pytest.raises(IntegrityError):
        await insert_link(test_db, link)


async def insert_link(session, link) -> None:
    """Flush a bad link inside a recoverable savepoint."""
    async with session.begin_nested():
        session.add(link)
        await session.flush()


@pytest.mark.parametrize("field", ["title", "body"])
@pytest.mark.parametrize("blank", ["", " \t\n"])
async def test_blank_digest_content_is_rejected(
    test_db, observations, field, blank
) -> None:
    """Published messages must contain displayable content."""
    run = await test_db.get(MonitorRun, observations[0].monitor_run_id)
    alert = Alert(
        monitor_id=run.monitor_id,
        monitor_run_id=run.id,
        title="Title",
        body="Body",
    )
    setattr(alert, field, blank)
    with pytest.raises(IntegrityError):
        await insert_link(test_db, alert)


async def test_monitor_purge_cascades_only_owned_alerts(
    test_db, observations
) -> None:
    """Purging one monitor leaves another owner's alert and ledger intact."""
    alert = await publish(test_db, observations[0], "event-a")
    other = await publish(test_db, observations[2], "event-a")
    await test_db.execute(delete(Monitor).where(Monitor.id == alert.monitor_id))
    assert list(await test_db.scalars(select(Alert.id))) == [other.id]
    assert list(await test_db.scalars(select(AlertObservation.alert_id))) == [
        other.id
    ]
