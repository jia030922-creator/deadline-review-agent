# Deadline Review Agent 开发说明

## 项目目标

本项目是一个任务交付验收 Agent。

Agent 接收任务标题、截止时间、提交时间、验收标准、提交说明和证据链接，依次执行：

1. 校验输入数据；
2. 判断任务是否迟交；
3. 将验收标准转化为逐项检查；
4. 使用确定性规则评估提交证据；
5. 汇总每项验收结果；
6. 生成最终结论、置信度和下一步修改建议；
7. 保存中间步骤和最终结果。

## Agent 输入

- task_title
- due_at
- submitted_at
- acceptance_criteria
- submission_text
- evidence_links

## Agent 输出

- task_status
- criteria_results
- final_decision
- confidence
- next_actions
- intermediate_steps
- evaluation_mode

## 状态枚举

任务时间状态：

- ON_TIME
- LATE

单项验收状态：

- PASS
- FAIL
- NEEDS_REVIEW

最终验收结论：

- PASS
- LATE_PASS
- NEEDS_REVISION
- FAIL
- NEEDS_REVIEW

## Day 1 开发范围

必须完成：

- Pydantic 输入输出 Schema
- 截止时间检查工具
- 验收标准解析工具
- 规则模式证据评估工具
- 决策聚合工具
- JSON 日志工具
- Agent workflow
- Streamlit 页面
- 示例输入输出
- 至少 5 个 pytest 测试
- 无 API Key 可运行

可以预留但不必完整实现：

- LLM 语义评估接口
- OPENAI_API_KEY 检测
- API 失败后回退规则模式

不要开发：

- 通用聊天机器人
- 多 Agent 系统
- 用户登录
- 数据库
- 支付功能
- Todo List
- 网页搜索
- 文件上传和文件内容解析
- 与任务验收无关的生产力功能

## 开发优先级

工作流能运行
> 结构化输出正确
> 测试通过
> 失败兜底
> README
> UI 美化

## 编码要求

- 使用 Python 3.10+
- 使用 Pydantic 定义输入输出
- 工具函数尽量保持纯函数
- 所有状态使用明确枚举或 Literal
- 不允许仅返回一段自然语言
- 输出必须可以转换为 JSON
- 缺少证据时不得擅自判定 PASS
- 无法确认的项目应标记为 NEEDS_REVIEW
- 日志中不得保存 API Key
