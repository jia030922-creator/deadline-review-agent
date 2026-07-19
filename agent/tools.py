"""Deterministic tools used by the Deadline Review Agent."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from agent.schemas import (
    CriterionEvidenceType,
    CriterionResult,
    CriterionStatus,
    DeadlineResult,
    EvidenceSource,
    FileEvidence,
    FileParseStatus,
    FinalDecision,
    ParsedCriterion,
    TaskStatus,
)


_NEGATIVE_MARKERS = (
    "未完成", "没有完成", "尚未", "缺少", "遗漏", "不支持", "无法", "失败",
    "不符合", "未提供", "未实现", "未发现", "不存在", "不包含", "不合法", "不是",
    "not completed", "not implemented",
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
_QUALITATIVE_MARKERS = (
    "创新性", "商业价值", "足够专业", "质量优秀", "代码质量", "用户体验优秀",
    "美观", "高质量", "清楚说明", "表达清晰", "具体可执行", "描述清楚",
    "professional enough", "commercial value", "innovative", "clearly explain",
    "actionable",
    "excellent quality",
)
_CHINESE_NUMBERS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
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


def _result(
    criterion: str,
    status: CriterionStatus,
    evidence: list[str],
    reason: str,
    action: str,
    *,
    evidence_type: CriterionEvidenceType,
    evidence_source: EvidenceSource = EvidenceSource.NONE,
    source_files: list[str] | None = None,
    conflict_detected: bool = False,
) -> CriterionResult:
    return CriterionResult(
        criterion=criterion,
        status=status,
        evidence=evidence,
        reason=reason,
        suggested_action=action,
        evidence_type=evidence_type,
        evidence_source=evidence_source,
        source_files=source_files or [],
        conflict_detected=conflict_detected,
    )


def _submission_claim_segment(
    criterion: str,
    submission_text: str,
    claim_terms: list[str] | None = None,
) -> str | None:
    terms = [term for term in (claim_terms or []) if term]
    for segment in (
        part.strip()
        for part in re.split(r"[\r\n。！？!?;；]+", submission_text)
        if part.strip()
    ):
        folded = segment.casefold()
        if any(marker in folded for marker in _NEGATIVE_MARKERS):
            continue
        if terms and any(term.casefold() in folded for term in terms):
            return segment
        if _relevance(criterion, segment) >= 0.25 and any(
            marker in folded for marker in _POSITIVE_MARKERS
        ):
            return segment
        if _normalize(criterion) and _normalize(criterion) in _normalize(segment):
            return segment
    return None


def _extract_page_limit(criterion: str) -> int | None:
    compact = re.sub(r"\s+", "", criterion.casefold())
    match = re.search(r"(?:不超过|最多|至多|控制在|只能有|仅限|限)([一二两三四五六七八九十两\d]+)页", compact)
    if not match and any(phrase in compact for phrase in ("一页以内", "单页")):
        return 1
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return _CHINESE_NUMBERS.get(raw)


def _requires_pdf(criterion: str) -> bool:
    compact = re.sub(r"\s+", "", criterion.casefold())
    return any(
        phrase in compact
        for phrase in (
            "必须是pdf", "必须提交pdf", "提交pdf文件", "提交pdf",
            "pdf格式", "上传pdf", "pdf文件",
        )
    )


def _requires_valid_json(criterion: str) -> bool:
    compact = re.sub(r"\s+", "", criterion.casefold())
    return "json" in compact and any(
        marker in compact for marker in ("合法", "有效", "格式", "语法正确", "valid")
    )


def _extract_required_content_terms(criterion: str) -> list[str]:
    match = re.search(r"(?:包含|必须出现|需要出现|应出现)(.+)$", criterion, flags=re.IGNORECASE)
    if not match:
        return []
    tail = match.group(1).strip(" ：:，,。；;")
    tail = re.sub(r"(?:等内容|等关键词|等信息)$", "", tail).strip()
    terms = [
        item.strip(" 《》\"'`：:，,。；;")
        for item in re.split(r"、|，|,|\s+(?:和|及|与|and)\s+", tail, flags=re.IGNORECASE)
    ]
    return [term for term in terms if 1 <= len(term) <= 60][:8]


def classify_criterion_evidence_type(criterion: ParsedCriterion | str) -> CriterionEvidenceType:
    """Classify which evidence channel can deterministically verify a criterion."""

    text = criterion.criterion if isinstance(criterion, ParsedCriterion) else criterion.strip()
    folded = text.casefold()
    if any(marker in folded for marker in _QUALITATIVE_MARKERS):
        return CriterionEvidenceType.MANUAL_REVIEW
    if _extract_page_limit(text) is not None:
        return CriterionEvidenceType.FILE_PAGE_COUNT
    if _requires_pdf(text):
        return CriterionEvidenceType.FILE_FORMAT
    if _requires_valid_json(text):
        return CriterionEvidenceType.JSON_VALIDITY
    if any(marker in folded for marker in ("链接", "link", "url")) and any(
        marker in folded for marker in ("提供", "提交", "必须", "github", "gitlab")
    ):
        return CriterionEvidenceType.LINK_PRESENCE
    if _extract_required_content_terms(text):
        return CriterionEvidenceType.FILE_CONTENT
    # Preserve Day 1's explicit statement checks for criteria outside known file/manual classes.
    return CriterionEvidenceType.SUBMISSION_STATEMENT


def _claim_evidence(
    claim: str | None,
    file_evidence: list[str],
) -> tuple[list[str], EvidenceSource, bool]:
    if claim:
        return [f"用户提交说明：{claim}", *file_evidence], EvidenceSource.MIXED, True
    return file_evidence, EvidenceSource.FILE_METADATA, False


def _content_snippet(text: str, term: str, radius: int = 60) -> str:
    folded = text.casefold()
    index = folded.find(term.casefold())
    if index < 0:
        return ""
    start = max(0, index - radius)
    end = min(len(text), index + len(term) + radius)
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    return f"…{snippet}…" if start or end < len(text) else snippet


def _evaluate_file_rules(
    criterion: str,
    submission_text: str,
    evidence_links: list[str],
    uploaded_files: list[FileEvidence],
    evidence_type: CriterionEvidenceType,
) -> CriterionResult:
    pdf_files = [file for file in uploaded_files if file.extension == ".pdf"]
    if evidence_type == CriterionEvidenceType.FILE_PAGE_COUNT:
        page_limit = _extract_page_limit(criterion) or 1
        claim_terms = [f"{page_limit}页"]
        if page_limit == 1:
            claim_terms.extend(("一页", "单页"))
        claim = _submission_claim_segment(criterion, submission_text, claim_terms)
        if not uploaded_files:
            return _result(
                criterion, CriterionStatus.NEEDS_REVIEW,
                [f"仅有用户声明：{claim}"] if claim else [],
                "仅有用户声明，没有可验证的 PDF 页数证据。" if claim else "该标准需要核对实际 PDF 页数，但没有上传文件。",
                "请上传可读取页数的 PDF 文件。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.SUBMISSION_TEXT if claim else EvidenceSource.NONE,
            )
        if not pdf_files:
            file_evidence = [f"实际上传：{file.filename}（{file.extension or '未知类型'}）" for file in uploaded_files]
            evidence, source, conflict = _claim_evidence(claim, file_evidence)
            return _result(
                criterion, CriterionStatus.FAIL, evidence,
                "用户提交说明与文件证据冲突，以文件证据为准。已上传文件中没有可供页数核验的 PDF。" if conflict else "已上传文件中没有可供页数核验的 PDF。",
                "请上传符合页数限制的 PDF 文件。",
                evidence_type=evidence_type,
                evidence_source=source,
                source_files=[file.filename for file in uploaded_files],
                conflict_detected=conflict,
            )
        counted = [
            file for file in pdf_files
            if file.page_count is not None
            and file.parse_status in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL}
        ]
        if not counted:
            return _result(
                criterion, CriterionStatus.NEEDS_REVIEW,
                [f"{file.filename}：页数未知，状态 {file.parse_status.value}" for file in pdf_files],
                "PDF 未能成功读取页数，无法验证页数要求。",
                "请重新上传可正常解析且未加密的 PDF。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.FILE_METADATA,
                source_files=[file.filename for file in pdf_files],
            )
        evidence = [f"文件 {file.filename}：实际 {file.page_count} 页" for file in counted]
        violating = [file for file in counted if (file.page_count or 0) > page_limit]
        if violating:
            actual = "、".join(f"{file.filename} 为 {file.page_count} 页" for file in violating)
            evidence, source, conflict = _claim_evidence(claim, evidence)
            return _result(
                criterion, CriterionStatus.FAIL, evidence,
                f"用户提交说明与文件证据冲突，以文件证据为准。文件要求不超过 {page_limit} 页，但{actual}。" if conflict else f"文件要求不超过 {page_limit} 页，但{actual}。",
                f"将 PDF 调整为不超过 {page_limit} 页后重新提交。",
                evidence_type=evidence_type,
                evidence_source=source,
                source_files=[file.filename for file in counted],
                conflict_detected=conflict,
            )
        return _result(
            criterion, CriterionStatus.PASS, evidence,
            f"实际 PDF 页数均不超过 {page_limit} 页，满足要求。",
            "无需修改；请保留当前 PDF 作为验收证据。",
            evidence_type=evidence_type,
            evidence_source=EvidenceSource.FILE_METADATA,
            source_files=[file.filename for file in counted],
        )

    if evidence_type == CriterionEvidenceType.FILE_FORMAT:
        claim = _submission_claim_segment(criterion, submission_text, ["pdf"])
        if not uploaded_files:
            return _result(
                criterion, CriterionStatus.NEEDS_REVIEW,
                [f"仅有用户声明：{claim}"] if claim else [],
                "仅有用户声明，尚未通过真实文件验证 PDF 格式。" if claim else "当前没有上传文件，无法确认 PDF 格式。",
                "请上传实际 PDF 文件。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.SUBMISSION_TEXT if claim else EvidenceSource.NONE,
            )
        if not pdf_files:
            file_evidence = [f"实际上传：{file.filename}（{file.extension or '未知类型'}）" for file in uploaded_files]
            evidence, source, conflict = _claim_evidence(claim, file_evidence)
            return _result(
                criterion, CriterionStatus.FAIL, evidence,
                "用户提交说明与文件证据冲突，以文件证据为准。验收标准要求 PDF，但实际上传文件中没有 PDF。" if conflict else "验收标准要求 PDF，但实际上传文件中没有 PDF。",
                "请改为上传 PDF 文件。",
                evidence_type=evidence_type,
                evidence_source=source,
                source_files=[file.filename for file in uploaded_files],
                conflict_detected=conflict,
            )
        readable = [file for file in pdf_files if file.parse_status in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL}]
        if readable:
            return _result(
                criterion, CriterionStatus.PASS,
                [f"文件 {file.filename}：PDF，解析状态 {file.parse_status.value}" for file in readable],
                "已上传可识别的 PDF 文件，满足格式要求。",
                "无需修改；请保留该 PDF。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.FILE_METADATA,
                source_files=[file.filename for file in readable],
            )
        return _result(
            criterion, CriterionStatus.NEEDS_REVIEW,
            [f"文件 {file.filename}：{file.parse_status.value}" for file in pdf_files],
            "文件扩展名为 PDF，但解析失败，无法确认它是有效 PDF。",
            "请上传可正常打开的未加密 PDF。",
            evidence_type=evidence_type,
            evidence_source=EvidenceSource.FILE_METADATA,
            source_files=[file.filename for file in pdf_files],
        )

    if evidence_type == CriterionEvidenceType.JSON_VALIDITY:
        claim = _submission_claim_segment(criterion, submission_text, ["json"])
        json_files = [file for file in uploaded_files if file.extension == ".json"]
        if not uploaded_files:
            return _result(
                criterion, CriterionStatus.NEEDS_REVIEW,
                [f"仅有用户声明：{claim}"] if claim else [],
                "仅有用户声明，没有上传 JSON 文件验证其合法性。" if claim else "没有上传 JSON 文件，无法验证其结构是否合法。",
                "请上传需要验收的 JSON 文件。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.SUBMISSION_TEXT if claim else EvidenceSource.NONE,
            )
        if not json_files:
            file_evidence = [f"实际上传：{file.filename}" for file in uploaded_files]
            evidence, source, conflict = _claim_evidence(claim, file_evidence)
            return _result(
                criterion, CriterionStatus.FAIL, evidence,
                "用户提交说明与文件证据冲突，以文件证据为准。验收标准要求合法 JSON，但实际没有上传 JSON。" if conflict else "验收标准要求合法 JSON，但上传文件中没有 JSON。",
                "请上传语法合法的 .json 文件。",
                evidence_type=evidence_type,
                evidence_source=source,
                source_files=[file.filename for file in uploaded_files],
                conflict_detected=conflict,
            )
        invalid = [file for file in json_files if file.parse_status == FileParseStatus.FAILED]
        valid = [file for file in json_files if file.parse_status == FileParseStatus.SUCCESS]
        if valid and not invalid:
            return _result(
                criterion, CriterionStatus.PASS,
                [f"文件 {file.filename}：JSON 结构解析成功" for file in valid],
                "JSON 文件已通过确定性语法解析。",
                "无需修改；请保留该 JSON。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.FILE_METADATA,
                source_files=[file.filename for file in valid],
            )
        if not invalid:
            return _result(
                criterion, CriterionStatus.NEEDS_REVIEW,
                [f"文件 {file.filename}：{file.parse_status.value}" for file in json_files],
                "JSON 文件未成功完成解析，无法确认其合法性。",
                "请重新上传可完整解析的 JSON 文件。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.FILE_METADATA,
                source_files=[file.filename for file in json_files],
            )
        file_evidence = [f"实际文件 {file.filename}：{file.parse_error or file.parse_status.value}" for file in invalid]
        evidence, source, conflict = _claim_evidence(claim, file_evidence)
        return _result(
            criterion, CriterionStatus.FAIL, evidence,
            "用户提交说明与文件证据冲突，以文件证据为准。上传的 JSON 文件未通过语法解析。" if conflict else "上传的 JSON 文件未通过语法解析。",
            "修复 JSON 语法错误后重新上传。",
            evidence_type=evidence_type,
            evidence_source=source,
            source_files=[file.filename for file in invalid],
            conflict_detected=conflict,
        )

    if evidence_type == CriterionEvidenceType.FILE_CONTENT:
        terms = _extract_required_content_terms(criterion)
        claim = _submission_claim_segment(criterion, submission_text, terms)
        if not uploaded_files:
            auxiliary = [f"仅有用户声明：{claim}"] if claim else []
            auxiliary.extend(f"未读取的证据链接：{link}" for link in evidence_links[:2])
            if claim and evidence_links:
                source = EvidenceSource.MIXED
            elif claim:
                source = EvidenceSource.SUBMISSION_TEXT
            elif evidence_links:
                source = EvidenceSource.EVIDENCE_LINK
            else:
                source = EvidenceSource.NONE
            return _result(
                criterion, CriterionStatus.NEEDS_REVIEW,
                auxiliary,
                "仅有用户声明，没有可验证文件证据。" if claim else "没有上传文件，无法验证文件内容要求。",
                "请上传可完整提取文本的文件以验证必需内容。",
                evidence_type=evidence_type,
                evidence_source=source,
            )
        usable = [
            file for file in uploaded_files
            if file.parse_status in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL}
        ]
        matching = [
            file for file in usable
            if all(term.casefold() in file.extracted_text.casefold() for term in terms)
        ]
        if matching:
            file = matching[0]
            snippets = [
                f"文件 {file.filename} 命中 {term}：{_content_snippet(file.extracted_text, term)}"
                for term in terms
            ]
            return _result(
                criterion, CriterionStatus.PASS, snippets,
                f"在 {file.filename} 的提取文本中发现全部必需内容。",
                "无需修改；请保留当前文件证据。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.FILE_CONTENT,
                source_files=[file.filename],
            )
        uncertain = [
            file for file in uploaded_files
            if file.parse_status != FileParseStatus.SUCCESS or file.text_truncated
        ]
        if uncertain:
            evidence = [
                f"文件 {file.filename}：{file.parse_status.value}"
                + ("，文本已截断" if file.text_truncated else "")
                for file in uncertain
            ]
            if claim:
                evidence.insert(0, f"仅有用户声明：{claim}")
            return _result(
                criterion, CriterionStatus.NEEDS_REVIEW, evidence,
                "文件解析不完整、文本被截断或文件不可用，未命中关键词不能据此判定缺失。",
                "请提供可完整解析的文件，或安排人工复核。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.MIXED if claim else EvidenceSource.FILE_CONTENT,
                source_files=[file.filename for file in uploaded_files],
            )
        file_evidence = [
            f"完整解析的文件 {file.filename} 未发现：{'、'.join(terms)}"
            for file in uploaded_files
        ]
        evidence, source, conflict = _claim_evidence(claim, file_evidence)
        return _result(
            criterion, CriterionStatus.FAIL, evidence,
            "用户提交说明与文件证据冲突，以文件证据为准。用户声称文件包含必需内容，但成功完整解析的文件中未发现。" if conflict else f"成功完整解析的文件中未发现必需内容：{'、'.join(terms)}。",
            f"在交付文件中补充：{'、'.join(terms)}。",
            evidence_type=evidence_type,
            evidence_source=EvidenceSource.MIXED if conflict else EvidenceSource.FILE_CONTENT,
            source_files=[file.filename for file in uploaded_files],
            conflict_detected=conflict,
        )

    raise ValueError(f"不支持的文件证据类型：{evidence_type.value}")


def evaluate_evidence_rule_based(
    criterion: ParsedCriterion | str,
    submission_text: str,
    evidence_links: list[str],
    uploaded_files: list[FileEvidence] | None = None,
) -> CriterionResult:
    """Evaluate one criterion conservatively using inspectable text rules."""

    criterion_text = criterion.criterion if isinstance(criterion, ParsedCriterion) else criterion.strip()
    evidence_type = classify_criterion_evidence_type(criterion_text)
    if evidence_type == CriterionEvidenceType.MANUAL_REVIEW:
        return _result(
            criterion_text,
            CriterionStatus.NEEDS_REVIEW,
            [],
            "该标准涉及主观或复杂语义判断，规则模式无法可靠验证。",
            "请安排人工复核，并提供明确、可衡量的评价依据。",
            evidence_type=evidence_type,
        )
    if evidence_type == CriterionEvidenceType.LINK_PRESENCE:
        if evidence_links:
            return _result(
                criterion_text,
                CriterionStatus.PASS,
                [f"用户提供的链接记录：{link}" for link in evidence_links[:2]],
                "验收标准只要求提供链接，已记录至少一个链接；系统未打开或验证链接内容。",
                "无需补充链接；如需验收链接内容，请人工打开核对。",
                evidence_type=evidence_type,
                evidence_source=EvidenceSource.EVIDENCE_LINK,
            )
        return _result(
            criterion_text,
            CriterionStatus.NEEDS_REVIEW,
            [],
            "未在 evidence_links 字段中找到链接，提交说明中的声明不能替代实际链接记录。",
            "请在证据链接字段中提供所需链接。",
            evidence_type=evidence_type,
        )
    if evidence_type in {
        CriterionEvidenceType.FILE_FORMAT,
        CriterionEvidenceType.FILE_PAGE_COUNT,
        CriterionEvidenceType.FILE_CONTENT,
        CriterionEvidenceType.JSON_VALIDITY,
    }:
        return _evaluate_file_rules(
            criterion_text,
            submission_text,
            evidence_links,
            uploaded_files or [],
            evidence_type,
        )

    segments = [part.strip() for part in re.split(r"[\r\n。！？!?;；]+", submission_text) if part.strip()]
    relevant = [(segment, _relevance(criterion_text, segment)) for segment in segments]

    conflicts = [
        segment
        for segment, score in relevant
        if score >= 0.25 and any(marker in segment.casefold() for marker in _NEGATIVE_MARKERS)
    ]
    if conflicts:
        evidence = [f"提交说明：{conflicts[0]}"]
        return _result(
            criterion_text,
            CriterionStatus.FAIL,
            evidence,
            "提交说明中存在与该验收标准直接相关的明确冲突或未完成陈述。",
            f"修复该问题并补充可核验的完成证据：{criterion_text}",
            evidence_type=evidence_type,
            evidence_source=EvidenceSource.SUBMISSION_TEXT,
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
        return _result(
            criterion_text,
            CriterionStatus.PASS,
            evidence,
            "该标准属于提交说明可验证类型，提交说明包含直接对应的肯定性陈述。",
            "无需修改；建议保留当前提交说明以便追溯。",
            evidence_type=evidence_type,
            evidence_source=EvidenceSource.MIXED if evidence_links else EvidenceSource.SUBMISSION_TEXT,
        )

    auxiliary = [f"证据链接：{link}" for link in evidence_links[:2]]
    reason = (
        "存在证据链接，但 Day 1 规则模式不读取链接内容，无法仅凭链接确认该标准。"
        if auxiliary
        else "提交说明中没有足够直接的正面证据，也没有发现明确冲突。"
    )
    return _result(
        criterion_text,
        CriterionStatus.NEEDS_REVIEW,
        auxiliary,
        reason,
        f"补充直接说明或可核验证据，明确证明：{criterion_text}",
        evidence_type=evidence_type,
        evidence_source=EvidenceSource.EVIDENCE_LINK if auxiliary else EvidenceSource.NONE,
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
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            folded = key.casefold()
            if any(secret in folded for secret in ("api_key", "apikey", "token", "secret")):
                continue
            if folded == "extracted_text" and isinstance(item, str) and len(item) > 1_000:
                sanitized[key] = item[:1_000] + "\n[日志文本预览已截断]"
            else:
                sanitized[key] = _sanitize(item)
        return sanitized
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
