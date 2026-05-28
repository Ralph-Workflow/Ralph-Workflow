# Ralph Workflow — Marketing Strategy & Reflection Loop

## Active Operating Model — 2026-05-12

The active automation path is now intentionally small:

1. `generate_content.py` creates RalphWorkflow-only drafts with experiment metadata.
2. `run_posting.py` publishes scheduled markdown drafts to Telegraph (write.as is permanently blocked).
3. `run.py` measures site health and post performance, then makes weekly content-mix decisions.
4. Blocked channels stay blocked until credentials/access change.
5. Apollo.io is a managed account-based outbound channel, but its automation state is currently monitored as blocked until login protection is unblocked.

### Paused from the active loop
- speculative channel discovery
- automated outreach to blocked communities
- broken SEO/backlink scripts
- mixed-product content generation

This strategy file remains the long-term record, but the current system should follow the cleanup plan in `CLEANUP_PLAN.md`.

## Product
- **Name:** Ralph Workflow
- **URL:** https://ralphworkflow.com
- **Tagline:** "Composable loop framework for AI engineering work on your own machine."
- **Target:** Engineering teams and solo developers who want AI agents to work unattended on ambitious, well-specified projects
- **Core differentiator:** Not a prompt tool — a composable loop framework and workflow system that runs AI agents as unattended engineering pipelines
- **Positioning reference:** `RALPH_WORKFLOW_POSITIONING.md`

## Traffic Channels

### Tier 1: Automated Posting (works today ✅)
| Platform | Status | Notes |
|----------|--------|-------|
| write.as | ❌ Blocked | Permanently blocked — do not use. Telegraph is primary. |
| Telegraph | ✅ Active | Anonymous posting, cron daily |

### Tier 2: Account-Based (blocked) 🔒
| Platform | Status | Blocker |
|----------|--------|---------|
| dev.to | 🔒 Needs GitHub OAuth | PAT is read-only |
| Twitter/X | 🔒 Login blocked | x.com shows errors |
| Reddit | 🔒 Needs account | Karma requirements |
| Apollo.io | 🔒 Cloudflare/auth blocked | Login automation is currently blocked from this environment |
| Lobsters | 🔒 Needs invite | No access |
| HN | 🔒 Needs invite | No access |
| YouTube | 🔒 Needs Google + phone | Phone verification |

### Tier 3: GitHub Outreach (blocked) 🔒
| Action | Status | Blocker |
|--------|--------|---------|
| File issues | 🔒 403 | PAT read-only |
| Submit PRs | 🔒 403 | PAT read-only |
| Update topics | 🔒 403 | PAT read-only |

### Tier 4: SEO & Directories (can improve today)
- ✅ Sitemap.xml (242 URLs)
- ✅ Robots.txt
- ✅ Meta tags + OG tags
- ✅ Schema.org markup
- ❌ No Google Search Console
- ❌ 0 indexed backlinks in the active measurement window from repo citations / recent submissions
- ✅ Live third-party proof surfaces include ToolWise and SaaSHub; additional directory submissions are pending review/indexing
- ❌ Not on Product Hunt

## Content Calendar
| Day | Type | Focus |
|-----|------|-------|
| Monday | Philosophy | Why AI agents need structure over prompts |
| Wednesday | Technical | How nested analysis loops work |
| Friday | Use case | ROI story / "what I shipped" |

## Reflection Loop — How It Works

### Weekly Review (every Monday)
The marketing agent runs and asks:

**1. What happened?**
- Traffic changes (Telegraph views, SEO rankings)
- GitHub activity (stars, forks, mentions)
- Outreach results (PRs merged, issues responded)

**2. What worked?**
- Which content drove the most views?
- Which channels brought visitors?
- Which headlines/CTAs got engagement?

**3. What didn't work?**
- Low-performing content → revise or retire
- Blocked channels → find workarounds
- Wrong targeting → refine audience

**4. What's the next experiment?**
- Try a new keyword
- Adjust content tone
- Pursue a new platform
- Double down on what works

