# Deploy Updated PyPI README

## One-command deploy

```bash
cd /home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror/ralph-workflow
cp /home/mistlight/.openclaw/workspace/agents/marketing/pypi_readme_deploy/README.pypi.md README.md
git add README.md
git commit -m "fix(pypi): update README with Codeberg CTA and install quickstart"
# Push to Codeberg first
git push origin main
# Then build and upload
python3 -m build
TWINE_PASSWORD="$PYPI_TOKEN" python3 -m twine upload dist/*.tar.gz dist/*.whl
```

## What this changes
- Adds Codeberg CTA (currently missing from PyPI description)
- Adds GitHub mirror link
- Adds install + quickstart instructions
- Links to ralphworkflow.com docs
- ~1700 chars (current PyPI desc is ~2840 chars of stale truncated README)

## Impact estimate
- 1,498 downloads/month see the updated README on pypi.org
- Every pip install shows `pip show ralph-workflow` from this description
- Currently: no Codeberg CTA. After deploy: direct link to primary repo.
