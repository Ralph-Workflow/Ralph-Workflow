# Marketing Automation Cleanup Plan

Date: 2026-05-12

## Goal
Turn the current marketing automation into a smaller, safer, self-improving loop focused on RalphWorkflow.

## Decisions

### Keep active
1. `generate_content.py`
   - Create scheduled RalphWorkflow drafts
   - Add experiment metadata so results can influence future decisions
2. `run_posting.py`
   - Publish only real scheduled drafts to write.as
   - Log post metadata and outcomes in a structured way
3. `run.py`
   - Collect daily metrics
   - Produce weekly decisions
   - Keep the system focused on channels that are actually usable
4. `sync_research.py`
   - Keep hourly tested sync in place

### Pause / disable
1. LLM cron jobs that duplicate or conflict with the scripted pipeline:
   - `SEO Agent`
   - `Content Engine Agent`
   - `Community & Outreach Agent`
2. Broken or noisy weekly jobs:
   - `seo-weekly`
   - `marketing-reflection`

### De-emphasize / leave dormant in code for now
- `seo_outreach.py`
- `channel_discovery.py`
- `reflection_engine.py`
- `agents/content/run.py`
- `agents/community/run.py`

They are not the active operating path anymore.

## New operating model

### Content loop
1. Generate 3 RalphWorkflow drafts per week
2. Attach metadata:
   - `experiment_id`
   - `content_type`
   - `keyword`
   - `cta`
   - `hypothesis`
3. Publish only scheduled longform drafts to write.as
4. Record URL, draft hash, metadata, and result

### Measurement loop
Daily:
- homepage health
- robots/sitemap health
- recent successful posts
- write.as views per post

Weekly:
- compare content types by average views
- identify best/worst topic buckets
- produce simple decisions:
  - continue
  - increase
  - reduce
  - hold
  - unblock later

## Cron target state
- `content-generator` → Mon/Wed/Fri 07:00 Europe/Berlin
- `content-poster` → Mon/Wed/Fri 08:00 Europe/Berlin
- `marketing-daily` → daily 09:00 Europe/Berlin
- `Push research findings to git repo` → hourly

Research sync includes curated workspace materials plus `memory/*.md` notes, not runtime `memory/.dreams/*` byproducts.

## Follow-up work after this cleanup
1. Move any remaining secrets out of scripts and rotate exposed credentials
2. Add Google Search Console or another trustworthy search metric source
3. Add click/conversion tracking once a stable CTA destination is chosen
4. Replace blocked-channel exploration with a manual unblock registry
5. Run a policy-safe unblock review agent that learns which legitimate next steps are most useful for each blocked channel
