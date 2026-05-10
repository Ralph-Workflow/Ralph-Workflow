# Monetization Strategy — HireAegis + RalphWorkflow

## The Two Products

### Product 1: HireAegis Bot Detection (hireaegis.com)
**What it does:** Flags bot/spam job applications in real-time. Not a black box. No CAPTCHAs. Export to ATS.
**Current status:** PUBLIC BETA — FREE. No pricing configured.
**Target customer:** Recruitment teams, HR departments, job board operators
**Moat:** Explainable findings (not a black box score), no candidate friction

### Product 2: HireAegis Interviewer (interview.hireaegis.com)
**What it does:** Live coding interview platform with real-time IDE + AI visibility + Docker previews
**Current status:** Built, NOT deployed publicly
**Target customer:** Engineering managers, tech recruiters
**Competitors:** CoderPad, HackerRank, Devskiller

### Product 3: RalphWorkflow (ralphworkflow.com)
**What it does:** CLI orchestrator for AI coding agents — spec-driven, unattended workflows
**Current status:** Free CLI, no hosted SaaS
**Target customer:** Developers, indie hackers
**Monetization:** Free CLI → SaaS upsell (hosted version with team analytics)

---

## Revenue Streams

### Stream 1: HireAegis Bot Detection — PAID TIERS
**Fastest path to revenue.** Recruitment tools sell. B2B SaaS.
- Free tier: 1 job posting, 50 applications/month
- Starter: $29/mo — 5 jobs, 500 applications/month
- Pro: $79/mo — unlimited jobs, 5000 applications/month + ATS export + priority
- Enterprise: $199/mo — unlimited everything + direct ATS integrations

**Action needed:** Configure pricing in LemonSqueezy, transition from "free beta" to "paid with free tier"

### Stream 2: HireAegis Interviewer — SUBSCRIPTION
**Medium path.** Needs deployment + traffic.
- Starter: $29/mo — 5 seats, 10 hours/month
- Professional: $79/mo — 20 seats, 50 hours/month
- Enterprise: $199/mo — unlimited

### Stream 3: RalphWorkflow — SaaS UPSELL
**Longer path.** Content drives this.
- Free CLI
- Hosted version: $9-29/mo — team dashboard, analytics, shared runs

### Stream 4: Consulting / Services
**Fastest to $1.** Offer setup + training.
- RalphWorkflow onboarding: $200
- HireAegis configuration consulting: $300/hr

---

## Current Gaps Blocking Revenue

### Gap 1: No pricing on HireAegis Bot Detection
The product is free beta with no upgrade path. Companies want to pay for this.
**Fix:** Configure pricing plans in LemonSqueezy, change landing page CTA from "free beta" to "start free, upgrade anytime"

### Gap 2: Interviewer not publicly deployed
interview.hireaegis.com doesn't exist yet.
**Fix:** Deploy HireAegisInterviewer, point subdomain, wire checkout

### Gap 3: No traffic
All three products have zero marketing.
**Fix:** Content engine + community outreach agents

### Gap 4: LemonSqueezy API blocked
Can't track revenue, subscriptions, or test checkout.
**Fix:** Get valid API key from LS dashboard

---

## 30-Day Priority Actions

### Week 1 — Get first dollar
- [ ] Add pricing to HireAegis Bot Detection (LemonSqueezy)
- [ ] Change landing page: "Free Beta" → "Free Tier + Paid Upgrade"
- [ ] Post to Hacker News, Indie Hackers, r/recruiting
- [ ] Get first paying customer

### Week 2-3 — Build traffic
- [ ] Deploy Interviewer at interview.hireaegis.com
- [ ] Create 10 content pieces (YouTube, dev.to, Twitter threads)
- [ ] SEO: fix technical issues on both sites

### Week 4 — Optimize
- [ ] Review which channels drove signups
- [ ] Double down on highest-converting channel
- [ ] Set up proper analytics (Plausible, Fathom, or simple LS conversion tracking)

---

## Metrics to Track
- MRR (Monthly Recurring Revenue)
- New signups per day
- Conversion rate: visitor → free trial → paid
- Content pieces published
- Traffic sources
- Customer Acquisition Cost (CAC)
