# Codeberg Conversion Link Coverage Fix
- **Date**: 2026-05-30T05:50 UTC
- **Commit**: `0bd58fc`

## Problem
35/36 blog posts contained Codeberg links. One post (`debugging-failed-overnight-ai-coding-run.md`) had zero in-body Codeberg references. The CTA footer (`_blog_repo_cta.html.erb`) includes Codeberg links, but in-body links carry more SEO and reader conversion weight.

## Fix
Added two Codeberg links to the closing paragraph of the troubleshooting guide:
- "Ralph Workflow" → linked to `https://codeberg.org/RalphWorkflow/Ralph-Workflow`
- "Inspect the workflow on Codeberg →" → linked to `https://codeberg.org/RalphWorkflow/Ralph-Workflow`

## Verification
- All 36 blog posts now contain Codeberg links (`grep -L "codeberg" content/blog/*.md` returns 0)
- Live page at `/blog/debugging-failed-overnight-ai-coding-run` shows 5 Codeberg references (2 in-body + 3 from CTA footer)

## Also fixed
- Deploy `indexnow_notify` task relative path → absolute path in `runtime_fidelity.rake` (was silently failing every deploy)
