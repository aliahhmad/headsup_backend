"""Exercise explicit worker startup and offline execution controls."""

from pathlib import Path

import pytest
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from app.api_admin import app
from app.commands import worker


def test_worker_once_uses_live_provider(
    runner: CliRunner,
    mocker: MockerFixture,
) -> None:
    """The live CLI selects a single queue check without a fixture."""
    run = mocker.patch(
        "app.commands.worker.run_worker", new_callable=mocker.AsyncMock
    )
    response = runner.invoke(app, ["worker", "--once"])
    assert response.exit_code == 0, response.output
    run.assert_awaited_once_with(None, once=True)


def test_fixture_is_validated_before_polling(
    runner: CliRunner,
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """An unsupported empty-source finding cannot claim a real queued run."""
    fixture = tmp_path / "invalid.json"
    fixture.write_text(
        '{"observations": [{"summary": "Unsupported", "sources": []}]}'
    )
    poll = mocker.patch(
        "app.commands.worker.poll", new_callable=mocker.AsyncMock
    )
    response = runner.invoke(
        app, ["worker", "--once", "--fixture", str(fixture)]
    )
    assert response.exit_code == 1
    assert "configuration is invalid" in response.output
    poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_waits_five_seconds(mocker: MockerFixture) -> None:
    """An empty queue waits five seconds instead of making a busy loop."""
    process = mocker.patch(
        "app.commands.worker.process_next",
        new_callable=mocker.AsyncMock,
        return_value=None,
    )
    sleep = mocker.patch(
        "app.commands.worker.asyncio.sleep",
        new_callable=mocker.AsyncMock,
        side_effect=KeyboardInterrupt,
    )
    with pytest.raises(KeyboardInterrupt):
        await worker.poll(mocker.AsyncMock(), mocker.AsyncMock(), once=False)
    process.assert_awaited_once()
    sleep.assert_awaited_once_with(5)
