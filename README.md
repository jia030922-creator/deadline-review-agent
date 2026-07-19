# Deadline Review Agent

Deadline Review Agent 是一个任务交付验收 Agent。它接收任务标题、截止时间、提交时间、验收标准、提交说明、证据链接和可选交付文件，输出逐项验收结果、最终决策、置信度、下一步建议、文件解析摘要与完整中间步骤。

Day 2 在保留 Day 1 规则验收能力的基础上，增加真实文件证据解析；Day 2.5 进一步修复了证据优先级。项目默认使用可解释、可重复的确定性规则，无需 API Key，不依赖 LangChain，也不是通用聊天机器人或 Todo List。

## 为什么它是 Agent

系统围绕“判断交付是否满足验收标准”这一目标，自主执行一组有顺序的工具步骤：校验输入、检查截止时间、接收文件解析结果、构建文件证据上下文、解析验收标准、逐项评估、聚合决策、生成建议、计算置信度并安全记录结果。每一步和最终输出均为结构化数据，过程可审计、结果可供程序继续处理。

## Day 2 文件上传功能

Streamlit 页面支持一次上传多个文件。当前支持：

- PDF（`.pdf`）：读取页数，并在 pypdf 能够提取时读取文本；
- TXT（`.txt`）：支持 UTF-8、UTF-8-SIG 和 GB18030 解码；
- Markdown（`.md`）：按文本文件解析；
- JSON（`.json`）：验证语法并转换为格式化文本。

暂不支持 Word、Excel、图片 OCR、压缩包、音视频、网页抓取、GitHub API 和云盘文件。不支持的类型在解析工具层返回 `UNSUPPORTED`，不会导致整个 workflow 崩溃。

证据链接字段仍然保留，但只是链接记录。Agent 不会联网打开链接，也不会把链接内容当作已经读取。

## 文件证据工作流

1. Streamlit 读取上传文件的文件名、MIME 类型和字节。
2. `parse_uploaded_file` 检查文件大小和扩展名，并调用对应解析器。
3. 原始字节被转换为 Pydantic `FileEvidence`；二进制不会进入 Agent 输入、输出或日志。
4. workflow 统计解析状态、PDF 页数、文本字符数和合法 JSON 数量。
5. `build_file_evidence_context` 按 `[文件 1]`、`[文件 2]` 边界组织元数据和已提取文本。
6. `classify_criterion_evidence_type` 先把标准分类为文件格式、页数、文件内容、JSON、链接、提交声明或人工复核。
7. 文件可验证标准优先检查实际文件格式、页数、JSON 有效性和明确必需关键词；只有文件无法验证时才参考用户声明，且通常为 `NEEDS_REVIEW`。
8. 不属于文件要求的提交说明型标准继续使用 Day 1 的直接声明规则。
9. 聚合最终决策、生成建议、计算置信度并尽力写入安全日志。

单个文件解析失败不会阻止其他文件或主 workflow。失败、加密、损坏或不支持的文件会明确保留状态和简短错误，但不会伪装成已读取成功。

## 文件证据优先原则

`submission_text` 是用户声明，不等于真实文件证据。证据优先级为：成功解析的文件内容、文件结构化元数据、用户提交说明、证据链接记录。成功解析的真实文件证据优先于用户自述。例如：

- 验收标准：`简历控制在一页`
- 提交说明：`简历已经控制在一页。`
- 实际上传的 `resume.pdf`：pypdf 读取为 2 页
- 单项结果：`FAIL`
- 理由：用户说明与文件证据冲突，以实际文件解析结果为准
- 最终结果：通常为 `NEEDS_REVISION`

如果 PDF 无法读取页数，规则不会猜测，而是返回 `NEEDS_REVIEW`。

