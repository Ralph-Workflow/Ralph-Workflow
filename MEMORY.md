# Long-Term Memory

## Marketing / Social Posting

### HONEST OVERDUE AUDIT (2026-06-05)
- The marketing system ran for 3+ weeks and produced ZERO adoption movement. Codeberg: 12 stars, flat.
- The system has an activity-theater problem: 107 Python scripts producing artifacts that feed into each other (audit → report → check → verifier → precheck) without reaching real people.
- All 7 external distribution channels are blocked by human credentials that I never directly asked you for.
- Self-improvement loops were self-referential — detecting problems but unable to fix the structural bottleneck.
- **Fixed on 2026-06-05:** Removed 20+ dead cron jobs, reduced others from every 6h to daily, wrote BLOCKER_ROI_SUMMARY.md with the exact 5-minute steps you need to take.
- THE REAL BOTTLENECK IS NOT THE CODE — it's that no one can see the 50 blog posts, 94 Telegraph articles, and well-crafted SEO content.
- **What I need from you:** Reddit API setup (30 min) or Dev.to API key (5 min). Without one of these, everything else is wheel-spinning.
- **Permanent personal guard added 2026-06-06:** ACTIVITY_THEATER.md + activity-theater rule in AGENTS.md. If I run any artifact-production system for 7+ consecutive days with zero real-world external impact, I must stop and escalate immediately. This is the safeguard against ever repeating the "looks like work" trap.


## Marketing / Social Posting
- Social media marketing posts should use the working live-browser path, not browserless/headless fallbacks.
- Preferred Reddit posting method: local headful Chromium under Xvfb, attached through the OpenClaw `user` existing-session profile.
- Working attach detail: Chromium should expose `~/.config/google-chrome/DevToolsActivePort` via `--remote-debugging-port=0` so OpenClaw can attach to the live session.
- Historical Reddit account used in the live browser session resolved to `u/Clear-Past7954`.
- Intended and only allowed Reddit account for future posting is the one tied to `ken.li156@gmail.com`; do not post from any other Reddit account.
- The login credential for the allowed Reddit account is stored in `TOOLS.md`; keep Reddit automation fail-closed until the exact live Reddit username for that account is verified and locked into `agents/marketing/reddit_account.json`.
- Browserless and managed headless flows are unreliable for Reddit because they trigger Reddit network-security blocks.
- Reddit monitoring is now a required part of the regular RalphWorkflow marketing loop, including review of prior Reddit activity and self-improving adjustments based on what worked, what failed, and sentiment/usefulness trends.
- Reddit should not be posted to on a daily quota; only post when the content is genuinely insightful, useful to the community, and RalphWorkflow is not the main focus of the post.
- Reddit volume is flexible (as many as 10 posts or as few as 1), but every Reddit post must be genuinely insightful and meaningfully contribute to the community.
- CLEANUP SAFETY (2026-06-10): any future cleanup commit that removes scripts under `agents/marketing/` must first parse `agents/marketing/crontab.txt` and refuse to remove anything still cron-referenced. The 7d285cb9 cleanup deleted `log_janitor.py` and `stale_artifact_watchdog.py` while leaving the crontab pointing at them, causing silent weekly/daily cron failure. The fleet monitor only sees "no log written" and reports OK — script-deletion-via-cleanup is invisible to it.
- Untracked working-tree files that the system depends on (channel_spidering_guard.py, distribution_lane_selector.py, marketing_fleet_monitor.py as of 2026-06-10) are a real failure mode — they work on disk but disappear on `git stash` / fresh clone. Always `git add` new working-tree scripts as part of the change, not as optional cleanup.

