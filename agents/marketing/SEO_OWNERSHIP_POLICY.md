# SEO Ownership Policy

## Hard boundary
- **SEO only lives in the Ralph-Site repo.**
- SEO includes sitemap generation, robots rules, canonical URLs, metadata, schema, crawl/indexing policy, search snippets, and public search presentation.
- Do **not** implement SEO changes in Ralph Workflow `README.md` files, Sphinx/manual source, or other non-site repos.

## What SEO agents must do
- If the task is SEO-related, work in `Ralph-Site`.
- If the current agent cannot safely edit `Ralph-Site`, hand the task to the agent/session that owns Ralph-Site.
- If a task mixes documentation information architecture with SEO/indexing work, split it into two tracks:
  - **Ralph Workflow repo:** docs content, manual structure, onboarding copy, proof/content lanes.
  - **Ralph-Site repo:** titles, meta tags, canonicals, sitemap, robots, wrappers, crawl/indexing behavior, search presentation.
- If the task cannot be split cleanly, fail closed and escalate instead of leaking SEO edits into docs-source repos.

## Admin/security rule
- Any admin-panel change must include a security review before commit.
- This includes admin pages, admin analytics, admin auth, admin settings, and other privileged operator surfaces.

## Enforcement intent
This policy exists so SEO agents do not drift into source-doc ownership and so public-search changes stay owned by the site layer.