对文件内容要求也采用同一原则：完整成功解析的文件明确缺少必需关键词时才可 `FAIL`；`PARTIAL`、`FAILED`、`UNSUPPORTED` 或文本已截断且未命中时一律为 `NEEDS_REVIEW`。没有上传文件时，即使用户声明“已经包含 Dify”，也只能得到 `NEEDS_REVIEW`。证据链接不会被打开，链接文字中出现关键词不能证明文件内容。

## 状态与规则边界

- `PASS`：真实文件足以确定满足文件标准，或标准本身明确要求填写提交说明且声明可直接核验。
- `FAIL`：真实文件明确违反标准，或提交说明存在明确冲突。
- `NEEDS_REVIEW`：证据不足、解析失败，或标准涉及主观与复杂语义。

规则模式支持以下有限、可解释判断：

- 明确要求 PDF；
- 一页、最多一页、不超过两页等简单 PDF 页数限制；
- 合法 JSON；
- “包含 Deadline Box”“必须出现安装步骤”等明确内容关键词；
- Day 1 的直接完成或明确未完成陈述。

创新性、商业价值、专业程度、优秀代码质量等标准不会被假装自动判断，而是进入 `NEEDS_REVIEW`。详细规则见 [`docs/evaluation.md`](docs/evaluation.md)。

## 安全限制

- 单个文件最大 5 MB；超限返回 `FAILED`。
- 每个文件最多保留 20,000 字符提取文本，超出时设置 `text_truncated=true`。
- 页面预览最多显示 2,000 字符。
- JSON 日志中的每个文件文本最多保存 1,000 字符预览。
- 原始二进制永不写入日志。
- 日志递归移除名称包含 `api_key`、`token` 或 `secret` 的字段。
- 解析错误只保留简短信息，不保存堆栈。
- 日志失败不会导致验收失败。

## 技术栈

- Python 3.10+
- Pydantic 2
- Streamlit
- pypdf
- pytest

## 项目结构

```text
.
├── agent/
│   ├── __init__.py
│   ├── schemas.py
│   ├── file_tools.py
│   ├── tools.py
│   ├── workflow.py
│   └── prompts.py
├── docs/
│   └── evaluation.md
├── examples/
│   ├── sample_input.json
│   └── sample_output.json
├── logs/
│   └── .gitkeep
├── tests/
│   ├── test_workflow.py
│   └── test_file_tools.py
├── app.py
├── requirements.txt
└── README.md
```

## 本地运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

页面提供“加载示例”，并保留任务标题、截止时间、提交时间、验收标准、提交内容和证据链接字段。选择文件并点击“开始验收”后，会显示文件名、类型、大小、解析状态、PDF 页数、文本预览、文件摘要和完整 JSON。

代码调用方式仍兼容 Day 1：

```python
import json

from agent.workflow import DeadlineReviewAgent

with open("examples/sample_input.json", encoding="utf-8") as file:
    payload = json.load(file)

result = DeadlineReviewAgent().run(payload)
print(result.model_dump_json(indent=2))
```

`uploaded_files` 是可选字段，默认空列表。页面会先把上传内容解析成 `FileEvidence`，不会把 Streamlit `UploadedFile` 对象传入 workflow。

## 测试

```powershell
python -m pytest -q
```

当前共 44 个测试，实际结果为 `44 passed`。测试包含原有 Day 1 场景，以及 TXT、Markdown、JSON、PDF 页数、格式要求、文件关键词、证据优先级、用户声明冲突、部分解析、损坏文件、不支持类型、文件大小、文本截断和未读取链接等场景。

## 当前限制

- PDF 文本提取依赖 pypdf，不包含 OCR；扫描型 PDF 通常只能核对页数。
- 不支持 Word、Excel、图片、压缩包、音视频和文件内嵌对象解析。
- 不联网读取证据链接、网页、GitHub 或云盘内容。
- 关键词规则只处理明确、有限的内容要求，不理解任意复杂自然语言。
- 不调用真实 LLM API；`agent/prompts.py` 只保留未来语义评估接口占位。
- 不含认证、数据库、多 Agent、支付或无关生产力功能。
