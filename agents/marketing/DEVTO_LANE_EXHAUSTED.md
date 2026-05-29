# Dev.to lane — permanently blocked from headless bootstrap

All Dev.to scripts in agents/marketing/ are blocked by reCAPTCHA on signup.
Both:
  - old credentials path (devto_api_key.py)
  - headless browserless bootstrap (devto_browserless_bootstrap.py,
    devto_local_bootstrap.py, devto_lane_bootstrap.py)
...confirmed the accounts never existed.

The log at agents/marketing/logs/marketing_2026-05-28_175148_devto_bootstrap.json
documents the final attempt (`no_account_could_login`).

## What this means
- Do not run devto_*.py scripts from cron or the marketing loop.
- The marketing_momentum_watchdog already lists dev.to in BLOCKED_CHANNELS.
- The only path forward is manual human signup: see
  drafts/MANUAL_BOOTSTRAP_HANDOFF_2026-05-28.md

## List of exhausted scripts
- agents/marketing/devto_api_key.py — exhausts old credential paths
- agents/marketing/devto_browserless_bootstrap.py — reCAPTCHA blocks headless
- agents/marketing/devto_lane_bootstrap.py — orchestrator that delegates to the above
- agents/marketing/devto_local_bootstrap.py — same reCAPTCHA barrier
- agents/marketing/devto_crossposter.py — depends on a working API key

Status: EXHAUSTED (all paths tried, all reCAPTCHA-blocked)
Needs: human Dev.to signup → API key in environment