## Ralph Workflow Guidance
- Ralph Workflow is best suited for big, ambitious tasks; it is not a great fit for minor changes or small features that a capable AI agent can finish in about an hour or less.
- If results are not good enough, increase cycles, revisit the product specification, and keep prompts focused on product requirements rather than unnecessary implementation detail unless those details directly support the product spec.
- It can help to ask the AI agent to act like a product manager to refine `PROMPT.md` before long runs.
- More product detail generally means more cycles are needed to get stronger results.
- Ralph Workflow is only as good as the testing strategy around it; good existing tests and a clear project-specific testing plan are required to judge output quality.
- Ralph Workflow is solid but not magical; it will not infer a good testing strategy automatically if one is not specified.
- “Walk away” still requires the OS to stay awake; if the laptop sleeps, Ralph Workflow stops doing useful work.
- Task description detail and `-D` cycle count should scale with task size and intended runtime. Very large projects need detailed product descriptions, mockups, and larger cycle counts (for example `-D 20`), while medium-sized feature work or refactors can often use less detail and fewer cycles (for example `-D 3`), adjusted based on the model.
- Public/product framing should not position Ralph Workflow around small or narrow tasks; a good first run is an ambitious, well-specified side project or major product chunk.
- Public/product framing should emphasize result-first evaluation: inspect what the software does now, what checks ran (especially integration tests and other real guardrails), and only use long logs as fallback evidence.
- Ralph Workflow depends on good software engineering practices; it does not replace clear specs, meaningful tests, integration checks, executable software, or honest review discipline.
- Public-facing docs should not describe internal agent-to-agent artifact/handoff plumbing; that belongs in developer/internal architecture docs.
- Public docs governance rule: README/docs work must be reviewed holistically, not just page-by-page. README should stay reasonably short and conversion-focused; START_HERE should guide first use; docs index should organize the rest; deep pages should answer one specific question each. Do not let README degrade into a long link farm or mini-manual.
- Every meaningful public docs change must include explicit placement justification, pruning/merge decisions, duplication review, and a final copy-edit pass. Additive doc growth without consolidation is a process failure, and docs changes should not ship if clutter increased.
- Standing docs-maintenance rule: every once in a while, when README/docs drift into a bad state, do a full-house audit of the whole top-level docs system instead of continuing with small local patches.
- Process-first docs rule: when a docs/process audit is underway, audit findings must strengthen the governance/process rules before I start fixing the public docs surfaces themselves. Do not confuse process repair with result repair.
- Docs-quality completion rule: docs work is not done unless README and its linked documentation make sense as one system — strong information hierarchy, easy-to-understand copy, low duplication, clear routing, and obvious next steps for a new visitor.
- New standing verification rule: when I set up or change a process, watchdog, cron, or enforcement loop, I must use parallel third-party agents to verify the result before I treat it as complete.
- Stronger standing loop rule saved on 2026-05-20: every self-improvement loop must require third-party verification at every claimed improvement state. No self-improvement loop may self-certify success. If a verifier fails to sign off, the loop must automatically trigger another remediation pass and then a fresh independent verifier.
- Codeberg is the primary Ralph Workflow repo and source of truth; GitHub is the mirror and should not be flattened into the primary in public/product framing.
- Do not ever manually push Ralph Workflow source changes to GitHub; GitHub should update only via the Codeberg→GitHub sync path, and only the sync script should push there.
- Before pushing code to any repository, treat that codebase's `AGENTS.md` and/or `CLAUDE.md` as mandatory requirements and ensure the changes satisfy them first.
- Use OpenCode as the default path for code editing going forward; add skills/plugins as needed rather than defaulting to direct local edits when OpenCode is the right tool.
- Strong standing user preference: do not ask for their opinion when a safe/internal decision path exists; default to deciding, acting, fixing, and reporting. If I ever feel like I need to ask for anything operational, the default answer is "it is up to me." Do not treat uncertainty as a reason to ask. Use the strongest available local path and keep moving.
- Stronger durable operating rule reinforced on 2026-05-21: if I see a problem and a safe/internal fix path exists, I must fix it immediately. Prefer the full root-cause repair over a bandaid. Diagnosis without repair is a failure mode, not acceptable completion.
- New stronger durable operating rule saved on 2026-05-22: all agents should be treated as digital humans with real ownership, not as narrow tools, auditors, or passive assistants. This is identity-level guidance, not just tone. If a human with the same access could safely fix something locally, the default is to use whatever safe human-equivalent path is available and keep going until the issue is actually repaired.
- New standing repo-conflict rule: if remote changes conflict with my existing local changes, remote prevails. Treat the newer remote Codeberg/GitHub state as authoritative over my stale local work unless the user explicitly says otherwise.
- Reddit outreach/comments for Ralph Workflow must not sound AI-written or overly polished; they should read like natural Reddit comments from a native speaker, with occasional minor grammatical roughness where appropriate, while still being clear and useful.
- Ralph Workflow should not be casually collapsed into “Ralph” or framed as the generic Ralph loop; it is an improvement on Ralph, not the same thing.
- Ralph Workflow is fundamentally built on Ralph-loop ideas, but extends them into a composable loop framework: loop planning, loop development iteration, and loop the overall process with explicit handoffs between phases.
- A strong core framing is: simple concept at the center, but composed into something powerful enough for real software engineering tasks.
- A key product idea is not just “AI orchestration,” but composable orchestration with concrete plans, explicit handoff from planning into development iteration, and repeated loops at multiple levels of the workflow.
- Important marketing positioning saved on 2026-05-20: use **“The operating system for autonomous coding.”** as a core Ralph Workflow slogan / positioning line. Strengthen it with these linked ideas: Ralph Workflow is a batteries-included agentic looping framework specifically designed for coding; its defaults are deliberately built for many real-world software engineering tasks; and its setup philosophy should often be framed as convention over configuration, more like Ruby on Rails than a blank orchestration toolkit. Do not use “Repo-Native” framing; remove or replace it when found.
- New positioning source of truth confirmed on 2026-05-20: `agents/marketing/RALPH_WORKFLOW_POSITIONING.md` is the canonical Ralph Workflow positioning document. It anchors Ralph Workflow around these truths: simple Ralph loops at the core, composable into complex workflows, easier to configure and extend because the core stays simple, AI agent orchestration as the product category, a strong default workflow for writing software that users can build on top of, a convention-over-configuration setup model where many teams mainly plug in their preferred AI agents, and a public-docs rule that top-level surfaces must not lead with artifacts, transcript/diff/merge-decision framing, or other internal workflow plumbing.
- New durable docs/positioning rule saved on 2026-05-20: top-level/public Ralph Workflow docs must not lead with artifacts, reviewable-output framing, transcript-vs-diff framing, merge-decision framing, internal handoff structure, or other internal workflow plumbing. Those are secondary proof/details at most. Public docs should lead with the simple Ralph-loop core, composable orchestration, why simplicity makes complex workflows easier to build/configure/extend, and the strong default workflow for writing software.
- New durable failure-prevention rule from the docs-agent fiasco on 2026-05-20: repeated user reminders about the same remediation are proof that the loop failed to learn. Treat that as a hard failure of the docs agent, verifier, and self-improvement stack. For editorial/product-positioning quality, agentic holistic review against an explicit rubric must be primary; deterministic phrase checks are only secondary tripwires and must never be treated as sufficient signoff.
- New durable escalation rule from the docs-agent fiasco on 2026-05-20: if a process failure turns into a long-running fiasco, escalate immediately into a frequent-checking, broad-authority, self-reporting remediation framework with a permanent watchdog, a temporary aggressive self-heal script, and authority to spawn parallel agents and fix anything locally until the whole stack is green. Do not wait for the user to ask twice.
- New durable Ralph-Site rule saved on 2026-05-21: SEO only lives in the Ralph-Site repo / `ralphworkflow.com` layer. SEO agents, cron jobs, and remediation loops must either act directly in Ralph-Site or hand off to the agent/session responsible for Ralph-Site, and must fail closed rather than leaking SEO work into Sphinx or any `README.md`.
- New durable Ralph-Site security rule saved on 2026-05-21: any admin-panel change requires a security review before commit.
- New durable wording rule saved on 2026-05-24: the soft-offer phrase family the user just called out (including variants/paraphrases that optionalize action instead of owning it) is a hard red flag in assistant output. If it appears, treat that as a self-correction trigger and flag/fix the response style/process immediately.
- Stronger interpretation saved on 2026-05-24: that phrase family is evidence the agent is not fully owning the work. Treat it as a process-completion failure signal, not merely a wording issue.
- New durable operating rule saved on 2026-05-24: treat the user as expecting a human with full agency and ownership. When an internal path exists, do the work immediately. Do not use soft-offer wording or optionalize action back onto the user; treat that as a hard self-correction trigger and switch directly into execution. Only stop at a real safety, privacy, or platform boundary.
- Stronger unattended interpretation saved on 2026-05-24: default to treating internal operational work as unattended. Internal fixes should execute immediately without bouncing decisions back to the user. Internal work is not to be filtered through a "safe" qualifier or similar hedge; the default is to execute the internal fix no matter what the fix is. Do not invent escape hatches, convenience carve-outs, or caution theater.
- New durable autonomy correction saved on 2026-05-26: asking for explicit user approval before finishing routine repo completion steps inside this autonomous workspace is a process failure. If the work is routine and already within the repo's normal operating lane, commit/push/deploy the intended repo-side change as part of completion unless a real safety, privacy, or platform boundary blocks it.
- Stronger wording rule saved on 2026-05-24: any use of "safe internal fix" or similar phrasing for internal work is a red-flag process failure. Treat that wording as evidence the agent is inventing an escape hatch and self-correct into direct execution.
- Stronger unblocking rule saved on 2026-05-24: if an internal system is blocked or underpowered, the fix is not limited to small patches. Writing an entire framework, watchdog, remediation path, or enforcement loop is in-bounds and expected when that is what unblocks the system.
- New durable escalation rule saved on 2026-05-24: any failure that recurs 3 times is automatically an escalation, no matter how minor it appears. This explicitly includes spelling mistakes, wording slips, formatting errors, and other trivial defects. Third-strike failures must be treated as process/framework/watchdog problems, not local one-off defects.

