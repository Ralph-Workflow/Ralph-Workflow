# Customer Segments — hypotheses, evidence, verdicts (2026-06-08, final)

> Living table, kept in sync with `icp_findings.md` §2. Each row has Apollo `total_entries` (size, with date
> and the query file), real-engager count (named in Apollo graph), and a verdict. Verdict rules:
> **PURSUE** = a real conversation / outreach test is justified; **WATCH** = keep monitoring, don't
> invest; **KILL** = stop spending Apollo queries on this; **PURSUE-SECONDARY** = small but high-purity,
> use as 1:1 seeds not list-builds.

| # | Segment | Apollo `total_entries` | Query file | Date | # named engagers | Verdict |
|---|---|---|---|---|---|---|
| 1 | Open Source Program Mgr / Head of OSS / OSS Community Mgr / OSS Lead | **127,087** | `apollo_ospm_2026-06-09.json` | 2026-06-09 00:00 | 3 (Logan-LlamaIndex, Weston-SPEQ, Alejandro-Mendoza) | **PURSUE (PRIMARY)** |
| 2 | Spec-driven development (builders) | 7 | `apollo_segment_sizing_2026-06-08_v4.json` C5 | 2026-06-08 ~20:04 | 2 (Weston-SPEQ, Alejandro-Mendoza) | **PURSUE-SECONDARY** |
| 3 | Developer Advocate / DevRel (broad) | 1,438 | morning probe (pre-run) | 2026-06-08 ~ | 0 in Ralph category | **KILL** — too broad; 0 useful engagers across all probes. Any narrow sub-probe should be listed separately. |
| 4 | AI-native developer | 181 | `apollo_segment_sizing_2026-06-08_v4.json` C10 | 2026-06-08 ~20:04 | 0 in Ralph category | **KILL** — small segment, 0 named engagers, no conversion path to Ralph. |
| 5 | Agentic coding | 20 | `apollo_segment_sizing_2026-06-08_v4.json` C2 | 2026-06-08 ~20:04 | 0 in Ralph category | **KILL** — mostly Alignerr contractors doing non-Ralph work. |
| 6 | Autonomous coding | 12 | `apollo_segment_sizing_2026-06-08_v4.json` C4 | 2026-06-08 ~20:04 | 0 (healthcare-billing) | **KILL** |
| 7 | Unattended AI agents | 0 | `apollo_segment_sizing_2026-06-08_v4.json` C3 | 2026-06-08 ~20:04 | 0 | **KILL** |
| 8 | Devrel AI agents | 1 | `apollo_segment_sizing_2026-06-08_v4.json` C7 | 2026-06-08 ~20:04 | 1 (Chris D., TiDB) | **KILL as segment** |
| 9 | Python devtools | 1 | `apollo_segment_sizing_2026-06-08_v4.json` C8 | 2026-06-08 ~20:04 | 1 (Kaiki P., Brazil) | **KILL** — single data point, no broader segment. Watch as individual if Brazil dev community engagement emerges. |
| 10 | Devrel AI coding | 0 | `apollo_segment_devrel_aicoding_2026-06-08.json` | 2026-06-08 22:12 | 0 | **KILL** |
| 11 | AI coding newsletter | 0 | this run | 2026-06-08 22:12 | 0 | **KILL as Apollo search** |
| 12 | Claude Code daily | 2 | `apollo_segment_sizing_2026-06-08_v4.json` C1 | 2026-06-08 ~20:04 | 0 | **KILL** — too small (2). Revisit if Claude Code + spec usage becomes a measurable trend. |
| 13 | Coding agent review | 0 | `apollo_segment_sizing_2026-06-08_v4.json` C9 | 2026-06-08 ~20:04 | 0 | **KILL** |
| 14 | Claude Code overnight | 0 | `apollo_segment_sizing_2026-06-08_v4.json` C6 | 2026-06-08 ~20:04 | 0 | **KILL** |
| 15 | OSS DevRel + AI (narrow) | 1 | `apollo_segment_oss_devrel_2026-06-08.json` | 2026-06-08 22:12 | 1 (Drazen U., Nym — Principal Engineer, OSS Maintainer 35M+ downloads) | **PURSUE-SECONDARY** — Drazen is a high-value 1:1 seed: OSS maintainer with massive distribution, AI/distributed-systems domain. Apollo enrichment returned obfuscated (no contact path without reveal credit). Needs manual channel discovery (GitHub Nym repo, LinkedIn). |
| **16** | **Engineering Leaders / Head of Eng / VP Eng / CTO (OLD ICP)** | **810,979** | `apollo_eng_leaders_2026-06-08_v2.json` | 2026-06-08 22:35 | 0 (proven zero-conversion) | **KILL** |
| **17** | **Dev Tool Curators / Awesome List Maintainers / Dev Newsletter Authors** | **0-1** | `(5 Apollo probes, this run)` | 2026-06-08 23:48 | 0 (not searchable in Apollo) | **KILL as Apollo segment** |
| **18** | **OSPM/DevRel at AI/devtools orgs (10–200 employees, filtered)** | **229** | `mixed_people/api_search` 2026-06-09 ~00:30 with `organization_num_employees_ranges=['10,50','50,100','100,200']` + `q_organization_keyword_tags=['artificial intelligence','developer tools','machine learning','ai agents']` | 2026-06-09 | 6 (Logan-LlamaIndex, Vinh-AITOMATIC, Mikyo-Arize AI, Claudia-OASIS, Cameron-Letta, Shawn-Composio) | **PURSUE (PRIMARY sub-segment of #1)** — this filter is the goldilocks zone: 229 named engagers at the exact ICP. 6 revealed contacts already: Chad @ Sentry, Cameron @ Letta, Shawn @ Composio, Claudia @ OASIS, Vinh @ AITOMATIC, Mikyo @ Arize AI. |
| **19** | **OSPM at devtools/CLI orgs (filtered)** | **14** | `mixed_people/api_search` 2026-06-09 ~00:30 with `q_organization_keyword_tags=['developer tools','cli','developer infrastructure']` | 2026-06-09 | 5 (Sentry, Meta, Salesforce x2, BrowserStack, Posit PBC, Arm) | **PURSUE-SECONDARY** — small but every entry is high-ICP. 1 reveal done: Chad @ Sentry (HOS, biggest audience). |
| **20** | **OSPM at observability orgs (verified emails only)** | **3** | `mixed_people/api_search` 2026-06-09 ~00:30 with `contact_email_status=['verified']` + `q_organization_keyword_tags=['developer tools','observability']` | 2026-06-09 | 3 (Mikyo-Arize AI, Logan-LlamaIndex, Michele-Dash0) | **PURSUE (PRIMARY sub-segment of #1)** — the verification filter is the highest-signal Apollo segment. 3 named engagers, all revealed, all have outreach drafts. |
| **21** | **Founding DevRel at agent frameworks** | **n/a** | synthesized from #18 sample | 2026-06-09 | 2 (Cameron Pfiffer @ Letta, Simon Ma***e @ Tessl) | **PURSUE-SECONDARY** — founding DevRel = high-influence, low-noise. Cameron revealed. |
| **22** | **DevRel broad (developer advocate / devrel / head of devrel)** | **3,739** | `mixed_people/api_search` 2026-06-09 with `person_titles=['developer advocate','developer relations','head of developer relations','devrel engineer']` | 2026-06-09 | 0 named (not profiled) | **KILL as Apollo segment** — too broad; 3,739 entries but Apollo data shows sample orgs are Limitless Labs, Vorwerk Group, Descope, Enlear, QuestDB, smallest.ai — mostly small orgs with no clear Ralph-ICP fit. Use the filtered OSPM sub-segments (#18, #20) instead. DevRel will be a PURSUE-SECONDARY only when filtered to agent frameworks (#21) or major dev tools (Sentry, BrowserStack, etc.). |

## Next research priorities
- (a) **MANUAL channel discovery for the 2 KILL-row engagers** (Weston @ SPEQ, Drazen @ Nym) — Apollo reveal failed for small orgs. Need browser-based search of their GitHub/orgs.
- (b) One real 1:1 Mom-Test conversation with an installer who churned (activation gap). Team at `ralphworkflow.com` is the lead source for this.
- (c) Send first 1:1 to Mikyo @ Arize AI (or Chad @ Sentry) within 24h — the breakthrough capability is in hand, the bottleneck is execution.

## Anti-theater guard
- No row in this table may stay **PURSUE** without at least one real conversational touchpoint per week.
  If a PURSUE segment has had 0 contacts for ≥7 days, downgrade to WATCH.
- KILL is final until new evidence (not just a new Apollo query) re-opens it.
- The 758-contact eng_leaders blast is the canonical lesson: volume ≠ conversion. Keep all Apollo budgets below 1 credit per contact revealed, and cap sends at 1-2/day.

| **23** | **Platform / Developer Productivity / Developer Experience / DevTools Engineer (the BUYER persona)** | **2,800,579** | `apollo_platform_2026-06-09.json` | 2026-06-09 06:00 | 10 (Stefan-TOPdesk, Tommaso-Gradle, Alan-Skydio, Rachel-Cruise, Matt-Aspect Build, Cameron-IMC Trading, Davidson-Woven by Toyota, Johan-Tweag, Nicholas-Motional, Michael-eurofunk) | **PURSUE-SECONDARY (Day 7+ V6 pivot)** — 22x the size of OSPM segment. This is the DIRECT USER persona, not the community amplifier. Title signals ('Developer Productivity Engineer') are the canonical buyer of unattended agent orchestration tooling. Sample orgs are devtools/AI/robotics companies (Gradle, Skydio, Cruise, IMC Trading) — all real devtools-aware buyers. NOT testing in this run because the OSPM A/B (V1+V2) is the in-flight experiment, and adding V6 mid-experiment would dilute signal + breach the 1-2 sends/day cap. Decision rule: activate V6 ONLY if V1+V2+V3+V4 produces 0/8 replies by Day 14. |


---

## Segment #24 — V1 angle live evidence (2026-06-09)

**V1 (AI-Observability-OSPM, id 6a274ca9db1a7c001413e49a) is now 7/30 enrolled.** Original 2: Mikyo King (Arize AI) + Michele Mancioppi (Dash0). Top-up 5: Gorakhnath Yadav (OpenObserve), Manas Sharma (OpenObserve), Dhruv Ahuja (SigNoz), Jugal Kishore (SigNoz), Austin Parker (honeycomb.io, OTel maintainer).

**Why this is the right angle:** every contact's day job is dev-evangelism for an observability/monitoring/tracing company. The Mom-Test question "how do your maintainers find new dev tools" lands hardest on the people whose customers ARE maintainers. If any of these 7 replies, the answer will be a real community-discovery pattern (newsletter, dev event, OSS directory, GitHub trending, peer word-of-mouth) — each one is a potential Ralph-amplification vector.

**Pull signal: OpenObserve + SigNoz + honeycomb.io are the 3 fastest-growing open-source observability orgs in 2026.** They compete with each other but share the same dev persona. If Ralph earns a mention in any of their dev channels, it lands with 100K+ devs simultaneously (the people who install but don't yet star).

**Sector reality check:** Observability is the AI-era dev tool category with the strongest "overnight + tested commits" pain — observability vendors ship nightly with the same loop Ralph helps devs build. Their own engineering orgs are the highest-fit users of Ralph's "loop while you sleep" pitch. (Not the Apollo audience for cold email — but a real 1:1 with a DevRel at one of these orgs could surface that connection.)

## Segment #25 — V3 angle live evidence (2026-06-09)

**V3 (AI-Agent-Composition, id 6a2757e1cf766a0014cbf939) is now 7/30 enrolled.** Original 2: Logan Markewich (LlamaIndex) + Vinh Luong (AITOMATIC). Top-up 5: Mateo Torres (Arcade.dev, agent auth), Richard Lin (Datastrato, OSS ecosystem), Hannes Hapke (Dataiku, open source), Nathan Tarbert (CopilotKit, DevRel), Prasad Sawant (Lyzr AI).

**Why this is the right angle:** every contact's company BUILDS an agent platform/framework. Their JTBD is "help my users run multi-step agent tasks reliably." The Mom-Test question "how do you run multi-step agent tasks overnight" lands hardest on the people whose users ask THEM that question. If any of these 7 replies, the answer is a real agent-iteration pattern (CI, eval harness, prompt logs) — and that pattern overlaps directly with what Ralph offers (run, get commits, iterate). The "compete or complement" question is the right one for this segment: Ralph is a "tested commits in your repo" tool, agent frameworks are "agent runs in their framework." Cross-pollination is the natural ask.

**Pull signal: Datastrato is OpenMetadata (the open-source metadata platform).** Richard Lin as Head of Open Source Ecosystem is a direct OSPM role — a 1:1 with him is the cleanest test of the OSPM advocate-seed hypothesis outside the OSPM-search segment.


## Segment #26 — pierodibello / 3-signal warm pool, 2026-06-10

**Persona hypothesis:** Claude Code / GitHub Copilot **agent-skill shipper** (senior software engineer at a regulated-industry tech team, building + sharing skills for the unattended agent loop). Distinct from the OSPM persona (#1/#18) because their distribution leverage is their REPO LIST, not their org's community — they ship code others copy, not news others amplify.

**Why this segment emerges now:** pierodibello (codeberg: pierodibello, GitHub: xpepper) is the only warm pool entrant with **3 independent signals**:
1. **Warm star+watch on Codeberg** (multi-signal in warm_pool.md)
2. **Ralph-Workflow is his 1 starred repo on Codeberg** (his only active public signal = strong pull)
3. **Month-long agent-skill ship stream on GitHub:** tcr-skill, pr-review-agent-skill, session-wrap-up, plan-feature-from-youtrack-agent-skill, perplexity-agent-skill=12★, boy-scout, agent_commands, gh-pr-summarise. He watches gsd-build/get-shit-done 64k★, davila7/claude-code-templates 28k★, EveryInc/claude_commands, ghuntley/loom.

**Org:** Prima Assicurazioni (@primait on GitHub) — Italian insurance carrier with a real tech team in Trento. Domain helloprima.com.

**Apollo record (verified):** id=611b9fe78a40e60001362714, name "Pietro Bello" Senior Software Engineer @ Prima, Trento IT. Email=pietro.bello@helloprima.com **email_status=extrapolated** (NOT verified). personal_emails=[]. LinkedIn=http://www.linkedin.com/in/pietrodibello.

**Verdict:** **PURSUE-WATCH** — one warm-pool match, real evidence of fit, but not enough to call it the new ICP. The right next action is a **public engineering comment on one of his GitHub agent-skill repos** (perplexity-agent-skill or tcr-skill) — 10x warmer than a cold email, and produces a verbatim reply if he engages. STAGED for when a GitHub auth token is provisioned (per MEMORY.md: GitHub token path is unresolved). Enroll in V3/V9 is BLOCKED on email verification.

**Anti-theater guard check:** This segment has 1 warm-pool match, not 5+. Do not over-claim. Tracked as evolving, not as the new ICP. The original OSPM persona is still the primary.

## Segment #27 — V1 (AI-Observability-OSPM) confirmed dead, 2026-06-10 12:00

**Live stats at 12:00:** V1 unique_scheduled=0, unique_delivered=5, unique_bounced=3 (3/5 = 60% bounce on delivered, 3/8 = 37.5% bounce on sent+delivered+bounced). active=False, status_reason=manual_pause. POST /abort returned HTTP 422 "already inactive" — R2 condition satisfied.

**Why this angle is dead:** 3 hard bounces on 5 delivered (the 2 surviving contacts: 1 opened, 1 clicked). The bounce rate of 60% on delivered is structural — too high to recover this specific arm. The surviving 2 contacts may still reply, but the arm as a whole cannot be re-activated.

**R2 protocol closed:** R2 says abort when ≥2 bounces at any n OR bounce >3% at n≥10. V1 hit the first clause. The hard-bounce contacts are listed in customer_discovery.jsonl for the V1 incident. Do NOT re-activate V1.

**Future variant:** V7 (AI-Observability-OSPM-clean) is the sister variant with the same angle but a different contact list. V7 has 0/10/1/0 (sched/del/bnc/rep) — paused, no sends yet. V7 is the clean-copy re-test of this angle. **DO NOT activate V7 yet** — the bounce issue may be a list-quality problem (some sectors have higher hard-bounce rates), not a copy problem. Wait until the V1 hard-bounce list is fully diagnosed and the surviving V1 contacts' email_status is re-verified.
