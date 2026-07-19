"""Pydantic models shared by the workflow, UI, examples, and tests."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

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
    LLM = "llm"


class ReviewInput(BaseModel):
    """Validated input for one delivery review."""

    model_config = ConfigDict(str_strip_whitespace=True)

    task_title: str = Field(min_length=1)
    due_at: datetime
    submitted_at: datetime
    acceptance_criteria: list[str] = Field(min_length=1)
    submission_text: str = ""
    evidence_links: list[str] = Field(default_factory=list)

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


class CriterionResult(BaseModel):
    criterion: str
    status: CriterionStatus
    evidence: list[str] = Field(default_factory=list)
    reason: str
    suggested_action: str


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
