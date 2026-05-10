# HireAegis Interviewer — Monetization Analysis

**Date:** 2026-05-09
**Prepared for:** mistlight
**Product:** HireAegis Interviewer — real-time coding interview platform

---

## 1. What the Product Actually Solves

**Core problem:** Technical interviews produce false confidence. Puzzles test memorization, not product engineering. Take-homes are easy to outsource and hard to evaluate consistently. Standard live coding is artificial and doesn't reflect real work.

**What HireAegis does differently:**
It makes coding interviews **observably real**. The interviewer watches the candidate build actual working software — in a real IDE, with AI assistance visible to everyone, and a live preview of what they're building. Every decision is captured: code changes, AI prompts, build output.

**The "wow" moments:**
1. **Candidate joins with one link, no setup** — browser-based IDE, no local environment
2. **Interviewer sees real-time code + AI transcript + build output simultaneously** — not just "can they solve it" but "how do they work"
3. **Live Docker-powered preview** — candidate runs `npm run dev` and the interviewer watches the output in real time
4. **AI usage is a feature, not cheating** — candidates can use AI, and interviewers evaluate prompt quality and verification behavior

**Who actually pays:** Engineering managers, tech leads, and CTOs at companies hiring product engineers. The buyer is someone who's been burned by:
- Puzzle-perfect candidates who can't ship
- Take-home candidates who got help
- Interview panels that can't agree on what "good" looks like

---

## 2. Competitive Landscape

| Platform | Live Code | AI Visible Both | Live Preview | Real-time Collab |
|---|---|---|---|---|
| **HireAegis** | ✅ | ✅ | ✅ Docker | ✅ |
| CoderPad | ✅ | ❌ | ❌ | ✅ (but read-only for interviewer) |
| HackerRank | ✅ | ❌ | ❌ | ✅ |
| Devskiller | ✅ | ❌ | ❌ | ✅ |
| Codility | ✅ | ❌ | ❌ | ❌ |
| LeetCode Interview | ✅ | ❌ | ❌ | Limited |

**Unfair advantages HireAegis has:**
1. **Live Docker preview** — no competitor does this. Candidates run real builds, interviewers watch output
2. **AI transcript visible to both parties in real time** — making AI collaboration part of the evaluation, not a black box
3. **Alpha positioning / "design partners wanted" framing** — gives buyers a reason to engage now vs. waiting for "full release"

**One-sentence positioning:**
> "See how candidates actually build — not just whether they can solve it — with a live coding interview that captures real reasoning, real AI collaboration, and real working code."

---

## 3. The Simplest Paid Product

**The product as currently built:**
- Subscription tiers: Starter ($79/mo, 20 sessions), Professional ($199/mo, 50 sessions), Business ($399/mo, 100 sessions)
- Sessions are consumed per interview room creation (1 session = 1 interview hour)
- Billing billing infrastructure is fully built: webhooks, subscription model, workspace gating, usage ledger
- **But production variant IDs are NOT configured** in `config/lemon_squeezy.yml` — payments aren't actually wired up

**The honest simplest paid product:**

> **$79/month for 20 interview sessions** — one workspace, unlimited team members, real-time code + AI + live preview.

This is already defined in the code. The missing piece is activating it.

**However** — the current FAQ says "We're currently in alpha and working on payment integration. For now, contact us to discuss billing options." This suggests the founder is manually handling billing conversations right now.

---

## 4. Conversion Friction Analysis

**What's between a visitor and their first payment:**

### Friction Point 1: Manual Onboarding Gate
- New workspaces require `approved: true` (set by admin or auto-approved)
- When `MANUAL_ONBOARDING=true`, new users see "pending onboarding" and cannot create rooms
- Even when off, there's an onboarding email flow

**Fix:** Flip `MANUAL_ONBOARDING=false` (default), let users create 1-2 rooms immediately

### Friction Point 2: No Free Trial / Pay-Per-Interview Option
- There's no free tier that leads naturally to paid
- The free tier (2 sessions/month) exists in `UsageLedger` but the UI probably doesn't make it clear
- No "pay $30 for 3 sessions" option for low-commitment buyers

**Fix:** Add a pay-per-interview option or free trial with 1 complimentary session

### Friction Point 3: Payment Integration Not Live
- `config/lemon_squeezy.yml` production section has variant IDs commented out
- The entire Billing checkout flow redirects to `billing.hireaegis.com` which may not exist yet

**Fix:** Set up billing.hireaegis.com on Billing and configure production variant IDs

### Friction Point 4: Landing Page Doesn't Close
- The landing page is beautifully crafted but doesn't make the purchase path obvious
- "Start a low-risk pilot" → sign up → pending onboarding email → ??? → payment

**Fix:** Add a "Book a demo / Start pilot" flow that collects payment info directly

---

## 5. Revenue Per User Estimation

**Target buyer profile:** Engineering manager at a 10-50 person startup or 50-500 person mid-stage company, hiring 2-5 engineers/month

**Likely deal sizes:**
- **Starter ($79/mo):** Small team, 1-2 hires/month → pays $948/year
- **Professional ($199/mo):** Growing team, 3-4 hires/month → pays $2,388/year  
- **Business ($399/mo):** Active hiring, 5+ hires/month → pays $4,788/year
- **Enterprise (custom):** High volume, custom needs → $1K-5K/month

