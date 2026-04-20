#!/usr/bin/env python3
"""Script to delete flat shim files from ralph/mcp/."""

import sys
from pathlib import Path

# Files to delete
SHIM_FILES = [
    "ralph/mcp/transport.py",
    "ralph/mcp/startup.py",
    "ralph/mcp/session.py",
    "ralph/mcp/capability_mapping.py",
    "ralph/mcp/env.py",
    "ralph/mcp/bridge.py",
    "ralph/mcp/tool_bridge.py",
    "ralph/mcp/tool_names.py",
    "ralph/mcp/tool_workspace.py",
    "ralph/mcp/tool_git_read.py",
    "ralph/mcp/tool_exec.py",
    "ralph/mcp/tool_artifact.py",
    "ralph/mcp/tool_coordination.py",
    "ralph/mcp/tool_websearch.py",
    "ralph/mcp/upstream_client.py",
    "ralph/mcp/upstream_config.py",
    "ralph/mcp/upstream_models.py",
    "ralph/mcp/upstream_registry.py",
    "ralph/mcp/upstream_validation.py",
    "ralph/mcp/agent_transport_probe.py",
    "ralph/mcp/artifacts.py",
    "ralph/mcp/audit_adapter.py",
    "ralph/mcp/file_backend.py",
    "ralph/mcp/plan_artifact.py",
    "ralph/mcp/policy_outcomes.py",
    "ralph/mcp/commit_message.py",
    "ralph/mcp/development_result_artifact.py",
]

def main():
    base_path = Path("/Users/mistlight/Projects/RalphWithReviewer/wt-75-better-mcp/ralph-workflow")
    
    deleted = []
    not_found = []
    for rel_path in SHIM_FILES:
        filepath = base_path / rel_path
        if filepath.exists():
            filepath.unlink()
            deleted.append(rel_path)
            print(f"Deleted: {rel_path}")
        else:
            not_found.append(rel_path)
            print(f"Not found: {rel_path}")
    
    print(f"\nDeleted: {len(deleted)}")
    print(f"Not found: {len(not_found)}")
    
    if not_found:
        print("\nWarning: Some files were not found:")
        for f in not_found:
            print(f"  {f}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
