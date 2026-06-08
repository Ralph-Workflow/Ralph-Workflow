# Ralph Workflow Positioning

_Last updated: 2026-06-07_

## Naming

- **Ralph Workflow** — the product. Full name, always capitalized.
- **`ralph`** (monospace, lowercase) — the CLI command. If you're talking about running the command, use `ralph`. If you're talking about the product, use "Ralph Workflow."
- **Ralph-loop** — the simple loop concept (plan → code → review → fix → verdict) that Ralph Workflow is built on. Rarely used in external facing copy.
- Don't use "Ralph" as standalone shorthand for Ralph Workflow. It's imprecise.

## Core Positioning

**Ralph Workflow is a simple loop that automates the multi-agent handoffs for coding.**

Install it, write your feature in PROMPT.md, run `ralph`, and the loop handles planning → implementation → review → fixes → verdict automatically. No copy-paste between agents.

## The One Sentence

You write the feature in PROMPT.md. Ralph Workflow runs it through the multi-agent loop — planning, implementation, review, fixes, and a final verdict — without you typing the same prompt into three different agents.

## Positioning vs Competitors

### vs General-Purpose Orchestrators (Hermes Agent, Conductor, etc.)
- **They give you a platform to *build* a coding workflow. Ralph Workflow gives you a default loop that just works.**
- Hermes is a general-purpose orchestrator — flexible but you configure it. Ralph Workflow has a simple default loop for coding.
- With Ralph Workflow: `pip install` → `PROMPT.md` → `ralph` gives you plan→code→review→fix→verdict. With them, you build that pipeline first.

### vs Interactive Coding Agents (Claude Code, Codex, OpenCode)
- **They're the agents you drive. Ralph Workflow is the loop that drives them.**
- Ralph Workflow orchestrates these agents — planning (cheap model), implementation (strong model), review (back to Claude Code).

### vs Manual Multi-Agent Usage
- **"Why type the same prompt 3 times?"**
- Manual: prompt Claude Code to plan → copy plan → paste into OpenCode to code → copy diff → paste into Claude Code to review → copy feedback → paste back into OpenCode to fix → loop.
- Ralph Workflow: write `PROMPT.md` once, run `ralph`, walk away. The automated handoffs are the default.

### vs Aider, Cursor, Continue
- **They're interactive pair-programming tools you steer. Ralph Workflow is for unattended runs you start and leave.**
- Aider/Cursor/Continue: interactive loop. Ralph Workflow: overnight run.

## The Default Loop

| Phase | What Happens |
|---|---|
| **Plan** | Analyzes the task before writing code (cheap model) |
| **Code** | Executes the plan in a capable coding agent |
| **Review** | Reads the diff, checks for bugs, edge cases, test gaps |
| **Fix loop** | Addresses review feedback automatically |
| **Verdict** | Produces diff + test results + human-judgment items |

All built-in. All default behavior of `ralph`.

## Key Marketing Angles

1. **"One prompt, not three."** — You write the feature once, Ralph Workflow handles agent handoffs.
2. **"Batteries included, not assembled."** — Ralph Workflow vs platforms that make you build your own pipeline.
3. **"Walk away."** — Start `ralph`, go do something else, come back to a verdict.
4. **"Simple is the point."** — It's a loop. That's enough.

## What Ralph Workflow Is NOT

- Not a general AI assistant
- Not a platform for building arbitrary agent workflows
- Not an interactive pair-programming tool
- Not a replacement for Claude Code or OpenCode — it orchestrates them
