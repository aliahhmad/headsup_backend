"""Explicit worker process for queued research; no scheduler or alerts."""

import asyncio
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from pydantic import ValidationError
from rich import print as rprint
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import get_settings
from app.database.db import async_engine, async_session
from app.logs import logger
from app.managers.monitor_worker import Researcher, process_next
from app.managers.research import OpenAIResearch
from app.schemas.research import ResearchResult

app = typer.Typer()
POLL_SECONDS = 5


class FixtureResearch:
    """Read an explicitly supplied offline result before claiming any work."""

    def __init__(self, path: Path) -> None:
        """Validate the complete fixture, including nonempty source lists."""
        self.result = ResearchResult.model_validate_json(path.read_text())

    async def __call__(
        self, objective: str, context: dict[str, Any]
    ) -> ResearchResult:
        """Return fixture findings without external requests."""
        del objective, context
        return self.result


async def poll(researcher: Researcher, *, once: bool) -> None:
    """Check immediately, then sleep five seconds between queue checks."""
    while True:
        try:
            run_id = await process_next(async_session, researcher)
        except SQLAlchemyError as exc:
            logger.error("Worker database failure ({})", type(exc).__name__)
            if once:
                raise
        else:
            if run_id is not None:
                rprint(f"Processed run {run_id}; inspect its status/results.")
            elif once:
                rprint("No executable queued run found.")
        if once:
            return
        await asyncio.sleep(POLL_SECONDS)


async def run_worker(fixture: Path | None, *, once: bool) -> None:
    """Keep network resources in the worker's single event loop."""
    try:
        if fixture is not None:
            rprint(
                "Fixture mode: saves supplied test findings to the database."
            )
            await poll(FixtureResearch(fixture), once=once)
        else:
            settings = get_settings()
            if not settings.openai_api_key.get_secret_value().strip():
                message = "Set OPENAI_API_KEY before starting live research."
                raise ValueError(message)
            async with httpx.AsyncClient() as client:
                await poll(OpenAIResearch(client, settings), once=once)
    finally:
        await async_engine.dispose()


@app.callback(invoke_without_command=True)
def worker(
    *,
    once: Annotated[
        bool,
        typer.Option(
            help="Check the queue once, process at most one run, then exit."
        ),
    ] = False,
    fixture: Annotated[
        str | None,
        typer.Option(
            help="Save offline fixture findings instead of calling OpenAI.",
        ),
    ] = None,
) -> None:
    """Poll queued monitor runs every five seconds and save observations."""
    try:
        asyncio.run(
            run_worker(
                Path(fixture) if fixture is not None else None, once=once
            )
        )
    except (OSError, ValidationError, ValueError):
        rprint(
            "[red]Worker configuration is invalid. Check OPENAI_API_KEY "
            "or the research fixture against docs/monitor-worker.md."
        )
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        rprint("[red]Worker database error. Check connectivity and migrations.")
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        rprint("Worker stopped.")
