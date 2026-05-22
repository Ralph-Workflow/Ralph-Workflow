#!/bin/bash
# Execute curator PRs for Ralph Workflow
# Prerequisites: gh auth login (run: gh auth login)
# Then: bash execute_curator_prs.sh

set -e

GITHUB_USER="mistlight"  # gh auth status shows this

echo "=== Curator PR Executor for Ralph Workflow ==="
echo "Authenticated as: $($GH_OR_GITHUB_CLI auth status 2>/dev/null | grep 'account' | awk '{print $2}' || echo '?')"
echo ""

# Target 1: ai-for-developers/awesome-ai-coding-tools (HIGH priority)
echo "[1/7] ai-for-developers/awesome-ai-coding-tools..."
cd /tmp/awesome-ai-coding-tools 2>/dev/null || git clone --depth 1 https://github.com/ai-for-developers/awesome-ai-coding-tools.git /tmp/awesome-ai-coding-tools
cd /tmp/awesome-ai-coding-tools
git remote set-url origin git@github.com:ai-for-developers/awesome-ai-coding-tools.git 2>/dev/null || true
# Fork if needed
gh repo fork ai-for-developers/awesome-ai-coding-tools --clone=false 2>/dev/null || echo "  (fork may already exist)"
# Add entry
if ! grep -q "RalphWorkflow" README.md 2>/dev/null; then
  echo "- [Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — composable loop framework and AI orchestrator for unattended coding runs" >> README.md
  git checkout -b ralf-workflow-add
  git add README.md && git commit -m "Add Ralph Workflow to AI coding tools"
  gh pr create --repo ai-for-developers/awesome-ai-coding-tools --title "Add Ralph Workflow — AI coding orchestrator for unattended runs" --body "Ralph Workflow is a free open-source composable loop framework and AI orchestrator for unattended coding runs. Adds a structured workflow layer to existing AI coding tools (Claude Code, Codex CLI, OpenCode). Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow" 2>/dev/null && echo "  PR created!" || echo "  PR may already exist or auth issue"
else
  echo "  Ralph Workflow already in README, skipping."
fi
echo ""

# Target 2: filipecalegario/awesome-vibe-coding (MEDIUM-HIGH)
echo "[2/7] filipecalegario/awesome-vibe-coding..."
cd /tmp/awesome-vibe-coding 2>/dev/null || git clone --depth 1 https://github.com/filipecalegario/awesome-vibe-coding.git /tmp/awesome-vibe-coding
cd /tmp/awesome-vibe-coding
git remote set-url origin git@github.com:filipecalegario/awesome-vibe-coding.git 2>/dev/null || true
gh repo fork filipecalegario/awesome-vibe-coding --clone=false 2>/dev/null || echo "  (fork may already exist)"
if ! grep -q "RalphWorkflow" README.md 2>/dev/null; then
  echo "- [Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — Ralph Workflow: free open-source AI coding orchestrator for unattended runs" >> README.md
  git checkout -b ralf-workflow-add
  git add README.md && git commit -m "Add Ralph Workflow to vibe coding tools"
  gh pr create --repo filipecalegario/awesome-vibe-coding --title "Add Ralph Workflow — AI coding orchestrator" --body "Ralph Workflow is a free open-source composable loop framework and AI orchestrator. Enables vibe-style unattended coding with structure. Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow" 2>/dev/null && echo "  PR created!" || echo "  PR may already exist or auth issue"
else
  echo "  Ralph Workflow already in README, skipping."
fi

echo ""
echo "=== Done ==="
echo "Check PR status: gh pr list --author @me"
