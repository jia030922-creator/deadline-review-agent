"""Optional OpenAI Responses API semantic review with conservative routing."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.prompts import SEMANTIC_EVALUATION_SYSTEM_PROMPT, SEMANTIC_EVALUATION_USER_TEMPLATE
from agent.schemas import (
    CriterionEvidenceType,
    CriterionResult,
    CriterionStatus,
    FileEvidence,
    FileParseStatus,
    LLMCriterionEvaluation,
    RequestedEvaluationMode,
    SafeLLMEvaluationResult,
)


MAX_LLM_EVIDENCE_CHARS = 6_000
MAX_EXCERPTS_PER_FILE = 2
EXCERPT_RADIUS = 500
MIN_READABLE_EVIDENCE_CHARS = 40
LLM_ACCEPTANCE_CONFIDENCE = 0.70
_EXTERNAL_FACT_MARKERS = (
    "最新", "实时", "市场排名", "行业第一", "网上", "网页内容", "链接内容",
    "github 仓库内容", "外部事实", "current market", "latest", "online",
)
_STOPWORDS = {
    "必须", "需要", "应该", "应当", "内容", "项目", "描述", "文件", "文档",
    "清楚", "说明", "具有", "是否", "以及", "the", "and", "with", "should",
    "must", "clearly", "describe",
}


def is_llm_available(api_key: str | None = None, model: str | None = None) -> bool:
    """Check configuration only; never performs a startup network request."""

    resolved_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
    resolved_model = model if model is not None else os.getenv("OPENAI_MODEL", "")
    return bool(resolved_key.strip() and resolved_model.strip())


def configured_model(model: str | None = None) -> str | None:
    value = model if model is not None else os.getenv("OPENAI_MODEL", "")
    return value.strip() or None


def _criterion_terms(criterion: str) -> list[str]:
    english = re.findall(r"[A-Za-z0-9_][A-Za-z0-9_ -]{1,30}", criterion)
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,8}", criterion)
    common_concepts = [
        concept for concept in ("问题", "解决方案", "结果", "修改建议", "可执行", "创新", "商业价值", "专业")
        if concept in criterion
    ]
    terms = [item.strip().casefold() for item in [*common_concepts, *english, *chinese_chunks]]
    return list(dict.fromkeys(term for term in terms if term and term not in _STOPWORDS))[:12]


def select_relevant_evidence(
    criterion: str,
    files: list[FileEvidence],
    *,
    max_chars: int = MAX_LLM_EVIDENCE_CHARS,
) -> str:
    """Select bounded, named excerpts without embeddings or whole-document uploads."""

    terms = _criterion_terms(criterion)
    blocks: list[str] = []
    used = 0
    for file in files:
        if file.parse_status not in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL}:
            continue
        text = file.extracted_text
        if not text:
            continue
        positions = sorted(
            {
                index
                for term in terms
                if (index := text.casefold().find(term)) >= 0
            }
        )
        excerpts: list[str] = []
        for index in positions[:MAX_EXCERPTS_PER_FILE]:
            start = max(0, index - EXCERPT_RADIUS)
            end = min(len(text), index + EXCERPT_RADIUS)
            excerpts.append(text[start:end].strip())
        if not excerpts:
            excerpts.append(text[: min(800, len(text))].strip())
        body = "\n--- 片段 ---\n".join(excerpt for excerpt in excerpts if excerpt)
        block = (
            f"[文件：{file.filename} | 状态：{file.parse_status.value} | "
            f"原提取文本是否截断：{'是' if file.text_truncated else '否'}]\n{body}"
        )
        remaining = max_chars - used
        if remaining <= 0:
            break
        block = block[:remaining]
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)[:max_chars]


def build_file_metadata_summary(files: list[FileEvidence], max_chars: int = 1_500) -> str:
    if not files:
        return "未提供文件。"
    lines = [
        (
            f"{file.filename}: 类型={file.extension or '未知'}, 状态={file.parse_status.value}, "
            f"页数={file.page_count if file.page_count is not None else '未知'}, "
            f"文本截断={'是' if file.text_truncated else '否'}"
        )
        for file in files
    ]
    return "\n".join(lines)[:max_chars]


def should_use_llm_for_criterion(
    criterion: str,
    criterion_type: CriterionEvidenceType,
    current_rule_result: CriterionResult,
    uploaded_files: list[FileEvidence],
    requested_evaluation_mode: RequestedEvaluationMode,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> bool:
    if requested_evaluation_mode == RequestedEvaluationMode.RULE_ONLY:
        return False
    if not is_llm_available(api_key, model):
        return False
    if current_rule_result.status != CriterionStatus.NEEDS_REVIEW:
        return False
    if criterion_type not in {
        CriterionEvidenceType.MANUAL_REVIEW,
        CriterionEvidenceType.FILE_CONTENT,
    }:
        return False
    if any(marker in criterion.casefold() for marker in _EXTERNAL_FACT_MARKERS):
        return False
    readable_chars = sum(
        len(file.extracted_text)
        for file in uploaded_files
        if file.parse_status in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL}
    )
    return readable_chars >= MIN_READABLE_EVIDENCE_CHARS


def _validate_parsed_response(parsed: Any) -> LLMCriterionEvaluation:
    if isinstance(parsed, LLMCriterionEvaluation):
        return LLMCriterionEvaluation.model_validate(parsed.model_dump())
    if isinstance(parsed, BaseModel):
        return LLMCriterionEvaluation.model_validate(parsed.model_dump())
    if isinstance(parsed, str):
        return LLMCriterionEvaluation.model_validate_json(parsed)
    return LLMCriterionEvaluation.model_validate(parsed)


def evaluate_criterion_with_llm(
    *,
    criterion: str,
    submission_text: str,
    relevant_file_excerpts: str,
    file_metadata_summary: str,
    deterministic_findings: str,
    model: str,
    api_key: str,
    client: Any | None = None,
    timeout_seconds: float = 20.0,
    max_retries: int = 1,
) -> LLMCriterionEvaluation:
    """Call Responses API Structured Outputs and validate the parsed payload again."""

    if client is None:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
    prompt = SEMANTIC_EVALUATION_USER_TEMPLATE.format(
        criterion=criterion,
        submission_text=submission_text[:2_000] or "未提供。",
        relevant_file_excerpts=relevant_file_excerpts or "未提供可读文件证据。",
        file_metadata_summary=file_metadata_summary,
        deterministic_findings=deterministic_findings,
    )
    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": SEMANTIC_EVALUATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        text_format=LLMCriterionEvaluation,
        store=False,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        output_text = getattr(response, "output_text", "")
        if output_text:
            parsed = output_text
    if parsed is None or parsed == "":
        raise ValueError("empty_or_refused_response")
    return _validate_parsed_response(parsed)


def _classify_error(exc: Exception) -> tuple[str, str]:
    name = exc.__class__.__name__
    if isinstance(exc, (TimeoutError,)) or name in {"APITimeoutError", "ReadTimeout", "ConnectTimeout"}:
        return "timeout", "LLM 调用超时，已保留规则结果。"
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return "authentication_error", "LLM 认证失败，已保留规则结果。"
    if name == "RateLimitError":
        return "rate_limit", "LLM 请求受到限流，已保留规则结果。"
    if name in {"APIConnectionError", "ConnectError", "NetworkError"}:
        return "network_error", "LLM 网络连接失败，已保留规则结果。"
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return "schema_validation_error", "LLM 返回内容未通过结构化校验，已保留规则结果。"
    if str(exc) == "empty_or_refused_response":
        return "empty_or_refused_response", "LLM 未返回可用的结构化结果，已保留规则结果。"
    return "api_error", "LLM 调用失败，已安全回退到规则结果。"


def safe_llm_evaluate(
    *,
    criterion: str,
    submission_text: str,
    relevant_file_excerpts: str,
    file_metadata_summary: str,
    deterministic_findings: str,
    api_key: str | None = None,
    model: str | None = None,
    client: Any | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> SafeLLMEvaluationResult:
    """Never raise API or schema failures into the review workflow."""

    started = time.perf_counter()
    resolved_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
    resolved_model = configured_model(model)
    if not resolved_key.strip():
        return SafeLLMEvaluationResult(
            success=False,
            error_type="missing_api_key",
            safe_error_message="未配置 OPENAI_API_KEY，已使用规则模式。",
            model=resolved_model,
        )
    if not resolved_model:
        return SafeLLMEvaluationResult(
            success=False,
            error_type="missing_model",
            safe_error_message="未配置 OPENAI_MODEL，已使用规则模式。",
        )
    try:
        resolved_timeout = timeout_seconds or float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
        resolved_retries = max_retries if max_retries is not None else int(os.getenv("OPENAI_MAX_RETRIES", "1"))
    except ValueError:
        return SafeLLMEvaluationResult(
            success=False,
            error_type="invalid_configuration",
            safe_error_message="LLM 超时或重试配置无效，已使用规则模式。",
            model=resolved_model,
        )
    try:
        result = evaluate_criterion_with_llm(
            criterion=criterion,
            submission_text=submission_text,
            relevant_file_excerpts=relevant_file_excerpts[:MAX_LLM_EVIDENCE_CHARS],
            file_metadata_summary=file_metadata_summary,
            deterministic_findings=deterministic_findings,
            model=resolved_model,
            api_key=resolved_key,
            client=client,
            timeout_seconds=resolved_timeout,
            max_retries=resolved_retries,
        )
        latency = int((time.perf_counter() - started) * 1_000)
        return SafeLLMEvaluationResult(success=True, result=result, latency_ms=latency, model=resolved_model)
    except Exception as exc:
        error_type, safe_message = _classify_error(exc)
        latency = int((time.perf_counter() - started) * 1_000)
        return SafeLLMEvaluationResult(
            success=False,
            error_type=error_type,
            safe_error_message=safe_message,
            latency_ms=latency,
            model=resolved_model,
        )
