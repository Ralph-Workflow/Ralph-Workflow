# V2 Re-activation Plan (post-R2, updated 2026-06-09 22:00)

**Status:** ABORTED at 16:00 per R2. Currently 0 contacts enrolled. Recoverable.

**Updated diagnosis (this run):** The R2 §1.5 diagnosis was based on the OFF-contract local draft (`2026-06-09_chad_sentry_draft.md`), but a fresh re-read of the **LIVE V2 Apollo template** (id `6a274cc9fe51450014fcb216`) at 22:00 shows it is already on-contract:
- Subject: "Where do your maintainers find new dev tools?" (45 chars, ≤50)
- Body: "Hi {{first_name}}, I'm Ken — I build Ralph Workflow, an open-source loop orchestrator: hand your coding agents a spec tonight, wake up to reviewable, tested commits. Dev-tools companies like {{company}} sit at the exact moment a maintainer realizes 'I need a tool, now' — a very different discovery path from the compare-SaaS-platforms cycle. So one question: when a maintainer in {{company}}'s community lands on a new dev tool, what actually converts them to a first install — README + install, a blog post, a friend's DM, your own community channels? I'm trying to figure out where a tool like mine even belongs in that flow, since 'agent loop orchestrator' isn't a category anyone searches for yet. If you're curious, the repo is here: https://github.com/Ralph-Workflow/Ralph-Workflow. If the question is off-base, just reply 'pass' and I'll stop."

**This means the 6-step fix plan in the V2 draft is largely moot** — only the "replace 'free OSS tool' framing" had actually been applied; the rest of the V2 body was never off-contract.

**Revised R2 root-cause hypothesis (more likely):**
1. **Recipient-domain factor (letta.com):** Apollo's commercial-promotion filter may be domain-sensitive. Letta's domain filter may flag cold outreach at a higher rate than Sentry's. The 0/2 delivered on V2 (1 spam-block + 1 soft-bounce) is consistent with 1 domain having stricter filtering.
2. **Sentry mail-server transient issue:** The chadwhitacre@sentry.io soft-bounce is likely not address-invalidity; Sentry's outbound mail infrastructure can transient-block recipients.
3. **Not copy-shape:** The on-contract V2 body is similar in shape to V1's (which delivered cleanly to Arize + Dash0). The difference is in the recipient-domain, not the message.

**Re-activation plan (when next run is ready to test):**

1. **Confirm clean state:** POST /emailer_campaigns/{V2_id} to verify V2 is `active:false, status_reason:manual_pause`, 0 contacts enrolled.
2. **Re-enroll the same 2 contacts** (Chad + Cameron) via POST /emailer_campaigns/{V2_id}/add_contact_ids with `send_email_from_email_account_id: 69b080dea7fa4d0019b912c2`. The contact_ids should still be valid (Chad id `?`, Cameron id `?` — need to look up from the previous enrollment).
3. **Optional: add 1-2 SAFE-TEST contacts first** to verify the spam-block is not reproducible on a known-clean recipient. Suggested safe-test targets: any internal ken@ralphworkflow.com alias (ken+a@gmail.com) or a known-friendly contact (Logan Markewich, if V3 produces a reply by then).
4. **Activate via POST /emailer_campaigns/{V2_id}/approve.**
5. **Monitor bounce rate per-contact on this run + the next 2 runs.** If unique_bounced/unique_delivered > 3% on the re-activation, immediately abort (R2 again) and **fall back to: do not re-activate V2 to these specific 2 recipients; try V2 with a different 2-contact set instead** (e.g., Weston + Drazen from the V4/V5 contact pool — though they need their own email-reveal pass first).
6. **If V2's re-activation delivers cleanly to both contacts:** log the re-activation milestone and let it run. V2 becomes the 3rd active variant in the program (replacing the lost V2-slot in the 2-variant A/B).

**Pre-staging for next run (when ready to execute):**
- [ ] GET V2 contact_ids for Chad and Cameron from prior enrollment (look up via Apollo contacts/search by name+org)
- [ ] Verify V2 is `active:false, status_reason:manual_pause`
- [ ] Decide whether to add a 1-contact SAFE-TEST first (recommended, but requires identifying a safe test address)
- [ ] Execute re-enrollment + activation
- [ ] Run R1 protocol on re-activated V2 within 12-24h of send

**Why this matters:** V2 is a recovered experiment slot. The V2 angle ("Sentry-maintainer-style DevRel/Developer Advocate at large dev-tool orgs") is a high-value angle — Sentry's reach is the largest in the V1-V5 set. Losing V2 weakens the program's reach. Re-activation is the right next move once the recipient-domain risk is bounded.

**Risk bound:** the R2 spam-block cost the program 1 unusable recipient-day. Re-activation costs another 1 recipient-day if it fails. Total cost: 2 recipient-days for the chance to recover a 100K+ audience angle. Worth it.