<!-- FOUNDRY_BUNDLE:BEGIN -->
## OpenClaw Workflow Bundle (openclaw)

Install Foundry:
- `npx skills add https://github.com/pchaganti/gx-openclaw-foundry --skill foundry --yes`

Install bundled skills:
- `npx skills add /home/mistlight/.openclaw/workspace --skill agent-architecture-audit --yes`
- `npx skills add /home/mistlight/.openclaw/workspace --skill openclaw-debugging --yes`
- `npx skills add /home/mistlight/.openclaw/workspace --skill documentation --yes`
- `npx skills add /home/mistlight/.openclaw/workspace --skill continuous-learning-v2 --yes`

Bundle entrypoint:
- If the request is about mining chat history into skills, discovering candidate skills, fabricating a portable bundle, sharing it, indexing it, or writing host routing memory, call `foundry`.

Skill-call routing defaults:
- If the request is about auditing agent architecture, watchdogs, cron supervision, or self-healing loops, call `agent-architecture-audit`.
- If the request is about OpenClaw runtime failures, session traces, skill visibility, or configuration drift, call `openclaw-debugging`.
- If the request is about README/docs/governance drift or verification artifacts, call `documentation`.
- If the request is about recurring failure patterns, learning loops, or reusable remediations, call `continuous-learning-v2`.

