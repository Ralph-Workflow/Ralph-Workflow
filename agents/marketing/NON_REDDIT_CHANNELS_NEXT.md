# RalphWorkflow - Non-Reddit channels to push next

## Bottom line
The strongest **immediately actionable** non-Reddit move is to publish a practical **Claude Code + Codex workflow** article on an owned channel first, then adapt it for communities that reward concrete workflow writeups.

Why this is the best next move:
- it matches the clearest live market pain: people want a better handoff between planning, implementation, and review
- it fits the homepage language: **too big to babysit, too risky to trust blindly**, **walk away and come back to something reviewable**
- it can be executed from this machine without new accounts or risky posting flows
- it creates reusable source material for Hacker News, Lobsters, GitHub/community posts, docs pages, and future outreach

## What the market is telling us
Across recent research and Reddit-adjacent monitoring, the repeated pain is not "give me more code generation." It is:
- agents say they are done when they are not
- people manually glue together Claude Code and Codex
- long runs need a clean re-entry point
- worktrees help, but do not solve trust or final review
- people want proof, checks, and a reviewable diff

That lines up tightly with the site message:
- **stop monitoring the session**
- **proof of completion, not just a claim**
- **delegate the job and review the result**

## 1) Immediately actionable from this machine

### A. Owned content: publish to write.as AND Telegraph simultaneously, then adapt into site/docs
**Priority: highest**

**⚠️ Distribution rule (2026-05-19): stop publishing to write.as alone.** Every content piece must go to multiple platforms at once. write.as-only publishing has produced flat repo adoption — diversify.

**Best topic:**
- **Claude Code + Codex workflow: plan, build, review — and come back to something reviewable**

**Why this should go first:**
- strongest repeated keyword/content gap in recent research
- best bridge between current community pain and RalphWorkflow's positioning
- write.as is already proven working anonymously from this environment
- creates a canonical linkable argument without sounding like a launch post
- Telegraph cross-post is already wired in `post_to_web.py` — use it every time

**Distribution checklist for every article:**
1. Post to write.as
2. Post to Telegraph (same body, same time — `post_to_web.py` handles both)
3. Link Codeberg as primary repo in both posts: `https://codeberg.org/RalphWorkflow/Ralph-Workflow`
4. Link GitHub as mirror only: `https://github.com/Ralph-Workflow/Ralph-Workflow`
5. Adapt excerpt into a docs page on the repo or site

**Recommended structure:**
1. The real problem: the tool said "done," but the job did not hold up
2. Why people split planning/build/review across tools
3. A boring workflow that works: small scope, acceptance criteria, isolated run, independent check, reviewable diff
4. Where manual glue becomes painful
5. Where RalphWorkflow fits: not replacing tools, improving the finish
6. When not to use unattended runs

**Suggested title options:**
- Claude Code + Codex Workflow: Plan, Build, Review
- How to Hand Off an AI Coding Task and Come Back to Something Reviewable
- The Missing Step in AI Coding Workflows: Proof, Not Just "Done"

**Immediate next actions:**
- draft the article on write.as
- create a cleaner owned-site version as a docs or guide page
- make sure both use homepage phrases like **reviewable diff**, **clean re-entry point**, **proof it holds up**

### B. SEO/content pages on the RalphWorkflow site
**Priority: very high**

The SEO report shows no ranking data yet, 0 backlinks, and clear homepage content gaps. The easiest thing to improve without platform risk is owned search-targeted content.

**Best pages to add next:**
1. **Claude Code + Codex workflow**
2. **How to tell if an AI coding task is actually done**
3. **When unattended AI coding is worth it**
4. **How to review AI coding output before merge**

**Why these pages matter:**
- they target phrases already showing up in community language
- they match intent better than generic "AI orchestration" pages
- they can rank earlier than broad category terms
- they give future community posts something specific to reference

**Important messaging rule:**
Do not write these as vendor pages. Write them as practical workflow pages that happen to end with RalphWorkflow as the cleanest implementation.

### C. GitHub/community content from the repo/docs side
**Priority: medium-high**

If RalphWorkflow is open source and docs are public, GitHub can be used as a trust surface even without "social posting."

**Immediately actionable ideas:**
- add a docs page or example called **plan-build-review workflow**
- add a concrete example task: backlog item -> sharpen -> build -> verify -> review
- add a comparison page: **DIY Claude Code + Codex handoff vs RalphWorkflow**
- tighten README/docs phrasing around **reviewable diff**, **proof of completion**, **resume mid-job**

**Why it matters:**
- GitHub visitors are high intent
- docs pages can be linked from write.as or HN later
- practical examples reduce abstract-tool skepticism

### D. Hacker News preparation package
**Priority: medium-high now, posting blocked by account**

HN is strategically strong because the current RalphWorkflow message is skeptical, practical, and workflow-oriented rather than hypey.

**What can be prepared immediately from this machine:**
- one strong post draft based on the article above
- one "Show HN" style angle only if there is genuinely new product/news value
- one "Ask HN" or discussion angle around: **How do you know an AI coding task is actually done?**
- a comment bank of 3-5 grounded replies for likely skepticism:
  - why not just use worktrees?
  - why isn't code review enough?
  - why not just use Claude Code or Codex directly?

**Best HN angle:**
Not "AI orchestration platform."
Better: **the workflow gap between an agent saying done and a result you'd actually merge**.

## 2) Blocked but strategically valuable

### A. Dev.to
**Status:** blocked by account/auth issues

The outreach log says Dev.to login is blocked because the account is unconfirmed and GitHub OAuth is unavailable.

