from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from pypdf import PdfWriter

from agent.file_tools import parse_pdf, parse_text_file
from agent.llm_evaluator import (
    MAX_LLM_EVIDENCE_CHARS,
    evaluate_criterion_with_llm,
    safe_llm_evaluate,
    select_relevant_evidence,
    should_use_llm_for_criterion,
)
from agent.schemas import (
    CriterionEvidenceType,
    CriterionResult,
    CriterionStatus,
    EvaluatedBy,
    EvaluationMode,
    FileEvidence,
    LLMCriterionEvaluation,
    RequestedEvaluationMode,
    ReviewInput,
)
from agent.workflow import DeadlineReviewAgent


NOW = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
MANUAL_CRITERION = "项目描述应清楚说明问题、解决方案和结果"


class FakeResponses:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(output_parsed=self.payload, output_text="")


class FakeClient:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.responses = FakeResponses(payload, error)


def valid_payload(**overrides):
    payload = {
        "status": "PASS",
        "confidence": 0.86,
        "evidence": ["项目材料分别说明了用户问题、解决方案和量化结果。"],
        "reason": "提供的文件片段形成了清晰的问题—方案—结果链路。",
        "suggested_action": "保留当前结构，并补充结果口径说明。",
        "evidence_excerpt": "问题：交付延迟；解决方案：自动提醒；结果：延迟下降 30%。",
        "limitations": ["仅依据提供的文件片段判断。"],
    }
    payload.update(overrides)
    return payload


def readable_file(text: str | None = None) -> FileEvidence:
    content = text or "问题是任务经常延迟。解决方案是增加自动提醒和截止时间检查。结果是延迟率下降 30%。"
    return parse_text_file("proposal.md", content.encode("utf-8"), "text/markdown")


def review_input(
    *,
    criterion: str = MANUAL_CRITERION,
    files: list[FileEvidence] | None = None,
    mode: RequestedEvaluationMode = RequestedEvaluationMode.AUTO,
    submission_text: str = "已完成项目描述。",
) -> ReviewInput:
    return ReviewInput(
        task_title="LLM 语义复核测试",
        due_at=NOW,
        submitted_at=NOW - timedelta(minutes=5),
        acceptance_criteria=[criterion],
        submission_text=submission_text,
        evidence_links=[],
        uploaded_files=files if files is not None else [readable_file()],
        requested_evaluation_mode=mode,
    )


