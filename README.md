# Deadline Review Agent

一个用于任务交付验收的 AI Agent 原型。

Deadline Review Agent 接收任务要求、截止时间、验收标准、提交内容和证据链接，按照预设工作流完成时间检查、验收标准解析、证据评估、决策汇总，并返回结构化的验收结果。

项目支持在没有 API Key 的情况下使用规则模式完整运行，并为后续接入 LLM 语义评估预留了扩展接口。

## 项目背景

传统待办工具通常只能记录任务是否完成，却很难进一步回答：

* 任务是否按时提交？
* 提交内容是否满足每一条验收标准？
* 当前证据是否足够？
* 哪些项目需要修改？
* 哪些结果必须由人工复核？

Deadline Review Agent 来自 Deadline Box 的任务交付场景，目标是把“任务已提交”进一步转化为“任务是否可以通过验收”的结构化判断。

## 为什么这是一个 Agent

本项目不是通用聊天机器人。

Deadline Review Agent 具备明确的任务目标和执行流程：

1. 接收任务及交付信息；
2. 调用截止时间检查工具；
3. 将自然语言验收标准解析为检查项；
4. 调用规则评估工具检查提交证据；
5. 汇总各项验收结果；
6. 生成最终决策和修改建议；
7. 保存中间步骤与最终日志；
8. 在无法可靠判断时主动标记人工复核。

Agent 不仅输出自然语言评价，还会返回可审计的结构化结果。

## 核心工作流

```text
任务输入
  ↓
输入校验
  ↓
截止时间检查
  ↓
验收标准解析
  ↓
逐项证据评估
  ↓
最终决策聚合
  ↓
修改建议生成
  ↓
中间步骤与结果日志
```

## 核心能力

* 判断任务是按时提交还是迟交；
* 将多条验收标准转换为结构化检查项；
* 根据提交文本和证据链接进行规则评估；
* 区分 `PASS`、`FAIL` 和 `NEEDS_REVIEW`；
* 汇总生成最终验收结论；
* 输出置信度和下一步修改建议；
* 保存完整中间步骤；
* 在日志写入失败时保证主工作流继续运行；
* 无 API Key 也可以完整运行。

## 状态说明

### 任务时间状态

| 状态        | 含义             |
| --------- | -------------- |
| `ON_TIME` | 在截止时间前或截止时间时提交 |
| `LATE`    | 超过截止时间提交       |

### 单项验收状态

| 状态             | 含义                 |
| -------------- | ------------------ |
| `PASS`         | 当前证据能够直接支持该验收标准    |
| `FAIL`         | 当前提交内容明确违反该验收标准    |
| `NEEDS_REVIEW` | 现有证据不足，需要补充信息或人工复核 |

### 最终验收结论

| 状态               | 含义                  |
| ---------------- | ------------------- |
| `PASS`           | 按时提交，所有验收标准均通过      |
| `LATE_PASS`      | 迟交，但所有验收标准均通过       |
| `NEEDS_REVISION` | 存在明确未满足的验收标准，需要修改   |
| `FAIL`           | 提交内容严重缺失或无法进行有效验收   |
| `NEEDS_REVIEW`   | 没有明确失败项，但部分标准无法自动确认 |

## 技术栈

* Python
* Streamlit
* Pydantic
* pytest

当前版本未使用 LangChain，也不依赖向量数据库。

## 项目结构

```text
deadline-review-agent/
├── README.md
├── AGENTS.md
├── app.py
├── agent/
│   ├── __init__.py
│   ├── workflow.py
│   ├── tools.py
│   ├── schemas.py
│   └── prompts.py
├── examples/
│   ├── sample_input.json
│   └── sample_output.json
├── tests/
│   └── test_workflow.py
├── logs/
│   └── .gitkeep
├── .env.example
├── .gitignore
└── requirements.txt
```

## 本地运行

### 1. 克隆仓库

```bash
git clone <仓库地址>
cd deadline-review-agent
```

### 2. 创建虚拟环境

Windows PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS 或 Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 启动应用

```bash
streamlit run app.py
```

浏览器通常会自动打开：

```text
http://localhost:8501
```

## 运行测试

```bash
pytest -v
```

## 示例输入

