# Reddit Fit Fix — 2026-05-22

## Problem
The monitor is finding credible workflow discussions but still reporting **0 honest RalphWorkflow mention fits**. That is not because Reddit is empty. It is because the selection logic is still overweighting thread families that are good research surfaces but weak mention targets: approval-loop, remote-control, CC+Codex handoff, and browser/observability complaints.

## Root cause
The monitor already separates discussion opportunities from mention fits in the report, but the query mix and scoring still over-sample saturated families and under-sample the stronger current lane:
- production failure
- review tax
- visible finish state
- done-but-unreviewed state
- merge-or-rerun decisions

In other words: the system is good at finding places where Ralph's message is relevant, but not yet selective enough about where a **brand mention** feels native.

## Fix applied
I updated `agents/marketing/reddit_monitor.py` to:
1. add new query families for **production_failure** and **visible_finish_state**
2. broaden the `broader_dev` lane with production/review-tax searches outside the usual narrow coding-tool subs
3. add stronger high-signal terms around **finished code**, **tested code**, **merge or re-run**, **open the result**, **workflow continuity**, and **verification delay**
4. explicitly down-rank `remote_supervision` for mention-fit
5. preserve the hard split between **discussion fit** and **mention fit** in the report output

## Recommended prompt / targeting rewrite
Replace any implicit target of:
- "find 5-10 good RalphWorkflow opportunities"

with:
- "find 5-10 credible workflow discussions, then separately decide whether RalphWorkflow would still feel native if mentioned lightly"

And add this explicit gate before any brand mention:

> If removing RalphWorkflow from the reply makes the comment better or more native to the thread, keep it as discussion-only and do not mention the product.

## Better targeting order
Rank thread families in this order for current live mention-fit:
1. **production failure / what breaks first in production**
2. **review tax / verification delay / done-but-unreviewed**
3. **visible finish state / what changed / merge or re-run**
4. **cleanup / archaeology / reconstruction**
5. **approval-loop / CC+Codex / remote-control** only as research-first unless the finish-state value case is unusually natural

## Operational rule
A day with **5 discussion opportunities / 0 mention fits** is now considered a healthy research day, not a failure. The failure mode is forcing mentions into weak threads.

## Next layer if mention-fit stays near zero
If the next 2-3 healthy-coverage passes still return 0 mention fits, shift effort from Reddit mention-hunting to:
- GitHub curator PRs
- comparison backlinks
- owned content syndication
- product-free Reddit comments used purely for message learning
