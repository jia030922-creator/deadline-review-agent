"""Deterministic tools used by the Deadline Review Agent."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from agent.schemas import (
    CriterionResult,
    CriterionStatus,
    DeadlineResult,
    FinalDecision,
    ParsedCriterion,
    TaskStatus,
)


_NEGATIVE_MARKERS = (
    "未完成", "没有完成", "尚未", "缺少", "遗漏", "不支持", "无法", "失败",
    "不符合", "未提供", "未实现", "not completed", "not implemented",
    "missing", "failed", "does not", "doesn't", "unsupported",
)
_POSITIVE_MARKERS = (
    "已完成", "已经完成", "已实现", "已提供", "已包含", "已通过", "符合",
    "支持", "完成了", "实现了", "提供了", "completed", "implemented",
    "provided", "included", "passed", "supports", "meets",
)
_GENERIC_WORDS = {
    "需要", "必须", "应该", "能够", "可以", "一个", "以及", "the", "and", "with",
    "must", "should", "have", "has", "this", "that", "into", "from",
}


def check_deadline(due_at: datetime, submitted_at: datetime) -> DeadlineResult:
    """Return time status and whole minutes late (rounded up)."""

    delta_seconds = (submitted_at - due_at).total_seconds()
    if delta_seconds <= 0:
        return DeadlineResult(task_status=TaskStatus.ON_TIME, late_minutes=0)
    late_minutes = int((delta_seconds + 59) // 60)
    return DeadlineResult(task_status=TaskStatus.LATE, late_minutes=late_minutes)


def parse_criteria(criteria: Iterable[str]) -> list[ParsedCriterion]:
    """Trim, remove exact duplicates, and preserve original order."""

    parsed: list[ParsedCriterion] = []
    seen: set[str] = set()
    for raw in criteria:
        cleaned = raw.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        parsed.append(
            ParsedCriterion(
                id=f"criterion_{len(parsed) + 1}",
                order=len(parsed) + 1,
                criterion=cleaned,
            )
        )
    return parsed


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text.casefold())


def _keywords(text: str) -> set[str]:
    english = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9_]{2,}", text)
        if token.casefold() not in _GENERIC_WORDS
    }
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    chinese: set[str] = set()
    for chunk in chinese_chunks:
        if chunk not in _GENERIC_WORDS:
            chinese.add(chunk)
        if len(chunk) >= 4:
            chinese.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return english | chinese


def _relevance(criterion: str, segment: str) -> float:
    normalized_criterion = _normalize(criterion)
    normalized_segment = _normalize(segment)
    if normalized_criterion and normalized_criterion in normalized_segment:
        return 1.0
    terms = _keywords(criterion)
    if not terms:
        return 0.0
    segment_normalized = segment.casefold()
    hits = sum(1 for term in terms if term.casefold() in segment_normalized)
    return hits / len(terms)


def evaluate_evidence_rule_based(
    criterion: ParsedCriterion | str,
    submission_text: str,
    evidence_links: list[str],
) -> CriterionResult:
    """Evaluate one criterion conservatively using inspectable text rules."""

    criterion_text = criterion.criterion if isinstance(criterion, ParsedCriterion) else criterion.strip()
    segments = [part.strip() for part in re.split(r"[\r\n。！？!?;；]+", submission_text) if part.strip()]
    relevant = [(segment, _relevance(criterion_text, segment)) for segment in segments]

    conflicts = [
        segment
        for segment, score in relevant
        if score >= 0.25 and any(marker in segment.casefold() for marker in _NEGATIVE_MARKERS)
    ]
    if conflicts:
        evidence = [f"提交说明：{conflicts[0]}"]
        return CriterionResult(
            criterion=criterion_text,
            status=CriterionStatus.FAIL,
            evidence=evidence,
            reason="提交说明中存在与该验收标准直接相关的明确冲突或未完成陈述。",
            suggested_action=f"修复该问题并补充可核验的完成证据：{criterion_text}",
        )

    direct_segments = [
        segment
        for segment, score in relevant
        if score >= 0.6
        and (
            any(marker in segment.casefold() for marker in _POSITIVE_MARKERS)
            or _normalize(criterion_text) in _normalize(segment)
        )
    ]
    if direct_segments:
        evidence = [f"提交说明：{direct_segments[0]}"]
        evidence.extend(f"证据链接：{link}" for link in evidence_links[:2])
        return CriterionResult(
            criterion=criterion_text,
            status=CriterionStatus.PASS,
            evidence=evidence,
            reason="提交说明包含与该标准直接对应的肯定性文本证据。",
            suggested_action="无需修改；建议保留当前证据以便追溯。",
        )

    auxiliary = [f"证据链接：{link}" for link in evidence_links[:2]]
    reason = (
        "存在证据链接，但 Day 1 规则模式不读取链接内容，无法仅凭链接确认该标准。"
        if auxiliary
        else "提交说明中没有足够直接的正面证据，也没有发现明确冲突。"
    )
    return CriterionResult(
        criterion=criterion_text,
        status=CriterionStatus.NEEDS_REVIEW,
        evidence=auxiliary,
        reason=reason,
        suggested_action=f"补充直接说明或可核验证据，明确证明：{criterion_text}",
    )


def aggregate_decision(
    task_status: TaskStatus,
    criteria_results: list[CriterionResult],
    *,
    severely_incomplete: bool = False,
) -> FinalDecision:
    """Aggregate criterion results according to the project decision table."""

    if severely_incomplete or not criteria_results:
        return FinalDecision.FAIL
    statuses = {result.status for result in criteria_results}
    if CriterionStatus.FAIL in statuses:
        return FinalDecision.NEEDS_REVISION
    if CriterionStatus.NEEDS_REVIEW in statuses:
        return FinalDecision.NEEDS_REVIEW
    return FinalDecision.PASS if task_status == TaskStatus.ON_TIME else FinalDecision.LATE_PASS


def _sanitize(value: Any) -> Any:
    """Recursively remove common secret-bearing keys before logging."""

    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if not any(secret in key.casefold() for secret in ("api_key", "apikey", "token", "secret"))
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def log_result(
    input_data: dict[str, Any],
    intermediate_steps: list[dict[str, Any]],
    output_data: dict[str, Any],
    log_dir: str | Path = "logs",
) -> str | None:
    """Best-effort JSON logging. Never raises into the main workflow."""

    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        filename = f"review_{now.strftime('%Y%m%dT%H%M%S_%fZ')}_{uuid4().hex[:8]}.json"
        payload = _sanitize(
            {
                "logged_at": now.isoformat(),
                "input": input_data,
                "intermediate_steps": intermediate_steps,
                "output": output_data,
            }
        )
        path = directory / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return str(path)
    except Exception:
        return None
