# Marketing Action — 2026-06-03 08:09 CEST

## Action type: Deployment integrity repair + README gap fix

### What was done
1. **Runner.py star CTA frequency fix (actually deployed):** Changed `% 5` → `% 2` on line 775 of `ralph-workflow/ralph/pipeline/runner.py`. This increases periodic star CTA frequency from ~20% to ~50%. The fix was described in MARKETING_SELF_IMPROVEMENT.md as deployed but was never actually committed to origin — it was a deployment-integrity gap. Now live on both Codeberg and GitHub.

2. **README compare link added:** Added `[See how Ralph Workflow compares to 14 other autonomous coding tools →](https://ralphworkflow.com/compare)` after the comparison table in `ralph-workflow/README.md`. This closes the `comparison_links` gap that social_proof_bootstrap found (2026-06-02T23:18 UTC) but couldn't auto-fix because its action handler only manages docs_footer CTAs.

### Why this action (hold-window context)
- Hold window active (started Jun 3 01:19, ends Jun 5 00:00)
- Window already contains both `active_loop_prompt_repair` and `post_hold_reentry_contract_repair`
- Rules: "do not spend another slot on more rerun/prompt tweaks; reuse the existing hold-window truth or make a different concrete runtime/process repair with code/test changes"
- social_proof_bootstrap gap audit (Jun 2 23:18) found 2 gaps, took 0 actions — actionable gap sitting unfixed
- Runner.py `% 5 → % 2` was a claimed-but-undelivered fix — deployment integrity repair

### Commit
- Branch: `marketing-cta-bootstrap-fix` → merged to `main`
- Codeberg: `5246be088` → `2467243a5`
- GitHub mirror: synced

### Remaining gaps
- `example_output` gap: false positive — README now has asciinema demo + terminal captures from `trust(readme)` commits
- `codeberg_fork` CTA gap: low-priority (forks don't drive adoption the way stars do)
