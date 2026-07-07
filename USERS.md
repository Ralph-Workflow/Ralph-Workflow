<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: this file is the single canonical community surface for
    Ralph Workflow. ECOSYSTEM.md, SHOWCASE.md, and CREDIT_TEMPLATE.md
    were merged into USERS.md and deleted as part of the docs cleanup;
    the 28 SEO-positioning sphinx pages were deleted in the same pass.
  - Why it belongs here: USERS.md is the only reader-facing surface that
    catalogs pattern implementations, active adopters, the shadow
    ecosystem, and the Geoffrey Huntley attribution. Folding three
    near-duplicate files into one canonical surface removes the
    social-proof sprawl the rubric calls out.
  - What was pruned, merged, or explicitly left alone: the Featured
    projects section was preserved at the top; the 3 unique Active
    community projects entries from ECOSYSTEM.md (Th0rgal, AnandChowdhary,
    vercel-labs) were merged into the Pattern implementations table; the
    "Want to be listed" + CREDIT_TEMPLATE badge markdown were merged into
    a single "How to add your project" section. No project rows were
    lost; the credit count for each featured entry is 0 per the project's
    honesty marker.
  - How duplication was reduced or contained: four near-duplicate
    top-level files (USERS.md, ECOSYSTEM.md, SHOWCASE.md, CREDIT_TEMPLATE.md)
    are now one file. External repo links carry the (verify: repo-exists)
    annotation; star counts were removed in favor of neutral descriptions.
  - How the route is clearer now than before: a reader evaluating the
    tool can find every pattern implementation, active adopter, and
    shadow-ecosystem signal in one place, with a single "How to add
    your project" section at the bottom. There is no longer an
    authoritative-feeling duplicate list to second-guess.
-->

# Who's Using Ralph Loop?

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition. **Hand it a well-specified coding task, let the
agents plan, build, verify, and fix, and come back to reviewable, tested
work.** The default workflow is strong enough to adopt as-is before you
customize anything.

The Ralph Loop pattern — plan → build → verify, iterated by an AI coding
agent until tests pass — is used by a growing ecosystem of practitioners,
tool builders, and enterprises. This page tracks the community: pattern
implementations, tool integrations, and active adopters.

