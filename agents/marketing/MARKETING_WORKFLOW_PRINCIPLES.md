# RalphWorkflow Marketing Workflow Principles

Use this to audit whether the marketing system is actually driving adoption.

## Goal
Drive more qualified traffic to the RalphWorkflow repositories, with Codeberg as the primary target and GitHub as the mirror target.

Success means:
- more people reach the repos
- more people inspect the repos seriously
- more people star/watch/fork/use it
- more people understand when it is the right tool and start from the repo surfaces

## Non-negotiable messaging requirement
RalphWorkflow marketing should consistently answer four questions:
1. What is it?
2. Who is it for?
3. Why is it different / better?
4. Why should someone use it now?

Reference: `agents/marketing/FOUR_MARKETING_QUESTIONS.md`

## Principle 1: Start from the bottleneck
Always ask: what is the current adoption bottleneck?

Typical bottlenecks:
- not enough relevant attention
- weak positioning / message confusion
- weak trust / not enough proof
- weak free-use path / unclear first step
- weak retention / no compelling follow-through
- weak public adoption signals

Do not spend most effort on top-of-funnel activity if the real bottleneck is conversion.

## Principle 2: Match message to pain
Best RalphWorkflow pain frames:
- too big to babysit
- too risky to trust blindly
- agent said done, but the task did not hold up
- need a reviewable diff, not just a summary
- want to walk away and come back to something reviewable
- want to wake up to a whole project or a large chunk of one completed overnight

Avoid generic hype and abstract orchestration language when a clearer pain-led message works better.

Core product truth to preserve:
- free and open source
- orchestrates existing agents on your own machine
- built for substantial unattended work
- overnight while you sleep
- wake up to reviewable output

Managed outbound/distribution channels should include Apollo.io alongside the other account-based surfaces, while keeping Codeberg as the primary repo destination and GitHub as the mirror destination.

## Principle 3: Optimize for repo traffic and adoption, not applause
A good marketing action should make it easier for someone to:
- reach the Codeberg repo first, or the GitHub mirror second
- understand what RalphWorkflow does
- know when to use it
- pick a first free real task
- judge the result honestly

If an action creates attention but does not increase qualified repo traffic or make the repo path clearer, it is lower leverage.

## Principle 4: Use proof assets
Trust grows from proof, not claims.

Strong proof assets:
- first-task guide
- example task spec
- example review bundle
- before/after workflow explanation
- when unattended coding works / does not work
- public docs and concrete examples

## Principle 5: Reuse content across channels
One strong idea should become:
- owned content
- repo/docs messaging
- community reply angles
- HN / Dev.to / Lobsters drafts
- Apollo account-based outbound variants
- SEO pages

## Principle 5a: Use Apollo.io carefully
- Use Apollo saved searches and search alerts to monitor new matching people and companies instead of manually reworking the same lists.
- Treat Apollo sequence performance and reports as measurement, not vanity. If the motion does not create qualified repo inspection or reply movement, it is not a healthy tactic.
- Protect deliverability with domain authentication, warmup/ramp-up, conservative sending limits, and caution around open/click tracking unless it is properly configured.
- Use Apollo tasks, workflows, and sequences only when they are measurable and safe for sender reputation.
- Current blocker truth: Apollo login automation is currently blocked by Cloudflare/auth protection from this environment, so Apollo must be treated as a monitored blocker until that access path is unblocked.

## Principle 6: Measure real movement
Track whether actions move:
- Codeberg traffic/adoption first
- GitHub mirror traffic/adoption second
- Codeberg stars/watchers/forks first
- GitHub mirror stars/watchers/forks second
- useful community engagement only when it plausibly leads to repo visits
- clearer repo conversion surfaces and stronger repo-directed CTA paths

Do not count mere activity as success.
A tactic is only healthy if it causes an observable change in adoption, trust, distribution quality, or conversion surfaces.
If the same tactic keeps running and the measurement window stays flat, mark that tactic as failing.

## Principle 7: Avoid local maxima
Do not get stuck in one channel, one message, or one asset type.
Keep asking:
- is Reddit still the best use of effort?
- is content still the bottleneck?
- should this shift to docs, repo conversion, examples, or distribution?

## Principle 8: No duplicate-topic posts without cross-links

When publishing a new blog post that covers a comparison target set already covered by an earlier post:

1. The newer post MUST explicitly cross-link to the earlier post with a differentiated purpose statement
2. The earlier post MUST gain a reciprocal link to the newer post
3. If the two posts cover the same tools and the same comparison angle, do not publish the second post — consolidate into the existing one instead
4. Each post must have a structurally distinct section organization (not just different word order)

