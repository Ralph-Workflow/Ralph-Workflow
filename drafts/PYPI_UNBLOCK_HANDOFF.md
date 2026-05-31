# PyPI Unblock Handoff — Single Action Required

**Created:** 2026-05-31 12:07 CEST
**Priority:** High (only unblock that could convert existing 1,297 downloads/month into Codeberg stars)
**Effort:** 5 minutes
**Prerequisites:** PyPI.org account for the `ralph-workflow` project

## Why this matters

PyPI is the **only external channel with demonstrated usage**: 1,297 monthly downloads (~5/day) as of May 2026. Every downloader is a `pip install ralph-workflow` user who chose RalphWorkflow — they're the highest-intent audience we have.

Currently: these users install the package but **never see a Codeberg CTA or project home page** because the package can't be updated without `PYPI_TOKEN`.

## The one action needed

Set the `PYPI_TOKEN` environment variable on the host (or add it to the secrets manager):

```bash
# 1. Get token from https://pypi.org/manage/account/token/
#    (needs a PyPI account with maintainer access to the ralph-workflow project)

# 2. Set it
export PYPI_TOKEN=pypi-AgEIcH...your-token-here

# 3. Verify
python3 agents/marketing/pypi_conversion_lane.py --dry-run
```

## What this unblocks

1. **PyPI README update**: Add Codeberg CTA + GitHub mirror CTA to the package page on PyPI.org
2. **Release description updates**: Each new release page gets a CTA footer linking back to the repo
3. **SEO backlink from pypi.org**: pypi.org/project/ralph-workflow has high domain authority — a backlink to codeberg.org is valuable for discoverability
4. **Conversion funnel**: pip install → PyPI page → Codeberg star

## What was killed as part of this repair

- **Cron job `pypi_auto_unblocker.py`** (every 12h) — removed; polls without auth, produces no external action
- **Cron job `pypi_conversion_lane.py`** (daily) — removed; can't publish without auth
- **Cron job `pypi_readiness_watchdog.py`** (daily) — removed; monitors a blocker it can't fix, produces only internal bookkeeping

All three produced internal log artifacts with zero external distribution. Once `PYPI_TOKEN` is set, the conversion lane can be re-enabled as a single cron job.

## After unblock

```bash
# Re-add the conversion lane (1/day is sufficient)
(crontab -l; echo "30 9 * * * /usr/bin/python3 /home/mistlight/.openclaw/workspace/agents/marketing/pypi_conversion_lane.py >> /home/mistlight/.openclaw/workspace/agents/marketing/logs/pypi_conversion_cron.log 2>&1") | crontab -
```
