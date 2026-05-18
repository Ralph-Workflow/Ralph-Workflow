"""Workspace tool handlers for MCP interactions.

Ports the Rust ``mcp_server::tool_workspace`` helpers into Python so MCP
handlers can read, list, search, and write workspace files while enforcing
session capabilities and edit area policies.
"""

from __future__ import annotations

from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
from ralph.mcp.tools.workspace._list_ops import list_dir_flat, match_glob
from ralph.mcp.tools.workspace._media_handlers import (
    handle_read_image,
    handle_read_media,
    persist_upstream_media_artifacts,
)
from ralph.mcp.tools.workspace._read_handlers import (
    handle_directory_tree,
    handle_list_allowed_roots,
    handle_list_directory,
    handle_list_directory_recursive,
    handle_read_file,
    handle_read_multiple_files,
    handle_search_files,
    handle_stat,
)
from ralph.mcp.tools.workspace._utils import (
    FULL_READ_DEFAULT_MAX_BYTES,
    MEDIA_READ_CAPABILITY,
    WORKSPACE_DELETE_CAPABILITY,
    WORKSPACE_EDIT_CAPABILITY,
    WORKSPACE_METADATA_READ_CAPABILITY,
    WORKSPACE_READ_CAPABILITY,
    WORKSPACE_WRITE_EPHEMERAL_CAPABILITY,
    WORKSPACE_WRITE_TRACKED_CAPABILITY,
    check_edit_area_restriction,
    infer_image_mime_type,
    is_parallel_worker,
    is_path_git_tracked,
    join_path,
    list_dir_entries,
    normalize_relative_path,
    required_string_param,
)
from ralph.mcp.tools.workspace._write_handlers import (
    handle_append_file,
    handle_copy_file,
    handle_create_directory,
    handle_delete_path,
    handle_edit_file,
    handle_move_file,
    handle_write_file,
)

__all__ = [
    "FULL_READ_DEFAULT_MAX_BYTES",
    "MEDIA_READ_CAPABILITY",
    "WORKSPACE_DELETE_CAPABILITY",
    "WORKSPACE_EDIT_CAPABILITY",
    "WORKSPACE_METADATA_READ_CAPABILITY",
    "WORKSPACE_READ_CAPABILITY",
    "WORKSPACE_WRITE_EPHEMERAL_CAPABILITY",
    "WORKSPACE_WRITE_TRACKED_CAPABILITY",
    "check_edit_area_restriction",
    "handle_append_file",
    "handle_copy_file",
    "handle_create_directory",
    "handle_delete_path",
    "handle_directory_tree",
    "handle_edit_file",
    "handle_grep_files",
    "handle_list_allowed_roots",
    "handle_list_directory",
    "handle_list_directory_recursive",
    "handle_move_file",
    "handle_read_file",
    "handle_read_image",
    "handle_read_media",
    "handle_read_multiple_files",
    "handle_search_files",
    "handle_stat",
    "handle_write_file",
    "infer_image_mime_type",
    "is_parallel_worker",
    "is_path_git_tracked",
    "is_policy_approved",
    "join_path",
    "list_dir_entries",
    "list_dir_flat",
    "match_glob",
    "normalize_relative_path",
    "persist_upstream_media_artifacts",
    "required_string_param",
]
