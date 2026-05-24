# Ralph Workflow Distribution Action Brief
Generated: 2026-05-25T00:55:00
Chosen lane: **directory_confirmation**

## Why this lane
- Directory submissions are paused and live listing proof already exists, so refresh approval/backlink evidence and reuse it in the next higher-intent lane instead of inventing another reset.
- Primary Codeberg adoption is flat in the current measurement window.
- 6 directory submissions already shipped in the last 24 hours.
- 7 curator contact attempts already shipped in the last 24 hours.
- 4 live external marketing action(s) already shipped in the last 6 hours.
- If no new outcome lands first, this short-window congestion clears at 2026-05-25T02:05:05. Before then, another live outbound action would mostly blur measurement.
- Fresh publisher-contact targets remain, but the short review window already has enough live external actions that another contact packet now would blur measurement more than it helps.
- Active repair window says to pause net-new directory submissions until current approval windows mature.
- Active repair window says to hold another same-family curator-contact burst and use a different lane.
- Reddit search coverage is degraded, so more monitor passes are lower leverage than third-party distribution prep.
- Reddit execution is fail-closed from this environment right now, so the loop should not treat another Reddit pass as a shippable distribution lane.
- HN/Lobsters has repeated as a blocked ceiling, so the loop should create a different distribution lane in the same run.
- Apollo is authenticated and the runtime has recent proof of a usable live import/sequence step, so managed outbound is a real lane here.
- Apollo already has an active measurement window until 2026-05-30T00:14:49.075391+02:00, so do not spend this run repackaging the same outbound lane.
- 5 curator outreach targets are already live in the queue, so the loop should advance or review them instead of regenerating the same packet.
- 25 curator targets are already inside active reply/backlink review windows, so another same-family outreach batch would mostly create unmeasurable overlap.
- The curator handoff packet is already current for the top prepared targets and was already delivered in this review window, so regenerating it again would be fake progress.
- Manual-contact-only curator targets are still waiting in the live queue (vivy-yi/awesome-agent-orchestration), so the loop should advance contact discovery + execution instead of inventing new reset work.
- The manual-contact execution packet is already current for the waiting targets and was already delivered in this review window, so selecting it again would be fake progress.
- Fresh publisher outreach already shipped in the current 7-day review window (AXME Code, Bollwerk / Werkstatt, HidsTech), so those targets should not be re-queued immediately.
- Some remaining publisher targets only expose non-runtime-executable channels (ctxt.dev / Signum), so they should not keep this lane looking actionable until a sendable path exists.
- The primary-repo-flat publisher contact packet is already current for the remaining untouched target set, so the loop should enforce follow-through instead of pretending a fresh packet is needed.
- Curator outreach already has enough live measurement windows open; the next move should create fresh demand capture instead of piling on more curator contact.
- The comparison/backlink queue already covers every prepared comparison page, so another comparison follow-through would also be fake activity.
- Low-intent directory distribution is already in a same-family burst, so another submission right now would mostly stack overlapping approval windows instead of creating a cleaner adoption read.
- Backlink status already shows 2 live directory listing(s), so the loop should reuse that evidence instead of acting like every submission is still opaque.
- The directory-confirmation snapshot is stale relative to the current submission burst, so refresh live listing/backlink evidence before adding more low-intent distribution.
- The prior StackOverflow draft pass returned zero candidates, so if that lane is chosen it must rely on the repaired API-driven search rather than the old scrape-only path.
- The StackOverflow packet was already delivered for manual placement in the current review window, so another handoff packet now would be fake progress.
- The post-cooldown StackOverflow slot already ran after the retry window and still produced no fresh placement-ready outcome, so retire this packet for now and spend the next slot elsewhere.
- Repo conversion proof assets already shipped recently, so this run should not loop on another docs-only proof-asset pass.

## Shared findings reused
- adoption_metrics_latest.json: Codeberg movement is the primary success gate
- channel_discovery.json: validated easy-submit directory lanes
- outreach-log.md: avoid duplicate submission work and repeated HN/Lobsters-only handoff
- market_intelligence_latest.json: reusable competitor comparisons and positioning truths
- apollo_status.json: managed outbound is authenticated and available for execution packaging

## Immediate directory confirmation work
- Re-run `agents/marketing/backlink_status.py` and reuse `backlink_status_latest.json` as the canonical live-listing snapshot
- Treat live listings as proof assets to reuse in curator/comparison packets instead of pretending all submissions are still pending black boxes
- Identify which approved listings already route to Codeberg first and which still need follow-up or evidence capture
- Do not count another net-new directory submission as progress until this confirmation pass is refreshed
