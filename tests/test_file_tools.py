from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

from pypdf import PdfWriter

from agent.file_tools import (
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_FILE_SIZE_BYTES,
    build_file_evidence_context,
    parse_json_file,
    parse_pdf,
    parse_text_file,
    parse_uploaded_file,
)
from agent.schemas import (
    CriterionEvidenceType,
    CriterionStatus,
    EvidenceSource,
    FileEvidence,
    FileParseStatus,
    FinalDecision,
    ReviewInput,
)
from agent.tools import classify_criterion_evidence_type
from agent.workflow import DeadlineReviewAgent


NOW = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)


def pdf_bytes(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_file_input(
    criterion: str,
    files: list[FileEvidence],
    submission_text: str = "",
    evidence_links: list[str] | None = None,
) -> ReviewInput:
    return ReviewInput(
        task_title="文件证据验收",
        due_at=NOW,
        submitted_at=NOW - timedelta(minutes=5),
        acceptance_criteria=[criterion],
        submission_text=submission_text,
        evidence_links=evidence_links or [],
        uploaded_files=files,
    )


def run_file_review(
    tmp_path,
    criterion: str,
    files: list[FileEvidence],
    submission_text: str = "",
    evidence_links: list[str] | None = None,
):
    return DeadlineReviewAgent(tmp_path / "logs").run(
        make_file_input(criterion, files, submission_text, evidence_links)
    )


def test_txt_file_extracts_text() -> None:
    result = parse_text_file("说明.txt", "安装步骤完成".encode("utf-8"), "text/plain")

    assert result.parse_status == FileParseStatus.SUCCESS
    assert result.extracted_text == "安装步骤完成"


def test_markdown_file_extracts_text() -> None:
    result = parse_text_file("README.md", b"# Title\n\nDeadline Box", "text/markdown")

    assert result.parse_status == FileParseStatus.SUCCESS
    assert "Deadline Box" in result.extracted_text
    assert result.extension == ".md"


def test_valid_json_is_formatted() -> None:
    result = parse_json_file("data.json", '{"name":"测试","ok":true}'.encode("utf-8"))

    assert result.parse_status == FileParseStatus.SUCCESS
    assert '"name": "测试"' in result.extracted_text


def test_invalid_json_returns_failed() -> None:
    result = parse_json_file("bad.json", b'{"missing": }')

    assert result.parse_status == FileParseStatus.FAILED
    assert "JSON 语法无效" in (result.parse_error or "")


def test_one_page_pdf_reports_page_count() -> None:
    result = parse_pdf("one.pdf", pdf_bytes(1))

    assert result.page_count == 1
    assert result.parse_status == FileParseStatus.PARTIAL


def test_two_page_pdf_reports_page_count() -> None:
    result = parse_pdf("two.pdf", pdf_bytes(2))

    assert result.page_count == 2


def test_one_page_requirement_passes(tmp_path) -> None:
    result = run_file_review(tmp_path, "简历控制在一页", [parse_pdf("resume.pdf", pdf_bytes(1))])

    assert result.criteria_results[0].status == CriterionStatus.PASS
    assert result.final_decision == FinalDecision.PASS


def test_two_page_requirement_fails(tmp_path) -> None:
    result = run_file_review(tmp_path, "简历只能有一页", [parse_pdf("resume.pdf", pdf_bytes(2))])

    assert result.criteria_results[0].status == CriterionStatus.FAIL
    assert result.final_decision == FinalDecision.NEEDS_REVISION


def test_uploaded_pdf_satisfies_pdf_format(tmp_path) -> None:
    result = run_file_review(tmp_path, "必须是 PDF 格式", [parse_pdf("delivery.pdf", pdf_bytes(1))])

    assert result.criteria_results[0].status == CriterionStatus.PASS


def test_non_pdf_file_fails_pdf_format(tmp_path) -> None:
    text_file = parse_text_file("delivery.txt", b"content", "text/plain")
    result = run_file_review(tmp_path, "提交 PDF 文件", [text_file])

    assert result.criteria_results[0].status == CriterionStatus.FAIL


def test_pdf_claim_without_upload_needs_review(tmp_path) -> None:
    result = run_file_review(tmp_path, "必须是 PDF 格式", [], "已完成，提交的是 PDF 格式。")

    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW


def test_file_text_with_required_keyword_passes(tmp_path) -> None:
    file = parse_text_file("notes.md", "项目使用 Deadline Box 管理截止时间".encode("utf-8"))
    result = run_file_review(tmp_path, "文档包含 Deadline Box", [file])

    assert result.criteria_results[0].status == CriterionStatus.PASS


def test_file_text_missing_required_keyword_fails(tmp_path) -> None:
    file = parse_text_file("notes.txt", "这里只介绍项目背景".encode("utf-8"))
    result = run_file_review(tmp_path, "文档中必须出现安装步骤", [file])

    assert result.criteria_results[0].status == CriterionStatus.FAIL


def test_file_page_count_overrides_user_claim(tmp_path) -> None:
    result = run_file_review(
        tmp_path,
        "简历控制在一页",
        [parse_pdf("resume.pdf", pdf_bytes(2))],
        "已完成，简历控制在一页。",
    )

    item = result.criteria_results[0]
    assert item.status == CriterionStatus.FAIL
    assert "用户提交说明与文件证据冲突" in item.reason


def test_single_failed_file_does_not_crash_workflow(tmp_path) -> None:
    failed = parse_pdf("broken.pdf", b"not a pdf")
    result = run_file_review(tmp_path, "必须是 PDF 格式", [failed])

    assert failed.parse_status == FileParseStatus.FAILED
    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW
    assert result.file_evidence_results[0].parse_status == FileParseStatus.FAILED


def test_unsupported_file_returns_unsupported() -> None:
    result = parse_uploaded_file("archive.zip", b"PK", "application/zip")

    assert result.parse_status == FileParseStatus.UNSUPPORTED


def test_oversized_file_returns_failed() -> None:
    result = parse_uploaded_file("large.txt", b"x" * (MAX_FILE_SIZE_BYTES + 1), "text/plain")

    assert result.parse_status == FileParseStatus.FAILED
    assert "5 MB" in (result.parse_error or "")


def test_long_text_is_truncated() -> None:
    result = parse_text_file("long.txt", b"a" * (MAX_EXTRACTED_TEXT_CHARS + 50))

    assert len(result.extracted_text) == MAX_EXTRACTED_TEXT_CHARS
    assert result.text_truncated is True


def test_valid_and_invalid_json_rules(tmp_path) -> None:
    valid = parse_json_file("valid.json", b'{"ok": true}')
    invalid = parse_json_file("invalid.json", b'{"ok": }')

    assert run_file_review(tmp_path, "必须是合法 JSON 格式", [valid]).criteria_results[0].status == CriterionStatus.PASS
    assert run_file_review(tmp_path, "必须是合法 JSON 格式", [invalid]).criteria_results[0].status == CriterionStatus.FAIL


def test_multiple_required_keywords_must_all_exist(tmp_path) -> None:
    file = parse_text_file("content.md", "AI 玩偶使用 Dify，并展示 Deadline Box。".encode("utf-8"))
    result = run_file_review(tmp_path, "包含 AI 玩偶、Dify 和 Deadline Box", [file])

    assert result.criteria_results[0].status == CriterionStatus.PASS


def test_qualitative_standard_requires_review(tmp_path) -> None:
    file = parse_text_file("proposal.md", "商业计划正文".encode("utf-8"))
    result = run_file_review(tmp_path, "项目具有较强商业价值", [file], "项目商业价值很强。")

    assert result.criteria_results[0].status == CriterionStatus.NEEDS_REVIEW


def test_failed_file_is_not_presented_as_extracted_content() -> None:
    failed = parse_pdf("broken.pdf", b"broken")
    context = build_file_evidence_context([failed])

    assert "解析状态：FAILED" in context
    assert "不可作为已读取文本使用" in context


def test_log_saves_only_short_file_text_preview(tmp_path) -> None:
    file = parse_text_file("long.txt", b"a" * 5_000)
    result = run_file_review(tmp_path, "包含 a", [file])
    log_path = next((tmp_path / "logs").glob("*.json"))
    payload = json.loads(log_path.read_text(encoding="utf-8"))

    assert result.file_evidence_results[0].extracted_text == "a" * 5_000
    logged_text = payload["input"]["uploaded_files"][0]["extracted_text"]
    assert len(logged_text) < 1_100
    assert "日志文本预览已截断" in logged_text


def test_file_content_passes_even_when_submission_text_omits_keyword(tmp_path) -> None:
    file = parse_text_file("resume.txt", "项目经历包括 Deadline Box。".encode("utf-8"))

    item = run_file_review(tmp_path, "文档中必须包含 Deadline Box", [file], "文档已经完成。").criteria_results[0]

    assert item.status == CriterionStatus.PASS
    assert item.evidence_source == EvidenceSource.FILE_CONTENT
    assert item.source_files == ["resume.txt"]
    assert "Deadline Box" in item.evidence[0]


def test_file_content_overrides_false_user_claim(tmp_path) -> None:
    file = parse_text_file("resume.txt", "这里只包含项目背景。".encode("utf-8"))

    item = run_file_review(tmp_path, "文档中必须包含 Dify", [file], "简历已经包含 Dify。").criteria_results[0]

    assert item.status == CriterionStatus.FAIL
    assert item.conflict_detected is True
    assert item.evidence_source == EvidenceSource.MIXED
    assert any("用户提交说明" in evidence for evidence in item.evidence)
    assert any("resume.txt" in evidence for evidence in item.evidence)


def test_user_claim_without_file_is_needs_review(tmp_path) -> None:
    item = run_file_review(tmp_path, "文档中必须包含 Dify", [], "简历已经包含 Dify。").criteria_results[0]

    assert item.status == CriterionStatus.NEEDS_REVIEW
    assert item.evidence_source == EvidenceSource.SUBMISSION_TEXT
    assert "仅有用户声明" in item.reason


def test_actual_pdf_page_count_passes_without_submission_claim(tmp_path) -> None:
    item = run_file_review(
        tmp_path, "简历控制在一页", [parse_pdf("resume.pdf", pdf_bytes(1))], "简历已提交。"
    ).criteria_results[0]

    assert item.status == CriterionStatus.PASS
    assert item.evidence_source == EvidenceSource.FILE_METADATA


def test_actual_txt_overrides_false_pdf_claim(tmp_path) -> None:
    file = parse_text_file("delivery.txt", b"content")
    item = run_file_review(tmp_path, "必须是 PDF 格式", [file], "提交文件是 PDF。").criteria_results[0]

    assert item.status == CriterionStatus.FAIL
    assert item.conflict_detected is True
    assert item.evidence_source == EvidenceSource.MIXED


def test_actual_pdf_passes_without_format_claim(tmp_path) -> None:
    item = run_file_review(
        tmp_path, "必须是 PDF 格式", [parse_pdf("delivery.pdf", pdf_bytes(1))], "文件已提交。"
    ).criteria_results[0]

    assert item.status == CriterionStatus.PASS
    assert item.source_files == ["delivery.pdf"]


def test_invalid_json_overrides_false_validity_claim(tmp_path) -> None:
    file = parse_json_file("broken.json", b'{"ok": }')
    item = run_file_review(tmp_path, "必须是合法 JSON", [file], "JSON 已经合法。").criteria_results[0]

    assert item.status == CriterionStatus.FAIL
    assert item.conflict_detected is True
    assert item.evidence_source == EvidenceSource.MIXED


def test_partial_file_without_keyword_is_needs_review(tmp_path) -> None:
    file = FileEvidence(
        filename="scan.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        size_bytes=100,
        page_count=1,
        extracted_text="可提取的部分文本",
        parse_status=FileParseStatus.PARTIAL,
        parse_error="部分页面无法提取文本",
    )
    item = run_file_review(tmp_path, "文档中必须包含 Dify", [file]).criteria_results[0]

    assert item.status == CriterionStatus.NEEDS_REVIEW


def test_truncated_file_without_keyword_is_needs_review(tmp_path) -> None:
    file = FileEvidence(
        filename="long.txt",
        extension=".txt",
        mime_type="text/plain",
        size_bytes=30_000,
        extracted_text="已提取但不完整的文本",
        parse_status=FileParseStatus.SUCCESS,
        text_truncated=True,
    )
    item = run_file_review(tmp_path, "文档中必须包含 Dify", [file]).criteria_results[0]

    assert item.status == CriterionStatus.NEEDS_REVIEW


def test_keyword_in_unread_link_cannot_pass_file_content(tmp_path) -> None:
    item = run_file_review(
        tmp_path,
        "文档中必须包含 Dify",
        [],
        "文档已提交。",
        ["https://example.com/Dify"],
    ).criteria_results[0]

    assert item.status == CriterionStatus.NEEDS_REVIEW
    assert item.evidence_source == EvidenceSource.EVIDENCE_LINK
    assert "未读取" in item.evidence[0]


def test_conflict_detection_is_recorded_in_intermediate_steps(tmp_path) -> None:
    file = parse_text_file("resume.txt", "没有目标关键词".encode("utf-8"))
    result = run_file_review(tmp_path, "文档中必须包含 Dify", [file], "已经包含 Dify。")
    step = next(step for step in result.intermediate_steps if step.step_name == "evidence_conflict_detection")

    assert step.status == "WARNING"
    assert "1 项" in step.summary


def test_criterion_evidence_type_classification() -> None:
    assert classify_criterion_evidence_type("必须是 PDF 格式") == CriterionEvidenceType.FILE_FORMAT
    assert classify_criterion_evidence_type("简历控制在一页") == CriterionEvidenceType.FILE_PAGE_COUNT
    assert classify_criterion_evidence_type("文档中必须包含 Dify") == CriterionEvidenceType.FILE_CONTENT
    assert classify_criterion_evidence_type("必须是合法 JSON") == CriterionEvidenceType.JSON_VALIDITY
    assert classify_criterion_evidence_type("必须提供 GitHub 链接") == CriterionEvidenceType.LINK_PRESENCE
    assert classify_criterion_evidence_type("说明本次修改内容") == CriterionEvidenceType.SUBMISSION_STATEMENT
    assert classify_criterion_evidence_type("内容具有创新性") == CriterionEvidenceType.MANUAL_REVIEW
