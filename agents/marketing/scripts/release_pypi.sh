#!/usr/bin/env bash
# PyPI release pipeline for ralph-workflow
# Usage: ./release_pypi.sh
#
# Prerequisites:
#   - PyPI token set via HATCH_INDEX_AUTH env var, or
#   - Token stored in ~/.pypirc like:
#       [pypi]
#       username = __token__
#       password = pypi-...
#
# This script is designed to run from the ralph-workflow package directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_DIR="/home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror/ralph-workflow"

cd "$PACKAGE_DIR"

echo "=== PyPI Release Pipeline for ralph-workflow v$(grep '__version__' ralph/__init__.py | head -1 | sed "s/.*= *['\"]//;s/['\"].*//") ==="

# Step 1: Verify clean working tree
if ! git diff --quiet --exit-code; then
  echo "❌ Working tree is dirty. Commit or stash changes first."
  exit 1
fi

# Step 2: Verify tag exists
TAG="v$(grep '__version__' ralph/__init__.py | head -1 | sed "s/.*= *['\"]//;s/['\"].*//")"
if ! git describe --tags --exact-match HEAD 2>/dev/null; then
  TAG_MATCH=$(git tag -l "$TAG" | head -1)
  if [ -z "$TAG_MATCH" ]; then
    echo "❌ Tag $TAG does not exist. Run: git tag $TAG -m '...' && git push origin $TAG && git push github $TAG"
    exit 1
  fi
  echo "⚠️  HEAD is not at $TAG, but tag exists. Proceeding with tag version."
fi

# Step 3: Build
echo ""
echo "=== Building wheel and sdist ==="
uv run --with hatchling hatch build
echo "✅ Build complete"

# Step 4: Twine check
echo ""
echo "=== Twine check ==="
twine check dist/*.whl dist/*.tar.gz
echo "✅ Twine check passed"

# Step 5: Check credentials
HAS_AUTH=false
if [ -n "${HATCH_INDEX_AUTH:-}" ]; then
  HAS_AUTH=true
elif [ -f "$HOME/.pypirc" ] && grep -q 'pypi-' "$HOME/.pypirc" 2>/dev/null; then
  HAS_AUTH=true
elif [ -n "${TWINE_PASSWORD:-}" ] || [ -n "${TWINE_USERNAME:-}" ]; then
  HAS_AUTH=true
fi

if [ "$HAS_AUTH" = false ]; then
  echo ""
  echo "⚠️  No PyPI credentials found. Create one of:"
  echo "   1. export HATCH_INDEX_AUTH=pypi-..."
  echo "   2. Create ~/.pypirc with token (see ~/.openclaw/workspace/agents/marketing/pypi_credential_setup.md)"
  echo "   3. export TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-..."
  echo ""
  echo "   Then re-run this script."
  exit 1
fi

# Step 6: Publish
echo ""
echo "=== Publishing to PyPI ==="
uv run --with hatchling hatch publish
echo "✅ Published to PyPI"

# Step 7: Verify
echo ""
echo "=== Verify PyPI page ==="
echo "Check: https://pypi.org/project/ralph-workflow/$TAG/"
echo ""
echo "Done 🎉"
