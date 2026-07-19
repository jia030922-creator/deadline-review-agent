# Deadline Review Agent

Deadline Review Agent 是一个任务交付验收 Agent。它接收任务标题、截止时间、提交时间、验收标准、提交说明和证据链接，输出逐项验收结果、最终决策、置信度、下一步建议与完整的中间步骤。

Day 1 MVP 默认使用可解释、可重复运行的确定性规则，不需要 API Key，也不依赖 LangChain。它不是通用聊天机器人、Todo List 或文件内容解析器。

## 为什么它是 Agent

它不是一次性的文本分类函数，而是围绕明确目标自主执行一组有顺序的工具步骤：校验输入、判断迟交、解析标准、逐项评估、聚合决策、生成建议、计算置信度、记录过程并保存结果。每一步都有结构化状态和摘要，最终输出也由 Pydantic Schema 约束，因此过程可审计、结果可供其他程序继续使用。

## 核心工作流

1. `validate input`：Pydantic 校验输入类型和必要字段。
2. `check deadline`：输出 `ON_TIME` 或 `LATE`，并计算迟交分钟数。
3. `parse criteria`：去除空白标准和完全重复项，保留原顺序。
4. `evaluate each criterion`：用规则模式逐项输出 `PASS`、`FAIL` 或 `NEEDS_REVIEW`。
5. `aggregate decision`：聚合为 `PASS`、`LATE_PASS`、`NEEDS_REVISION`、`FAIL` 或 `NEEDS_REVIEW`。
6. 生成下一步建议并计算置信度。
7. 记录中间步骤，尽力将输入、过程和输出保存到 `logs/`。
8. 返回可序列化为 JSON 的结构化结果。

## 规则模式

- `PASS`：提交说明中存在与标准直接对应的肯定性文本证据。证据链接可以辅助说明，但链接本身不会让所有标准自动通过。
- `FAIL`：提交说明中出现与该标准相关的明确冲突，例如“未完成”“缺少”“不支持”或 `missing`、`failed`。
- `NEEDS_REVIEW`：没有足够正面证据，也没有明确冲突。关键词不存在不会被直接当成失败。

明确冲突优先于正面表达。Day 1 不访问证据链接，也不读取文件内容，因此仅有链接时会保守地要求人工复核。

决策聚合规则：

- 全部 `PASS` 且按时：`PASS`
- 全部 `PASS` 但迟交：`LATE_PASS`
- 存在明确 `FAIL`：`NEEDS_REVISION`
- 无 `FAIL` 但存在 `NEEDS_REVIEW`：`NEEDS_REVIEW`
- 提交说明和证据均为空等明显空提交：`FAIL`

## 技术栈

- Python 3.10+
- Pydantic 2
- Streamlit
- pytest

## 项目结构

```text
.
├── agent/
│   ├── __init__.py
│   ├── schemas.py
│   ├── tools.py
│   ├── workflow.py
│   └── prompts.py
├── examples/
│   ├── sample_input.json
│   └── sample_output.json
├── logs/
│   └── .gitkeep
├── tests/
│   └── test_workflow.py
├── app.py
├── requirements.txt
└── README.md
```

## 本地运行

在项目根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

浏览器打开 Streamlit 显示的本地地址。页面支持“加载示例”，可直接填充一组可演示数据。

不启动页面时，也可以在 Python 中调用：

```python
import json

from agent.workflow import DeadlineReviewAgent

payload = json.loads(open("examples/sample_input.json", encoding="utf-8").read())
result = DeadlineReviewAgent().run(payload)
print(result.model_dump_json(indent=2))
```

## 示例输入输出

完整示例见：

- `examples/sample_input.json`
- `examples/sample_output.json`

输出包含：

```json
{
  "task_status": "ON_TIME",
  "final_decision": "PASS",
  "confidence": 0.98,
  "criteria_results": [],
  "next_actions": [],
  "intermediate_steps": [],
  "evaluation_mode": "rule_based"
}
```

## 运行测试

```powershell
python -m pytest -q
```

测试覆盖按时通过、迟交通过、明确违反、缺少证据、无效验收标准、标准清理、空提交和日志写入失败兜底。

## 日志与安全

每次运行会尝试在 `logs/` 下生成带 UTC 时间戳的 JSON 文件。日志函数会递归移除名称包含 `api_key`、`token` 或 `secret` 的字段。日志写入失败不会中断主流程，返回结果的最后一个中间步骤会标为 `WARNING`。

## LLM 扩展预留

`agent/prompts.py` 提供 Day 2 语义评估 Prompt 占位，输出保留 `evaluation_mode` 字段。未来可以添加独立 evaluator 并在失败时回退到现有规则工具，不需要引入 LangChain。

## 当前限制

- 规则模式只能分析提交说明文本，不能理解同义表达的全部变化。
- 不访问网页，不验证证据链接内容。
- 不上传或解析文件。
- 不调用真实 LLM API。
- 不含认证、数据库、多 Agent、支付或无关生产力功能。
