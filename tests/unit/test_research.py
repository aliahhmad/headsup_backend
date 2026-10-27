"""Verify real Responses request payloads and citation-grounded extraction."""

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.config.settings import get_settings
from app.managers.research import OpenAIResearch, ResearchError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def research_response() -> dict[str, Any]:
    """A Responses envelope with citations separate from consulted sources."""
    return {
        "status": "completed",
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "type": "search",
                    "sources": [{"url": "https://example.com/unrelated"}],
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Company A opened applications.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.com/jobs",
                                "title": "Official listing",
                                "start_index": 0,
                                "end_index": 30,
                            }
                        ],
                    }
                ],
            },
        ],
    }


def extraction_response(source_id: str = "s1") -> dict[str, Any]:
    """Represent structured JSON as text inside a message, as the API does."""
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "observations": [
                                    {
                                        "summary": "Company A is hiring.",
                                        "source_ids": [source_id],
                                    }
                                ]
                            }
                        ),
                    }
                ],
            }
        ],
    }


async def test_requests_and_source_mapping() -> None:
    """HTTPX requests map cited IDs to backend-controlled URLs."""
    requests: list[dict[str, Any]] = []
    responses = [research_response(), extraction_response()]

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-placeholder"
        return httpx.Response(200, json=responses.pop(0))

    settings = get_settings().model_copy(
        update={
            "openai_api_key": SecretStr("test-placeholder"),
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        result = await OpenAIResearch(client, settings)(
            "Find internships", {"memories": []}
        )
    assert len(requests) == 2  # noqa: PLR2004
    assert requests[0]["tools"] == [{"type": "web_search"}]
    assert requests[0]["tool_choice"] == "required"
    assert requests[0]["store"] is False
    assert requests[1]["text"]["format"]["strict"] is True
    assert "unrelated" not in requests[1]["input"]
    section = json.loads(requests[1]["input"])["research_sections"][0]
    assert section["citations"][0]["source_id"] == "s1"
    assert section["text"] == "Company A opened applications."
    evidence = result.observations[0].sources[0]
    assert str(evidence.source_url) == "https://example.com/jobs"
    assert evidence.source_title == "Official listing"


@pytest.mark.parametrize(
    "problem",
    [
        "unknown_source",
        "empty_sources",
        "refusal",
        "incomplete",
        "http_error",
    ],
)
async def test_bad_provider_outputs_are_rejected(problem: str) -> None:
    """Invalid outputs cannot become finalized observations."""
    first = research_response()
    second = extraction_response()
    status = 200
    if problem == "unknown_source":
        second = extraction_response("invented-id")
    elif problem == "empty_sources":
        second["output"][0]["content"][0]["text"] = json.dumps(
            {"observations": [{"summary": "Unsupported", "source_ids": []}]}
        )
    elif problem == "refusal":
        first["output"][1]["content"] = [
            {"type": "refusal", "refusal": "Cannot answer"}
        ]
    elif problem == "incomplete":
        first["status"] = "incomplete"
    else:
        status = 429
    responses = [first, second]

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=responses.pop(0))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(ResearchError, match="openai_"):
            await OpenAIResearch(client, get_settings())("Find jobs", {})


async def test_no_citations_returns_no_findings() -> None:
    """Consulted results without supporting citations are not evidence."""
    response = research_response()
    response["output"][1]["content"][0]["annotations"] = []
    count = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        result = await OpenAIResearch(client, get_settings())("Find jobs", {})
    assert result.observations == []
    assert count == 1
