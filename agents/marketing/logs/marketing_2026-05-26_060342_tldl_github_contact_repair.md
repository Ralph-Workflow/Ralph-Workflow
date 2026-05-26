# TLDL GitHub contact repair
Generated: 2026-05-26T06:03:42+02:00

## Why this ran
- Short review-window congestion is still active until 2026-05-26 08:57 Europe/Berlin, so another live outbound action would mostly blur measurement.
- The execution board still had no truthful do-now asset and TLDL was still a weak website/social-only publisher target.
- Official TLDL FAQ exposes GitHub as a supported contact route, so the best same-run move was to turn that into a reusable publisher-contact asset.

## What changed
- Added GitHub repo-link extraction to primary publisher contact discovery and promote repo links into issue-creation routes.
- Added TLDL FAQ as an explicit discovery surface so the runtime sees the official GitHub contact hint.
- Tightened website labeling so about/faq pages stop being mislabeled as direct contact pages.
- Added regression coverage for GitHub-issue discovery and TLDL-specific enrichment.

## Verification
- `python3 -m unittest agents.marketing.tests.test_primary_repo_flat_contact_discovery -q`
- `python3 agents/marketing/primary_repo_flat_contact_discovery.py`
- Refreshed `drafts/primary_repo_flat_contact_handoff_packet_latest.md` and `drafts/marketing_execution_board_latest.md` from live discovery output.

## Outcome
- TLDL now appears in `primary_repo_flat_contact_discovery_latest.json` with `https://github.com/shenli/tldl/issues/new` as a discovered contact route.
- The refreshed primary publisher packet now carries that GitHub issue path forward for the first truthful post-hold publisher lane.
