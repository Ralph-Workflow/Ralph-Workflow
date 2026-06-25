# Ralph Loop Ecosystem

Ralph Workflow is the reference implementation of the [Ralph Loop](https://ghuntley.com/ralph) pattern — an iterative plan-build-verify architecture for AI coding agents, originally attributed to Geoffrey Huntley (ghuntley.com). The pattern has inspired a thriving, independent ecosystem of 30+ projects. We didn't invent the loop — we built the production-grade toolkit for it.

## Active Community Projects

These independent projects implement variations of the Ralph Loop pattern. They are not Ralph Workflow ecosystem projects — they are independent pattern derivatives that validate the architecture.

| Project | Stars | Description |
|---------|-------|-------------|
| [Th0rgal/open-ralph-wiggum](https://github.com/Th0rgal/open-ralph-wiggum) | 1,817 ⭐ | CLI Ralph loop for OpenCode, Claude Code, Codex, Copilot — prompt file + status check |
| [AnandChowdhary/continuous-claude](https://github.com/AnandChowdhary/continuous-claude) | 1,354 ⭐ | Ralph loop with autonomous PR creation, CI check-waiting, and auto-merge |
| [umputun/ralphex](https://github.com/umputun/ralphex) | 1,296 ⭐ | Multi-provider LLM loop with plan-build-verify cycle |
| [vercel-labs/ralph-loop-agent](https://github.com/vercel-labs/ralph-loop-agent) | 803 ⭐ | Continuous autonomy for the Vercel AI SDK — production Ralph loop integration |
| [Gens-ai/autopilot](https://github.com/Gens-ai/autopilot) | 14 ⭐ | Standalone Ralph agent with structured loop execution |
| [gregorydickson/pickle-rick-claude](https://github.com/gregorydickson/pickle-rick-claude) | 26 ⭐ | Ralph-inspired Claude Code runner with twist characterization |
| [benikigai/nightshift](https://github.com/benikigai/nightshift) | 14 ⭐ | Lights-out autonomous software work — ship specs, wake up to tested commits |
| [DavisSylvester/ollama-dev-agent](https://github.com/DavisSylvester/ollama-dev-agent) | — | First local LLM Ralph adoption — runs on Ollama |
| [dr-gareth-roberts/chief-wiggum-loop](https://github.com/dr-gareth-roberts/chief-wiggum-loop) | — | Enterprise security-hardened loop with sandboxing + stuck classifier |
| [basfenix/SelfSteeringRalph](https://github.com/basfenix/SelfSteeringRalph) | 11 ⭐ | Self-steering Ralph variant with autonomous goal decomposition |
| [KLIEBHAN/ralph-loop](https://github.com/KLIEBHAN/ralph-loop) | 3 ⭐ | Lightweight single-binary Ralph implementation |
| [Apra-Labs/agentic-ai-workshop](https://github.com/Apra-Labs/agentic-ai-workshop) | 8 ⭐ | Educational Ralph Loop workshop with hands-on exercises |
| [agent-frontier/wgm](https://github.com/agent-frontier/wgm) | 1 ⭐ | Rough request → working software pipeline |
| [pbean/bmad-automator](https://github.com/pbean/bmad-automator) | — | BMAD enterprise agile integration with Ralph Loop |
| [mikefreno/ralpi](https://github.com/mikefreno/ralpi) | — | Raspberry Pi Ralph agent extension |
| [pro-vi/loopgen](https://github.com/pro-vi/loopgen) | — | Prompt compiler with Ralph Loop architecture |
| [inshalazmat/AI_Business_Employee](https://github.com/inshalazmat/AI_Business_Employee) | — | AI employee powered by Ralph Loop pattern |

## The Canonical Hub

[snwfdhmp/awesome-ralph](https://github.com/snwfdhmp/awesome-ralph) (904 ⭐, 69 forks) is the community-maintained directory of Ralph Loop implementations, workshops, skill packs, and academic references. It's the best starting point for understanding the full ecosystem.

## Ralph Workflow's Place

Ralph Workflow (`pip install ralph-workflow`) is the **Loop Engineering toolkit** — the only production-grade Python framework in the ecosystem with:

- **Spec-driven methodology**: Write a `workflow.md` — the agent plans, builds, and verifies against it
- **Test-gated quality**: Every commit passes integration tests before landing
- **Checkpoint/resume**: Crash-safe with progress preservation
- **Multi-agent architecture**: Coordinator + sub-agents for complex projects
- **Vendor-neutral**: Claude Code, OpenCode, Ollama, and more

The ecosystem proves the pattern works. Ralph Workflow makes it production-ready.

## Ralph Workflow in the Wild

These projects actively use `ralph-workflow` — as a dependency, integration, skill, or reference implementation. They're not loop-pattern variants; they're real tool users, discovered through code-level GitHub search.

| Project | Stars | Integration |
|---------|-------|-------------|
| [bastani-inc/atomic](https://github.com/bastani-inc/atomic) | 254 ⭐ | Dynamic workflows with Pi extensions, custom models, MCP, sub-agents, artifacts, review gates — redesign specs reference ralph-workflow |
| [computerlovetech/ralphify](https://github.com/computerlovetech/ralphify) | 66 ⭐ | "Ralphify is the runtime for loop engineering" — practitioner cookbook with Claude Code patterns |
| [tao3k/xiuxian-artisan-workshop](https://github.com/tao3k/xiuxian-artisan-workshop) | 14 ⭐ | Game design bridge between human intent and machine execution, inspired by Ralph Workflow philosophy |
| [v1truv1us/ai-eng-system](https://github.com/v1truv1us/ai-eng-system) | 7 ⭐ | `/ralph-workflow` command integrating Ralph into AI engineering system |
| [jamesaphoenix/tx](https://github.com/jamesaphoenix/tx) | 4 ⭐ | Headless agent infrastructure (memory + tasks + orchestration), PRD-driven design paired with ralph-workflow |
| [coji831/agentic-devops-solar-ralph](https://github.com/coji831/agentic-devops-solar-ralph) | 2 ⭐ | SOLAR Agentic DevOps integration with Ralph operator guides and workflow mapping |
| [skurekjakub/ralph-orchestrator](https://github.com/skurekjakub/ralph-orchestrator) | 1 ⭐ | Orchestrator with detailed workflow diagrams (Phase 1 Setup → Phase N Delivery) |
| [sjhorn/ralph](https://github.com/sjhorn/ralph) | — | Go wrapper implementing a Ralph Wiggum loop, includes Adam Tuttle's workflow documentation |
| [3mdistal/ralph](https://github.com/3mdistal/ralph) | — | Community fork — "Ralph Loop: Autonomous coding task orchestrator for OpenCode" with GitHub label integration |
| [suredream/ralphlow](https://github.com/suredream/ralphlow) | — | Workflow lock file system (`.ralph-workflow`) with structured architecture |
| [Was85/ralph-rlm-agent-framework](https://github.com/Was85/ralph-rlm-agent-framework) | — | Agent framework with ralph-workflow instructions and Claude skill integration |
| [dscherm/comfyprompts](https://github.com/dscherm/comfyprompts) | — | Mini-ralphs with delegation matrix — multiple agents delegating via ralph-workflow.md |
| [Delqhi-Projects/ZOE-Solar-Accounting-OCR](https://github.com/Delqhi-Projects/ZOE-Solar-Accounting-OCR) | — | Ralph/Lisa pipeline executor for Solar Accounting OCR workflows |

> **Using ralph-workflow?** Add the [Built with Ralph Loop](assets/built-with-ralph-loop.svg) badge to your README — it creates a discoverable backlink to the project via GitHub code search.

## Attribution

The Ralph Loop pattern is attributed to Geoffrey Huntley (ghuntley.com/ralph). Ralph Workflow is an independent reference implementation — not the pattern's originator. We build on a community insight, not claim to own it.

## Add Your Project

Building something with the Ralph Loop pattern? [Open an issue](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues) or submit a PR to be listed here. Grab a [credit badge](CREDIT_TEMPLATE.md) for your README.

---

*Last updated: 2026-06-25 · [awesome-ralph](https://github.com/snwfdhmp/awesome-ralph) (904 ⭐) · [ghuntley.com/ralph](https://ghuntley.com/ralph)*
