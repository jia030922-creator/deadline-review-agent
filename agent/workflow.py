"""Single-agent orchestration for deterministic delivery review."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent import tools
from agent.schemas import (
    CriterionStatus,
    EvaluationMode,
    IntermediateStep,
    ReviewInput,
    ReviewOutput,
)


class DeadlineReviewAgent:
    """Run the Day 1 rule-based review workflow."""

    def __init__(self, log_dir: str | Path = "logs") -> None:
        self.log_dir = Path(log_dir)

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

        results = [
            tools.evaluate_evidence_rule_based(item, data.submission_text, data.evidence_links)
            for item in parsed
        ]
        counts = {status.value: sum(result.status == status for result in results) for status in CriterionStatus}
        steps.append(
            self._step(
                "evaluate_criteria",
                "evaluate_evidence_rule_based",
                "SUCCESS",
                f"逐项评估完成：PASS {counts['PASS']}，FAIL {counts['FAIL']}，NEEDS_REVIEW {counts['NEEDS_REVIEW']}。",
            )
        )

        severely_incomplete = not data.submission_text.strip() and not data.evidence_links
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

        confidence = self._calculate_confidence(results, severely_incomplete)
        steps.append(
            self._step(
                "calculate_confidence",
                "workflow.calculate_confidence",
                "SUCCESS",
                f"规则评估置信度为 {confidence:.2f}。",
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
            evaluation_mode=EvaluationMode.RULE_BASED,
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
    def _calculate_confidence(results: list[Any], severely_incomplete: bool) -> float:
        if severely_incomplete:
            return 0.98
        if not results:
            return 0.0
        determinate = sum(result.status != CriterionStatus.NEEDS_REVIEW for result in results)
        evidence_ratio = sum(bool(result.evidence) for result in results) / len(results)
        return round(min(0.98, 0.55 + 0.35 * determinate / len(results) + 0.08 * evidence_ratio), 2)
