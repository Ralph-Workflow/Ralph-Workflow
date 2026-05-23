# Ralph Workflow Distribution Action Brief
Generated: 2026-05-23T17:44:46
Chosen lane: **distribution_reset**

## Why this lane
- The proof-asset lane already shipped recently and the current external lanes are still saturated or in-flight; create fresh reset targets instead of looping on the same docs and handoff surfaces.
- Primary Codeberg adoption is flat in the current measurement window.
- 1 owned-content posts already shipped in the last 36 hours.
- 13 directory submissions already shipped in the last 24 hours.
- 12 curator contact attempts already shipped in the last 24 hours.
- Reddit search coverage is degraded, so more monitor passes are lower leverage than third-party distribution prep.
- Reddit execution is fail-closed from this environment right now, so the loop should not treat another Reddit pass as a shippable distribution lane.
- Apollo is authenticated and the runtime has recent proof of a usable live import/sequence step, so managed outbound is a real lane here.
- Apollo already has an active measurement window until 2026-05-30T00:14:49.075391+02:00, so do not spend this run repackaging the same outbound lane.
- 14 curator targets are already inside active reply/backlink review windows, so another same-family outreach batch would mostly create unmeasurable overlap.
- Curator outreach already has enough live measurement windows open; the next move should create fresh demand capture instead of piling on more curator contact.
- The comparison/backlink queue already covers every prepared comparison page, so another comparison follow-through would also be fake activity.
- Low-intent directory distribution is already in a same-family burst, so another submission right now would mostly stack overlapping approval windows instead of creating a cleaner adoption read.
- The prior StackOverflow draft pass returned zero candidates, so if that lane is chosen it must rely on the repaired API-driven search rather than the old scrape-only path.
- A fresh StackOverflow answer draft already exists, so do not rerun the same demand-capture lane until that asset is posted, reused, or ages out of the current review window.
- The StackOverflow handoff packet is already current, so regenerating it again would be fake progress.
- Repo conversion proof assets already shipped recently, so this run should not loop on another docs-only proof-asset pass.

## Shared findings reused
- adoption_metrics_latest.json: Codeberg movement is the primary success gate
- channel_discovery.json: validated easy-submit directory lanes
- outreach-log.md: avoid duplicate submission work and repeated HN/Lobsters-only handoff
- market_intelligence_latest.json: reusable competitor comparisons and positioning truths
- apollo_status.json: managed outbound is authenticated and available for execution packaging

## Recent owned-content already shipped
- AI Coding Tool Comparison: Claude Code, Cursor, Aider, and the Workflow Layer Most Teams Actually Need (telegraph)

## Immediate queue-reset work
- Do not count curator or comparison queue follow-through alone as a fresh repair
- Reuse `market_intelligence_latest.json` and current queue logs to define the next untouched target classes
- Add genuinely new third-party citation/backlink targets before the next outreach-prep execution
- Keep Codeberg as the only primary CTA while expanding the target universe