> **Using Ralph Loop or Ralph Workflow?** Add yourself by [opening an
> issue](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues) or
> submitting a PR. Grab the
> [Built with Ralph Loop](assets/built-with-ralph-loop.svg) badge for
> your README (see [How to add your project](#how-to-add-your-project)).

---

## Featured projects

These projects ship with the Ralph Loop pattern. Each one self-attests
plan-build-verify agent cycles and represents real software built with
the pattern. Verified entries carry a `(verify: repo-exists)` annotation
matching the public social-proof convention; the credit count is `0` for
each featured entry below per the project's honesty marker (see
[CLAIMS_LEDGER.md](docs/CLAIMS_LEDGER.md) §SC2).

| Project | What was built |
|---------|----------------|
| [umputun/ralphex](https://github.com/umputun/ralphex) (verify: repo-exists) | Multi-provider LLM loop — plan, build, verify across OpenAI, Anthropic, Ollama. The largest Ralph Loop implementation outside the reference toolkit. |
| [bastani-inc/atomic](https://github.com/bastani-inc/atomic) (verify: repo-exists) | Dynamic workflows with Pi extensions, custom models, MCP, sub-agents, artifacts, review gates, and mid-run steering. |
| [computerlovetech/ralphify](https://github.com/computerlovetech/ralphify) (verify: repo-exists) | Runtime for loop engineering — practitioner cookbook with Claude Code patterns. |
| [benikigai/nightshift](https://github.com/benikigai/nightshift) (verify: repo-exists) | Lights-out autonomous software work — ship specs, wake up to tested commits. |
| [tao3k/xiuxian-artisan-workshop](https://github.com/tao3k/xiuxian-artisan-workshop) (verify: repo-exists) | Game design bridge between human intent and machine execution, inspired by Ralph Workflow philosophy. |

---

## Pattern implementations

These independent projects implement the Ralph Loop architecture in their
own tools and frameworks. They validate the pattern independently of the
Ralph Workflow reference implementation.

| Project | Description | Verify |
|---------|-------------|--------|
| [umputun/ralphex](https://github.com/umputun/ralphex) | Multi-provider LLM loop with plan-build-verify cycle | (verify: repo-exists) |
| [bastani-inc/atomic](https://github.com/bastani-inc/atomic) | Dynamic workflows with Pi extensions, custom models, MCP, sub-agents, review gates | (verify: repo-exists) |
| [computerlovetech/ralphify](https://github.com/computerlovetech/ralphify) | Runtime for loop engineering — practitioner cookbook with Claude Code patterns | (verify: repo-exists) |
| [Th0rgal/open-ralph-wiggum](https://github.com/Th0rgal/open-ralph-wiggum) | CLI Ralph loop for OpenCode, Claude Code, Codex, Copilot — prompt file + status check | (verify: repo-exists) |
| [AnandChowdhary/continuous-claude](https://github.com/AnandChowdhary/continuous-claude) | Ralph loop with autonomous PR creation, CI check-waiting, and auto-merge | (verify: repo-exists) |
| [vercel-labs/ralph-loop-agent](https://github.com/vercel-labs/ralph-loop-agent) | Continuous autonomy for the Vercel AI SDK — production Ralph loop integration | (verify: repo-exists) |
| [gregorydickson/pickle-rick-claude](https://github.com/gregorydickson/pickle-rick-claude) | Ralph-inspired Claude Code runner with twist characterization | (verify: repo-exists) |
| [benikigai/nightshift](https://github.com/benikigai/nightshift) | Lights-out autonomous software work — ship specs overnight | (verify: repo-exists) |
| [Gens-ai/autopilot](https://github.com/Gens-ai/autopilot) | Standalone Ralph agent with structured loop execution | (verify: repo-exists) |
| [tao3k/xiuxian-artisan-workshop](https://github.com/tao3k/xiuxian-artisan-workshop) | Game design bridge between human intent and machine execution | (verify: repo-exists) |
| [basfenix/SelfSteeringRalph](https://github.com/basfenix/SelfSteeringRalph) | Self-steering variant with autonomous goal decomposition | (verify: repo-exists) |
| [Apra-Labs/agentic-ai-workshop](https://github.com/Apra-Labs/agentic-ai-workshop) | Educational Ralph Loop workshop with hands-on exercises | (verify: repo-exists) |
| [v1truv1us/ai-eng-system](https://github.com/v1truv1us/ai-eng-system) | `/ralph-workflow` command integrating into AI engineering system | (verify: repo-exists) |
| [jamesaphoenix/tx](https://github.com/jamesaphoenix/tx) | Headless agent infrastructure with memory + tasks + orchestration | (verify: repo-exists) |
| [KLIEBHAN/ralph-loop](https://github.com/KLIEBHAN/ralph-loop) | Lightweight single-binary Ralph implementation | (verify: repo-exists) |
| [coji831/agentic-devops-solar-ralph](https://github.com/coji831/agentic-devops-solar-ralph) | SOLAR Agentic DevOps integration with operator guides | (verify: repo-exists) |
| [agent-frontier/wgm](https://github.com/agent-frontier/wgm) | Rough request → working software pipeline | (verify: repo-exists) |
| [skurekjakub/ralph-orchestrator](https://github.com/skurekjakub/ralph-orchestrator) | Orchestrator with detailed workflow diagrams | (verify: repo-exists) |
| [DavisSylvester/ollama-dev-agent](https://github.com/DavisSylvester/ollama-dev-agent) | First local LLM adoption — runs on Ollama | (verify: repo-exists) |
| [dr-gareth-roberts/chief-wiggum-loop](https://github.com/dr-gareth-roberts/chief-wiggum-loop) | Enterprise security-hardened loop with sandboxing + stuck classifier | (verify: repo-exists) |
| [pbean/bmad-automator](https://github.com/pbean/bmad-automator) | BMAD enterprise agile integration | (verify: repo-exists) |
| [mikefreno/ralpi](https://github.com/mikefreno/ralpi) | Raspberry Pi Ralph agent extension | (verify: repo-exists) |
| [pro-vi/loopgen](https://github.com/pro-vi/loopgen) | Prompt compiler with Ralph Loop architecture | (verify: repo-exists) |
| [inshalazmat/AI_Business_Employee](https://github.com/inshalazmat/AI_Business_Employee) | AI employee powered by Ralph Loop pattern | (verify: repo-exists) |
| [sjhorn/ralph](https://github.com/sjhorn/ralph) | Go wrapper implementing a Ralph Wiggum loop | (verify: repo-exists) |
| [3mdistal/ralph](https://github.com/3mdistal/ralph) | Community fork — OpenCode orchestrator | (verify: repo-exists) |
| [suredream/ralphlow](https://github.com/suredream/ralphlow) | Workflow lock file system with structured architecture | (verify: repo-exists) |
| [Was85/ralph-rlm-agent-framework](https://github.com/Was85/ralph-rlm-agent-framework) | Agent framework with ralph-workflow instructions | (verify: repo-exists) |
| [dscherm/comfyprompts](https://github.com/dscherm/comfyprompts) | Mini-ralphs with delegation matrix | (verify: repo-exists) |
| [Delqhi-Projects/ZOE-Solar-Accounting-OCR](https://github.com/Delqhi-Projects/ZOE-Solar-Accounting-OCR) | Ralph/Lisa pipeline executor for accounting OCR | (verify: repo-exists) |

## Shadow ecosystem — Skill registries, MCP & media (last scouted 2026-06-30)

| Project | Type | Description | Verify |
|---------|------|-------------|--------|
| [majiayu000/claude-skill-registry](https://github.com/majiayu000/claude-skill-registry) | Skill Registry | Ralph Workflow SKILL.md in community skill registry — distribution channel | (verify: repo-exists) |
| [dgbau/rl](https://github.com/dgbau/rl) | RL Project | Reinforcement learning project with ralph-workflow skill integration | (verify: repo-exists) |
| [salmanrrana/brain-dump](https://github.com/salmanrrana/brain-dump) | OpenCode Integration | `@ralph` command loading ralph-workflow skill in OpenCode | (verify: repo-exists) |
| [fbratten/8me](https://github.com/fbratten/8me) | MCP Server | FastMCP server named "ralph-workflow" in tier3 agent pipeline | (verify: repo-exists) |
| [tincke10/Barto-MCP](https://github.com/tincke10/Barto-MCP) | MCP Server | MCP server with ralph-workflow tool in server.ts | (verify: repo-exists) |
| [plusplusoneplusplus/shortcuts](https://github.com/plusplusoneplusplus/shortcuts) | IDE Integration | RalphWorkflowPane React component in development shortcuts toolkit | (verify: repo-exists) |
| [robheat/ainformed-dev](https://github.com/robheat/ainformed-dev) | Media Coverage | AI news article: "Ralph Workflow — a free, open-source AI orchestrator for everyone" (2026-05-12) | (verify: repo-exists) |
| [huifeideyu-1121/ai-info-aggregator](https://github.com/huifeideyu-1121/ai-info-aggregator) | Media Coverage | AI Daily newsletter covering Show HN launch (2026-05-13) | (verify: repo-exists) |
| [robzilla79/forgecore-newsletter](https://github.com/robzilla79/forgecore-newsletter) | Media Coverage | Forgecore newsletter — research raw on Ralph Workflow Show HN (2026-05-12) | (verify: repo-exists) |
| [fanshanhong/claude-skills-cn](https://github.com/fanshanhong/claude-skills-cn) | Chinese Content | Chinese-language Claude skills article featuring Ralph Workflow (2026-06-02) | (verify: repo-exists) |
| [Brickea/daily-program](https://github.com/Brickea/daily-program) | Individual Adopter | Daily programmer tracking ralph-workflow 0.8.6 in daily notes | (verify: repo-exists) |
| [leolilisisy/gameList](https://github.com/leolilisisy/gameList) | Game Project | Game project using `@ralph-workflow.md` in memory-bank agent template | (verify: repo-exists) |
| [arisng/github-copilot-fc](https://github.com/arisng/github-copilot-fc) | Enterprise Planning | Ralph Workflow version governance documentation in GitHub Copilot config | (verify: repo-exists) |
| [tallesborges/zdx](https://github.com/tallesborges/zdx) | Enterprise Planning | `ralph-workflow-engine.md` superseding workflow-bundles architecture | (verify: repo-exists) |
| [szabgab/pydigger-data](https://github.com/szabgab/pydigger-data) | PyPI Index | ralph-workflow PyPI package data indexed in pydigger | (verify: repo-exists) |

## Active adopters (Ralph Workflow as a dependency or reference)

These projects actively use `ralph-workflow` — as a dependency,
integration, skill, or reference implementation. Discovered through
code-level GitHub search.

| Project | Integration |
|---------|-------------|
| [bastani-inc/atomic](https://github.com/bastani-inc/atomic) (verify: repo-exists) | Dynamic workflows with Pi extensions, custom models, MCP, sub-agents, artifacts, review gates — redesign specs reference ralph-workflow |
| [computerlovetech/ralphify](https://github.com/computerlovetech/ralphify) (verify: repo-exists) | "Ralphify is the runtime for loop engineering" — practitioner cookbook with Claude Code patterns |
| [tao3k/xiuxian-artisan-workshop](https://github.com/tao3k/xiuxian-artisan-workshop) (verify: repo-exists) | Game design bridge between human intent and machine execution, inspired by Ralph Workflow philosophy |
| [v1truv1us/ai-eng-system](https://github.com/v1truv1us/ai-eng-system) (verify: repo-exists) | `/ralph-workflow` command integrating Ralph into AI engineering system |
| [jamesaphoenix/tx](https://github.com/jamesaphoenix/tx) (verify: repo-exists) | Headless agent infrastructure (memory + tasks + orchestration), PRD-driven design paired with ralph-workflow |
| [coji831/agentic-devops-solar-ralph](https://github.com/coji831/agentic-devops-solar-ralph) (verify: repo-exists) | SOLAR Agentic DevOps integration with Ralph operator guides and workflow mapping |
| [skurekjakub/ralph-orchestrator](https://github.com/skurekjakub/ralph-orchestrator) (verify: repo-exists) | Orchestrator with detailed workflow diagrams (Phase 1 Setup → Phase N Delivery) |
| [sjhorn/ralph](https://github.com/sjhorn/ralph) (verify: repo-exists) | Go wrapper implementing a Ralph Wiggum loop, includes Adam Tuttle's workflow documentation |
| [3mdistal/ralph](https://github.com/3mdistal/ralph) (verify: repo-exists) | Community fork — "Ralph Loop: Autonomous coding task orchestrator for OpenCode" with GitHub label integration |
| [suredream/ralphlow](https://github.com/suredream/ralphlow) (verify: repo-exists) | Workflow lock file system (`.ralph-workflow`) with structured architecture |
| [Was85/ralph-rlm-agent-framework](https://github.com/Was85/ralph-rlm-agent-framework) (verify: repo-exists) | Agent framework with ralph-workflow instructions and Claude skill integration |
| [dscherm/comfyprompts](https://github.com/dscherm/comfyprompts) (verify: repo-exists) | Mini-ralphs with delegation matrix — multiple agents delegating via ralph-workflow.md |
| [Delqhi-Projects/ZOE-Solar-Accounting-OCR](https://github.com/Delqhi-Projects/ZOE-Solar-Accounting-OCR) (verify: repo-exists) | Ralph/Lisa pipeline executor for Solar Accounting OCR workflows |

## The community hub

[**awesome-ralph**](https://github.com/snwfdhmp/awesome-ralph) (verify: repo-exists) is the community-maintained directory of Ralph Loop implementations, workshops, skill packs, and academic references. Start there to understand the full scope of the ecosystem.

## How to add your project

If you built something with the Ralph Loop pattern or with Ralph Workflow
itself, add the badge below to your README — it takes 30 seconds and
creates a discoverable backlink. Then open an issue or send a PR to be
listed on this page.

```markdown
[![Built with Ralph Loop](https://codeberg.org/RalphWorkflow/Ralph-Workflow/raw/branch/main/assets/built-with-ralph-loop.svg)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
```

Renders as:

[![Built with Ralph Loop](https://codeberg.org/RalphWorkflow/Ralph-Workflow/raw/branch/main/assets/built-with-ralph-loop.svg)](https://codeberg.org/RalphWorkflow/Ralph-Workflow)

The badge signals that you're using the Ralph Loop pattern — plan →
build → verify, iterated by an AI coding agent until tests pass. It
links back to the Ralph Workflow reference implementation, helping
others discover the pattern and the toolkit.

After you add the badge:

1. Your project becomes discoverable via
   [GitHub code search](https://github.com/search?q=built-with-ralph-loop&type=code)
   for `built-with-ralph-loop`.
2. Open an issue on
   [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues)
   or submit a PR to be listed in the Pattern implementations table
   above.

What makes a good entry:

1. **Real project** — a repo with commits, not a template or fork
2. **Ralph Loop usage** — your agent runs plan-build-verify cycles, whether via Ralph Workflow directly or your own implementation
3. **Shipped software** — the agent produced working commits, not just artifacts

---

*Attribution: The Ralph Loop pattern is attributed to [Geoffrey Huntley](https://ghuntley.com/ralph). Ralph Workflow is an independent reference implementation — not the pattern's originator.*
