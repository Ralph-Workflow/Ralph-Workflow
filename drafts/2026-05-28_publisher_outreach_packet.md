# Publisher Outreach Packet — May 28, 2026
Generated: 2026-05-28T21:52 CEST
Source: publisher_discovery_lane.py (fresh run, 50 results, 437 saturated domains filtered)
Status: QUEUED — SMTP unavailable from this environment, ready for manual send or SMTP-enabled env

## Target 1: GetStream.io — "9 Best AI Orchestration Tools in 2026"
**URL:** https://getstream.io/blog/best-ai-orchestration-tools/
**Score:** 7 (comparison + orchestration + guide)
**Why:** High-authority comparison page covering LangGraph, CrewAI, AutoGen, Agent Squad, Haystack — no open-source loop composer in the list. Ralph Workflow fills a gap (workflow-layer orchestration vs. framework orchestration).
**Contact approach:** Stream has a known developer blog with an editorial team; contact via blog author or Stream's developer relations.

### Email body
```
Subject: Ralph Workflow — free open-source loop composer missing from your orchestration comparison

Hi,

I read your "9 Best AI Orchestration Tools in 2026" guide — great coverage of
LangGraph, CrewAI, AutoGen, and the framework layer.

One category that's missing from most comparisons is the workflow composer layer:
tools that wrap existing coding agents (Claude Code, Codex CLI, etc.) into a
repo-native orchestration loop, instead of requiring you to build the workflow
yourself on top of a general framework.

Ralph Workflow is a free open-source tool in exactly this space:
- Plan → Build → Verify → Repeat loop with strong defaults
- Runs your existing agents on your own machine
- Ends in finished, tested code and a bounded review surface
- Free and open source: https://codeberg.org/RalphWorkflow/Ralph-Workflow

Would you consider adding it to your comparison? It's a natural complement to
the framework tools you already cover — different layer, same audience.

Happy to answer any questions.

Best,
[Your name]
```

## Target 2: OpenAgents.org — "10 Best AI Coding Agents in 2026"
**URL:** https://openagents.org/blog/posts/2026-05-21-best-ai-coding-agents
**Score:** 6 (comparison + review + guide + coding)
**Why:** Comprehensive comparison covering Claude Code, Codex CLI, Aider, Cursor, Windsurf, Goose, Gemini CLI, Amazon Q, Cline, and OpenAgents Launcher. Mentions multi-agent but only in context of their own launcher. Ralph Workflow's loop-composer approach is adjacent but different (orchestrates the agents they compare, rather than launching them).
**Contact approach:** This is the OpenAgents blog — they have a product (OpenAgents Launcher). Could be a partnership or at minimum a citation opportunity on the comparison page.

### Email body
```
Subject: Ralph Workflow — the workflow layer your comparison page is dancing around

Hi OpenAgents team,

Your "10 Best AI Coding Agents in 2026" comparison is the most thorough I've seen.
Especially the multi-agent column in the comparison table — you're the only overview
that tracks that dimension.

One thing that's missing from the comparison landscape: once you've picked your
coding agent(s), how do you actually structure a complete overnight run so it
ends in something reviewable? That's the layer between "pick an agent" and
"get a finished PR" — and it's where Ralph Workflow sits.

Ralph Workflow wraps any coding agent (Claude Code, Codex, Aider — all the ones
you cover) in a plan→build→verify loop with strong defaults. Free, open source,
runs locally on your machine.

Repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
Walkthrough: https://ralphworkflow.com/blog/real-task-walkthrough-overnight-refactoring

I think it'd make a strong addition to your comparison as the workflow composer
layer above the individual agents you already track. Happy to provide a short
description or answer questions.

Best,
[Your name]
```

## Target 3: AppIntent.com — "The 11 Best Agentic Orchestration Platforms for 2026"
**URL:** https://www.appintent.com/software/ai/agentic-orchestration/
**Score:** 5 (comparison + review + orchestration)
**Why:** Covers CrewAI, Superagent, Fixie.ai, FlowiseAI, AgentGPT, LlamaIndex, SuperAGI, Google Vertex, AutoGen, Bedrock Agents, LangChain. All are platforms/frameworks. None are lightweight loop composers that run locally with your existing agents. Ralph Workflow fills a clear gap.
**Contact approach:** AppIntent is a software review/comparison site. They have a contact form or editorial submission path.

### Email body
```
Subject: Ralph Workflow — lightweight loop composer missing from your orchestration platforms guide

Hi AppIntent team,

Your "11 Best Agentic Orchestration Platforms for 2026" comparison is comprehensive,
but it focuses entirely on the platform/framework layer — CrewAI, AutoGen, LangChain, etc.

There's a lighter category that deserves a spot: loop composers. These aren't
platforms you build on — they're tools you install and run today with your existing
agents. Ralph Workflow is the open-source leader in this space:

- pip install ralph-workflow → ralph init → ralph
- Wraps your existing coding agents (Claude Code, Codex CLI, etc.)
- Plan → Build → Verify → Repeat loop with strong defaults
- Ends in finished, tested code and a bounded review surface
- Free and open source: https://codeberg.org/RalphWorkflow/Ralph-Workflow

It's a different category than the platforms you cover — much closer to "pick it up
and run an overnight task" than "build your multi-agent system on this framework."

Would you consider adding it as a distinct category entry? Happy to provide
a description, screenshots, or any other material you'd need.

Best,
[Your name]
```

## Status
- SMTP unavailable from this environment → queued, not sent
- All 3 targets are fresh (zero mentions in outreach-log.md)
- All 3 are comparison articles that already cite competitors — high citation-fit
- When SMTP becomes available, send via `send_curator_email.py` or equivalent
