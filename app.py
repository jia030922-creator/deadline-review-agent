"""Streamlit UI for the Deadline Review Agent."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from agent.file_tools import parse_uploaded_file
from agent.schemas import FileEvidence, FileParseStatus, RequestedEvaluationMode, ReviewInput
from agent.workflow import DeadlineReviewAgent


ROOT = Path(__file__).resolve().parent
SAMPLE_PATH = ROOT / "examples" / "sample_input.json"


def load_sample() -> None:
    sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    due = datetime.fromisoformat(sample["due_at"])
    submitted = datetime.fromisoformat(sample["submitted_at"])
    st.session_state.update(
        {
            "task_title": sample["task_title"],
            "due_date": due.date(),
            "due_time": due.time().replace(tzinfo=None),
            "submitted_date": submitted.date(),
            "submitted_time": submitted.time().replace(tzinfo=None),
            "acceptance_criteria": "\n".join(sample["acceptance_criteria"]),
            "submission_text": sample["submission_text"],
            "evidence_links": "\n".join(sample["evidence_links"]),
            "requested_evaluation_mode": "auto",
        }
    )


def initialize_defaults() -> None:
    now = datetime.now().replace(second=0, microsecond=0)
    defaults = {
        "task_title": "",
        "due_date": date.today(),
        "due_time": (now + timedelta(hours=2)).time(),
        "submitted_date": date.today(),
        "submitted_time": now.time(),
        "acceptance_criteria": "",
        "submission_text": "",
        "evidence_links": "",
        "requested_evaluation_mode": "auto",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def parse_uploads(uploaded: list[object]) -> list[FileEvidence]:
    """Convert Streamlit upload objects into serializable evidence models."""

    results: list[FileEvidence] = []
    for item in uploaded:
        filename = getattr(item, "name", "unnamed")
        mime_type = getattr(item, "type", None)
        try:
            file_bytes = item.getvalue()  # type: ignore[attr-defined]
            results.append(parse_uploaded_file(filename, file_bytes, mime_type))
        except Exception as exc:
            results.append(
                FileEvidence(
                    filename=filename,
                    extension=Path(filename).suffix.casefold(),
                    mime_type=mime_type,
                    size_bytes=0,
                    parse_status=FileParseStatus.FAILED,
                    parse_error=f"上传文件处理失败：{str(exc)[:200]}",
                )
            )
    return results


st.set_page_config(page_title="Deadline Review Agent", page_icon="✅", layout="wide")
initialize_defaults()

st.title("Deadline Review Agent")
st.caption("用提交说明、证据链接和真实文件证据，执行可追溯的任务交付验收。")
st.button("加载示例", on_click=load_sample)

with st.form("review_form"):
    st.text_input("任务标题", key="task_title", placeholder="例如：发布 Day 1 MVP")
    left, right = st.columns(2)
    with left:
        st.date_input("截止日期", key="due_date")
        st.time_input("截止时间", key="due_time", step=60)
    with right:
        st.date_input("提交日期", key="submitted_date")
        st.time_input("提交时间", key="submitted_time", step=60)
    st.text_area(
        "验收标准（每行一条）",
        key="acceptance_criteria",
        height=140,
        placeholder="README 包含运行说明\n至少有 5 个测试",
    )
    st.text_area("提交内容", key="submission_text", height=180)
    st.text_area("证据链接（每行一个）", key="evidence_links", height=100)
    st.selectbox(
        "评估模式",
        options=[mode.value for mode in RequestedEvaluationMode],
        key="requested_evaluation_mode",
        format_func=lambda value: {
            "auto": "自动模式",
            "rule_only": "仅规则模式",
            "llm_enabled": "启用 LLM 增强",
        }[value],
        help=(
            "自动模式：配置 API Key 和模型后，只复核规则无法判断的复杂标准；"
            "仅规则模式：不调用外部模型；启用 LLM 增强：尝试语义复核，失败自动回退。"
        ),
    )
    uploaded = st.file_uploader(
        "交付文件（可多选，单个文件建议不超过 5 MB）",
        type=["pdf", "txt", "md", "json"],
        accept_multiple_files=True,
        help="支持 PDF、TXT、Markdown 和 JSON；证据链接不会被联网打开。",
    )
    submitted = st.form_submit_button("开始验收", type="primary", use_container_width=True)

if submitted:
    try:
        file_evidence = parse_uploads(uploaded)
        payload = ReviewInput(
            task_title=st.session_state.task_title,
            due_at=datetime.combine(st.session_state.due_date, st.session_state.due_time),
            submitted_at=datetime.combine(st.session_state.submitted_date, st.session_state.submitted_time),
            acceptance_criteria=lines(st.session_state.acceptance_criteria),
            submission_text=st.session_state.submission_text,
            evidence_links=lines(st.session_state.evidence_links),
            uploaded_files=file_evidence,
            requested_evaluation_mode=st.session_state.requested_evaluation_mode,
        )
        result = DeadlineReviewAgent(log_dir=ROOT / "logs").run(payload)
    except ValidationError as exc:
        st.error("输入无效，请检查任务标题和验收标准是否已填写。")
        with st.expander("查看校验详情"):
            st.json(exc.errors(include_url=False))
    except Exception as exc:  # UI boundary: show a useful error instead of a blank page.
        st.error(f"验收执行失败：{exc}")
    else:
        st.divider()
        st.subheader("验收结论")
        first, second, third, fourth = st.columns(4)
        first.metric("最终决策", result.final_decision.value)
        second.metric("时间状态", result.task_status.value)
        third.metric("置信度", f"{result.confidence:.0%}")
        fourth.metric("实际评估模式", result.evaluation_mode.value)

        st.subheader("LLM 语义复核状态")
        llm_left, llm_middle, llm_right = st.columns(3)
        llm_left.write(f"LLM 可用：{'是' if result.llm_available else '否'}")
        llm_middle.write(f"模型：{result.llm_model or '未配置'}")
        llm_right.write(f"发生回退：{'是' if result.llm_fallback_used else '否'}")
        if (
            result.requested_evaluation_mode == RequestedEvaluationMode.LLM_ENABLED
            and not result.llm_available
        ):
            st.warning("已选择 LLM 增强，但未配置 OPENAI_API_KEY 或 OPENAI_MODEL；已安全回退规则模式。")
        st.caption("API Key 不会在页面、结果或日志中显示。LLM 不能覆盖文件格式、页数、JSON 或其他硬规则。")

        st.subheader("文件解析摘要")
        st.write(result.file_evidence_summary)
        if result.file_evidence_results:
            for index, file in enumerate(result.file_evidence_results, start=1):
                page_label = f" · {file.page_count} 页" if file.page_count is not None else ""
                with st.expander(
                    f"{index}. [{file.parse_status.value}] {file.filename} · "
                    f"{file.extension or '未知类型'} · {file.size_bytes:,} 字节{page_label}"
                ):
                    st.write(f"MIME 类型：{file.mime_type or '未知'}")
                    st.write(f"解析状态：{file.parse_status.value}")
                    if file.parse_error:
                        st.warning(file.parse_error)
                    if file.extracted_text:
                        preview = file.extracted_text[:2_000]
                        st.text_area(
                            "提取文本预览（最多 2,000 字符）",
                            preview,
                            height=220,
                            disabled=True,
                            key=f"file_preview_{index}",
                        )
                        if len(file.extracted_text) > len(preview) or file.text_truncated:
                            st.caption("预览或提取文本已截断。")
        else:
            st.info("本次未上传文件，验收继续使用提交说明和证据链接。")

        st.subheader("逐项验收结果")
        for index, item in enumerate(result.criteria_results, start=1):
            with st.expander(f"{index}. [{item.status.value}] {item.criterion}", expanded=True):
                st.caption(
                    f"证据类型：{item.evidence_type.value} · 证据来源：{item.evidence_source.value} · "
                    f"评估者：{item.evaluated_by.value}"
                )
                if item.source_files:
                    st.write(f"来源文件：{'、'.join(item.source_files)}")
                if item.conflict_detected:
                    st.error("发现用户提交说明与真实文件证据冲突；本项以文件证据为准。")
                if (
                    not item.source_files
                    and item.evidence_type.value
                    in {"FILE_FORMAT", "FILE_PAGE_COUNT", "FILE_CONTENT", "JSON_VALIDITY"}
                ):
                    st.warning("仅有用户声明，尚未通过真实文件验证。")
                st.write(item.reason)
                if item.llm_metadata.attempted:
                    if item.llm_metadata.success:
                        st.success(
                            f"LLM 结构化复核成功 · 模型 {item.llm_metadata.model or '未知'} · "
                            f"{item.llm_metadata.latency_ms} ms"
                        )
                        if item.llm_evaluation:
                            st.caption(
                                f"模型自评 confidence：{item.llm_evaluation.confidence:.0%}（非统计学概率）"
                            )
                            if item.llm_evaluation.limitations:
                                st.write("复核限制：" + "；".join(item.llm_evaluation.limitations))
                    else:
                        st.warning(item.llm_metadata.safe_error_message or "LLM 复核失败，已保留规则结果。")
                if item.evidence:
                    st.markdown("**证据**")
                    for evidence in item.evidence:
                        st.write(f"- {evidence}")
                st.info(item.suggested_action)

        st.subheader("下一步修改建议")
        for action in result.next_actions:
            st.write(f"- {action}")

        with st.expander("中间步骤"):
            st.dataframe(
                [step.model_dump(mode="json") for step in result.intermediate_steps],
                use_container_width=True,
                hide_index=True,
            )
        with st.expander("完整 JSON 输出"):
            st.json(result.model_dump(mode="json"))
