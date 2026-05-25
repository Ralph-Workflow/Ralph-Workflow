# Primary-repo-flat contact route quality repair

- Generated: 2026-05-25T03:47:00+02:00
- Type: **REPAIRED / DISTRIBUTION_ARCHITECTURE**

## Why this was the right move now
- The active short review window still has **no truthful do-now outbound send**.
- The live prepared publisher packet still pointed **NxCode** at weak role emails (`legal@`, `privacy@`, `support@`) and **TIMEWELL** at placeholder-tainted email extraction.
- That would have made the next post-hold manual contact slot weaker than it needed to be, even though the shared packet lane is the current replacement tactic for flat Codeberg adoption.

## What I changed
- Patched `agents/marketing/primary_repo_flat_contact_discovery.py` to:
  - demote weak role emails behind public contact routes,
  - stop explicit NxCode/TIMEWELL discovery from leaning on legal/privacy pages,
  - keep placeholder email filtering intact.
- Patched `agents/marketing/distribution_lane_executor.py` so the handoff packet prefers real site contact paths over weak role-email fallbacks and safely handles timezone-aware packet refresh calls.
- Added regression coverage in:
  - `agents/marketing/tests/test_primary_repo_flat_contact_discovery.py`
  - `agents/marketing/tests/test_distribution_lane_executor_contact_suggestion.py`
- Regenerated the shared artifacts:
  - `agents/marketing/logs/primary_repo_flat_contact_discovery_latest.json`
  - `drafts/primary_repo_flat_contact_discovery_latest.md`
  - `drafts/primary_repo_flat_contact_handoff_packet_latest.md`

## Shared findings reused
- `marketing_workflow_audit_latest.json`
- `distribution_lane_latest.json`
- `marketing_execution_board_latest.md`
- `primary_repo_flat_contact_discovery_latest.json`
- `adoption_metrics_latest.json`
- Live target pages:
  - `https://www.nxcode.io/resources/news/codex-vs-cursor-vs-claude-code-2026`
  - `https://www.nxcode.io/ar/contact`
  - `https://timewell.jp/en/columns/ai-coding-tools-complete-benchmark-2026`
  - `https://timewell.jp/company`

## Verification
- `python3 -m unittest agents.marketing.tests.test_primary_repo_flat_contact_discovery agents.marketing.tests.test_distribution_lane_executor_contact_suggestion -q`
- Regenerated packet successfully via `_write_primary_repo_flat_contact_handoff_packet(...)`
- Confirmed refreshed packet now leads:
  - **NxCode** → site contact path first, not legal/privacy/support email
  - **TIMEWELL** → public site/company contact path first

## Outcome
The next post-hold primary-repo-flat publisher packet is now cleaner, more truthful, and less likely to waste the next Codeberg-first contact attempt on low-quality channels.
