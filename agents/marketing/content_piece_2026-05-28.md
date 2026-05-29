# The Missing Step in AI Coding Workflows: Proof, Not Just "Done"

You've been there. You give an AI agent a task. It runs. It says "done." You close the laptop, satisfied. Then you come back, open the diff, and find:

- The code compiles but the logic is wrong
- It solved example A but broke B and C
- The task "ran" but the output is unusable
- It added 300 lines of dead code and removed two critical checks

The tool said done. The job didn't hold up.

This isn't a tool problem. It's a verification gap — and it's the most expensive blind spot in modern AI-assisted development.

## 1. The Real Problem: "Done" Is Not a Contract

When a human says "done," there's subtext. Maybe they tested it. Maybe they ran the lint suite. Maybe they checked edge cases. When an AI agent says "done," there's no subtext at all — the task executor exited with code 0 and maybe the output matched a regex pattern.

The difference is proof of completion. Humans can reason about what "done" means. Agents follow a script. If the script doesn't include verification — and most don't — the "done" signal is worse than useless. It's a false sense of security.

> "too big to babysit, too risky to trust blindly"

## 2. Why People Split Claude Code + Codex With No Clean Handoff

A common pattern I see: use Claude Code to plan and build the structure, then switch to Codex for execution. Or the reverse. The logic is sound — each tool has strengths.

But there's a catch: **neither tool naturally hands off to the other.** You end up with:

- Claude Code left a session open with half-applied changes
- Codex started fresh and stepped on something
- No shared notion of "what's been verified"
- The only handoff mechanism is copy-pasting terminal output

This works for small tasks. For anything real, it collapses. The problem isn't tool choice — it's that no tool provides a **reviewable handoff surface** between sessions.

## 3. The Boring Workflow That Actually Works

After enough cycles, a pattern emerges. It's not elegant. It's not flashy. But it works:

**Step 1: Sharpen the scope.**
Before any agent runs, write down exactly what "done" means. Acceptance criteria. File paths. Expected output format. The sharper the scope, the less room for the agent to interpret "done" as "I did a thing."

**Step 2: Isolated run.**
Each task gets its own environment. A fresh worktree, a clean branch, a dedicated container. No contamination from the last run.

**Step 3: Build under constraints.**
The agent builds against the acceptance criteria. Not "improve this file" — "add function X to file Y that accepts input Z and returns output W."

**Step 4: Verify.**
Check the output against the criteria. Run the tests. Review the diff. If it passes, it's done. If not, it's not — no matter what the agent says.

**Step 5: Produce a reviewable diff.**
The output is a clean, mergeable change set. Someone — or some automated gate — can review it on its own merits, without needing to replay the session.

This is Plan → Build → Verify. And it's the only thing that scales.

## 4. Where Manual Glue Becomes a Pain

The workflow above works. But it's manual. Every time you:

- Create a fresh worktree
- Copy the acceptance criteria into the agent prompt
- Wait for the run to finish
- Check whether it actually met the criteria
- Apply or discard the changes

...you're doing work the system should handle. The friction is low for one task. For ten tasks across a week, it adds up. For a team running dozens of agent-driven changes, manual glue becomes the bottleneck.

This is the gap RalphWorkflow fills: **sharpening → building → verifying → producing reviewable output** without manual session babysitting.

> "stop monitoring the session, start reviewing the result"

## 5. Where the Gap Actually Lives: Finish-State Verification

The industry talks a lot about tooling choice. Claude Code vs Codex. Cursor vs Copilot. What nobody talks about is **finish-state verification** — the thing that happens *after* the agent finishes but *before* you review the work.

This is the blind spot. The agent ran. It produced output. But was the output correct? Was it complete? Did it introduce regressions?

Without a verification step in the loop, you're trusting the agent's own assessment. And the agent always thinks it did great.

The fix isn't a better agent. The fix is a workflow that separates **execution** from **verification** and requires proof of the latter.

## 6. When Unattended Runs Are Worth It (And When They're Not)

Unattended runs are the dream: "start the job and close the laptop." But they're only safe when the acceptance criteria are narrow and well-defined:

**Good for unattended:**
- "Format all files in directory X"
- "Generate boilerplate for CRUD endpoints"
- "Run linting and fix warnings"
- "Refactor a well-typed function with tests"

**Bad for unattended:**
- "Improve the architecture"
- "Fix performance issues"
- "Refactor the authentication flow"
- Any task where "done" is ambiguous

The threshold is **verifiability.** If you can write a script or a test that proves the output is correct, the run can be unattended. If you need human judgment to decide if it's done, you need to be present.

## Try It: RalphWorkflow

[RalphWorkflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) is an open-source CLI toolkit that formalizes this exact pattern. It wraps the sharpening → build → verify → reviewable diff loop into commands you can run and walk away from.

- **Plan:** Define scope and acceptance criteria before any agent touches code
- **Build:** Execute agents in isolated environments against your criteria
- **Verify:** Check output against what you asked for, not what the agent thinks it did
- **Review:** Clean diffs you can evaluate without replaying the session

If you're spending more time watching AI coding sessions than reviewing their output, you're in the verification gap. [Check out the repo](https://codeberg.org/RalphWorkflow/Ralph-Workflow) (also mirrored on [GitHub](https://github.com/Ralph-Workflow/Ralph-Workflow)).

---

*This article is published as part of open-source documentation for RalphWorkflow. All tools mentioned are linked for reference; no affiliate relationships or sponsorships exist.*
