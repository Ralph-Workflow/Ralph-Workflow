#!/usr/bin/env python3
"""U4 mechanical rename script.

Renames commit_cleanup -> commit_review across the repository.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path("/opt/data/RalphProjects/Ralph-Workflow/wt-03-commit-review-flow")

GIT_MOVES = [
    # git
    ("ralph-workflow/ralph/git/commit_cleanup.py", "ralph-workflow/ralph/git/commit_review.py"),
    # mcp artifacts
    ("ralph-workflow/ralph/mcp/artifacts/_commit_cleanup.py", "ralph-workflow/ralph/mcp/artifacts/_commit_review.py"),
    ("ralph-workflow/ralph/mcp/artifacts/_commit_cleanup_action.py", "ralph-workflow/ralph/mcp/artifacts/_commit_review_action.py"),
    ("ralph-workflow/ralph/mcp/artifacts/format_docs/commit_cleanup.md", "ralph-workflow/ralph/mcp/artifacts/format_docs/commit_review.md"),
    ("ralph-workflow/ralph/mcp/artifacts/format_docs/examples/commit_cleanup.md", "ralph-workflow/ralph/mcp/artifacts/format_docs/examples/commit_review.md"),
    ("ralph-workflow/ralph/mcp/artifacts/markdown/specs/commit_cleanup.py", "ralph-workflow/ralph/mcp/artifacts/markdown/specs/commit_review.py"),
    # phases
    ("ralph-workflow/ralph/phases/_commit_cleanup_actions.py", "ralph-workflow/ralph/phases/_commit_review_actions.py"),
    ("ralph-workflow/ralph/phases/_commit_cleanup_catalog.py", "ralph-workflow/ralph/phases/_commit_review_catalog.py"),
    ("ralph-workflow/ralph/phases/_commit_cleanup_outcome.py", "ralph-workflow/ralph/phases/_commit_review_outcome.py"),
    ("ralph-workflow/ralph/phases/commit_cleanup.py", "ralph-workflow/ralph/phases/commit_review.py"),
    # prompts
    ("ralph-workflow/ralph/prompts/commit_cleanup.py", "ralph-workflow/ralph/prompts/commit_review.py"),
    ("ralph-workflow/ralph/prompts/templates/commit_cleanup.jinja", "ralph-workflow/ralph/prompts/templates/commit_review.jinja"),
    # skills content
    ("ralph-workflow/ralph/skills/content/submit-commit-cleanup-artifact.md", "ralph-workflow/ralph/skills/content/submit-commit-review-artifact.md"),
    # tests
    ("ralph-workflow/tests/integration/_commit_cleanup_always_loopback_invoker.py", "ralph-workflow/tests/integration/_commit_review_always_loopback_invoker.py"),
    ("ralph-workflow/tests/integration/test_commit_cleanup_rock_solid.py", "ralph-workflow/tests/integration/test_commit_review_rock_solid.py"),
    ("ralph-workflow/tests/integration/test_pipeline_commit_cleanup_to_commit_e2e.py", "ralph-workflow/tests/integration/test_pipeline_commit_review_to_commit_e2e.py"),
    ("ralph-workflow/tests/integration/test_pipeline_final_commit_cleanup_to_final_commit_e2e.py", "ralph-workflow/tests/integration/test_pipeline_final_commit_review_to_final_commit_e2e.py"),
    ("ralph-workflow/tests/mcp/test_md_commit_cleanup_spec.py", "ralph-workflow/tests/mcp/test_md_commit_review_spec.py"),
    ("ralph-workflow/tests/test_commit_cleanup_artifact.py", "ralph-workflow/tests/test_commit_review_artifact.py"),
    ("ralph-workflow/tests/test_commit_cleanup_diff.py", "ralph-workflow/tests/test_commit_review_diff.py"),
    ("ralph-workflow/tests/test_commit_cleanup_invariants.py", "ralph-workflow/tests/test_commit_review_invariants.py"),
    ("ralph-workflow/tests/test_commit_cleanup_verify_gate.py", "ralph-workflow/tests/test_commit_review_verify_gate.py"),
    ("ralph-workflow/tests/test_git_commit_cleanup.py", "ralph-workflow/tests/test_git_commit_review.py"),
    ("ralph-workflow/tests/test_phases_commit_cleanup.py", "ralph-workflow/tests/test_phases_commit_review.py"),
    # opencode skill dirs
    (".opencode/skills/submit-commit-cleanup-artifact", ".opencode/skills/submit-commit-review-artifact"),
    ("ralph-workflow/.opencode/skills/submit-commit-cleanup-artifact", "ralph-workflow/.opencode/skills/submit-commit-review-artifact"),
]

SYMLINKS = [
    (".agents/skills/submit-commit-cleanup-artifact", ".agents/skills/submit-commit-review-artifact", "../../.opencode/skills/submit-commit-review-artifact"),
    (".claude/skills/submit-commit-cleanup-artifact", ".claude/skills/submit-commit-review-artifact", "../../.opencode/skills/submit-commit-review-artifact"),
    (".codex/skills/submit-commit-cleanup-artifact", ".codex/skills/submit-commit-review-artifact", "../../.opencode/skills/submit-commit-review-artifact"),
    (".gemini/antigravity-cli/skills/submit-commit-cleanup-artifact", ".gemini/antigravity-cli/skills/submit-commit-review-artifact", "../../../.opencode/skills/submit-commit-review-artifact"),
    ("ralph-workflow/.agents/skills/submit-commit-cleanup-artifact", "ralph-workflow/.agents/skills/submit-commit-review-artifact", "../../.opencode/skills/submit-commit-review-artifact"),
    ("ralph-workflow/.claude/skills/submit-commit-cleanup-artifact", "ralph-workflow/.claude/skills/submit-commit-review-artifact", "../../.opencode/skills/submit-commit-review-artifact"),
    ("ralph-workflow/.codex/skills/submit-commit-cleanup-artifact", "ralph-workflow/.codex/skills/submit-commit-review-artifact", "../../.opencode/skills/submit-commit-review-artifact"),
    ("ralph-workflow/.gemini/antigravity-cli/skills/submit-commit-cleanup-artifact", "ralph-workflow/.gemini/antigravity-cli/skills/submit-commit-review-artifact", "../../../.opencode/skills/submit-commit-review-artifact"),
]

REPLACEMENTS = [
    ("development_final_commit_cleanup", "development_final_commit_review"),
    ("development_commit_cleanup", "development_commit_review"),
    ("commit_cleanup_iteration", "commit_review_iteration"),
    ("commit_cleanup_identity", "commit_review_identity"),
    ("submit-commit-cleanup-artifact", "submit-commit-review-artifact"),
    ("commit_cleanup.jinja", "commit_review.jinja"),
    ("COMMIT_CLEANUP", "COMMIT_REVIEW"),
    ("CommitCleanup", "CommitReview"),
    ("commit_cleanup", "commit_review"),
    ("commit-cleanup", "commit-review"),
    ("Commit cleanup", "Commit review"),
    ("Commit Cleanup", "Commit Review"),
    ("commit cleanup", "commit review"),
]

EXCLUDED_PATHS = {
    ".git",
    ".agent",
    ".pi-subagents",
    "docs/superpowers/plans",
    "ralph-workflow/docs/plans",
    "tests/agents/invoke/fixtures",
    "ralph-workflow/tests/agents/invoke/fixtures",
    "tests/pipeline/test_commit_timer_characterisation.py",
    "ralph-workflow/tests/pipeline/test_commit_timer_characterisation.py",
    "ralph/git/proposal_seal.py",
    "ralph-workflow/ralph/git/proposal_seal.py",
    "tests/test_git_proposal_seal.py",
    "ralph-workflow/tests/test_git_proposal_seal.py",
    "ralph/phases/_commit_review_tripwires.py",
    "ralph-workflow/ralph/phases/_commit_review_tripwires.py",
    "tests/test_commit_review_tripwires.py",
    "ralph-workflow/tests/test_commit_review_tripwires.py",
    "scripts/run_u4_rename.py",
}

def is_excluded(rel_path_str: str) -> bool:
    for exc in EXCLUDED_PATHS:
        if rel_path_str == exc or rel_path_str.startswith(exc + "/"):
            return True
    return False

def main() -> None:
    os.chdir(REPO_ROOT)
    print("Step 1: Running git mv for files and directories...")
    for src, dst in GIT_MOVES:
        if os.path.exists(src):
            subprocess.run(["git", "mv", src, dst], check=True)
            print(f"  git mv {src} -> {dst}")
        else:
            print(f"  SKIP (not found): {src}")

    print("Step 2: Updating symlinks...")
    for old_link, new_link, target in SYMLINKS:
        if os.path.islink(old_link) or os.path.exists(old_link):
            subprocess.run(["git", "rm", "-f", old_link], check=False)
            print(f"  git rm -f {old_link}")
        new_link_path = Path(new_link)
        if new_link_path.is_symlink() or new_link_path.exists():
            new_link_path.unlink()
        new_link_path.symlink_to(target)
        subprocess.run(["git", "add", "-f", new_link], check=True)
        print(f"  created symlink {new_link} -> {target}")

    print("Step 3: Performing text replacements...")
    # Get all git tracked files
    res = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    all_files = [f.strip() for f in res.stdout.splitlines() if f.strip()]
    
    modified_count = 0
    for rel_path in all_files:
        if is_excluded(rel_path):
            continue
        full_path = REPO_ROOT / rel_path
        if not full_path.is_file() or full_path.is_symlink():
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
            
        new_content = content
        for old_str, new_str in REPLACEMENTS:
            if old_str in new_content:
                new_content = new_content.replace(old_str, new_str)
                
        if new_content != content:
            full_path.write_text(new_content, encoding="utf-8")
            modified_count += 1
            print(f"  Modified: {rel_path}")

    print(f"Modified {modified_count} files.")

    print("Step 4: Regenerating policy_explain_default.txt...")
    explain_cmd = """
    from ralph.policy.loader import load_policy
    from ralph.policy.explain import explain_policy
    from ralph.policy.render import render_explanation_ascii
    from pathlib import Path
    b = load_policy(Path('ralph/policy/defaults'))
    print(render_explanation_ascii(explain_policy(b)))
    """
    res = subprocess.run(
        ["uv", "run", "--directory", "ralph-workflow", "python", "-c", explain_cmd],
        capture_output=True, text=True, check=True
    )
    fixture_path = REPO_ROOT / "ralph-workflow" / "tests" / "fixtures" / "policy_explain_default.txt"
    fixture_path.write_text(res.stdout, encoding="utf-8")
    print("  Regenerated policy_explain_default.txt")

    print("Step 5: Verifying skill mirror freshness...")
    subprocess.run(
        ["uv", "run", "python", "-m", "ralph.testing.audit_skill_mirror_freshness"],
        cwd=REPO_ROOT / "ralph-workflow", check=True
    )
    print("U4 Rename script completed successfully!")

if __name__ == "__main__":
    main()
