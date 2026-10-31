"""Verify real Responses request payloads and citation-grounded extraction."""

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config.settings import get_settings
from app.managers.research import OpenAIResearch, ResearchError
from app.schemas.research import (
    KnowledgeUpdate,
    ResearchFixture,
    ResearchResult,
)


@pytest.mark.parametrize(
    "problem",
    ["missing_decisions", "string_boolean", "unknown_ref", "duplicate_ref"],
)
async def test_alert_decisions_are_strictly_validated(problem: str) -> None:
    """Invalid decisions cannot silently become an all-false response."""
    decision = {
        "observation_ref": "finding_1",
        "should_alert": False,
        "what_changed": "No material change",
        "why_it_matters": "Background only",
        "development_key": None,
    }
    payload = {
        "content": "Knowledge",
        "change_reason": "Reason",
        "alert_decisions": [decision],
    }
    if problem == "missing_decisions":
        del payload["alert_decisions"]
    elif problem == "string_boolean":
        decision["should_alert"] = "false"
    elif problem == "unknown_ref":
        decision["observation_ref"] = "invented"
    else:
        payload["alert_decisions"].append(decision.copy())
    response = extraction_response()
    response["output"][0]["content"][0]["text"] = json.dumps(payload)

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    finding = ResearchResult.model_validate(
        {
            "observations": [
                {
                    "summary": "Finding",
                    "sources": [{"source_url": "https://example.com"}],
                }
            ]
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(
            ResearchError, match="openai_knowledge_invalid_response"
        ):
            await OpenAIResearch(client, get_settings()).merge_knowledge(
                "Objective", "Old", finding
            )


@pytest.mark.parametrize("positive", [True, False])
async def test_composition_payload_uses_separate_model(
    *, positive: bool
) -> None:
    """Compose supplied candidates using a separate model without tools."""
    requests = []
    payload = {
        "should_alert": positive,
        "reason": "Selection reason",
        "title": "Update" if positive else None,
        "body": "Message" if positive else None,
        "observation_refs": ["finding_1"] if positive else [],
    }
    response = extraction_response()
    response["output"][0]["content"][0]["text"] = json.dumps(payload)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    settings = get_settings().model_copy(
        update={"openai_alert_model": "test-composer"}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        result = await OpenAIResearch(client, settings).compose_alert(
            "Objective",
            [{"observation_ref": "finding_1", "summary": "Finding"}],
            {
                "memories": [
                    {"kind": "monitor_context", "content": "Accepted context"}
                ]
            },
        )
    assert result.should_alert is positive
    assert requests[0]["model"] == "test-composer"
    assert requests[0]["store"] is False
    assert "tools" not in requests[0]
    assert json.loads(requests[0]["input"])["monitor_context"] == [
        "Accepted context"
    ]
    assert requests[0]["text"]["format"]["strict"] is True


@pytest.mark.parametrize(
    "problem",
    [
        "unknown_ref",
        "duplicate_ref",
        "false_content",
        "blank_title",
        "string_boolean",
        "refusal",
        "incomplete",
        "http_error",
    ],
)
async def test_invalid_composition_is_rejected(problem: str) -> None:
    """Invalid composition is a retryable error, never a published alert."""
    payload = {
        "should_alert": True,
        "reason": "Reason",
        "title": "Title",
        "body": "Body",
        "observation_refs": ["finding_1"],
    }
    if problem == "unknown_ref":
        payload["observation_refs"] = ["invented"]
    elif problem == "duplicate_ref":
        payload["observation_refs"] *= 2
    elif problem == "false_content":
        payload["should_alert"] = False
    elif problem == "blank_title":
        payload["title"] = "   "
    elif problem == "string_boolean":
        payload["should_alert"] = "true"
    response = extraction_response()
    response["output"][0]["content"][0]["text"] = json.dumps(payload)
    if problem == "refusal":
        response["output"][0]["content"] = [{"type": "refusal"}]
    if problem == "incomplete":
        response["status"] = "incomplete"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429 if problem == "http_error" else 200, json=response
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(ResearchError, match="openai_"):
            await OpenAIResearch(client, get_settings()).compose_alert(
                "Objective", [{"observation_ref": "finding_1"}], {}
            )


async def test_empty_composition_makes_no_request() -> None:
    """Reject empty candidates without making a provider request."""

    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Empty candidates must not make a provider request")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(ValueError, match="No eligible"):
            await OpenAIResearch(client, get_settings()).compose_alert(
                "Objective", [], {}
            )


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


def knowledge_response(
    content: str = "Old finding. Company A is hiring.",
) -> dict[str, Any]:
    """A complete replacement snapshot in a Responses envelope."""
    response = extraction_response()
    response["output"][0]["content"][0]["text"] = json.dumps(
        {
            "content": content,
            "change_reason": "Added the supported Company A finding.",
            "alert_decisions": [],
        }
    )
    return response


async def test_knowledge_merge_payload_and_full_snapshot() -> None:
    """The merger sees previous knowledge and evidence, without other memory."""
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=knowledge_response())

    result = ResearchResult.model_validate(
        {
            "observations": [
                {
                    "summary": "Company A is hiring.",
                    "sources": [{"source_url": "https://example.com/jobs"}],
                }
            ]
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        update = await OpenAIResearch(client, get_settings()).merge_knowledge(
            "Find jobs", "Old finding.", result
        )
    assert update.content == "Old finding. Company A is hiring."
    payload = requests[0]
    assert json.loads(payload["input"]) == {
        "objective": "Find jobs",
        "previous_knowledge": "Old finding.",
        "observations": [
            {
                "observation_ref": "finding_1",
                **result.observations[0].model_dump(mode="json"),
            }
        ],
        "monitor_context": [],
        "history": {},
        "is_initial": False,
    }
    assert payload["store"] is False
    assert "tools" not in payload
    schema = payload["text"]["format"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == {
        "content",
        "change_reason",
        "alert_decisions",
    }


@pytest.mark.parametrize(
    "problem",
    [
        "blank",
        "oversized",
        "invalid_json",
        "extra_fields",
        "missing_reason",
        "refusal",
        "incomplete",
        "http_error",
    ],
)
async def test_invalid_knowledge_is_rejected(problem: str) -> None:
    """Bad merges fail safely without persisting arbitrary provider output."""
    response = knowledge_response()
    code = "openai_knowledge_invalid_response"
    status = 200
    if problem == "blank":
        response = knowledge_response("   ")
    elif problem == "oversized":
        response = knowledge_response("x" * 20001)
    elif problem == "invalid_json":
        response["output"][0]["content"][0]["text"] = "not json"
    elif problem in {"extra_fields", "missing_reason"}:
        data = {"content": "Valid content"}
        if problem == "extra_fields":
            data.update(change_reason="Reason", monitor_context="Forbidden")
        response["output"][0]["content"][0]["text"] = json.dumps(data)
    elif problem == "refusal":
        response["output"][0]["content"] = [{"type": "refusal"}]
        code = "openai_refusal"
    elif problem == "incomplete":
        response["status"] = "incomplete"
        code = "openai_incomplete"
    else:
        status = 429
        code = "openai_http_429"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=response)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(ResearchError, match=f"^{code}$"):
            await OpenAIResearch(client, get_settings()).merge_knowledge(
                "Find jobs", "Old knowledge", ResearchResult(observations=[])
            )


async def test_fixture_requires_explicit_knowledge_for_findings() -> None:
    """Offline runs cannot silently bypass the knowledge phase."""
    finding = {
        "summary": "Finding",
        "sources": [{"source_url": "https://example.com"}],
    }
    for payload in [
        {"observations": [finding]},
        {"observations": [finding], "knowledge_update": None},
        {
            "observations": [],
            "knowledge_update": KnowledgeUpdate(
                content="Unsupported", change_reason="No evidence"
            ),
        },
    ]:
        with pytest.raises(ValidationError):
            ResearchFixture.model_validate(payload)
    fixture = ResearchFixture(observations=[], knowledge_update=None)
    assert fixture.observations == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ReadTimeout, "openai_timeout"),
        (httpx.ConnectError, "openai_network_error"),
    ],
)
async def test_knowledge_transport_failure(
    error: type[httpx.HTTPError], code: str
) -> None:
    """Transport failures expose a safe code, without provider error text."""

    def handle(request: httpx.Request) -> httpx.Response:
        message = "sensitive provider content"
        raise error(message)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(ResearchError, match=f"^{code}$"):
            await OpenAIResearch(client, get_settings()).merge_knowledge(
                "Find jobs", "Old knowledge", ResearchResult(observations=[])
            )
