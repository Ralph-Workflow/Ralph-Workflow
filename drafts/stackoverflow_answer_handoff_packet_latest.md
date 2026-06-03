# Ralph Workflow StackOverflow Answer Handoff Packet
Generated: 2026-06-03T03:15:01.445928

## Why this is still the live answer lane
- The same high-intent question is still the strongest qualified StackOverflow target in the current window.
- A recent polished answer already exists, so the right move is to reuse the proven asset instead of generating duplicate draft churn.
- Codeberg remains the primary repo CTA.

## Target
- **Question:** Autonomous mode / wrapper for Claude Code?
- **URL:** https://stackoverflow.com/questions/79896243/autonomous-mode-wrapper-for-claude-code
- **Current score:** 5.0
- **Current answers:** 2
- **Reused draft:** `/home/mistlight/.openclaw/workspace/drafts/stackoverflow/so_answer_2026-06-02_autonomous-mode-wrapper-for-claude-code.md`

## Final answer text
```md
If the goal is "give it a high-level task and let it keep going until there is something real to review," I would stop looking for a single Claude Code flag and put it inside an outer workflow instead.

Claude Code does have `--resume`, `--continue`, and `--max-turns` flags, but they optimize for session longevity rather than outcome quality. A long-running single session drifts — the model forgets the acceptance criteria, hallucinates scope creep, and produces a wall of changes that are painful to review. The real problem isn't duration; it's structure.

Here's the pattern that actually works for overnight unattended runs:

### 1. Bound the task before the agent starts

The agent's scope is the only safety boundary you have. Write one paragraph of acceptance criteria before any code changes happen:

```markdown
# Task: Add rate-limiting to /api/ endpoints
- Decorate all public /api/ views with a configurable rate limiter
- Use Django's existing cache backend (no Redis dependency)
- Tests must pass: pytest api/ -x --tb=short
- Non-goals: auth changes, admin panel, frontend rate-limit UX
```

This is your contract. If the agent touches auth or frontend code, it's out of scope and the run should fail.

### 2. Run in phases, not one endless session

A single Claude Code session that plans, implements, AND verifies will rationalize its own mistakes. Separate the phases:

1. **Plan phase** — Claude Code reads relevant files, proposes a change plan. You review the plan (not the code). 5 minutes of human review here saves hours of debugging later.
2. **Execute phase** — implement only what the plan approved. No scope creep.
3. **Verify phase** — run your test suite, linter, type checker. Output: pass/fail evidence, not a summary.
4. **Review packet** — the agent packages a diff + test results + unresolved concerns into one markdown file. This is what you read at 9 AM.

The rule: no passing verification output, no completion.

### 3. Auto-continue only between phases, never across failures

Let the wrapper continue to the next phase when the current one succeeds. But if verification fails or the agent tries to leave scope, stop. "Should I continue?" is a control-plane question — the wrapper should decide based on evidence, not blindly say yes because the model asked nicely.

Concretely, the useful ingredients are:

- A budget for retries / loop count per phase (not "let it run forever")
- A persisted task spec on disk (survives crashes/timeouts/interruptions)
- A verification gate (tests, build, lint — whatever matches the task)
- A finish contract that produces a diff + check results, not just a text summary
- Resume/checkpoint support so a long run can recover cleanly from a mid-run failure

### 4. What to start with tonight

Pick one small, well-tested module. Run it with plan-first, verify-last discipline. Don't merge unless the tests pass AND the diff makes sense. One task, one evening — you'll know by morning whether the pipeline holds up.

That's the difference between an agent session and an unattended coding workflow. The structure matters more than the model.

### If you want to skip building this from scratch

The plan→execute→verify→review pattern with TOML task specs, checkpoints, and verification gates is what Ralph Workflow implements — free, open-source, runs the agents you already have on your own machine. [Here's the full getting-started flow](https://asciinema.org/a/JDnY0Xyh5qcgu9kd) (init → diagnose → dry-run, ~19 seconds). Primary repo: [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow) with a [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow). Whatever tool you pick, the loop structure matters more than the model — bounded tasks with separated verification beat unlimited chat sessions every time.

---

_Disclosure: I work on Ralph Workflow, a free/open-source composable agent orchestrator that implements the plan→execute→verify→review loop structure described above. Codeberg-first, runs with the agents you already have._
```

## Outcome contract
- Expected outcome: one live StackOverflow-compatible placement or manual reuse that sends qualified evaluators to Codeberg first.
- Replacement condition: if this exact packet still has no placement path by the next review window, switch the lane instead of regenerating the same answer again.