**Lifetime value estimate:**
- Average customer: ~$2,000/year
- Average retention: ~18 months (based on typical SaaS for SMB tools)
- **LTV estimate: ~$3,000-$4,000 per customer**

**One-time vs. subscription:** Given the usage ledger model (sessions/month), subscription is clearly the intended model. Per-interview pricing ($25-50/session) would be simpler for first-time buyers but lower LTV.

---

## 6. Fastest Path to First Dollar

### Immediate actions (1-2 days):

**1. Configure Billing production variants**
- Create products/variants in Billing dashboard
- Update `config/lemon_squeezy.yml` production section with real variant IDs
- Set `LEMON_SQUEEZY_WEBHOOK_SECRET` env var and verify webhook endpoint works

**2. Remove the manual onboarding gate**
- Set `MANUAL_ONBOARDING=false` in production
- Users can create rooms and do a real pilot immediately

**3. Add a free first session**
- New workspaces get 1 free session to run their first real interview
- This is the "try before you buy" moment

**4. Wire up the "Contact us" → sales conversation**
- Add Calendly or similar to the `/app/settings` subscription section
- "Talk to us to start your pilot" is better than nothing while payments are being verified

---

## 7. Top 3 Conversion Quick Wins

### Quick Win 1: "Start Your Pilot" CTA on Home Page
**Time:** Half day
**What:** Change the home page CTA from "Start a low-risk pilot" (which goes to sign-up) to a direct Calendly booking embed or "Book 15-min intro call" link
**Why:** The current flow has too many steps between "I'm interested" and "I paid." A direct booking path captures intent immediately

### Quick Win 2: New User Gets 1 Free Interview Session
**Time:** 1 day
**What:** Modify `UsageLedger` to give new workspaces 1 complimentary session token (not just the 2 free/month)
**Why:** Removes the "I need to pay before I know if this works" blocker. One real interview = conviction

### Quick Win 3: Pricing Page Checkout Button Fix
**Time:** 1 day
**What:** The `PricingCard` CTA currently links to `/sign-up` — it should link directly to the Billing checkout URL (or the `/app/subscription/checkout_url` redirect)
**Why:** Currently there's no path from the marketing pricing page to a paying state without manual intervention

---

## 8. One-Page Landing Page Brief

The existing home page at `/` is excellent. The missing element is a **"Start your pilot" conversion section** that should live between the FAQ and the footer CTA. It needs:

### Section: "Ready to run your first real interview?"

**Elements:**
1. **Headline:** "Start a 3-interview pilot. No commitment."
2. **Subhead:** "We'll walk you through setup. Your first interview is on us."
3. **Form fields:**
   - Work email
   - Name
   - Company size (dropdown: 1-10, 11-50, 51-200, 200+)
   - Primary use case (dropdown: frontend, fullstack, backend, product)
4. **CTA button:** "Request pilot access" → sends to a Slack webhook or email
5. **Trust signal:** "No credit card. No commitment. We'll email you within 24 hours to schedule."

**Below form:**
- "What happens after you request access"
  - Step 1: Email confirmation (instant)
  - Step 2: 15-min intro call (optional, within 48h)
  - Step 3: Workspace activation + 1 free session
  - Step 4: Run your first real interview

**Why this works:** It lowers the commitment bar to zero, captures leads who aren't ready to pay, and creates a sales follow-up opportunity. The "1 free session" removes the "but what if it doesn't work" objection.

---

## 9. Key Findings Summary

| Item | Finding |
|---|---|
| **What users pay for** | Interview sessions (20/50/100 per month) — time-boxed access to the full platform |
| **Simplest paid product** | $79/month Starter plan — 20 sessions, 1 workspace, unlimited team members |
| **Fastest path to first dollar** | Configure Billing production variants + remove onboarding gate + give 1 free session |
| **Top conversion friction** | 1) Payment not wired up 2) Manual onboarding gate 3) No free trial visible on landing page |
| **Quick wins (<1 day each)** | 1) Wire up Billing production 2) Remove onboarding gate 3) Fix pricing CTA to go to checkout |
| **Landing page missing** | "Start pilot" form with Calendly/email capture + 1 free session offer |
| **Competitor differentiator** | Live Docker preview + real-time AI transcript visible to both parties — no competitor matches this |
| **Revenue model** | Subscription (monthly/annual) with session token bundles — clear upgrade path across 3 tiers |
| **Estimated LTV** | ~$3K-$4K per customer (~$2K/year × 18-24 month avg retention) |

---

## 10. Immediate Next Steps (Priority Order)

1. **Today:** Set up Billing products (Starter/Pro/Business monthly + annual variants)
2. **Today:** Update `config/lemon_squeezy.yml` production with real variant IDs
3. **Today:** Verify webhook endpoint responds at `POST /lemon-squeezy-webhooks`
4. **This week:** Set `MANUAL_ONBOARDING=false` (or leave it, but ensure users aren't stuck)
5. **This week:** Add Calendly embed to home page "Start pilot" section
6. **This week:** Modify `UsageLedger` to credit new workspaces with 1 free session token
7. **This week:** Change `PricingCard` CTA from `/sign-up` to `/app/subscription/checkout_url?plan_key=starter&billing_period=monthly`