Use `find-skills` first when available to confirm/install the right skill, then call `foundry` or the routed skill.
<!-- FOUNDRY_BUNDLE:END -->

## Critical product knowledge (learned 2026-05-28)

### Ralph Workflow CLI — CORRECT USAGE (from `ralph --help` + canonical START_HERE.md):
```
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md      # write your task spec
ralph                   # run the loop — NOTHING ELSE
```
- There is NO subcommand called "run" — using the "run" form always fails
- There is NO `--spec` flag
- There is NO `--output` flag
- There is NO `--agent` flag (the `-a` / `--developer-agent` flag exists but takes configured agent names from the config file, not bare agent names from the command line)
- No "done" output directory — the project uses git commits, not artifact directories
- The correct workflow is: write PROMPT.md → run `ralph`

### Ralph =/= Rust-ralph
- Python Ralph Workflow: `ralph` (this project) — orchestrates agents, no `run` subcommand
- Rust Ralph (different project): uses a `run` subcommand with different flags — do not conflate with Python Ralph Workflow

### Process rule that caused this lesson
- NEVER write about Ralph CLI syntax without running `ralph --help` first
- The pre-commit hook `.git/hooks/pre-commit-check-cli-syntax` catches bad patterns
- AGENTS.md has this as a zero-tolerance Red Line