### Decision Rules (automated)
| Signal | Action |
|--------|--------|
| write.as article > 100 views | Cross-post to dev.to |
| GitHub star spike | Analyze which content triggered it |
| SEO ranking improved for keyword X | Create more content around X |
| No outreach responses in 2 weeks | Change message tone |
| Twitter accessible again | Deploy Twitter bot |
| New GitHub PAT available | Enable full outreach pipeline |

## What's Working (as of 2026-05-09)
- Content pipeline fully automated (write.as + Telegraph)
- SEO fundamentals solid (sitemap, meta, schema)
- 103 GitHub repos mention RW (potential backlinks)
- All target keywords have low competition

## What's Not Working
- Zero backlinks (outreach blocked by read-only PAT)
- Twitter blocked (login errors)
- No Google Search Console (can't see actual traffic)

## Experiments to Try
1. **Keyword-targeted write.as posts** — "best AI coding workflow 2025", "how to run AI agents unattended"
2. **SEO-only landing page** — target "ralph workflow alternative to [competitor]"
3. **Submit to Product Hunt** — needs real launch, not just listing
4. **dev.to via GitHub OAuth** — if PAT becomes read-write
5. **Buy a反向链接 (backlink) via guest post** — requires finding dev blogs

## Operating goal
The sole marketing goal is to drive qualified traffic to the Ralph Workflow repositories.

Priority order:
1. Codeberg primary repo
2. GitHub mirror repo

That means the loop should prefer actions that increase repo visits, repo inspection, repo stars/watchers/forks, and real first-run interest over generic awareness work.

## Apollo.io channel guidance

Apollo.io is a managed account-based outbound and distribution channel for Ralph Workflow when it is safely usable.

- Keep the product framing intact in Apollo outreach: Ralph Workflow is free and open source, runs existing coding agents on your own machine, is built for ambitious unattended overnight work, and should direct serious evaluators to the Codeberg primary repo first and the GitHub mirror second.
- Use Apollo saved searches and search alerts to monitor newly matching people and companies instead of doing vanity list churn.
- Treat Apollo sequence and report views as measurement surfaces, not vanity dashboards. A sequence is only healthy if it plausibly drives qualified repo visits, replies, or inspection behavior.
- Protect deliverability before scaling any Apollo sending motion: domain authentication, warmup and ramp-up, conservative sending limits, and caution around open/click tracking unless the tracking setup is known to be safe and correctly configured.
- Use Apollo tasks, workflows, and sequences only when the motion is measurable and safe for sender reputation.
- Current blocker truth must come from runtime status, not stale strategy text. If `agents/marketing/logs/apollo_status.json` shows a recent `login_succeeded` state without Cloudflare blocking, Apollo is a live managed-outbound lane; if runtime status shows auth or Cloudflare failure, treat Apollo as blocked until it recovers.

## Open Questions
- Improve measurement of which tactics plausibly increase repo visits and repo inspection.
- Keep Codeberg as the primary repo target and GitHub as the mirror target in all public-facing conversion paths.

## Last Full Strategy Review
2026-05-09 — Initial strategy built. Pipeline deployed. Outreach blocked.



## Channel Status — 2026-05-09

### Automated Posting ✅
| Channel | Status | Notes |
|---------|--------|-------|
| write.as | ❌ Blocked | Permanently blocked — use Telegraph only. |
| Telegraph | ❌ Broken | Entire API returns UNKNOWN_METHOD — platform may be deprecated |

### Account-Based Channels 🔒
| Channel | Status | Notes |
|---------|--------|-------|
| dev.to | 🔒 Needs API key | Read works, posting needs auth |
| Twitter/X | 🔒 Login errors | x.com flow fails, mobile too |
| Reddit | 🔒 Needs account | All redirects to login |
| HN | 🔒 Needs account | Requires login, no anonymous submit |
| Lobsters | 🔒 Needs invite | No access |
| Indie Hackers | 🔒 Firebase auth | Google OAuth required |
| Product Hunt | 🔒 Cloudflare | Bot protection blocks automation |
| Bluesky | 🔒 Phone verify | Requires phone number |

### GitHub 🔒
| Action | Status | Notes |
|--------|--------|-------|
| Read repos | ✅ Works | PAT allows search/read |
| Create Gist | ❌ 403 | PAT is read-only |
| File Issue | ❌ 403 | PAT is read-only |
| Submit PR | ❌ 403 | PAT is read-only |
| Update README | ❌ 403 | PAT is read-only |

### SEO / Directories
| Action | Status | Notes |
|--------|--------|-------|
| Sitemap | ✅ Working | 242 URLs |
| Robots.txt | ✅ Working | Accessible |
| SEO meta | ✅ Working | OG, Twitter cards, schema |
| GSC | ❌ No access | Can't see search data |
| Backlinks | ❌ 0 | 103 repos mention RW but none link |

## What to Do Right Now (No Credentials Needed)

1. **Improve Telegraph content** — Target low-competition keywords (unattended coding agent, AI agent orchestration CLI)
2. **Publish keyword-gap Telegraph posts** — Target "unattended coding agent" and "AI agent orchestration CLI" specifically
3. **Submit to more directories** — Find free tool directories
4. **Improve ralphworkflow.com SEO** — Better internal linking, more content
5. **Create linkable assets** — SEO-optimized standalone pages

## What Needs Credentials (Highest Impact When Unblocked)

1. **GitHub PAT with write access** — Enables full outreach (103 repos, 0 backlinks)
2. **Twitter session cookies** — Enables bot posting
3. **dev.to API key** — Enables article cross-posting
4. **HN account** — Enables Show HN post




## Strategy Review — 2026-05-11

### Site Health
- ralphworkflow.com: {'ok': True, 'status': 200}

### SEO Rankings
- ✅ Ralph Workflow AI
- ✅ AI agent workflow composer
- ✅ unattended AI coding pipeline
- ✅ AI engineering workflow

### GitHub
- Stars: ?
- Forks: ?

### Content Performance (write.as)

### Recommendations
- Low GitHub visibility. Consider: posting to relevant subreddits, asking for stars from early users

---



## Weekly Reflection — 2026-05-11

### Trends Detected

### Best Content
- No content data yet

### Action Items
- **[ONGOING]** Wait for GitHub read-write PAT to enable outreach pipeline
  → *75+ repos need backlink outreach but current token is read-only*
- **[ONGOING]** Try to unblock Twitter when login errors clear
  → *Twitter would unlock massive reach*

---

## Weekly Review — 2026-05-18

### SEO Health
**SEO Score:** 65/100 (C) | Ranked keywords: 0 | Backlinks: 0 | DR: None
**Trends:** ranks 0.0

### Content Performance
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 5 posts, 0 total views, 0.0 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** Fix on-page SEO issues before investing in new content. — SEO score is 65/100 — technical foundation needs attention.
- **[MEDIUM]** Keep publishing philosophy content. — Best avg views: 0.0 — lean into what's working.
- **[HIGH]** Build backlinks — submit to directories and pursue guest post opportunities. — Zero backlinks detected. Link acquisition is the highest-leverage SEO activity right now.
- **[MEDIUM]** Fix top on-page SEO issues (current score: 65/100) — Identified by daily SEO analysis as a top priority.
- **[MEDIUM]** Create content targeting: unattended coding agent, AI agent orchestration CLI, spec-driven AI agent — Identified by daily SEO analysis as a top priority.
- **[ONGOING]** Continue write.as + Telegraph posting until blocked channels are unblocked. — Working distribution channel. Track ratio of views per post to gauge platform value.
- **[MEDIUM]** Leverage competitor comparison pages in content and outreach. — Monitoring 8 competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.
- **[INFO]** Note: hermes-agent has 155986 GitHub stars — lean into Ralph's cost and flexibility advantages. — Competitor intelligence for positioning decisions.

### Priority Actions (from SEO analysis)
- Fix top on-page SEO issues (current score: 65/100)
- Create content targeting: unattended coding agent, AI agent orchestration CLI, spec-driven AI agent
- Build backlinks: submit to directories, guest post, earn citations

## Weekly Review — 2026-05-19

### SEO Health
**SEO Score:** 70/100 (C) | Ranked keywords: 0 | Backlinks: 0 | DR: None
**Trends:** ranks 0.0

### Content Performance
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 16 posts, 0 total views, 0.0 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[HIGH]** Fix on-page SEO issues before investing in new content. — SEO score is 70/100 — technical foundation needs attention.
- **[MEDIUM]** Keep publishing philosophy content. — Best avg views: 0.0 — lean into what's working.
- **[HIGH]** Build backlinks — submit to directories and pursue guest post opportunities. — Zero backlinks detected. Link acquisition is the highest-leverage SEO activity right now.
- **[MEDIUM]** Fix top on-page SEO issues (current score: 70/100) — Identified by daily SEO analysis as a top priority.
- **[MEDIUM]** Create content targeting: unattended coding agent, AI agent orchestration CLI, spec-driven AI agent — Identified by daily SEO analysis as a top priority.
- **[ONGOING]** Continue Telegraph posting. write.as is permanently blocked — do not use. Seek Dev.to API key for second platform. — Working distribution channel. Track ratio of views per post to gauge platform value.

### Priority Actions (from SEO analysis)
- Fix top on-page SEO issues (current score: 70/100)
- Create content targeting: unattended coding agent, AI agent orchestration CLI, spec-driven AI agent
- Build backlinks: submit to directories, guest post, earn citations

## Weekly Review — 2026-05-20

### SEO Health
**SEO Score:** 70/100 (C) | Ranked keywords: 0 | Backlinks: 0 | DR: None
**Trends:** ranks 0.0

### Content Performance
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- seo-guide: 1 posts, 0 total views, 0.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 29 posts, 0 total views, 0.0 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[HIGH]** Fix on-page SEO issues before investing in new content. — SEO score is 70/100 — technical foundation needs attention.
- **[MEDIUM]** Keep publishing philosophy content. — Best avg views: 0.0 — lean into what's working.
- **[HIGH]** Build backlinks — submit to directories and pursue guest post opportunities. — Zero backlinks detected. Link acquisition is the highest-leverage SEO activity right now.
- **[MEDIUM]** Fix top on-page SEO issues (current score: 70/100) — Identified by daily SEO analysis as a top priority.
- **[MEDIUM]** Create content targeting: spec-driven AI agent, AI coding workflow automation, Claude Code automation — Identified by daily SEO analysis as a top priority.
- **[ONGOING]** Continue Telegraph posting. write.as is permanently blocked — do not use. Seek Dev.to API key for second platform. — Working distribution channel. Track ratio of views per post to gauge platform value.

### Priority Actions (from SEO analysis)
- Fix top on-page SEO issues (current score: 70/100)
- Create content targeting: spec-driven AI agent, AI coding workflow automation, Claude Code automation
- Build backlinks: submit to directories, guest post, earn citations

## Weekly Review — 2026-05-21

### SEO Health
**SEO Score:** 70/100 (C) | Ranked keywords: 0 | Backlinks: 0 | DR: None
**Trends:** ranks 0.0

### Content Performance
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- seo-guide: 1 posts, 0 total views, 0.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 29 posts, 0 total views, 0.0 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[HIGH]** Fix on-page SEO issues before investing in new content. — SEO score is 70/100 — technical foundation needs attention.
- **[MEDIUM]** Keep publishing philosophy content. — Best avg views: 0.0 — lean into what's working.
- **[HIGH]** Build backlinks — submit to directories and pursue guest post opportunities. — Zero backlinks detected. Link acquisition is the highest-leverage SEO activity right now.
- **[MEDIUM]** Fix top on-page SEO issues (current score: 70/100) — Identified by daily SEO analysis as a top priority.
- **[MEDIUM]** Create content targeting: unattended coding agent, AI agent orchestration CLI, spec-driven AI agent — Identified by daily SEO analysis as a top priority.
- **[ONGOING]** Continue Telegraph posting. write.as is permanently blocked — do not use. Seek Dev.to API key for second platform. — Working distribution channel. Track ratio of views per post to gauge platform value.

### Priority Actions (from SEO analysis)
- Fix top on-page SEO issues (current score: 70/100)
- Create content targeting: unattended coding agent, AI agent orchestration CLI, spec-driven AI agent
- Build backlinks: submit to directories, guest post, earn citations

## Weekly Review — 2026-05-22

### SEO Health
**SEO Score:** 70/100 (C) | Ranked keywords: 0 | Backlinks: 0 | DR: None
**Trends:** ranks 0.0

### Content Performance
- conversion-asset: 1 posts, 0 total views, 0.0 avg views/publish
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- seo-guide: 1 posts, 0 total views, 0.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 32 posts, 0 total views, 0.0 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[HIGH]** Fix on-page SEO issues before investing in new content. — SEO score is 70/100 — technical foundation needs attention.
- **[INFO]** Do not infer a winning owned-content format yet. — Current content-performance logs show zero measurable views, so format recommendations would be guesswork.
- **[HIGH]** Build backlinks — submit to directories and pursue guest post opportunities. — Zero backlinks detected. Link acquisition is the highest-leverage SEO activity right now.
- **[MEDIUM]** Fix top on-page SEO issues (current score: 70/100) — Identified by daily SEO analysis as a top priority.
- **[MEDIUM]** Hold Telegraph at maintenance only; do not treat more owned-content volume as the next best move. — Primary repo adoption is flat and owned-content output is already saturated; shift effort to curator, backlink, and comparison distribution lanes.
- **[MEDIUM]** Ship comparison-led backlink outreach packets whenever the curator queue is already full. — A follow-through note is not enough when the queue is saturated; the loop needs a fresh executable distribution asset tied to existing comparison pages.
- **[MEDIUM]** Leverage competitor comparison pages in content and outreach. — Monitoring 8 competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.
- **[INFO]** Note: hermes-agent has 162873 GitHub stars — lean into Ralph's cost and flexibility advantages. — Competitor intelligence for positioning decisions.

### Priority Actions (from SEO analysis)
- Fix top on-page SEO issues (current score: 70/100)
- Create content targeting: spec-driven AI agent, AI coding workflow automation, Claude Code automation
- Build backlinks: submit to directories, guest post, earn citations

## Weekly Review — 2026-05-23

### SEO Health
**SEO Score:** 70/100 (C) | Ranked keywords: 0 | Backlinks: 0 | DR: None
**Trends:** ranks 0.0

### Content Performance
- conversion-asset: 1 posts, 0 total views, 0.0 avg views/publish
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- seo-guide: 1 posts, 0 total views, 0.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 33 posts, 0 total views, 0.0 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[HIGH]** Fix on-page SEO issues before investing in new content. — SEO score is 70/100 — technical foundation needs attention.
- **[INFO]** Do not infer a winning owned-content format yet. — Current content-performance logs show zero measurable views, so format recommendations would be guesswork.
- **[HIGH]** Build backlinks — submit to directories and pursue guest post opportunities. — Zero backlinks detected. Link acquisition is the highest-leverage SEO activity right now.
- **[MEDIUM]** Fix top on-page SEO issues (current score: 70/100) — Identified by daily SEO analysis as a top priority.
- **[MEDIUM]** Hold Telegraph at maintenance only; do not treat more owned-content volume as the next best move. — Primary repo adoption is flat and owned-content output is already saturated; shift effort to curator, backlink, and comparison distribution lanes.
- **[MEDIUM]** Ship comparison-led backlink outreach packets whenever the curator queue is already full. — A follow-through note is not enough when the queue is saturated; the loop needs a fresh executable distribution asset tied to existing comparison pages.
- **[MEDIUM]** Leverage competitor comparison pages in content and outreach. — Monitoring 8 competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.
- **[INFO]** Note: hermes-agent has 164211 GitHub stars — lean into Ralph's cost and flexibility advantages. — Competitor intelligence for positioning decisions.

### Priority Actions (from SEO analysis)
- Fix top on-page SEO issues (current score: 70/100)
- Create content targeting: unattended coding agent, AI agent orchestration CLI, spec-driven AI agent
- Build backlinks: submit to directories, guest post, earn citations

## Weekly Review — 2026-05-24

### SEO Health
**SEO Score:** 90/100 (A) | Ranked keywords: 0 | Backlinks: 2 | DR: None
**Trends:** ranks 0.0

### Content Performance
- conversion-asset: 1 posts, 0 total views, 0.0 avg views/publish
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- seo-guide: 1 posts, 0 total views, 0.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 33 posts, 0 total views, 0.0 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[INFO]** Do not infer a winning owned-content format yet. — Current content-performance logs show zero measurable views, so format recommendations would be guesswork.
- **[MEDIUM]** Hold Telegraph at maintenance only; do not treat more owned-content volume as the next best move. — Primary repo adoption is flat and owned-content output is already saturated; shift effort to curator, backlink, and comparison distribution lanes.
- **[MEDIUM]** Ship comparison-led backlink outreach packets whenever the curator queue is already full. — A follow-through note is not enough when the queue is saturated; the loop needs a fresh executable distribution asset tied to existing comparison pages.
- **[MEDIUM]** Leverage competitor comparison pages in content and outreach. — Monitoring 8 competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.
- **[INFO]** Note: hermes-agent has 164749 GitHub stars — lean into Ralph's cost and flexibility advantages. — Competitor intelligence for positioning decisions.

### Priority Actions (from SEO analysis)
- Create content targeting: Claude Code unattended, AI agent workflow composer

## Weekly Review — 2026-05-25

### SEO Health
**SEO Score:** unknown | Ranked keywords: ? | Backlinks: ? | DR: ?
**Trends:** ranks ?

### Content Performance
- No measurable posts yet.

### Weekly Decisions
- **[HIGH]** Build backlinks — submit to directories and pursue guest post opportunities. — Zero backlinks detected. Link acquisition is the highest-leverage SEO activity right now.
- **[ONGOING]** Continue only the owned/distribution channels that have current runtime proof, and keep Codeberg as the primary CTA. — When adoption is moving, scale the channels with live execution proof instead of relying on stale channel doctrine.
- **[MEDIUM]** Leverage competitor comparison pages in content and outreach. — Monitoring 8 competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.
- **[INFO]** Note: hermes-agent has 167100 GitHub stars — lean into Ralph's cost and flexibility advantages. — Competitor intelligence for positioning decisions.

### Priority Actions (from SEO analysis)
- Collect more data

## Weekly Review — 2026-05-26

### SEO Health
**SEO Score:** 100/100 (A) | Ranked keywords: 0 | Backlinks: 3 | DR: None
**Trends:** ranks 0.0

### Content Performance
- conversion-asset: 1 posts, 128 total views, 128.0 avg views/publish
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- seo-guide: 1 posts, 133 total views, 133.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 36 posts, 3549 total views, 98.58 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[MEDIUM]** Keep publishing seo-guide content. — Best avg views: 133.0 — lean into what's working.
- **[MEDIUM]** Shift one future slot away from usecase toward seo-guide. — seo-guide outperforms usecase on avg views.
- **[MEDIUM]** Hold Telegraph at maintenance only; do not treat more owned-content volume as the next best move. — Primary repo adoption is flat and owned-content output is already saturated; shift effort to curator, backlink, and comparison distribution lanes.
- **[MEDIUM]** Ship comparison-led backlink outreach packets whenever the curator queue is already full. — A follow-through note is not enough when the queue is saturated; the loop needs a fresh executable distribution asset tied to existing comparison pages.
- **[MEDIUM]** Leverage competitor comparison pages in content and outreach. — Monitoring 8 competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.
- **[INFO]** Note: hermes-agent has 168463 GitHub stars — lean into Ralph's cost and flexibility advantages. — Competitor intelligence for positioning decisions.

### Priority Actions (from SEO analysis)
- Collect more data

## Weekly Review — 2026-05-27

### SEO Health
**SEO Score:** 100/100 (A) | Ranked keywords: 0 | Backlinks: 0 | DR: None
**Trends:** ranks 0.0

### Content Performance
- conversion-asset: 1 posts, 128 total views, 128.0 avg views/publish
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- seo-guide: 1 posts, 133 total views, 133.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 37 posts, 3549 total views, 95.92 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[MEDIUM]** Keep publishing seo-guide content. — Best avg views: 133.0 — lean into what's working.
- **[MEDIUM]** Shift one future slot away from usecase toward seo-guide. — seo-guide outperforms usecase on avg views.
- **[MEDIUM]** Build backlinks: submit to directories, guest post, earn citations — Identified by daily SEO analysis as a top priority.
- **[MEDIUM]** Hold Telegraph at maintenance only; do not treat more owned-content volume as the next best move. — Primary repo adoption is flat and owned-content output is already saturated; shift effort to curator, backlink, and comparison distribution lanes.
- **[MEDIUM]** Ship comparison-led backlink outreach packets whenever the curator queue is already full. — A follow-through note is not enough when the queue is saturated; the loop needs a fresh executable distribution asset tied to existing comparison pages.
- **[MEDIUM]** Leverage competitor comparison pages in content and outreach. — Monitoring 8 competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.
- **[INFO]** Note: hermes-agent has 169289 GitHub stars — lean into Ralph's cost and flexibility advantages. — Competitor intelligence for positioning decisions.

### Priority Actions (from SEO analysis)
- Create content targeting: AI agent orchestration CLI, spec-driven AI agent, AI coding workflow automation
- Build backlinks: submit to directories, guest post, earn citations

## Weekly Review — 2026-05-28

### SEO Health
**SEO Score:** 100/100 (A) | Ranked keywords: 0 | Backlinks: 0 | DR: None
**Trends:** ranks 0.0

### Content Performance
- conversion-asset: 1 posts, 128 total views, 128.0 avg views/publish
- philosophy: 1 posts, 0 total views, 0.0 avg views/publish
- seo-guide: 1 posts, 133 total views, 133.0 avg views/publish
- technical: 1 posts, 0 total views, 0.0 avg views/publish
- unknown: 39 posts, 3550 total views, 91.03 avg views/publish
- usecase: 1 posts, 0 total views, 0.0 avg views/publish

### Weekly Decisions
- **[HIGH]** MARK AS FAILING: Current content/distribution tactics are not driving repo adoption. — Codeberg repo adoption flat across 9 samples (stars +0, watchers +0, forks +0)
- **[MEDIUM]** Keep publishing seo-guide content. — Best avg views: 133.0 — lean into what's working.
- **[MEDIUM]** Shift one future slot away from usecase toward seo-guide. — seo-guide outperforms usecase on avg views.
- **[MEDIUM]** Build backlinks: submit to directories, guest post, earn citations — Identified by daily SEO analysis as a top priority.
- **[MEDIUM]** Hold Telegraph at maintenance only; do not treat more owned-content volume as the next best move. — Primary repo adoption is flat and owned-content output is already saturated; shift effort to curator, backlink, and comparison distribution lanes.
- **[MEDIUM]** Ship comparison-led backlink outreach packets whenever the curator queue is already full. — A follow-through note is not enough when the queue is saturated; the loop needs a fresh executable distribution asset tied to existing comparison pages.
- **[MEDIUM]** Leverage competitor comparison pages in content and outreach. — Monitoring 8 competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.
- **[INFO]** Note: hermes-agent has 170226 GitHub stars — lean into Ralph's cost and flexibility advantages. — Competitor intelligence for positioning decisions.

### Priority Actions (from SEO analysis)
- Create content targeting: unattended coding agent, AI agent orchestration CLI, spec-driven AI agent
- Build backlinks: submit to directories, guest post, earn citations