def pdf_bytes(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def run_with_fake(tmp_path, payload=None, error=None, input_data: ReviewInput | None = None):
    client = FakeClient(valid_payload() if payload is None else payload, error)
    result = DeadlineReviewAgent(
        tmp_path / "logs",
        llm_client=client,
        api_key="test-api-key-not-real",
        llm_model="test-model",
    ).run(input_data or review_input())
    return result, client


def test_no_api_key_uses_rule_based(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    result = DeadlineReviewAgent(tmp_path / "logs").run(review_input())

    assert result.evaluation_mode == EvaluationMode.RULE_BASED
    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW


def test_available_llm_reviews_complex_needs_review(tmp_path) -> None:
    result, client = run_with_fake(tmp_path)

    assert len(client.responses.calls) == 1
    assert result.criteria_results[0].status == CriterionStatus.PASS
    assert result.criteria_results[0].evaluated_by == EvaluatedBy.LLM_SEMANTIC_REVIEW
    assert result.criteria_results[0].llm_evaluation is not None


def test_deterministic_pass_does_not_call_llm(tmp_path) -> None:
    data = review_input(criterion="必须是 PDF 格式", files=[parse_pdf("one.pdf", pdf_bytes(1))])
    result, client = run_with_fake(tmp_path, input_data=data)

    assert result.criteria_results[0].status == CriterionStatus.PASS
    assert not client.responses.calls


def test_deterministic_fail_does_not_call_llm(tmp_path) -> None:
    data = review_input(criterion="必须是 PDF 格式", files=[readable_file()])
    result, client = run_with_fake(tmp_path, input_data=data)

    assert result.criteria_results[0].status == CriterionStatus.FAIL
    assert not client.responses.calls


def test_two_page_pdf_fail_cannot_be_changed_by_llm(tmp_path) -> None:
    data = review_input(
        criterion="简历控制在一页",
        files=[parse_pdf("resume.pdf", pdf_bytes(2))],
        submission_text="简历已经控制在一页。",
    )
    result, client = run_with_fake(tmp_path, input_data=data)

    assert result.criteria_results[0].status == CriterionStatus.FAIL
    assert result.criteria_results[0].evaluated_by == EvaluatedBy.DETERMINISTIC_RULE
    assert not client.responses.calls


def test_valid_llm_payload_passes_pydantic_validation() -> None:
    parsed = LLMCriterionEvaluation.model_validate(valid_payload())

    assert parsed.status == CriterionStatus.PASS
    assert parsed.confidence == 0.86


def test_invalid_json_response_falls_back(tmp_path) -> None:
    result, _ = run_with_fake(tmp_path, payload="{invalid json")

    assert result.evaluation_mode == EvaluationMode.FALLBACK_RULE_BASED
    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW
    assert result.criteria_results[0].llm_metadata.error_type == "schema_validation_error"


def test_unknown_status_response_falls_back(tmp_path) -> None:
    result, _ = run_with_fake(tmp_path, payload=valid_payload(status="MAYBE"))

    assert result.evaluation_mode == EvaluationMode.FALLBACK_RULE_BASED
    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW


def test_timeout_falls_back(tmp_path) -> None:
    result, _ = run_with_fake(tmp_path, error=TimeoutError("private timeout detail"))

    assert result.evaluation_mode == EvaluationMode.FALLBACK_RULE_BASED
    assert result.criteria_results[0].llm_metadata.error_type == "timeout"
    assert "private timeout detail" not in (result.criteria_results[0].llm_metadata.safe_error_message or "")


def test_authentication_failure_falls_back(tmp_path) -> None:
    AuthenticationError = type("AuthenticationError", (Exception,), {})
    result, _ = run_with_fake(tmp_path, error=AuthenticationError("secret response"))

    assert result.criteria_results[0].llm_metadata.error_type == "authentication_error"
    assert result.llm_fallback_used is True


def test_empty_evidence_pass_is_not_accepted(tmp_path) -> None:
    result, _ = run_with_fake(tmp_path, payload=valid_payload(evidence=[]))

    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW
    assert "未达到接受门槛" in result.criteria_results[0].reason


def test_low_confidence_decision_stays_needs_review(tmp_path) -> None:
    result, _ = run_with_fake(tmp_path, payload=valid_payload(confidence=0.49))

    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW
    assert result.evaluation_mode == EvaluationMode.LLM_ENHANCED


def test_only_user_claim_without_actual_evidence_does_not_call_llm(tmp_path) -> None:
    data = review_input(files=[], submission_text="项目描述已经非常清楚。")
    result, client = run_with_fake(tmp_path, input_data=data)

    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW
    assert not client.responses.calls


def test_rule_only_keeps_complex_standard_for_review(tmp_path) -> None:
    data = review_input(mode=RequestedEvaluationMode.RULE_ONLY)
    result, client = run_with_fake(tmp_path, input_data=data)

    assert result.evaluation_mode == EvaluationMode.RULE_BASED
    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW
    assert not client.responses.calls


def test_llm_enabled_without_key_is_safe_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    data = review_input(mode=RequestedEvaluationMode.LLM_ENABLED)

    result = DeadlineReviewAgent(tmp_path / "logs").run(data)

    assert result.evaluation_mode == EvaluationMode.FALLBACK_RULE_BASED
    assert result.llm_available is False


def test_successful_review_sets_llm_enhanced_mode(tmp_path) -> None:
    result, _ = run_with_fake(tmp_path)

    assert result.evaluation_mode == EvaluationMode.LLM_ENHANCED
    assert result.llm_model == "test-model"


def test_failed_review_sets_fallback_mode(tmp_path) -> None:
    result, _ = run_with_fake(tmp_path, error=RuntimeError("server detail"))

    assert result.evaluation_mode == EvaluationMode.FALLBACK_RULE_BASED
    assert result.llm_fallback_used is True


def test_log_never_contains_api_key(tmp_path) -> None:
    result, _ = run_with_fake(tmp_path)
    log_path = next((tmp_path / "logs").glob("*.json"))
    logged = log_path.read_text(encoding="utf-8")

    assert result.final_decision.value == "PASS"
    assert "test-api-key-not-real" not in logged
    assert "OPENAI_API_KEY" not in logged


def test_selected_evidence_and_prompt_are_bounded() -> None:
    file = readable_file("问题与解决方案。" + "x" * 20_000)
    selected = select_relevant_evidence(MANUAL_CRITERION, [file])
    client = FakeClient(valid_payload())

    evaluate_criterion_with_llm(
        criterion=MANUAL_CRITERION,
        submission_text="声明" * 3_000,
        relevant_file_excerpts=selected,
        file_metadata_summary="proposal.md: SUCCESS",
        deterministic_findings="规则状态=NEEDS_REVIEW",
        model="test-model",
        api_key="test-key",
        client=client,
    )

    assert len(selected) <= MAX_LLM_EVIDENCE_CHARS
    call = client.responses.calls[0]
    assert call["store"] is False
    assert call["text_format"] is LLMCriterionEvaluation


def test_router_rejects_hard_rule_and_accepts_supported_manual_review() -> None:
    manual_result = CriterionResult(
        criterion=MANUAL_CRITERION,
        status=CriterionStatus.NEEDS_REVIEW,
        reason="需复核",
        suggested_action="复核",
    )

    assert should_use_llm_for_criterion(
        MANUAL_CRITERION,
        CriterionEvidenceType.MANUAL_REVIEW,
        manual_result,
        [readable_file()],
        RequestedEvaluationMode.AUTO,
        api_key="key",
        model="model",
    )
    assert not should_use_llm_for_criterion(
        "必须是 PDF",
        CriterionEvidenceType.FILE_FORMAT,
        manual_result,
        [readable_file()],
        RequestedEvaluationMode.AUTO,
        api_key="key",
        model="model",
    )


def test_llm_demo_input_routes_three_hard_rules_and_two_semantic_reviews(tmp_path) -> None:
    data = ReviewInput.model_validate_json(
        Path("examples/llm_review_input.json").read_text(encoding="utf-8")
    )
    result, client = run_with_fake(tmp_path, input_data=data)

    assert [item.status for item in result.criteria_results] == [CriterionStatus.PASS] * 5
    assert len(client.responses.calls) == 2
    assert [item.evaluated_by for item in result.criteria_results[:3]] == [
        EvaluatedBy.DETERMINISTIC_RULE,
        EvaluatedBy.DETERMINISTIC_RULE,
        EvaluatedBy.DETERMINISTIC_RULE,
    ]