```json
{
  "task_title": "完成一版 AI 产品经理简历",
  "due_at": "2026-07-19T22:00:00",
  "submitted_at": "2026-07-19T22:30:00",
  "acceptance_criteria": [
    "必须是 PDF 格式",
    "包含 AI 玩偶、Dify 和 Deadline Box 三个项目",
    "每个项目至少包含一项可量化成果",
    "简历控制在一页"
  ],
  "submission_text": "已完成简历，包含 AI 玩偶、Dify 和 Deadline Box 三个项目，目前是两页，其中 Deadline Box 有 GitHub 链接。",
  "evidence_links": [
    "https://github.com/example/deadline-box"
  ]
}
```

## 示例输出

```json
{
  "task_status": "LATE",
  "final_decision": "NEEDS_REVISION",
  "confidence": 0.88,
  "evaluation_mode": "rule_based",
  "criteria_results": [
    {
      "criterion": "必须是 PDF 格式",
      "status": "NEEDS_REVIEW",
      "evidence": "提交内容未提供可验证的 PDF 文件信息",
      "reason": "现有证据无法确认文件格式",
      "suggested_action": "补充 PDF 文件或可访问的文件链接"
    },
    {
      "criterion": "包含 AI 玩偶、Dify 和 Deadline Box 三个项目",
      "status": "PASS",
      "evidence": "提交文本明确提到了 AI 玩偶、Dify 和 Deadline Box",
      "reason": "三个指定项目名称均出现在提交说明中",
      "suggested_action": ""
    },
    {
      "criterion": "每个项目至少包含一项可量化成果",
      "status": "NEEDS_REVIEW",
      "evidence": "提交说明未列出各项目的量化成果",
      "reason": "现有文本不足以验证每个项目是否包含量化结果",
      "suggested_action": "为每个项目补充至少一项可量化成果"
    },
    {
      "criterion": "简历控制在一页",
      "status": "FAIL",
      "evidence": "提交文本说明当前简历为两页",
      "reason": "提交内容明确违反一页限制",
      "suggested_action": "压缩简历内容，将总页数调整为一页"
    }
  ],
  "next_actions": [
    "将简历压缩至一页",
    "补充 PDF 文件或可访问链接",
    "为每个项目补充至少一项可量化成果"
  ]
}
```

## 规则模式与 LLM 模式

当前版本默认使用规则模式：

```text
evaluation_mode = rule_based
```

规则模式不需要 API Key，可以完成：

* 时间判断；
* 明确关键词和直接证据检查；
* 明确冲突识别；
* 缺失证据识别；
* 决策聚合；
* 修改建议生成。

后续版本将支持可选的 LLM 语义评估模式。

当配置了模型 API Key 时，Agent 可以使用 LLM 处理更复杂的语义验收标准；当 API 调用失败、超时或返回非法结构时，系统将回退到规则模式，并将低置信度项目标记为 `NEEDS_REVIEW`。

## 设计原则

### 1. 不把无法确认的结果强行判为通过

如果当前证据不足，Agent 会返回：

```text
NEEDS_REVIEW
```

而不是猜测任务已经满足要求。

### 2. 规则检查与语义评估分离

确定性规则负责时间、字段、链接和明确冲突检查；LLM 只作为后续可选的语义增强能力。

### 3. 输出可审计

每次验收都会保留：

* 使用的工具；
* 中间步骤；
* 单项判断；
* 判断依据；
* 最终决策；
* 下一步建议。

### 4. 失败不影响主流程

日志保存失败或可选模型调用失败时，Agent 应继续返回可用结果，而不是让整个验收流程崩溃。

## 当前限制

* 规则模式主要依赖提交文本中的直接证据；
* 当前版本不会自动访问证据链接并读取网页内容；
* 当前版本不会解析 PDF、Word 或图片文件；
* 对复杂、隐含或主观的验收标准，可能需要人工复核；
* 置信度是工作流内部的启发式评分，不代表统计学概率；
* LLM 语义评估将在后续版本中完善。

## 后续计划

* 接入可选 LLM 语义评估；
* 增加模型调用超时、重试和结构校验；
* 支持文件内容解析；
* 支持证据链接访问与验证；
* 增加更多边界测试和评估数据集；
* 与 Deadline Box 的任务数据进行集成。

## 项目来源

Deadline Review Agent 来源于个人项目 Deadline Box 的任务交付场景。

Deadline Box 负责创建任务、设置截止时间、记录提交和保存历史；Deadline Review Agent 则专注于对交付内容进行自动化验收和结构化判断。
