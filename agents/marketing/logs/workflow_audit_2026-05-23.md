# Marketing workflow audit — 2026-05-23 21:11 CEST

- **Verdict:** Codeberg adoption is still flat, so the live problem is still **distribution and message to primary-repo conversion**. The loop is shipping actions, but the last 24 hours over-indexed on **same-family directory submissions** and **same-family curator outreach**, which creates noisy approval/reply windows instead of a clean adoption read.
- **Primary repo status:** Codeberg remains **10⭐ / 2👁 / 2🍴** with **+0 / +0 / +0** across the recent 9-sample window.
- **Mirror status:** GitHub remains **0⭐ / 2👁 / 0🍴** and should stay secondary.

## What actually worked
- The system did ship real external actions instead of stalling: recent listings/outreach include ToolShelf, OpenAgents, AI Tools, NavTools, AIforCode, and Apollo launch.
- Shared findings remain coherent across the loop: `MARKETING_WORKFLOW_PRINCIPLES.md`, `FOUR_MARKETING_QUESTIONS.md`, `market_intelligence_latest.json`, adoption metrics, Reddit analysis, and StackOverflow lane artifacts all point to the same bottleneck.
- Apollo is a real lane now, but it is already in a **measurement window until 2026-05-30** and should not be repackaged before then.

## What did not work
- **Codeberg movement is flat.** That makes the current distribution mix failing until it produces a measurable delta.
- **Directory-submission burst is low-signal now.** 16 submissions in the last 24h is not a growth story; it is overlapping review windows.
- **Curator-contact burst is also low-signal now.** 48 attempts in the last 24h is saturation, not precision.
- **Reddit remains blocked/degraded**, so it is not the right lane to spend this run on.
- **StackOverflow exists but is not yet converting into live answers.** Current output shows one real question and a reuse/handoff state rather than posted demand capture.

## What is repetitive
- Repeating the same diagnosis: flat Codeberg, Reddit blocked, Apollo measuring, directories/outreach saturated.
- Treating more same-family submissions or same-family outreach as “action” when they mostly worsen attribution.
- Prior runtime behavior that could mark repairs as measurement-pending **before** a qualifying repair had actually shipped.

## Runtime repair shipped in this run
- **Patched `agents/marketing/run.py`** so audit repairs only advance from `needs_execution` to `pending_measurement` **after** a qualifying execution happens.
- This prevents fake-green behavior where handoff-only/internal artifacts could make the audit look healthier than the real outcome system.
- Added regression tests in `agents/marketing/tests/test_run_repair_mode.py` covering:
  - handoff-only execution does **not** advance `primary_repo_flat`
  - live external execution **does** advance `primary_repo_flat`
  - pause/overlap repairs can advance when the loop actually chooses a different lane
- Verification: `python3 -m unittest agents.marketing.tests.test_run_repair_mode -v` passed on **2026-05-23**.

## What should change now
1. **Do not send more net-new directory submissions** until current review windows mature or prove dead.
2. **Do not send another same-family curator burst** while 48 recent attempts are still inside reply/backlink windows.
3. **Use the next active lane on higher-intent demand capture or execution follow-through**:
   - StackOverflow answer execution / handoff
   - manual-contact packet execution for prepared curator targets with non-GitHub contacts
   - comparison-backlink follow-through only if it creates clearer Codeberg attribution than another broad burst
4. Keep Codeberg as the only success surface that matters for this loop.

## Shared findings reused
- `agents/marketing/MARKETING_SELF_IMPROVEMENT.md`
- `agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md`
- `agents/marketing/FOUR_MARKETING_QUESTIONS.md`
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/adoption_metrics_latest.md`
- `agents/marketing/logs/reddit_post_analysis.md`
- `agents/marketing/logs/stackoverflow_answer_lane_latest.json`
- `/home/mistlight/.openclaw/workspace/outreach-log.md`

## Immediate recommendation to the next run
- Treat **same-family burst avoidance** as enforced policy, not advice.
- Prefer a lane that creates either:
  - a **live high-intent demand-capture asset**, or
  - a **manual execution packet for already-prepared non-GitHub contacts**,
  without pretending another reset, packet refresh, or low-intent listing is progress.
