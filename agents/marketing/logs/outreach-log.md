# Outreach Log

## 2026-05-27
- **Codeberg**: +1 star (11 total). Codeberg remains the primary adoption surface. GitHub mirror flat (0 stars, 2 watchers).

## 2026-05-28 10:49 CEST — PyPI README Conversion Repair

**Action:** Applied conversion-optimized PyPI-facing README to `ralph-workflow/README.md` (commit `d7cdc101`). Pushed to Codeberg + GitHub.

**Why:** PyPI v0.8.7 showed 1,498 downloads/month with ZERO Codeberg links in the body. Project URLs sidebar had Repository and Issues links, but no visible star/watch/fork CTA existed anywhere on the page. This was a massive `distribution_and_message_to_primary_repo_conversion` leak.

**What changed:**
- Added ⭐ Star on Codeberg CTA block at top
- Expanded install section with Codeberg clone URL
- Added product positioning, proof-before-install, After Your First Run sections
- Built and verified sdist+wheel packages (0.8.8) — Codeberg star CTA confirmed in PKG-INFO
- Codeberg push: success
- GitHub mirror push: success
- **PyPI publish: BLOCKED — no PYPI_TOKEN in environment**

**Impact:** Once published, ~1,498 monthly PyPI downloaders will see an explicit Codeberg star CTA in the page body for the first time.

**Next:** User publishes: `cd repos/Ralph-Workflow/github-mirror/ralph-workflow && hatch publish` (requires PYPI_TOKEN)
