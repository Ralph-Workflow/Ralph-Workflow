# Ralph Workflow — Fresh Reddit Bodies (2026-05-22)
Generated: 2026-05-22 05:35 CEST
Status: NEW — structurally different from the 6 logged bodies
Purpose: Replace bodies that share the `contrast opener → handoff framing → proof bundle → product close` cadence

## Body 1 — Outcome-First (no problem opener)
Shape: Concrete result → mechanism → tool context → evaluator path
Does NOT start with a problem statement, question, or "I used to..."

---
Three hours of real backlog work, done by 7am. Not a demo task — an actual three-file refactor with a test suite update.

What made it possible was not a better model. It was defining the finish line before starting: one paragraph spec, bounded scope, named finish criterion. Then the agent worked, and in the morning the output was a diff I could read, test evidence I could verify, and a short named list of what still needed a call.

Most AI coding discussions focus on the model. The more useful variable is the spec at the start and the review surface at the end.

Ralph Workflow is free open-source software that runs existing AI coding tools (Claude Code, Codex CLI, OpenCode) through that pattern on your own machine. Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow

---

## Body 2 — Contrarian Data Point (no question opener)
Shape: Counter-intuitive fact → explanation → product context → link
Does NOT use "what's the actual test" or similar question openers

---
The failure mode I see most often with AI coding agents is not the agent itself — it's that nobody defined what success looks like before starting.

Give an agent an underspecified task and it will confidently finish before the task is actually done. The confidence is not a signal. The diff is.

The setup that changed this for me: write the finish line before running. Not a long prompt. A one-paragraph spec with a named finish criterion. Run the agent. In the morning, open the diff, not the summary.

Ralph Workflow is a free open-source workflow that enforces exactly this — spec-first, evidence-based finish, your judgment at the end. On your own machine. Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow

---

## Body 3 — Pure Mechanism (no before/after narrative)
Shape: How-to frame → step sequence → product → link
Does NOT use "I used to spend" or "what fixed it" or "what changed my setup" constructions

---
How to set up a coding agent for overnight backlog work:

1. Write a one-paragraph spec before starting. Not a prompt — a finish-line definition. What does 'done' look like, specifically?

2. Run the agent against that spec. Let it work unsupervised.

3. In the morning: open the diff first, not the summary. Read the checks. Make the calls that actually need a human.

Step 3 is where most setups fail — they end in a confident blob instead of a review surface. Ralph Workflow is built around step 3: it tries to end every run with finished code, check evidence, and a short named list of open decisions instead of a summary. Runs existing agents on your own machine. Free and open-source. Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow

---

## Validation checklist (before posting)
- [ ] Opening line does not appear verbatim in any of the 6 logged bodies
- [ ] Opening line is not a paraphrased version of a recent opening
- [ ] Shape is genuinely different from `contrast opener → handoff framing → proof bundle → product close`
- [ ] No banned opening lines (handoff/model-stack/output-judgment/stop-asking variants)
- [ ] Product mention comes after meaningful content, not as the entire point
