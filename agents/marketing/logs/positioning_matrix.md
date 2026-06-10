# POSITIONING × ICP MATRIX — what we KNOW about which wording converts (live, 2026-06-10T21:15:56)

> Auto-accumulated every gate run by apollo_positioning_matrix.py. THIS FILE is the
> program's knowledge deliverable: run the loop → this fills in. Winner claims appear
> ONLY at >=30/arm + >=3 replies (real test wave ≈200).

## The matrix (one row per angle×ICP arm, live Apollo stats)
| Arm | Angle × ICP | enrolled | delivered | replies | bounced | status |
|---|---|---|---|---|---|---|
| V1 | AI Observability OSPM | 14 | 5 | 0 | 3 | DEAD (R2 60.0% bounce; do not re-activate) |
| V11 | ClickerMomTest v2 AI Agent | 3 | 0 | 0 | 0 | UNTESTED (inactive) |
| V2 | DevTool OSPM DevRel | 2 | 0 | 0 | 1 | UNTESTED (inactive) |
| V3 | AI Agent Composition | 30 | 30 | 0 | 0 | POWERED-NULL (30 delivered, 0 replies — angle/channel not converting) |
| V4 | SpecDriven OpenStandards | 2 | 0 | 0 | 0 | UNTESTED (inactive) |
| V5 | OSS Maintainer Distribution | 1 | 0 | 0 | 0 | UNTESTED (inactive) |
| V7 | AI Observability OSPM clean | 10 | 0 | 0 | 1 | UNTESTED (inactive) |
| V8 | ClickerMomTest | 15 | 0 | 0 | 0 | UNTESTED (inactive) |
| V9 | AI Observability DevRel | 31 | 31 | 0 | 0 | POWERED-NULL (31 delivered, 0 replies — angle/channel not converting) |

## ✅ WHAT WE KNOW SO FAR (evidence-grade only)
- **POWERED-NULL (D28): 2 arm(s) (V9, V3) reached n>=30 delivered with 0 replies (61 delivered total).** This is a REAL result, not 'too early': cold-email-for-replies is NOT converting these angles/ICPs. The marketing conclusion is to change the ASK and shift energy to warm pool + community + launch (MARKETING_COVERAGE_MAP.md H-items), NOT to add a third powered arm to the same null channel.
- V2 (DevTool OSPM DevRel): 1 spam-block(s) — this WORDING trips Apollo's commercial filter for this ICP (V2 incident class); copy lesson, not angle lesson.
- LEARNING (OSS maintainer / AI coding tool builder): "Credited Ralph Loop as direct inspiration in their own project's README (organic_word_of_mouth -- the ONLY thi" → Builders value genuine, concrete usefulness -- the README credit proves that when Ralph provides real working 
- LEARNING (OSS maintainer / peer in 'unattended Cla): "README: 'A Claude Code skill for running Claude unattended on a planned work track — overnight, day-trip, mult" → This is the 'next Nightcrawler' candidate per MARKETING_PRINCIPLES.md §8 (advocate-amplification). Maintainer 
- LEARNING (OSS prospect who reached the ralphworkfl): ""https://ralphworkflow.com 登录注册相关全部无响应" ("Login and registration on ralphworkflow.com completely unresponsive"" → **THE SITE IS A DISTRIBUTION BLOCKER.** Two ways to read this: (a) FIX it — make the landing page explicitly s
- LEARNING (Peer builder in the spec-driven + unatte): ""I was missing the following: A system that is not primped on one language or framework, A straightforward rep" → **THE NEXT NIGHTCRAWLER CANDIDATE.** Marco + speq-skill = the exact same ICP + same audience + complementary p
- LEARNING (spec-driven-evangelist (V4 angle: SpecDr): "Software is Free. Code is Generic. Spec Driven Development will pioneer the way in the age of AI." → HIGH-CONFIDENCE V4 ICP MATCH. V4's name (SpecDriven-OpenStandards) is exactly her framing. She is the top warm

## §2 H-ITEMS (launch / star-movers — the highest-leverage per-canon §0/§8)
| ID | Item | Status (2026-06-10 21:30) | Evidence |
|---|---|---|---|
| **H1** | Mail subdomain + mail-warming | ✅ **RESOLVED 2026-06-10 21:30** | warmbox vendor running, `is_opted_in_mailwarming=True` persisted, 14-day cycle (2026-06-10→2026-06-24), 10-40 daily, 30% daily reply rate. Stale `apollo_account_truth.md` still says `never_started` — auto-regen on next gate run will reconcile. Delta in `drafts/2026-06-10_h1_mailwarming_unblocked.md`. |
| **H2** | Show HN (Ralph Workflow) | staged | `drafts/2026-06-10_launch_assets_READY.md` has the post. Tue-Thu 14-16 UTC window; today is Wed 21:30 local = 19:30 UTC — too late for today's window; tomorrow Thu 14-16 UTC = the last good slot. |
| **H3** | awesome-list PR | ✅ **FIRED 2026-06-10** | Issue #124 on bradAGI/awesome-cli-coding-agents. Maintainer processing 5 other PRs in queue (#119-#123) from today; our submission is in the queue, no maintainer response yet. |
| **H4** | Nightcrawler co-publish | staged | `drafts/2026-06-10_launch_assets_READY.md` §3 has the note. Nightcrawler maintainer credit was the only thing that has ever moved Ralph stars — co-publish on a real concrete result (v0.5.0 release) is the next data point. |
| **H5** | Install→star site leak | owner-gated | Per `customer_discovery #5` (naixiu issue #8): ralphworkflow.com is a 404-equivalent for the SaaS-shaped audience. Owner needs to either (a) put `pip install ralph-workflow` above the fold, or (b) redirect to Codeberg. Not a marketer action; flagged. |
| **H6** | Branch `fix/positioning-readme-showcase` | staged | Commit d0ce1bf (README retired-phrase fix + SHOWCASE.md with Nightcrawler as entry #1). Awaiting merge on Codeberg. Not merged this run — owner-gated. |

## ❌ WHAT WE DO NOT KNOW YET (the honest gap list — this drives the next runs)
- NO angle×ICP combination has proven conversion yet (no powered comparison; 0 attributable stars).
- Star attribution: no per-channel mechanism beyond repo-surface split — a star today is not attributable to an angle.

## How the loop uses this file
- MARKETER: read in STEP 1; every positioning/copy refinement cites a row or learning here;
  duty 4 feeds the thinnest active arm; new angles only with a row-level rationale.
- EVALUATOR: this matrix must MOVE run-over-run (n up, learnings added, unknowns shrinking).
  A static matrix across runs = the program is not learning = first-class defect.
