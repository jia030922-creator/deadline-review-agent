"""Single-agent orchestration for deterministic delivery review."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent import file_tools, llm_evaluator, tools
from agent.schemas import (
    CriterionEvidenceType,
    CriterionStatus,
    EvaluationMode,
    EvaluatedBy,
    EvidenceSource,
    FileEvidence,
    FileParseStatus,
    IntermediateStep,
    LLMCallMetadata,
    RequestedEvaluationMode,
    ReviewInput,
    ReviewOutput,
)


class DeadlineReviewAgent:
    """Run the backward-compatible rule-based review workflow."""

    def __init__(
        self,
        log_dir: str | Path = "logs",
        *,
        llm_client: Any | None = None,
        api_key: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.llm_client = llm_client
        self.api_key = api_key
        self.llm_model = llm_model

    def run(self, review_input: ReviewInput | dict[str, Any]) -> ReviewOutput:
        steps: list[IntermediateStep] = []

        data = review_input if isinstance(review_input, ReviewInput) else ReviewInput.model_validate(review_input)
        steps.append(self._step("validate_input", "Pydantic", "SUCCESS", "输入字段与类型校验通过。"))

        deadline = tools.check_deadline(data.due_at, data.submitted_at)
        steps.append(
            self._step(
                "check_deadline",
                "check_deadline",
                "SUCCESS",
                f"时间状态为 {deadline.task_status.value}，迟交 {deadline.late_minutes} 分钟。",
            )
        )

        file_summary = self._summarize_files(data.uploaded_files)
        unsuccessful = sum(
            file.parse_status in {FileParseStatus.FAILED, FileParseStatus.UNSUPPORTED}
            for file in data.uploaded_files
        )
        steps.append(
            self._step(
                "file_evidence_parsing",
                "parse_uploaded_file / receive_file_evidence",
                "WARNING" if unsuccessful else "SUCCESS",
                file_summary,
            )
        )

        file_context = file_tools.build_file_evidence_context(data.uploaded_files)
        steps.append(
            self._step(
                "file_evidence_context_building",
                "build_file_evidence_context",
                "SUCCESS",
                f"已按文件边界构建证据上下文，共 {len(file_context)} 个字符。",
            )
        )

        parsed = tools.parse_criteria(data.acceptance_criteria)
        if not parsed:
            raise ValidationError.from_exception_data("ReviewInput", [])
        steps.append(
            self._step(
                "parse_criteria",
                "parse_criteria",
                "SUCCESS",
                f"清理后得到 {len(parsed)} 条唯一验收标准。",
            )
        )

        criterion_types = [tools.classify_criterion_evidence_type(item) for item in parsed]
        type_counts = {
            evidence_type.value: sum(item == evidence_type for item in criterion_types)
            for evidence_type in CriterionEvidenceType
        }
        used_types = "，".join(f"{name} {count}" for name, count in type_counts.items() if count)
        steps.append(
            self._step(
                "classify_criterion_evidence_type",
                "classify_criterion_evidence_type",
                "SUCCESS",
                f"验收标准证据类型分类完成：{used_types}。",
            )
        )

        results = [
            tools.evaluate_evidence_rule_based(
                item,
                data.submission_text,
                data.evidence_links,
                data.uploaded_files,
            )
            for item in parsed
        ]
        for result in results:
            result.evaluated_by = (
                EvaluatedBy.MANUAL_REVIEW_REQUIRED
                if result.status == CriterionStatus.NEEDS_REVIEW
                else EvaluatedBy.DETERMINISTIC_RULE
            )
        counts = {status.value: sum(result.status == status for result in results) for status in CriterionStatus}
        steps.append(
            self._step(
                "evaluate_criteria",
                "evaluate_evidence_rule_based",
                "SUCCESS",
                f"逐项评估完成：PASS {counts['PASS']}，FAIL {counts['FAIL']}，NEEDS_REVIEW {counts['NEEDS_REVIEW']}。",
            )
        )
        conflicts = [result.criterion for result in results if result.conflict_detected]
        steps.append(
            self._step(
                "evidence_conflict_detection",
                "criterion evidence comparison",
                "WARNING" if conflicts else "SUCCESS",
                f"发现 {len(conflicts)} 项用户声明与文件证据冲突：{'；'.join(conflicts)}。"
                if conflicts
                else "未发现用户声明与成功解析文件之间的证据冲突。",
            )
        )

        resolved_api_key = self.api_key if self.api_key is not None else os.getenv("OPENAI_API_KEY", "")
        resolved_model = llm_evaluator.configured_model(self.llm_model)
        llm_available = llm_evaluator.is_llm_available(resolved_api_key, resolved_model)
        routed_indices = [
            index
            for index, (item, evidence_type, result) in enumerate(zip(parsed, criterion_types, results))
            if llm_evaluator.should_use_llm_for_criterion(
                item.criterion,
                evidence_type,
                result,
                data.uploaded_files,
                data.requested_evaluation_mode,
                api_key=resolved_api_key,
                model=resolved_model,
            )
        ]
        deterministic_count = len(results) - len(routed_indices)
        steps.append(
            self._step(
                "llm_routing",
                "should_use_llm_for_criterion",
                "SUCCESS",
                f"{len(results)} 条标准中，{deterministic_count} 条保留确定性/人工规则结果，"
                f"{len(routed_indices)} 条进入可选 LLM 语义复核。",
            )
        )

        protected_results = [result.model_copy(deep=True) for result in results]
        llm_attempts = 0
        llm_successes = 0
        fallback_errors: list[str] = []
        for index in routed_indices:
            llm_attempts += 1
            original = protected_results[index]
            excerpts = llm_evaluator.select_relevant_evidence(
                parsed[index].criterion,
                data.uploaded_files,
            )
            safe_result = llm_evaluator.safe_llm_evaluate(
                criterion=parsed[index].criterion,
                submission_text=data.submission_text,
                relevant_file_excerpts=excerpts,
                file_metadata_summary=llm_evaluator.build_file_metadata_summary(data.uploaded_files),
                deterministic_findings=(
                    f"规则状态={original.status.value}；规则理由={original.reason}；"
                    "原规则 PASS/FAIL 不允许改写；当前仅复核 NEEDS_REVIEW。"
                ),
                api_key=resolved_api_key,
                model=resolved_model,
                client=self.llm_client,
            )
            metadata = LLMCallMetadata(
                attempted=True,
                success=safe_result.success,
                model=safe_result.model,
                latency_ms=safe_result.latency_ms,
                error_type=safe_result.error_type,
                safe_error_message=safe_result.safe_error_message,
                fallback_used=not safe_result.success,
            )
            results[index].rule_status_before_llm = original.status
            results[index].llm_metadata = metadata
            if not safe_result.success or safe_result.result is None:
                results[index].evaluated_by = EvaluatedBy.MANUAL_REVIEW_REQUIRED
                fallback_errors.append(safe_result.error_type or "unknown_error")
                continue

            llm_successes += 1
            semantic = safe_result.result
            results[index].llm_evaluation = semantic
            can_accept_decisive = (
                semantic.status in {CriterionStatus.PASS, CriterionStatus.FAIL}
                and semantic.confidence >= llm_evaluator.LLM_ACCEPTANCE_CONFIDENCE
                and bool(semantic.evidence)
                and bool(semantic.evidence_excerpt.strip())
            )
            results[index].evaluated_by = EvaluatedBy.LLM_SEMANTIC_REVIEW
            results[index].evidence_source = EvidenceSource.FILE_CONTENT
            results[index].source_files = [
                file.filename
                for file in data.uploaded_files
                if file.parse_status in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL}
                and file.extracted_text
            ]
            results[index].evidence = [
                *semantic.evidence,
                f"LLM 引用片段：{semantic.evidence_excerpt[:500]}",
            ]
            results[index].reason = semantic.reason
            results[index].suggested_action = semantic.suggested_action
            if semantic.status == CriterionStatus.NEEDS_REVIEW or not can_accept_decisive:
                results[index].status = CriterionStatus.NEEDS_REVIEW
                if semantic.status != CriterionStatus.NEEDS_REVIEW:
                    results[index].reason = (
                        f"LLM 结果未达到接受门槛（confidence={semantic.confidence:.2f}，"
                        "或缺少具体证据），保持 NEEDS_REVIEW。"
                    )
            else:
                results[index].status = semantic.status

        steps.append(
            self._step(
                "llm_semantic_evaluation",
                "safe_llm_evaluate / Responses API",
                "SUCCESS" if llm_attempts == llm_successes else ("WARNING" if llm_attempts else "SKIPPED"),
                f"实际尝试 {llm_attempts} 次 LLM 复核，结构化成功 {llm_successes} 次。"
                if llm_attempts
                else "本次没有符合路由条件的标准，未调用 LLM。",
            )
        )

        explicit_unavailable = (
            data.requested_evaluation_mode == RequestedEvaluationMode.LLM_ENABLED
            and not llm_available
        )
        fallback_used = bool(fallback_errors or explicit_unavailable)
        if explicit_unavailable:
            fallback_errors.append("missing_llm_configuration")
        steps.append(
            self._step(
                "fallback_handling",
                "safe_llm_evaluate",
                "WARNING" if fallback_used else "SUCCESS",
                f"发生安全回退：{len(fallback_errors)} 项，错误类型：{'、'.join(fallback_errors)}。"
                if fallback_used
                else "未发生 LLM 失败回退。",
            )
        )

        locked_count = 0
        for index, original in enumerate(protected_results):
            if original.status in {CriterionStatus.PASS, CriterionStatus.FAIL}:
                locked_count += 1
                results[index] = original
        steps.append(
            self._step(
                "deterministic_result_protection",
                "workflow deterministic lock",
                "SUCCESS",
                f"已锁定 {locked_count} 条确定性 PASS/FAIL；LLM 未被允许覆盖硬规则或文件事实。",
            )
        )

        if llm_successes:
            actual_evaluation_mode = EvaluationMode.LLM_ENHANCED
        elif fallback_used:
            actual_evaluation_mode = EvaluationMode.FALLBACK_RULE_BASED
        else:
            actual_evaluation_mode = EvaluationMode.RULE_BASED

        severely_incomplete = (
            not data.submission_text.strip()
            and not data.evidence_links
            and not data.uploaded_files
        )
        decision = tools.aggregate_decision(
            deadline.task_status,
            results,
            severely_incomplete=severely_incomplete,
        )
        steps.append(
            self._step("aggregate_decision", "aggregate_decision", "SUCCESS", f"最终决策为 {decision.value}。")
        )

        next_actions = self._generate_next_actions(results, deadline.late_minutes)
        steps.append(
            self._step(
                "generate_next_actions",
                "workflow.generate_next_actions",
                "SUCCESS",
                f"生成 {len(next_actions)} 条下一步建议。",
            )
        )

        confidence = self._calculate_confidence(
            results,
            severely_incomplete,
            data.uploaded_files,
            llm_attempts=llm_attempts,
            llm_successes=llm_successes,
            fallback_used=fallback_used,
        )
        steps.append(
            self._step(
                "calculate_confidence",
                "workflow.calculate_confidence",
                "SUCCESS",
                f"综合确定性结果、文件完整性、LLM 参与和回退情况，置信度为 {confidence:.2f}。",
            )
        )
        steps.append(
            self._step(
                "record_intermediate_steps",
                "workflow",
                "SUCCESS",
                "已记录结构化中间步骤。",
            )
        )

        output = ReviewOutput(
            task_status=deadline.task_status,
            final_decision=decision,
            confidence=confidence,
            criteria_results=results,
            next_actions=next_actions,
            intermediate_steps=steps,
            evaluation_mode=actual_evaluation_mode,
            requested_evaluation_mode=data.requested_evaluation_mode,
            llm_available=llm_available,
            llm_model=resolved_model,
            llm_fallback_used=fallback_used,
            file_evidence_results=data.uploaded_files,
            file_evidence_summary=file_summary,
        )
        log_path = tools.log_result(
            data.model_dump(mode="json"),
            [step.model_dump(mode="json") for step in steps],
            output.model_dump(mode="json"),
            self.log_dir,
        )
        output.intermediate_steps.append(
            self._step(
                "log_final_result",
                "log_result",
                "SUCCESS" if log_path else "WARNING",
                f"结果已保存至 {log_path}。" if log_path else "日志写入失败，但验收结果已正常返回。",
            )
        )
        return output

    @staticmethod
    def _step(step_name: str, tool: str, status: str, summary: str) -> IntermediateStep:
        return IntermediateStep(step_name=step_name, tool=tool, status=status, summary=summary)

    @staticmethod
    def _generate_next_actions(results: list[Any], late_minutes: int) -> list[str]:
        actions = [result.suggested_action for result in results if result.status != CriterionStatus.PASS]
        if late_minutes > 0:
            actions.append(f"本次提交迟交 {late_minutes} 分钟；请记录原因并改进后续时间安排。")
        if not actions:
            actions.append("所有验收标准均已通过，无需修改；请归档交付证据。")
        return list(dict.fromkeys(actions))

    @staticmethod
    def _calculate_confidence(
        results: list[Any],
        severely_incomplete: bool,
        uploaded_files: list[FileEvidence] | None = None,
        *,
        llm_attempts: int = 0,
        llm_successes: int = 0,
        fallback_used: bool = False,
    ) -> float:
        if severely_incomplete:
            return 0.98
        if not results:
            return 0.0
        determinate = sum(result.status != CriterionStatus.NEEDS_REVIEW for result in results)
        evidence_ratio = sum(bool(result.evidence) for result in results) / len(results)
        parsed_file_bonus = 0.0
        if uploaded_files and any(
            file.parse_status in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL}
            for file in uploaded_files
        ):
            parsed_file_bonus = 0.02
        deterministic_ratio = sum(
            result.evaluated_by == EvaluatedBy.DETERMINISTIC_RULE for result in results
        ) / len(results)
        llm_ratio = llm_successes / len(results)
        conflict_penalty = 0.04 if any(result.conflict_detected for result in results) else 0.0
        fallback_penalty = 0.08 if fallback_used else 0.0
        unused_attempt_penalty = 0.02 if llm_attempts > llm_successes and not fallback_used else 0.0
        score = (
            0.48
            + 0.32 * determinate / len(results)
            + 0.08 * evidence_ratio
            + 0.06 * deterministic_ratio
            + parsed_file_bonus
            - 0.04 * llm_ratio
            - fallback_penalty
            - conflict_penalty
            - unused_attempt_penalty
        )
        return round(max(0.0, min(0.98, score)), 2)

    @staticmethod
    def _summarize_files(files: list[FileEvidence]) -> str:
        if not files:
            return "未上传文件证据；继续使用提交说明和证据链接进行验收。"
        usable = sum(
            file.parse_status in {FileParseStatus.SUCCESS, FileParseStatus.PARTIAL}
            for file in files
        )
        failed = sum(file.parse_status == FileParseStatus.FAILED for file in files)
        unsupported = sum(file.parse_status == FileParseStatus.UNSUPPORTED for file in files)
        partial = sum(file.parse_status == FileParseStatus.PARTIAL for file in files)
        pdf_pages = sum(file.page_count or 0 for file in files if file.extension == ".pdf")
        text_chars = sum(len(file.extracted_text) for file in files)
        valid_json = sum(
            file.extension == ".json" and file.parse_status == FileParseStatus.SUCCESS
            for file in files
        )
        return (
            f"收到 {len(files)} 个文件：可用 {usable} 个（其中部分解析 {partial} 个），"
            f"失败 {failed} 个，不支持 {unsupported} 个；PDF 共 {pdf_pages} 页，"
            f"提取文本共 {text_chars} 个字符，合法 JSON {valid_json} 个。"
        )
