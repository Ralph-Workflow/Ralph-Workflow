# Apollo ACCOUNT GROUND TRUTH — live snapshot 2026-06-10T21:35:48

> Auto-generated every gate run by `apollo_account_truth.py`. This is what the account
> ACTUALLY looks like right now. If your plan contradicts this file, your plan is wrong.

## Sending mailboxes
- **(NO EMAIL — junk account, NEVER use)** — active=True, default=False, no signature configured
- **ken@ralphworkflow.com** — active=True, default=True, **signature auto-appended to every send:** “Ken Li ·  · Ralph Workflow ·  ·  · ralphworkflow.com”
  - daily send limit: 100 · mail-warming: started (warmbox cycle 2026-06-10→2026-06-24, 10-40/day, always-on=True) · unhealthy-domain send-block: False
  - measured deliverability (week 2026-06-01→2026-06-07): score **83.6/100** · delivered 79.2% · hard-bounce 4.0% · spam-block 0.8% · open 4.9% · domain-health 5.0/5 · domain-age score 2.0/5

## Sequences (live stats)
- **Ralph-AB-V11-ClickerMomTest-v2-AI-Agent-2026-06-10** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=0 spam_blocked=0
- **None** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=0 spam_blocked=0
- **Ralph-AB-V9-AI-Observability-DevRel-2026-06-10** — active=True · scheduled=31 delivered=31 opened=0 replied=0 bounced=0 spam_blocked=0
- **Ralph-AB-V8-ClickerMomTest-2026-06-10** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=0 spam_blocked=0
- **tmp_creation** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=0 spam_blocked=0
- **Ralph-AB-V7-AI-Observability-OSPM-clean-2026-06-09** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=1 spam_blocked=0
- **Ralph-AB-V5-OSS-Maintainer-Distribution-2026-06-09** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=0 spam_blocked=0
- **Ralph-AB-V4-SpecDriven-OpenStandards-2026-06-09** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=0 spam_blocked=0
- **Ralph-AB-V3-AI-Agent-Composition-2026-06-09** — active=True · scheduled=30 delivered=30 opened=0 replied=0 bounced=0 spam_blocked=0
- **Ralph-AB-V2-DevTool-OSPM-DevRel-2026-06-09** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=1 spam_blocked=1
- **Ralph-AB-V1-AI-Observability-OSPM-2026-06-09** — active=False · scheduled=0 delivered=5 opened=0 replied=0 bounced=3 spam_blocked=0
- **Ralph Workflow Test 2026-06-09 (dry-run)** — active=False · scheduled=0 delivered=0 opened=0 replied=0 bounced=0 spam_blocked=0
- **tokenmaxxing** — active=False · scheduled=0 delivered=401 opened=30 replied=0 bounced=16 spam_blocked=1
- **Ralph Workflow Seq** — active=False · scheduled=0 delivered=1138 opened=160 replied=1 bounced=293 spam_blocked=196

## HOW APOLLO ACTUALLY BEHAVES (verified facts — read before ANY sequence/template work)
1. **The mailbox SIGNS every send.** The sending mailbox has a configured signature (shown above)
   that Apollo appends automatically. NEVER put a sign-off in a template body — it double-signs.
   (Incident: V1-V5 templates shipped "— Ken" on top of the mailbox signature, 2026-06-09.)
2. **Per-contact mailbox binding is authoritative.** Contacts enroll bound to a mailbox via
   `add_contact_ids`; campaign-level `mailbox=None` is cosmetic and does NOT block delivery —
   but an unbound CONTACT sends nothing while logging success. Always verify binding after enroll.
3. **Stat fields have mixed units.** `unique_*` fields are COUNTS; `bounce_rate`/`spam_block_rate`
   on a campaign are 0-1 FRACTIONS (0.5 = 50%). Never compare a fraction to a percent threshold.
4. **Apollo pre-screens content for spam.** A send can be `spam_blocked` (counted, never delivered)
   purely on template content — long bodies, commercial phrasing ("free … tool"), disclosure
   paragraphs all triggered it (V2 incident, 2026-06-09). Short, single-question, personal copy passes.
5. **Delivery is business-hours-paced**, not instant on activation: `unique_scheduled` drains into
   `unique_delivered` over hours/days. 0 opens an hour after activation is NOT a signal.
6. **Endpoints:** use `mixed_people/api_search` (NOT the deprecated `mixed_people/search`) and
   `people/match` for enrichment (reveals consume credits). Activation = POST
   `/emailer_campaigns/{id}/approve`, pause = `/abort`. Consult https://docs.apollo.io/reference
   before using ANY endpoint not listed here — never invent one.
7. **Guard H (apollo_activate_floor.py) code-enforces OUTREACH_COPY_CONTRACT.md**: persona
   signatures, call/meeting asks, manual sign-offs, or a missing repo link will BLOCK activation.

