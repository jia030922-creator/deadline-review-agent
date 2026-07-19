"""Safe, deterministic parsers for supported uploaded evidence files."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from agent.schemas import FileEvidence, FileParseStatus


MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 20_000
SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".json"}


def _short_error(error: Exception | str) -> str:
    message = " ".join(str(error).split())
    return message[:300] or "未知解析错误"


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_EXTRACTED_TEXT_CHARS:
        return text, False
    return text[:MAX_EXTRACTED_TEXT_CHARS], True


def _base_evidence(
    filename: str,
    file_bytes: bytes,
    mime_type: str | None,
    status: FileParseStatus,
    *,
    page_count: int | None = None,
    extracted_text: str = "",
    parse_error: str | None = None,
    text_truncated: bool = False,
) -> FileEvidence:
    return FileEvidence(
        filename=Path(filename).name,
        extension=Path(filename).suffix.casefold(),
        mime_type=mime_type,
        size_bytes=len(file_bytes),
        page_count=page_count,
        extracted_text=extracted_text,
        parse_status=status,
        parse_error=parse_error,
        text_truncated=text_truncated,
    )


def _too_large(filename: str, file_bytes: bytes, mime_type: str | None) -> FileEvidence | None:
    if len(file_bytes) <= MAX_FILE_SIZE_BYTES:
        return None
    return _base_evidence(
        filename,
        file_bytes,
        mime_type,
        FileParseStatus.FAILED,
        parse_error=f"文件超过 5 MB 限制（实际 {len(file_bytes)} 字节）。",
    )


def parse_uploaded_file(filename: str, file_bytes: bytes, mime_type: str | None) -> FileEvidence:
    """Route raw upload bytes to a supported parser without leaking UI objects."""

    safe_name = Path(filename).name or "unnamed"
    extension = Path(safe_name).suffix.casefold()
    oversized = _too_large(safe_name, file_bytes, mime_type)
    if oversized:
        return oversized
    if extension == ".pdf":
        return parse_pdf(safe_name, file_bytes, mime_type)
    if extension in {".txt", ".md"}:
        return parse_text_file(safe_name, file_bytes, mime_type)
    if extension == ".json":
        return parse_json_file(safe_name, file_bytes, mime_type)
    return _base_evidence(
        safe_name,
        file_bytes,
        mime_type,
        FileParseStatus.UNSUPPORTED,
        parse_error=f"不支持的文件类型：{extension or '无扩展名'}。",
    )


def parse_pdf(filename: str, file_bytes: bytes, mime_type: str | None = "application/pdf") -> FileEvidence:
    """Extract PDF page count and available text using pypdf."""

    oversized = _too_large(filename, file_bytes, mime_type)
    if oversized:
        return oversized
    try:
        reader = PdfReader(BytesIO(file_bytes))
        if reader.is_encrypted and reader.decrypt("") == 0:
            return _base_evidence(
                filename,
                file_bytes,
                mime_type,
                FileParseStatus.FAILED,
                parse_error="PDF 已加密，无法使用空密码读取。",
            )
        page_count = len(reader.pages)
        page_texts: list[str] = []
        extraction_errors = 0
        for page in reader.pages:
            try:
                page_texts.append(page.extract_text() or "")
            except Exception:
                extraction_errors += 1
        extracted_text, truncated = _truncate("\n\n".join(page_texts).strip())
        partial = extraction_errors > 0 or not extracted_text
        error = None
        if extraction_errors:
            error = f"{extraction_errors} 页文本提取失败，但页数读取成功。"
        elif not extracted_text:
            error = "PDF 页数读取成功，但未提取到文本。"
        return _base_evidence(
            filename,
            file_bytes,
            mime_type,
            FileParseStatus.PARTIAL if partial else FileParseStatus.SUCCESS,
            page_count=page_count,
            extracted_text=extracted_text,
            parse_error=error,
            text_truncated=truncated,
        )
    except Exception as exc:
        return _base_evidence(
            filename,
            file_bytes,
            mime_type,
            FileParseStatus.FAILED,
            parse_error=f"PDF 解析失败：{_short_error(exc)}",
        )


def _decode_text(file_bytes: bytes) -> tuple[str | None, str | None]:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return file_bytes.decode(encoding), None
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc.reason}")
    return None, "; ".join(errors)


def parse_text_file(filename: str, file_bytes: bytes, mime_type: str | None = None) -> FileEvidence:
    """Decode TXT or Markdown as UTF-8/UTF-8-SIG, then GB18030."""

    oversized = _too_large(filename, file_bytes, mime_type)
    if oversized:
        return oversized
    text, error = _decode_text(file_bytes)
    if text is None:
        return _base_evidence(
            filename,
            file_bytes,
            mime_type,
            FileParseStatus.FAILED,
            parse_error=f"文本解码失败：{_short_error(error or '')}",
        )
    extracted_text, truncated = _truncate(text)
    return _base_evidence(
        filename,
        file_bytes,
        mime_type,
        FileParseStatus.SUCCESS,
        extracted_text=extracted_text,
        text_truncated=truncated,
    )


def parse_json_file(filename: str, file_bytes: bytes, mime_type: str | None = "application/json") -> FileEvidence:
    """Validate and pretty-print a JSON document for deterministic evaluation."""

    oversized = _too_large(filename, file_bytes, mime_type)
    if oversized:
        return oversized
    text, decode_error = _decode_text(file_bytes)
    if text is None:
        return _base_evidence(
            filename,
            file_bytes,
            mime_type,
            FileParseStatus.FAILED,
            parse_error=f"JSON 文本解码失败：{_short_error(decode_error or '')}",
        )
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return _base_evidence(
            filename,
            file_bytes,
            mime_type,
            FileParseStatus.FAILED,
            parse_error=f"JSON 语法无效：第 {exc.lineno} 行第 {exc.colno} 列，{_short_error(exc.msg)}。",
        )
    formatted = json.dumps(value, ensure_ascii=False, indent=2)
    extracted_text, truncated = _truncate(formatted)
    return _base_evidence(
        filename,
        file_bytes,
        mime_type,
        FileParseStatus.SUCCESS,
        extracted_text=extracted_text,
        text_truncated=truncated,
    )


def build_file_evidence_context(files: list[FileEvidence]) -> str:
    """Create bounded, clearly separated evidence context for the workflow."""

    if not files:
        return "未上传文件证据。"
    blocks: list[str] = []
    for index, file in enumerate(files, start=1):
        lines = [
            f"[文件 {index}]",
            f"文件名：{file.filename}",
            f"文件类型：{file.extension or '未知'}",
            f"MIME 类型：{file.mime_type or '未知'}",
            f"文件大小：{file.size_bytes} 字节",
            f"解析状态：{file.parse_status.value}",
            f"页数：{file.page_count if file.page_count is not None else '未知'}",
        ]
        if file.parse_error:
            lines.append(f"解析说明：{file.parse_error}")
        if file.parse_status in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL} and file.extracted_text:
            lines.extend(("提取内容：", file.extracted_text))
            if file.text_truncated:
                lines.append("[提取内容已截断]")
        else:
            lines.append("提取内容：未成功提取，不可作为已读取文本使用。")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
