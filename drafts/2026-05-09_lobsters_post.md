# Lobsters Post

**Title:** Ralph Workflow — spec-driven AI coding agent orchestrator (4+ hour unattended runs)

**Body:**

Been working on this for a few months. Ralph Workflow is a CLI tool that orchestrates AI coding agents through a structured Plan → Develop → Verify loop.

The key insight: most agent tools let the AI wander. Ralph constrains it to a SPEC.md you write upfront. Every commit is traceable to a spec item. You review the git log like a PR queue.

**How it works:**
1. You write SPEC.md (what you want, not how)
2. `ralph run` kicks off the loop
3. Planning agent writes PLAN.md breaking down the spec
4. Dev agent writes code
5. Verify agent checks against plan
6. If passes → commit. If fails → refine and retry.
7. Loop until spec is done or token budget exhausted

**Stack:**
- CLI in Rust (fast, single binary install)
- Configurable agents per phase (I use GPT-4o for planning, Claude Code for dev, o1 for verification)
- Works with any model that exposes an OpenAI-compatible API

**What it's NOT:**
- A chatbot interface
- A copilot replacement
- Something you stare at while it runs

**What it IS:**
- Something you set up before end of day, comes back tomorrow to review commits

The interesting part is the verification phase. Most agent tools don't have a separate verify step — they assume the model knows when it's done. Ralph forces explicit verification against the plan, which catches a lot of hallucinated requirements.

Demo repo with examples in the README.

**Questions welcome.** Especially interested in how others handle the "when is it actually done?" problem.