Violating this creates duplicate-content risk with Google. Two posts covering the same 8-tool comparison target set with zero internal links looks like thin content spam to a search engine — even if both posts are individually well-written.

**This is a process gate, not a recommendation.** Before committing any new blog post, verify against existing content for tool-set overlap.

## Principle 9: Re-analyze the workflow itself
At regular intervals, review:
- what work was done
- what that work was supposed to improve
- whether it actually improved attention, trust, free use, or adoption
- what the next highest-leverage move is
- which tactics are failing and should be stopped, rewritten, or replaced

Every meaningful tactic should have:
- an expected outcome
- a measurement window
- a kill condition

If a tactic produces output but no movement, the loop must be able to say clearly that it is not working.
If a safe local repair path exists, the loop must execute that repair itself instead of stopping at recommendations.
If a question comes up that might normally be bounced to the human, the default operational answer is: it is up to the agent to decide, act, verify, and continue unless the action is genuinely irreversible, externally sensitive, or unsafe to guess.

## Operational discipline: cooldown windows

If the Reddit volume guard is active (cooldown state), do not run another monitor pass. The monitor will correctly return "no posting." Running it again during the same cooldown window produces redundant analysis with zero distribution output.

Instead, use the cooldown window to:
- Draft 2–3 fresh Reddit comment bodies for the best known upcoming opportunities
- Check whether non-Reddit distribution channels (HN, Lobsters) are accessible and worth pursuing
- Prepare the next GitHub conversion surface improvement

Before any non-Reddit directory submission, first search `outreach-log.md` and current marketing notes for that domain/tool name. Duplicate submissions burn cycles, blur measurement, and can make the workflow look productive without actually expanding distribution.

Only run the monitor when there is a realistic path to an actual post.

## Reddit posting discipline (mandatory, non-negotiable)

### Hard banned openings — NEVER use verbatim or paraphrased
The following first-lines are permanently banned from any Reddit post, comment, or reply:
- "Honestly the part I'd optimize first is the handoff, not the model stack."
- "My default is to optimize for a clean morning-after review, not maximum autonomy."
- "The best improvement I've seen is making the output easier to judge, not making the agent look busier."
- Any opening that starts with "I've had the best results when I stop optimizing" or any near-variant
- Any opening that starts with "I've had better results when I stop asking"

Before writing any Reddit body, read `agents/marketing/logs/reddit_posts.jsonl` and verify:
(a) Your opening line does not appear verbatim in any recent post
(b) Your opening line is not a paraphrased version of a recent opening
(c) Your first line uses a genuinely fresh angle — new pain frame, new structure, new voice

If you cannot guarantee a fresh opening, do not post. Write the body with a genuinely different first sentence or skip the post.

### Fresh opening template bank (rotate these structures, do not copy verbatim)
- Statement of a common failure: "The thing that breaks first in unattended coding runs is not the code — it's the handoff."
- Reframe the premise: "The question isn't which model is smartest. It's which workflow actually ends in something you can review."
- Concrete observation: "What I kept getting wrong early on was treating 'the agent said done' as a finish line instead of a checkpoint."

## Principle 10: Break the local maximum

A system that optimizes itself can get very good at doing things that don't matter. This is the *local maximum trap*: each audit fixes hygiene, tightens guards, and improves internal metrics while actual outcomes — repo stars, downloads-to-stars conversion, external trust — stay flat.

When the system has completed 48+ audit patches with zero adoption delta, it is not failing to improve. It is succeeding at optimizing the wrong things. The solution is not another monitoring patch. It is a structural break: a new agent, a new lane, a new execution path that targets the actual bottleneck (social proof, not content) and accepts that some internal perfection metrics will regress while outcome metrics get the budget.

**Mandate**:
- If the primary adoption metric (Codeberg star delta) remains zero after 3 consecutive audit runs, the system MUST create a new agent, replace a stale lane, or deploy a structural change — not generate another report.
- Internal hygiene improvements (wording tightenings, guard refinements, duplicate suppression) are not substitutes for growth actions.
- Every new marketing agent MUST name its expected outcome, measurement window, and replacement condition if it fails within that window.
- Runtime changes (commits, pushes, agent creation) beat report-generation when outcomes are flat.

**Diagnostic**:
If `skipped_regeneration_guard` or `noop` appears in the daily log for 3+ consecutive runs, the lane is saturated and must be replaced, not refined.

## Working question
What is the highest-leverage thing I can do right now to increase real RalphWorkflow adoption?

## Evaluation gate
Before calling the loop "self-improving," verify that it can answer all of these:
- What tactic ran?
- What outcome was expected?
- What changed in the measurement window?
- If nothing changed, did the loop mark the tactic as failing?
- What is being stopped, rewritten, or replaced because of that failure?
