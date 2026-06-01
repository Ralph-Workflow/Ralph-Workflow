# Marketing execution — SaaSHub alternatives-page Codeberg routing correction

- Timestamp: 2026-05-25 21:19 Europe/Berlin
- Action: **Posted a native correction comment on SaaSHub's Ralph Workflow alternatives page requesting Codeberg-first repo routing**
- Channel: **directory confirmation / third-party surface repair**
- Status: **submitted and email-confirmed; pending manual approval**

## Why this was the highest-leverage move now
- The execution board named the **directory secondary-surface repair packet** as the only truthful do-now asset.
- `backlink_status_latest.json` shows the live SaaSHub alternatives page still exposes the GitHub mirror but not the canonical Codeberg repo.
- Another same-family publisher/contact burst would have blurred measurement instead of fixing the active third-party routing gap.
- Reddit remains suspended from this runtime, and Apollo/publisher lanes are already inside live review windows.

## Shared findings reused
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/backlink_status_latest.json`
- `agents/marketing/logs/reddit_post_analysis_latest.json`

## What ran
Submitted a public discussion comment on:
- https://www.saashub.com/ralph-workflow-alternatives

Comment body:
> Small correction from the project side: the canonical Ralph Workflow repo lives on Codeberg, with GitHub kept as a mirror. The main SaaSHub product page already reflects that, but this alternatives page currently exposes only the GitHub repo link. If you surface repo links here, please add the primary repo first: https://codeberg.org/RalphWorkflow/Ralph-Workflow

## Result right now
- SaaSHub accepted the submission with **HTTP 200**.
- The confirmation email was retrieved from the IONOS inbox and the verification link was clicked successfully.
- SaaSHub returned: **"Thanks for confirming your comment! It will appear online as soon as we have manually approved it."**
- This is now a real queued correction on the live page; the remaining blocker is SaaSHub manual moderation, not local follow-through.

## Verification
- Fetched the live alternatives surface before posting and confirmed it still lacked a Codeberg repo link.
- Submitted the native `/comments` form with a valid CSRF token and page referer.
- Retrieved the fresh SaaSHub confirmation mail from the IONOS inbox and opened the verification link.
- Verified the returned page contained SaaSHub's success notice: `Thanks for confirming your comment! It will appear online as soon as we have manually approved it.`

## Expected effect
If the confirmation is completed and the comment is approved, SaaSHub will have a page-native correction request tied directly to the live GitHub-only secondary surface instead of leaving the Codeberg routing gap implicit.
