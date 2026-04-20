#!/usr/bin/env python3
"""Script to fix MCP flat shim imports to canonical paths.

This handles the case where ralf.mcp.artifacts is a PACKAGE, not a module.
The flat shim ralph.mcp.artifacts re-exported from ralph.mcp.artifacts.store.
So 'from ralph.mcp.artifacts import X' should become 'from ralph.mcp.artifacts.store import X'.
"""

import sys
from pathlib import Path

# Mapping of flat shim module -> canonical module
# Only for modules that were FLAT FILES, not packages
IMPORT_REPLACEMENTS = {
    # These were flat files in ralph/mcp/ that re-exported from submodules
    "ralph.mcp.upstream_validation": "ralph.mcp.upstream.validation",
    "ralph.mcp.agent_transport_probe": "ralph.mcp.upstream.agent_probe",
    "ralph.mcp.transport": "ralph.mcp.protocol.transport",
    "ralph.mcp.startup": "ralph.mcp.protocol.startup",
    "ralph.mcp.env": "ralph.mcp.protocol.env",
    "ralph.mcp.capability_mapping": "ralph.mcp.protocol.capability_mapping",
    "ralph.mcp.session": "ralph.mcp.protocol.session",
    "ralph.mcp.upstream_config": "ralph.mcp.upstream.config",
    "ralph.mcp.upstream_models": "ralph.mcp.upstream.models",
    "ralph.mcp.upstream_registry": "ralph.mcp.upstream.registry",
    "ralph.mcp.upstream_client": "ralph.mcp.upstream.client",
    "ralph.mcp.commit_message": "ralph.mcp.artifacts.commit_message",
    "ralph.mcp.plan_artifact": "ralph.mcp.artifacts.plan",
    "ralph.mcp.policy_outcomes": "ralph.mcp.artifacts.policy_outcomes",
    "ralph.mcp.development_result_artifact": "ralph.mcp.artifacts.development_result",
    "ralph.mcp.file_backend": "ralph.mcp.artifacts.file_backend",
    "ralph.mcp.audit_adapter": "ralph.mcp.artifacts.audit_adapter",
    "ralph.mcp.bridge": "ralph.mcp.artifacts.bridge",
    "ralph.mcp.tool_names": "ralph.mcp.tools.names",
    "ralph.mcp.tool_workspace": "ralph.mcp.tools.workspace",
    "ralph.mcp.tool_git_read": "ralph.mcp.tools.git_read",
    "ralph.mcp.tool_exec": "ralph.mcp.tools.exec",
    "ralph.mcp.tool_artifact": "ralph.mcp.tools.artifact",
    "ralph.mcp.tool_coordination": "ralph.mcp.tools.coordination",
    "ralph.mcp.tool_websearch": "ralph.mcp.tools.websearch",
    "ralph.mcp.tool_bridge": "ralph.mcp.tools.bridge",
}


def fix_content(content: str) -> tuple[str, int]:
    """Fix imports in content. Returns (fixed_content, count_of_replacements)."""
    original = content
    count = 0

    for old_imp, new_imp in IMPORT_REPLACEMENTS.items():
        # Handle 'from X import' patterns
        if f"from {old_imp} import" in content:
            content = content.replace(f"from {old_imp} import", f"from {new_imp} import")
            count += 1

        # Handle 'import X' patterns (not ideal but handle it)
        import_pattern = f"import {old_imp}"
        if import_pattern in content and f"from {old_imp}" not in content:
            # Only replace if it's the full module name followed by comma/as
            if f"import {old_imp}," in content:
                content = content.replace(f"import {old_imp},", f"import {new_imp},")
                count += 1
            elif f"import {old_imp} as" in content:
                content = content.replace(f"import {old_imp} as", f"import {new_imp} as")
                count += 1

        # Handle string references like "ralph.mcp.upstream.validation.something"
        # Only for module-level strings (after dot separator)
        if f'"{old_imp}.' in content:
            content = content.replace(f'"{old_imp}.', f'"{new_imp}.')
            count += 1

        if f"'{old_imp}." in content:
            content = content.replace(f"'{old_imp}.", f"'{new_imp}.")
            count += 1

    return content, count


def fix_file(filepath: Path) -> bool:
    """Fix imports in a single file. Returns True if modified."""
    content = filepath.read_text()
    original = content

    content, count = fix_content(content)

    if content != original:
        filepath.write_text(content)
        return True
    return False


def main():
    base_path = Path(__file__).parent

    modified = []

    # Process all Python files
    for py_file in base_path.rglob("*.py"):
        if fix_file(py_file):
            rel = py_file.relative_to(base_path)
            modified.append(str(rel))
            print(f"Fixed: {rel}")

    print(f"\nTotal files modified: {len(modified)}")
    if modified:
        print("Modified files:")
        for f in modified:
            print(f"  - {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
