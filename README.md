# Deadline Review Agent

一个面向任务交付的结构化验收 Agent：检查截止时间、解析真实文件、逐项执行确定性规则，并可选使用 LLM 复核规则无法判断的语义标准。

## 输入与输出

输入包括任务标题、截止/提交时间、验收标准、用户提交说明、证据链接、可选 PDF/TXT/Markdown/JSON 文件和评估模式。输出包括时间状态、逐项证据、最终决策、综合置信度、修改建议、文件解析结果、LLM 审计元数据和完整中间步骤，全部可序列化为 JSON。

## 为什么这是 Agent

它围绕“验收任务交付”目标自主编排输入校验、文件解析、标准分类、确定性工具、LLM 路由、语义复核、硬规则保护、决策聚合和审计日志，而不是只生成一段自然语言。

## 架构原则

- 确定性规则负责截止时间、格式、PDF 页数、JSON 合法性、完整文件关键词和证据冲突。
- 成功解析的文件证据优先于用户 `submission_text` 声明。
- LLM 只参与有实际可读材料、规则仍为 `NEEDS_REVIEW` 的语义标准。
- LLM 不能覆盖确定性 `PASS/FAIL`、文件事实或空提交结论。
- 无 API Key 时完整使用规则模式，文件验收和页面均可运行。
- API 超时、认证、限流、拒绝或非法结构会自动保留规则结果并安全回退。
- 证据链接只记录，不联网打开，也不证明链接目标内容。

详细流程见 [系统架构](docs/architecture.md) 和 [验收规则](docs/evaluation.md)。

## 快速运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

页面截图建议放在 `docs/images/app-overview.png`；提交展示材料时可在此处加入：

```markdown
![Deadline Review Agent 页面](docs/images/app-overview.png)
```

## 三种实际评估模式

- `rule_based`：用户选择仅规则，或自动模式下没有完整 LLM 配置。
- `llm_enhanced`：至少一个符合条件的标准成功完成结构化语义复核。
- `fallback_rule_based`：显式计划使用 LLM，但配置缺失、API 失败或输出非法，最终保留规则结果。

页面提供：

- 自动模式：存在 Key 和模型时，仅复核符合条件的 `NEEDS_REVIEW`。
- 仅规则模式：绝不调用外部模型。
- 启用 LLM 增强：尝试语义复核；不可用时安全回退。

## LLM 路由与硬规则保护

只有同时满足以下条件才调用 LLM：

1. 原规则状态为 `NEEDS_REVIEW`；
2. 标准类型为 `MANUAL_REVIEW`，或可语义复核的 `FILE_CONTENT`；
3. 存在足够的实际可读文件文本；
4. 标准不依赖未读取链接或外部事实；
5. 用户未选择 `rule_only`；
6. 已配置 `OPENAI_API_KEY` 和 `OPENAI_MODEL`。

workflow 会保存确定性结果快照。原规则为 `PASS/FAIL` 时不进入 LLM；合并后还会再次恢复锁定结果。因此两页 PDF、错误格式、非法 JSON、完整文件明确缺词等事实不会被模型改写。

LLM 的决定性 `PASS/FAIL` 只有在结构合法、证据和引用片段非空、且自评 confidence 不低于 `0.70` 时才接受。该 confidence 只是模型自评，不是统计学概率。

## 最小化模型输入

`select_relevant_evidence` 使用简单关键词在文件中选取有限上下文：

- 每个文件最多若干片段；
- 总文件证据不超过 6,000 字符；
- 保留文件名、解析状态和截断标记；
- 没有命中时只提供有限开头；
- 不使用 embedding、向量数据库或 Agent 框架。

调用使用官方 OpenAI Python SDK 的 Responses API Structured Outputs，返回内容由 Pydantic 二次校验，并设置 `store=False`。项目没有硬编码唯一真实模型名称，必须通过环境配置选择。

## 环境配置

复制 `.env.example` 中的字段到运行环境：

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_TIMEOUT_SECONDS=20
OPENAI_MAX_RETRIES=1
```

项目不会主动读取或打印 Key。当前不依赖 `python-dotenv`；请通过操作系统、部署平台或启动脚本注入环境变量。真实 `.env` 已被 `.gitignore` 忽略。

## 文件支持与安全限制

支持：

- PDF：pypdf 页数与可提取文本；
- TXT、Markdown：UTF-8、UTF-8-SIG、GB18030；
- JSON：语法验证与格式化文本。

限制：

- 单个文件最大 5 MB；
- 每个文件提取文本最多 20,000 字符；
- 页面文件预览最多 2,000 字符；
- 日志中文件文本最多 1,000 字符预览；
- 不保存原始文件二进制、API Key、完整环境变量、隐私路径或异常堆栈。

## 项目结构

```text
.
├── agent/
│   ├── schemas.py
│   ├── file_tools.py
│   ├── tools.py
│   ├── llm_evaluator.py
│   ├── workflow.py
│   └── prompts.py
├── docs/
│   ├── architecture.md
│   └── evaluation.md
├── examples/
│   ├── sample_input.json
│   ├── sample_output.json
│   ├── llm_review_input.json
│   └── llm_review_output.json
├── tests/
│   ├── test_workflow.py
│   ├── test_file_tools.py
│   └── test_llm_evaluator.py
├── app.py
├── requirements.txt
└── README.md
```

## 示例

- `examples/sample_input.json`：无文件、无 LLM 的保守规则示例。
- `examples/sample_output.json`：仅有用户声明时的 `NEEDS_REVIEW`。
- `examples/llm_review_input.json`：文件硬规则与语义标准混合输入。
- `examples/llm_review_output.json`：展示 `deterministic_rule` 与 `llm_semantic_review`。

## 测试

所有 LLM 测试使用 mock，不访问网络、不产生 API 费用：

```powershell
python -m pytest -q --basetemp=.pytest-temp
```

当前共 65 个测试，覆盖 Day 1、Day 2、Day 2.5，以及无 Key、路由、结构化输出、超时、认证失败、非法状态、低置信度、空证据、硬规则保护、回退模式、日志脱敏、模型输入上限和完整演示输入路由。

## 当前限制

- 不读取网页、GitHub 或证据链接目标内容。
- 不支持 OCR；扫描 PDF 可能只能读取页数。
- 不支持 Word、Excel、图片、压缩包、音视频或云盘 API。
- LLM 输出仍可能有误，只参与规则无法判断的有限语义标准。
- 复杂业务价值、法律、专业质量等判断仍可能需要人工复核。
- overall confidence 和 LLM confidence 都不是统计学概率。
- 不包含认证、数据库、多 Agent、LangChain、LangGraph 或向量数据库。