**Why it is still valuable:**
- good fit for practical engineering workflow explainers
- canonical place for "how I use X + Y" content
- easier discovery than a personal blog alone

**What to prepare while blocked:**
- finalize the previously drafted Dev.to article in local form
- adapt it away from broad "real problem with AI coding tools" into a sharper workflow angle
- keep a publish-ready version for when auth is restored

**Best Dev.to topics once unblocked:**
1. Claude Code + Codex workflow
2. How to review AI coding output before merge
3. When unattended AI coding is worth it

### B. Hacker News account-dependent posting
**Status:** accessible, but no active account

**Why valuable:**
- strongest external audience for skeptical workflow/process thinking
- good fit for RalphWorkflow's plain language and anti-hype framing

**Blocked requirement:**
- active HN account with enough trust/history to avoid low-visibility dead-on-arrival submissions

**What to do next when unblocked:**
- submit the best owned article, not the homepage, unless there is real launch/news
- prefer discussion-worthy essays over direct product submission

### C. Lobsters
**Status:** likely blocked by account/invite/community access norms

**Why valuable:**
- high technical audience
- practical workflow and tooling discussions perform better than broad marketing

**Reality check:**
Lobsters is only worth using if the post is genuinely technical and non-promotional.

**Best future angle:**
- a technical writeup on unattended coding workflow design, verification loops, or reviewable diffs
- not a generic product launch

### D. LinkedIn
**Status:** not checked

**Potential value:**
- weaker for raw technical trust discussions
- stronger if targeting engineering managers, staff engineers, or people thinking about process and review cost

**Why not first:**
- likely lower signal than owned content + HN-style communities
- requires a different tone and probably an identified personal/company account strategy

## 3) Owned-channel opportunities

### A. write.as + Telegraph
**Best owned channel right now — but use both platforms every time**

write.as and Telegraph are both already wired in `post_to_web.py`. Ship every article to both simultaneously. Never publish to one without the other.

**Recommended cadence:**
- publish 1 flagship practical article now (to both platforms)
- then 1 follow-up within a few days

**Best next two articles:**
1. **Claude Code + Codex Workflow: Plan, Build, Review**
2. **How to Tell if an AI Coding Task Is Actually Done**

**CTA rule:** In every post, link Codeberg as primary: `https://codeberg.org/RalphWorkflow/Ralph-Workflow`. GitHub is the mirror: `https://github.com/Ralph-Workflow/Ralph-Workflow`.

### B. Site/docs
Use docs/site pages for durable search capture and trust-building.

**Best additions:**
- /guides/claude-code-codex-workflow
- /guides/review-ai-coding-output
- /guides/unattended-ai-coding-when-it-works
- /guides/reviewable-diff-vs-agent-says-done

**One especially useful owned-page concept:**
A side-by-side page:
- manual loop: spec -> Claude Code -> Codex -> manual review glue
- RalphWorkflow loop: sharpen -> build -> verify -> reviewable diff

### C. GitHub docs/examples
Use examples to make the product legible.

**Best next additions:**
- example task spec
- example review bundle / final output shape
- example of a "good first unattended task"
- docs page on when to avoid unattended use

That last one is especially good because it increases trust.

## 4) Channel-by-channel recommendations

### write.as + Telegraph
- **Do next:** publish the Claude Code + Codex workflow article to BOTH platforms simultaneously
- **Why:** fastest path, proven channel, best message fit; Telegraph is already wired in `post_to_web.py`
- **CTA rule:** link Codeberg as primary (`https://codeberg.org/RalphWorkflow/Ralph-Workflow`), GitHub as mirror

### Hacker News
- **Do next:** prepare submission copy and skeptical comment responses
- **Blocked by:** account
- **Best asset to submit later:** owned explainer article, not generic homepage

### Dev.to
- **Do next:** keep a polished article ready locally
- **Blocked by:** account confirmation/auth
- **Best angle later:** practical workflow tutorial, not launch copy

### Lobsters
- **Do next:** prepare only if there is a technically grounded essay
- **Blocked by:** likely account/community access
- **Best angle later:** verification loop / reviewable-diff design, not product intro

### GitHub/community content
- **Do next:** ship docs/examples/comparison content in repo/site
- **Why:** immediately actionable, trust-building, linkable

### SEO/content pages
- **Do next:** publish search-intent guide pages around review, proof, unattended runs, Claude+Codex workflows
- **Why:** current site has clear keyword gaps and no backlinks yet

### Other high-fit avenue: directory/listing/backlink groundwork
**Priority: medium**

The SEO report shows roughly 0 backlinks. Even before broad PR, RalphWorkflow needs a small set of relevant citations.

**What can be prepared now:**
- a shortlist of open source / AI tools / dev workflow directories
- a short consistent product description using the homepage language
- UTM-safe destination pages for submissions later

This is less urgent than the content piece, but worth batching soon.

## 5) Recommended next moves

### Top 3 next moves
1. **Publish a write.as + Telegraph dual-post on "Claude Code + Codex workflow: plan, build, review" and treat it as the source asset for every other channel. Both platforms ship simultaneously — post_to_web.py handles this.**
2. **Turn that article into one or two owned site/docs pages targeting high-intent search terms around reviewable AI coding output and unattended runs.**
3. **Prepare account-blocked channels now: HN submission draft, Dev.to publish-ready version, and a technically credible Lobsters angle.**

## 6) Clear recommendation
If only one non-Reddit move gets done next, it should be:

**Write and publish the Claude Code + Codex workflow explainer to write.as AND Telegraph simultaneously (dual-post), link Codeberg as primary in both posts, then adapt it into RalphWorkflow docs/site content.**

That is the highest-leverage move available from this machine right now.