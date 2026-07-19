"""Pydantic models shared by the workflow, UI, examples, and tests."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskStatus(str, Enum):
    ON_TIME = "ON_TIME"
    LATE = "LATE"


class CriterionStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class FinalDecision(str, Enum):
    PASS = "PASS"
    LATE_PASS = "LATE_PASS"
    NEEDS_REVISION = "NEEDS_REVISION"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class EvaluationMode(str, Enum):
    RULE_BASED = "rule_based"
    LLM_ENHANCED = "llm_enhanced"
    FALLBACK_RULE_BASED = "fallback_rule_based"
    LLM = "llm"  # Backward-compatible legacy value; workflow does not emit it.


class RequestedEvaluationMode(str, Enum):
    AUTO = "auto"
    RULE_ONLY = "rule_only"
    LLM_ENABLED = "llm_enabled"


class EvaluatedBy(str, Enum):
    DETERMINISTIC_RULE = "deterministic_rule"
    LLM_SEMANTIC_REVIEW = "llm_semantic_review"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class FileParseStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


class CriterionEvidenceType(str, Enum):
    FILE_FORMAT = "FILE_FORMAT"
    FILE_PAGE_COUNT = "FILE_PAGE_COUNT"
    FILE_CONTENT = "FILE_CONTENT"
    JSON_VALIDITY = "JSON_VALIDITY"
    LINK_PRESENCE = "LINK_PRESENCE"
    SUBMISSION_STATEMENT = "SUBMISSION_STATEMENT"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class EvidenceSource(str, Enum):
    FILE_METADATA = "FILE_METADATA"
    FILE_CONTENT = "FILE_CONTENT"
    SUBMISSION_TEXT = "SUBMISSION_TEXT"
    EVIDENCE_LINK = "EVIDENCE_LINK"
    MIXED = "MIXED"
    NONE = "NONE"


class FileEvidence(BaseModel):
    """Serializable evidence extracted from one uploaded file."""

    model_config = ConfigDict(str_strip_whitespace=True)

    filename: str
    extension: str
    mime_type: str | None = None
    size_bytes: int = Field(ge=0)
    page_count: int | None = Field(default=None, ge=0)
    extracted_text: str = ""
    parse_status: FileParseStatus
    parse_error: str | None = None
    text_truncated: bool = False

    @field_validator("extension")
    @classmethod
    def normalize_extension(cls, value: str) -> str:
        cleaned = value.strip().casefold()
        return f".{cleaned}" if cleaned and not cleaned.startswith(".") else cleaned


class ReviewInput(BaseModel):
    """Validated input for one delivery review."""

    model_config = ConfigDict(str_strip_whitespace=True)

    task_title: str = Field(min_length=1)
    due_at: datetime
    submitted_at: datetime
    acceptance_criteria: list[str] = Field(min_length=1)
    submission_text: str = ""
    evidence_links: list[str] = Field(default_factory=list)
    uploaded_files: list[FileEvidence] = Field(default_factory=list)
    requested_evaluation_mode: RequestedEvaluationMode = RequestedEvaluationMode.AUTO

    @field_validator("acceptance_criteria")
    @classmethod
    def require_a_real_criterion(cls, value: list[str]) -> list[str]:
        if not any(item.strip() for item in value):
            raise ValueError("acceptance_criteria 至少需要一条非空标准")
        return value

    @field_validator("evidence_links")
    @classmethod
    def clean_links(cls, value: list[str]) -> list[str]:
        return [link.strip() for link in value if link.strip()]


class ParsedCriterion(BaseModel):
    id: str
    order: int
    criterion: str


class DeadlineResult(BaseModel):
    task_status: TaskStatus
    late_minutes: int = Field(ge=0)


ShortLLMText = Annotated[str, Field(max_length=500)]


class LLMCriterionEvaluation(BaseModel):
    """Strict semantic-review payload returned by the model."""

    status: CriterionStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[ShortLLMText] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=1_500)
    suggested_action: str = Field(min_length=1, max_length=1_000)
    evidence_excerpt: str = Field(default="", max_length=2_000)
    limitations: list[ShortLLMText] = Field(default_factory=list, max_length=8)


class LLMCallMetadata(BaseModel):
    attempted: bool = False
    success: bool = False
    model: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    error_type: str | None = None
    safe_error_message: str | None = None
    fallback_used: bool = False


class SafeLLMEvaluationResult(BaseModel):
    success: bool
    result: LLMCriterionEvaluation | None = None
    error_type: str | None = None
    safe_error_message: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    model: str | None = None


class CriterionResult(BaseModel):
    criterion: str
    status: CriterionStatus
    evidence: list[str] = Field(default_factory=list)
    reason: str
    suggested_action: str
    evidence_type: CriterionEvidenceType = CriterionEvidenceType.MANUAL_REVIEW
    evidence_source: EvidenceSource = EvidenceSource.NONE
    source_files: list[str] = Field(default_factory=list)
    conflict_detected: bool = False
    evaluated_by: EvaluatedBy = EvaluatedBy.DETERMINISTIC_RULE
    rule_status_before_llm: CriterionStatus | None = None
    llm_metadata: LLMCallMetadata = Field(default_factory=LLMCallMetadata)
    llm_evaluation: LLMCriterionEvaluation | None = None


class IntermediateStep(BaseModel):
    step_name: str
    tool: str
    status: str
    summary: str


class ReviewOutput(BaseModel):
    task_status: TaskStatus
    final_decision: FinalDecision
    confidence: float = Field(ge=0.0, le=1.0)
    criteria_results: list[CriterionResult]
    next_actions: list[str]
    intermediate_steps: list[IntermediateStep]
    evaluation_mode: EvaluationMode = EvaluationMode.RULE_BASED
    requested_evaluation_mode: RequestedEvaluationMode = RequestedEvaluationMode.AUTO
    llm_available: bool = False
    llm_model: str | None = None
    llm_fallback_used: bool = False
    file_evidence_results: list[FileEvidence] = Field(default_factory=list)
    file_evidence_summary: str = "未上传文件证据。"
