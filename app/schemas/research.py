"""Validated research outputs, independent of database and provider objects."""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StrictBool,
    model_validator,
)


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


class AlertDecision(BaseModel):
    """A decision about one supplied observation, before publication."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    observation_ref: str = Field(min_length=1)
    should_alert: StrictBool
    what_changed: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    development_key: str | None

    @model_validator(mode="after")
    def validate_development(self) -> AlertDecision:
        """Only positive decisions identify a publishable development."""
        if self.should_alert:
            if not self.development_key:
                msg = "Positive decisions need a development key"
                raise ValueError(msg)
        elif self.development_key is not None:
            msg = "Negative decisions need a null development key"
            raise ValueError(msg)
        return self


class KnowledgeUpdate(BaseModel):
    """Complete replacement knowledge, never a delta or configuration edit."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=20000)
    change_reason: str = Field(min_length=1, max_length=1000)
    # Old offline fixtures explicitly produce no candidates unless supplied.
    alert_decisions: list[AlertDecision] = Field(default_factory=list)


class KnowledgeAssessment(KnowledgeUpdate):
    """Live responses must explicitly include decisions, even when empty."""

    alert_decisions: list[AlertDecision]


class AlertComposition(BaseModel):
    """One final digest or an explicit decision to remain quiet."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    should_alert: StrictBool
    reason: str = Field(min_length=1)
    title: str | None
    body: str | None
    observation_refs: list[str]

    @model_validator(mode="after")
    def validate_publication(self) -> AlertComposition:
        """Require supported content only for positive publication decisions."""
        if self.should_alert:
            if not self.title or not self.body or not self.observation_refs:
                msg = "A digest needs content and observations"
                raise ValueError(msg)
        elif (
            self.title is not None
            or self.body is not None
            or self.observation_refs
        ):
            msg = "A negative decision cannot contain an alert"
            raise ValueError(msg)
        if len(set(self.observation_refs)) != len(self.observation_refs):
            msg = "Duplicate observation references"
            raise ValueError(msg)
        return self


class ResearchFixture(ResearchResult):
    """Offline evidence and an explicit, already-merged knowledge snapshot."""

    knowledge_update: KnowledgeUpdate | None
    alert_composition: AlertComposition | None = None

    @model_validator(mode="after")
    def validate_knowledge_update(self) -> ResearchFixture:
        """Reject incomplete fixtures before a worker claims database work."""
        if bool(self.observations) != (self.knowledge_update is not None):
            message = "Supply knowledge_update exactly when findings exist"
            raise ValueError(message)
        positive = bool(
            self.knowledge_update
            and any(
                d.should_alert for d in self.knowledge_update.alert_decisions
            )
        )
        if positive != (self.alert_composition is not None):
            msg = "Supply alert_composition exactly for true decisions"
            raise ValueError(msg)
        if self.knowledge_update is not None:
            validate_decision_refs(self.knowledge_update, self)
        if (
            self.alert_composition is not None
            and self.knowledge_update is not None
        ):
            allowed = {
                decision.observation_ref
                for decision in self.knowledge_update.alert_decisions
                if decision.should_alert
            }
            if not set(self.alert_composition.observation_refs).issubset(
                allowed
            ):
                message = "Composition references must identify true decisions"
                raise ValueError(message)
        return self


def referenced_observations(result: ResearchResult) -> list[dict[str, object]]:
    """Assign request-local references before persistence."""
    return [
        {
            "observation_ref": f"finding_{index}",
            **finding.model_dump(mode="json"),
        }
        for index, finding in enumerate(result.observations, start=1)
    ]


def validate_decision_refs(
    update: KnowledgeUpdate, result: ResearchResult
) -> None:
    """Reject invented or repeated decisions before committing research."""
    allowed = {
        f"finding_{index}" for index in range(1, len(result.observations) + 1)
    }
    refs = [decision.observation_ref for decision in update.alert_decisions]
    if len(set(refs)) != len(refs) or not set(refs).issubset(allowed):
        msg = "Invalid alert decision references"
        raise ValueError(msg)


class ExtractedFinding(BaseModel):
    """The extraction model must reference backend-assigned citation IDs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=20000)
    source_ids: list[str] = Field(min_length=1, max_length=20)


class Extraction(BaseModel):
    """Structured response schema sent to OpenAI for consolidation."""

    model_config = ConfigDict(extra="forbid")

    observations: list[ExtractedFinding] = Field(max_length=50)
