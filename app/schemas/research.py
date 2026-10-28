"""Validated research outputs, independent of database and provider objects."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class Evidence(BaseModel):
    """A cited URL; unsupported page metadata is intentionally absent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_url: HttpUrl
    source_title: str | None = None


class Finding(BaseModel):
    """One development with at least one supporting source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=20000)
    sources: list[Evidence] = Field(min_length=1, max_length=20)


class ResearchResult(BaseModel):
    """A successful check can return zero evidence-backed findings."""

    model_config = ConfigDict(extra="forbid")

    observations: list[Finding] = Field(max_length=50)


class KnowledgeUpdate(BaseModel):
    """Complete replacement knowledge, never a delta or configuration edit."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=20000)
    change_reason: str = Field(min_length=1, max_length=1000)


class ResearchFixture(ResearchResult):
    """Offline evidence and an explicit, already-merged knowledge snapshot."""

    knowledge_update: KnowledgeUpdate | None

    @model_validator(mode="after")
    def validate_knowledge_update(self) -> ResearchFixture:
        """Reject incomplete fixtures before a worker claims database work."""
        if bool(self.observations) != (self.knowledge_update is not None):
            message = "Supply knowledge_update exactly when findings exist"
            raise ValueError(message)
        return self


class ExtractedFinding(BaseModel):
    """The extraction model must reference backend-assigned citation IDs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=20000)
    source_ids: list[str] = Field(min_length=1, max_length=20)


class Extraction(BaseModel):
    """Structured response schema sent to OpenAI for consolidation."""

    model_config = ConfigDict(extra="forbid")

    observations: list[ExtractedFinding] = Field(max_length=50)
