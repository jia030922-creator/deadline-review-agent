from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent import tools
from agent.schemas import CriterionStatus, FinalDecision, ReviewInput, TaskStatus
from agent.workflow import DeadlineReviewAgent


BASE_TIME = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)


def make_input(**overrides: object) -> ReviewInput:
    values: dict[str, object] = {
        "task_title": "Day 1 MVP",
        "due_at": BASE_TIME,
        "submitted_at": BASE_TIME - timedelta(minutes=10),
        "acceptance_criteria": ["README includes run instructions", "pytest suite is provided"],
        "submission_text": (
            "Completed: README includes run instructions. "
            "Implemented: pytest suite is provided."
        ),
        "evidence_links": ["https://example.com/repository"],
    }
    values.update(overrides)
    return ReviewInput.model_validate(values)


def test_on_time_all_pass_returns_pass(tmp_path) -> None:
    result = DeadlineReviewAgent(tmp_path / "logs").run(make_input())

    assert result.task_status == TaskStatus.ON_TIME
    assert result.final_decision == FinalDecision.PASS
    assert all(item.status == CriterionStatus.PASS for item in result.criteria_results)
    assert result.model_dump(mode="json")["evaluation_mode"] == "rule_based"


def test_late_all_pass_returns_late_pass(tmp_path) -> None:
    payload = make_input(submitted_at=BASE_TIME + timedelta(minutes=12))
    result = DeadlineReviewAgent(tmp_path / "logs").run(payload)

    assert result.task_status == TaskStatus.LATE
    assert result.final_decision == FinalDecision.LATE_PASS
    assert any("12 分钟" in action for action in result.next_actions)


def test_explicit_conflict_returns_needs_revision(tmp_path) -> None:
    payload = make_input(
        submission_text=(
            "Completed: README includes run instructions. "
            "The pytest suite is missing and was not completed."
        )
    )
    result = DeadlineReviewAgent(tmp_path / "logs").run(payload)

    assert result.criteria_results[1].status == CriterionStatus.FAIL
    assert result.final_decision == FinalDecision.NEEDS_REVISION


def test_missing_evidence_requires_review(tmp_path) -> None:
    payload = make_input(
        acceptance_criteria=["Performance report is attached"],
        submission_text="Work is ready.",
        evidence_links=[],
    )
    result = DeadlineReviewAgent(tmp_path / "logs").run(payload)

    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW
    assert result.final_decision == FinalDecision.NEEDS_REVIEW


@pytest.mark.parametrize("criteria", [[], ["  ", ""]])
def test_empty_or_blank_criteria_are_invalid(criteria) -> None:
    with pytest.raises(ValidationError):
        make_input(acceptance_criteria=criteria)


def test_parse_criteria_removes_blanks_and_exact_duplicates() -> None:
    parsed = tools.parse_criteria([" first ", "", "first", "second", "second "])

    assert [item.criterion for item in parsed] == ["first", "second"]
    assert [item.order for item in parsed] == [1, 2]


def test_log_failure_does_not_crash_workflow(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(tools, "log_result", lambda *args, **kwargs: None)

    result = DeadlineReviewAgent(tmp_path / "unwritable").run(make_input())

    assert result.final_decision == FinalDecision.PASS
    assert result.intermediate_steps[-1].step_name == "log_final_result"
    assert result.intermediate_steps[-1].status == "WARNING"


def test_empty_submission_is_a_severe_failure(tmp_path) -> None:
    result = DeadlineReviewAgent(tmp_path / "logs").run(
        make_input(submission_text="", evidence_links=[])
    )

    assert result.final_decision == FinalDecision.FAIL
    assert all(item.status == CriterionStatus.NEEDS_REVIEW for item in result.criteria_results)
