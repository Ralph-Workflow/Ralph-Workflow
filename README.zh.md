# Ralph Workflow

> 主仓库：[codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — Star/Issues/Discussion 请到 Codeberg。

**Ralph Workflow 是“编码代理的自动驾驶仪”（autopilot for coding agents）——**一个免费、开源的 **AI 代理编排器（AI agent orchestrator）**，把你已经在用的编码代理放到你本地机器上跑循环工程（Loop Engineering）。 把规格书交给你的编码代理，离开，回来就能看到可审查、已测试的提交。这是实现 Ralph Loop 模式的 27+ 个独立项目之一（见 [USERS.md](USERS.md)）。新概念？阅读 [什么是 Loop Engineering？](https://ralphworkflow.com/blog/what-is-loop-engineering-2026)

Ralph Workflow 把简单的 Ralph 循环（plan → build → verify）扩展为可组合的 **loop framework**——每个阶段可独立循环、可失败恢复、互相交接。默认工作流足够强，开箱即可用；当你准备好再自定义。

围绕一个简单的 Ralph 循环核心，组合成可扩展的工作流，由你给定的 `PROMPT.md` 规格驱动。

🌐 **[中文](#)** | **[English](README.md)**

![Codeberg stars](https://img.shields.io/codeberg/stars/RalphWorkflow/Ralph-Workflow) ![GitHub stars](https://img.shields.io/github/stars/Ralph-Workflow/Ralph-Workflow) ![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg) ![PyPI downloads](https://img.shields.io/pypi/dm/ralph-workflow.svg) ![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg) ![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)
[![Built with Ralph Loop](assets/built-with-ralph-loop.svg)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)

🌐 **[ralphworkflow.com](https://ralphworkflow.com)** — 完整网站，含对比指南、首次运行指南、博客。

## 快速安装

```bash
pipx install ralph-workflow   # 1. 安装
# 或者
pip install ralph-workflow     # 或 PyPI 安装
```

```bash
cd /path/to/your/project      # 2. 进入你的真实 git 仓库
ralph --init                  # 3. 生成 .agent/ 与 PROMPT.md
ralph --diagnose              # 4. 预检：验证 agent CLI / MCP / 能力是否健康
$EDITOR PROMPT.md             # 5. 编写本次运行的具体任务规格
ralph                         # 6. 在无人值守下运行整个工作流
```

在真实的人工 shell 中执行以上命令，**不要**在任何由 Ralph Workflow 管理的
agent 会话内运行。`ralph --diagnose` 是运行前的预检——它会告诉你当前 agent、
MCP 服务器、能力包是否健康、缺失、不可达、降级或需要修复。
参见 [诊断页](ralph-workflow/docs/sphinx/diagnostics.md) 了解每个检查的意义。

## Loop Engineering 是什么？

Loop Engineering 是一种**为无人值守执行而设计的编码代理模式**：规格 → 自主编码 → 自动验证 → 人类审查。代理在循环中运行，每个循环都基于上一个循环的成果——不是一次性提示，不是人工审查循环。

在 27+ 个独立实现了这一模式的项目中（包括 100+ 真实集成），结论是一致的：**AI 代理在结构化循环中表现更好。**（来源：[ECOSYSTEM.md](ECOSYSTEM.md) 与 [USERS.md](USERS.md)，2026-06）

📖 **中文开发者入门路径：**
1. [什么是 Loop Engineering？](https://ralphworkflow.com/blog/what-is-loop-engineering-2026) — 定义这一类别
2. [5 个真实世界的用例](https://ralphworkflow.com/blog/unattended-ai-coding-agent-real-world-use-cases-2026) — 无人值守 AI 编码的具体应用
3. [30+ 项目的生态系统](https://ralphworkflow.com/blog/ralph-workflow-ecosystem-2026) — 谁在构建 Loop Engineering
4. [生态系统页面](https://ralphworkflow.com/ecosystem/) — 模式总览 + 如何添加你的项目

## 为什么用 Ralph Workflow？

**这不是"更好的 Copilot"。** Copilot、Cursor、Aider、Claude Code 解决的是交互式编码问题——你在旁边审查每一行。Ralph Workflow 解决的是**无人值守执行**问题：你把工作交给它，它自主完成，你回来审查结果。

- **本地优先** — 在你的机器上运行，用你自己的代理
- **代理无关** — Claude Code、Codex、OpenCode、Nanocoder、AGY、Pi.dev
- **免费且开源** — AGPL v3
- **生产可用** — 持续 PyPI 发布与活跃维护（具体数字见仓库内 README 与 ECOSYSTEM，不要在此处逐字复制）

**Agent 鉴权由你自己负责。** Ralph Workflow 不存储、不读取、不代理任何 agent 凭证；每个 agent CLI 用它**自己的原生鉴权**（vendor 登录 或 API key）。详见 [Agent CLI lifecycle](ralph-workflow/docs/sphinx/agents.md)。

## 添加徽章

如果你的项目使用了 Ralph Workflow（或任何 Ralph Loop 实现），可以添加徽章：

```markdown
[![Built with Ralph Loop](https://codeberg.org/RalphWorkflow/Ralph-Workflow/raw/branch/main/assets/built-with-ralph-loop.svg)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
```

## 更多信息

- [完整 English README](README.md)
- [ralphworkflow.com](https://ralphworkflow.com) — 完整网站
- [awesome-ralph](https://github.com/snwfdhmp/awesome-ralph) (904⭐) — Loop Engineering 社区目录
- [Codeberg 仓库](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — Star/Issues/Discussion
