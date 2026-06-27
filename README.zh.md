# Ralph Workflow

> 镜像仓库：[codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — Star/Issues/Discussion 请到 Codeberg。

**一个早期的、生产可用的 Loop Engineering 编码代理工具包。** 把规格书交给你的编码代理，离开，回来就能看到可审查、已测试的提交。这是实现 Ralph Loop 模式的 27+ 个独立项目之一（见 [USERS.md](USERS.md)）。新概念？阅读 [什么是 Loop Engineering？](https://ralphworkflow.com/blog/what-is-loop-engineering-2026)

Ralph Workflow 是一个免费、开源的 Loop Engineering 框架，运行你已经有的编码代理——Claude Code、Codex 或 OpenCode——在你的本地机器上。

🌐 **[中文](#)** | **[English](README.md)**

![Codeberg stars](https://img.shields.io/codeberg/stars/RalphWorkflow/Ralph-Workflow) ![GitHub stars](https://img.shields.io/github/stars/Ralph-Workflow/Ralph-Workflow) ![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg) ![PyPI downloads](https://img.shields.io/pypi/dm/ralph-workflow.svg) ![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg) ![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)
[![Built with Ralph Loop](assets/built-with-ralph-loop.svg)](https://github.com/Ralph-Workflow/Ralph-Workflow)

🌐 **[ralphworkflow.com](https://ralphworkflow.com)** — 完整网站，含对比指南、首次运行指南、博客。

## 快速安装

```bash
pipx install ralph-workflow
# 或者
pip install ralph-workflow
```

```bash
# 初始化一个项目
ralph init

# 编写你的 pipeline.toml
# 运行一个循环
ralph run
```

## Loop Engineering 是什么？

Loop Engineering 是一种**为无人值守执行而设计的编码代理模式**：规格 → 自主编码 → 自动验证 → 人类审查。代理在循环中运行，每个循环都基于上一个循环的成果——不是一次性提示，不是人工审查循环。

在 27+ 个独立实现了这一模式的项目中，结论是一致的：**AI 代理在结构化循环中表现更好。**

📖 **中文开发者入门路径：**
1. [什么是 Loop Engineering？](https://ralphworkflow.com/blog/what-is-loop-engineering-2026) — 定义这一类别
2. [5 个真实世界的用例](https://ralphworkflow.com/blog/unattended-ai-coding-agent-real-world-use-cases-2026) — 无人值守 AI 编码的具体应用
3. [30+ 项目的生态系统](https://ralphworkflow.com/blog/ralph-workflow-ecosystem-2026) — 谁在构建 Loop Engineering
4. [生态系统页面](https://ralphworkflow.com/ecosystem/) — 模式总览 + 如何添加你的项目

## 为什么用 Ralph Workflow？

**这不是"更好的 Copilot"。** Copilot、Cursor、Aider、Claude Code 解决的是交互式编码问题——你在旁边审查每一行。Ralph Workflow 解决的是**无人值守执行**问题：你把工作交给它，它自主完成，你回来审查结果。

- **本地优先** — 在你的机器上运行，用你自己的代理
- **代理无关** — Claude Code、Codex、OpenCode、Ollama
- **免费且开源** — AGPL v3
- **生产可用** — 13,230+ PyPI 总下载量，4,911 最近 30 天下载量

## 添加徽章

如果你的项目使用了 Ralph Workflow（或任何 Ralph Loop 实现），可以添加徽章：

```markdown
[![Built with Ralph Loop](https://raw.githubusercontent.com/Ralph-Workflow/Ralph-Workflow/main/assets/built-with-ralph-loop.svg)](https://github.com/Ralph-Workflow/Ralph-Workflow)
```

## 更多信息

- [完整 English README](README.md)
- [ralphworkflow.com](https://ralphworkflow.com) — 完整网站
- [awesome-ralph](https://github.com/snwfdhmp/awesome-ralph) (904⭐) — Loop Engineering 社区目录
- [Codeberg 仓库](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — Star/Issues/Discussion
