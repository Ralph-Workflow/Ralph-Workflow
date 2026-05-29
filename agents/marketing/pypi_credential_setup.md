# PyPI Credential Setup for ralph-workflow

## The blocking state
- **PyPI has v0.8.7** (stale README, no Codeberg CTA, 2,840 chars)
- **Repo has v0.8.8** (Codeberg-optimized README, 7 Codeberg mentions, 4,896 chars)
- **1,498 downloads/month** are seeing the stale README
- **v0.8.8 wheel + sdist** already built, twine-checked, and tagged — ready to publish
- **Tag v0.8.8** already pushed to both Codeberg and GitHub mirror

## What's needed
A PyPI API token with upload permission for the `ralph-workflow` project.

## How to create the token
1. Go to https://pypi.org/manage/account/token/
2. Click "Add API token"
3. Scope: **"ralph-workflow" project only** (or entire account if you prefer)
4. Name: `ralph-workflow-release`
5. Copy the token (starts with `pypi-`)

## How to install the token
**Option A (recommended — env var):**
```bash
export HATCH_INDEX_AUTH=pypi-xxxxxxxx
./agents/marketing/scripts/release_pypi.sh
```

**Option B (persistent — .pypirc):**
```bash
cat > ~/.pypirc << 'EOF'
[pypi]
username = __token__
password = pypi-xxxxxxxx
EOF
chmod 600 ~/.pypirc
./agents/marketing/scripts/release_pypi.sh
```

## After publishing
```bash
# Verify the README rendered correctly
curl -s https://pypi.org/project/ralph-workflow/ | grep -c codeberg
# Should show Codeberg mentions in the page
```

## Impact estimate
- 1,498 monthly downloaders see zero Codeberg CTAs right now
- The v0.8.8 README has 7 Codeberg mentions + star CTA
- Even a 1% conversion rate from those downloads = ~15 new Codeberg visits/month
- Current Codeberg: 12 stars (flat window)
