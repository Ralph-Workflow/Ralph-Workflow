# Manual Bootstrap Handoff — 2026-05-28

Three distribution lanes are blocked by headless-proof requirements and need human
intervention to unlock. None of them can be automated from this runtime.

## 1. PyPI token (publish new version)

**Impact:** 1,498 downloads/month from PyPI with ZERO Codeberg CTAs in the
live package README, because the conversion-optimized README (commit d7cdc101)
is already pushed but cannot be published without a PyPI API token.

**What you do:**
1. Go to https://pypi.org/manage/account/token/
2. Create a token scoped to the `ralph-workflow` project
3. Save it to `~/.pypirc`:
```
[pypi]
  username = __token__
  password = pypi-xxxxxxxx
```
4. The marketing loop will automatically detect the token on next run

**Or, one-shot manual publish:**
```
cd /home/mistlight/.openclaw/workspace/ralph-workflow-canon
python3 -m build
twine upload dist/*
```

## 2. GitHub CLI authentication (Discussions lane)

**Impact:** GitHub Discussions is the strongest identified-but-unused autonomous
distribution lane. Reddit is permanently blocked from this Hetzner runtime,
but GitHub Discussions on the mirror repo is an unblocked, high-intent surface
for developer community growth.

**What you do:**
```
gh auth login
```
Follow the interactive prompts. Once authenticated, the GitHub Discussions
lane bootstrap can post seed discussions and monitor engagement autonomously.

## 3. Dev.to account (manual one-time)

**Impact:** Dev.to is a high-reach developer content surface, but signup is
blocked by reCAPTCHA (headless signup impossible). Both the old-credentials
path and the browserless bootstrap script confirmed: accounts never existed.

**What you do:**
1. Go to https://dev.to/enter and create an account manually
2. Navigate to Settings → Extensions → Generate API key
3. Save the API key to the environment:
```
export DEVTO_API_KEY=xxxxxxxx
```
4. Once available, the marketing loop's crossposter can publish blog content

## What changes today without human intervention

Even without these manual steps, three concrete repairs went live in this run:
- **Cron reduced from 48/day to 6/day** (00:00/04:00/08:00/12:00/16:00/20:00)
- **Reddit permanently retired** from the active marketing pipeline (monitor,
  retrospective, and report generation all skip when `execution_blocked_permanent`)
- **Material-change gate wired into run.py** (daily 9am run now respects the
  same 4-hour cooldown as the watchdog cron)

## Verification checklist

- [ ] PyPI token available → `twine upload dist/*` → PyPI README updated
- [ ] `gh auth status` returns logged-in → Discussions lane activates on next cycle
- [ ] `echo $DEVTO_API_KEY` returns a key → blog crosspost lane activates
