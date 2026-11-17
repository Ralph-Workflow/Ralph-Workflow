from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.tools.bridge._types import JsonObject, ToolHandler


def _load_ralph_mcp_explore_handlers_handle_ralph_graph() -> ToolHandler:
    from ralph.mcp.explore.handlers import handle_ralph_graph as _handler
    return _handler

def _load_ralph_mcp_explore_handlers_handle_ralph_index_status() -> ToolHandler:
    from ralph.mcp.explore.handlers import handle_ralph_index_status as _handler
    return _handler

def _load_ralph_mcp_explore_handlers_handle_ralph_reindex() -> ToolHandler:
    from ralph.mcp.explore.handlers import handle_ralph_reindex as _handler
    return _handler

def _load_ralph_mcp_tools__exec_job_tools_handle_exec_cancel() -> ToolHandler:
    from ralph.mcp.tools._exec_job_tools import handle_exec_cancel as _handler
    return _handler

def _load_ralph_mcp_tools__exec_job_tools_handle_exec_wait() -> ToolHandler:
    from ralph.mcp.tools._exec_job_tools import handle_exec_wait as _handler
    return _handler

def _load_ralph_mcp_tools_coordination_handle_coordinate() -> ToolHandler:
    from ralph.mcp.tools.coordination import handle_coordinate as _handler
    return _handler

def _load_ralph_mcp_tools_coordination_handle_declare_complete() -> ToolHandler:
    from ralph.mcp.tools.coordination import handle_declare_complete as _handler
    return _handler

def _load_ralph_mcp_tools_coordination_handle_read_env() -> ToolHandler:
    from ralph.mcp.tools.coordination import handle_read_env as _handler
    return _handler

def _load_ralph_mcp_tools_coordination_handle_report_progress() -> ToolHandler:
    from ralph.mcp.tools.coordination import handle_report_progress as _handler
    return _handler

def _load_ralph_mcp_tools_exec_handle_exec_command() -> ToolHandler:
    from ralph.mcp.tools.exec import handle_exec_command as _handler
    return _handler

def _load_ralph_mcp_tools_git_read_handle_git_diff() -> ToolHandler:
    from ralph.mcp.tools.git_read import handle_git_diff as _handler
    return _handler

def _load_ralph_mcp_tools_git_read_handle_git_log() -> ToolHandler:
    from ralph.mcp.tools.git_read import handle_git_log as _handler
    return _handler

def _load_ralph_mcp_tools_git_read_handle_git_show() -> ToolHandler:
    from ralph.mcp.tools.git_read import handle_git_show as _handler
    return _handler

def _load_ralph_mcp_tools_git_read_handle_git_status() -> ToolHandler:
    from ralph.mcp.tools.git_read import handle_git_status as _handler
    return _handler

def _load_ralph_mcp_tools_md_artifact_handle_discard_md_draft() -> ToolHandler:
    from ralph.mcp.tools.md_artifact import handle_discard_md_draft as _handler
    return _handler

def _load_ralph_mcp_tools_md_artifact_handle_edit_md_artifact() -> ToolHandler:
    from ralph.mcp.tools.md_artifact import handle_edit_md_artifact as _handler
    return _handler

def _load_ralph_mcp_tools_md_artifact_handle_finalize_md_artifact() -> ToolHandler:
    from ralph.mcp.tools.md_artifact import handle_finalize_md_artifact as _handler
    return _handler

def _load_ralph_mcp_tools_md_artifact_handle_get_md_draft() -> ToolHandler:
    from ralph.mcp.tools.md_artifact import handle_get_md_draft as _handler
    return _handler

def _load_ralph_mcp_tools_md_artifact_handle_stage_md_artifact() -> ToolHandler:
    from ralph.mcp.tools.md_artifact import handle_stage_md_artifact as _handler
    return _handler

def _load_ralph_mcp_tools_md_artifact_handle_submit_md_artifact() -> ToolHandler:
    from ralph.mcp.tools.md_artifact import handle_submit_md_artifact as _handler
    return _handler

def _load_ralph_mcp_tools_md_artifact_handle_verify_md_artifact() -> ToolHandler:
    from ralph.mcp.tools.md_artifact import handle_verify_md_artifact as _handler
    return _handler

def _load_ralph_mcp_tools_unsafe_exec_handle_unsafe_exec() -> ToolHandler:
    from ralph.mcp.tools.unsafe_exec import handle_unsafe_exec as _handler
    return _handler

def _load_ralph_mcp_tools_websearch_handle_web_search() -> ToolHandler:
    from ralph.mcp.tools.websearch import handle_web_search as _handler
    return _handler

def _load_ralph_mcp_tools_webvisit_handle_download_url() -> ToolHandler:
    from ralph.mcp.tools.webvisit import handle_download_url as _handler
    return _handler

def _load_ralph_mcp_tools_webvisit_handle_visit_url() -> ToolHandler:
    from ralph.mcp.tools.webvisit import handle_visit_url as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_append_file() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_append_file as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_copy_file() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_copy_file as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_create_directory() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_create_directory as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_delete_path() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_delete_path as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_directory_tree() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_directory_tree as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_edit_file() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_edit_file as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_grep_files() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_grep_files as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_list_allowed_roots() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_list_allowed_roots as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_list_directory() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_list_directory as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_list_directory_recursive() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_list_directory_recursive as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_media_capture_tool() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_media_capture_tool as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_move_file() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_move_file as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_read_file() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_read_file as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_read_image() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_read_image as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_read_media() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_read_media as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_read_multiple_files() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_read_multiple_files as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_search_files() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_search_files as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_stat() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_stat as _handler
    return _handler

def _load_ralph_mcp_tools_workspace_handle_write_file() -> ToolHandler:
    from ralph.mcp.tools.workspace import handle_write_file as _handler
    return _handler

_HANDLER_LOADERS: dict[tuple[str, str], Callable[[], ToolHandler]] = {
    ("ralph.mcp.explore.handlers", "handle_ralph_graph"): _load_ralph_mcp_explore_handlers_handle_ralph_graph,
    ("ralph.mcp.explore.handlers", "handle_ralph_index_status"): _load_ralph_mcp_explore_handlers_handle_ralph_index_status,
    ("ralph.mcp.explore.handlers", "handle_ralph_reindex"): _load_ralph_mcp_explore_handlers_handle_ralph_reindex,
    ("ralph.mcp.tools._exec_job_tools", "handle_exec_cancel"): _load_ralph_mcp_tools__exec_job_tools_handle_exec_cancel,
    ("ralph.mcp.tools._exec_job_tools", "handle_exec_wait"): _load_ralph_mcp_tools__exec_job_tools_handle_exec_wait,
    ("ralph.mcp.tools.coordination", "handle_coordinate"): _load_ralph_mcp_tools_coordination_handle_coordinate,
    ("ralph.mcp.tools.coordination", "handle_declare_complete"): _load_ralph_mcp_tools_coordination_handle_declare_complete,
    ("ralph.mcp.tools.coordination", "handle_read_env"): _load_ralph_mcp_tools_coordination_handle_read_env,
    ("ralph.mcp.tools.coordination", "handle_report_progress"): _load_ralph_mcp_tools_coordination_handle_report_progress,
    ("ralph.mcp.tools.exec", "handle_exec_command"): _load_ralph_mcp_tools_exec_handle_exec_command,
    ("ralph.mcp.tools.git_read", "handle_git_diff"): _load_ralph_mcp_tools_git_read_handle_git_diff,
    ("ralph.mcp.tools.git_read", "handle_git_log"): _load_ralph_mcp_tools_git_read_handle_git_log,
    ("ralph.mcp.tools.git_read", "handle_git_show"): _load_ralph_mcp_tools_git_read_handle_git_show,
    ("ralph.mcp.tools.git_read", "handle_git_status"): _load_ralph_mcp_tools_git_read_handle_git_status,
    ("ralph.mcp.tools.md_artifact", "handle_discard_md_draft"): _load_ralph_mcp_tools_md_artifact_handle_discard_md_draft,
    ("ralph.mcp.tools.md_artifact", "handle_edit_md_artifact"): _load_ralph_mcp_tools_md_artifact_handle_edit_md_artifact,
    ("ralph.mcp.tools.md_artifact", "handle_finalize_md_artifact"): _load_ralph_mcp_tools_md_artifact_handle_finalize_md_artifact,
    ("ralph.mcp.tools.md_artifact", "handle_get_md_draft"): _load_ralph_mcp_tools_md_artifact_handle_get_md_draft,
    ("ralph.mcp.tools.md_artifact", "handle_stage_md_artifact"): _load_ralph_mcp_tools_md_artifact_handle_stage_md_artifact,
    ("ralph.mcp.tools.md_artifact", "handle_submit_md_artifact"): _load_ralph_mcp_tools_md_artifact_handle_submit_md_artifact,
    ("ralph.mcp.tools.md_artifact", "handle_verify_md_artifact"): _load_ralph_mcp_tools_md_artifact_handle_verify_md_artifact,
    ("ralph.mcp.tools.unsafe_exec", "handle_unsafe_exec"): _load_ralph_mcp_tools_unsafe_exec_handle_unsafe_exec,
    ("ralph.mcp.tools.websearch", "handle_web_search"): _load_ralph_mcp_tools_websearch_handle_web_search,
    ("ralph.mcp.tools.webvisit", "handle_download_url"): _load_ralph_mcp_tools_webvisit_handle_download_url,
    ("ralph.mcp.tools.webvisit", "handle_visit_url"): _load_ralph_mcp_tools_webvisit_handle_visit_url,
    ("ralph.mcp.tools.workspace", "handle_append_file"): _load_ralph_mcp_tools_workspace_handle_append_file,
    ("ralph.mcp.tools.workspace", "handle_copy_file"): _load_ralph_mcp_tools_workspace_handle_copy_file,
    ("ralph.mcp.tools.workspace", "handle_create_directory"): _load_ralph_mcp_tools_workspace_handle_create_directory,
    ("ralph.mcp.tools.workspace", "handle_delete_path"): _load_ralph_mcp_tools_workspace_handle_delete_path,
    ("ralph.mcp.tools.workspace", "handle_directory_tree"): _load_ralph_mcp_tools_workspace_handle_directory_tree,
    ("ralph.mcp.tools.workspace", "handle_edit_file"): _load_ralph_mcp_tools_workspace_handle_edit_file,
    ("ralph.mcp.tools.workspace", "handle_grep_files"): _load_ralph_mcp_tools_workspace_handle_grep_files,
    ("ralph.mcp.tools.workspace", "handle_list_allowed_roots"): _load_ralph_mcp_tools_workspace_handle_list_allowed_roots,
    ("ralph.mcp.tools.workspace", "handle_list_directory"): _load_ralph_mcp_tools_workspace_handle_list_directory,
    ("ralph.mcp.tools.workspace", "handle_list_directory_recursive"): _load_ralph_mcp_tools_workspace_handle_list_directory_recursive,
    ("ralph.mcp.tools.workspace", "handle_media_capture_tool"): _load_ralph_mcp_tools_workspace_handle_media_capture_tool,
    ("ralph.mcp.tools.workspace", "handle_move_file"): _load_ralph_mcp_tools_workspace_handle_move_file,
    ("ralph.mcp.tools.workspace", "handle_read_file"): _load_ralph_mcp_tools_workspace_handle_read_file,
    ("ralph.mcp.tools.workspace", "handle_read_image"): _load_ralph_mcp_tools_workspace_handle_read_image,
    ("ralph.mcp.tools.workspace", "handle_read_media"): _load_ralph_mcp_tools_workspace_handle_read_media,
    ("ralph.mcp.tools.workspace", "handle_read_multiple_files"): _load_ralph_mcp_tools_workspace_handle_read_multiple_files,
    ("ralph.mcp.tools.workspace", "handle_search_files"): _load_ralph_mcp_tools_workspace_handle_search_files,
    ("ralph.mcp.tools.workspace", "handle_stat"): _load_ralph_mcp_tools_workspace_handle_stat,
    ("ralph.mcp.tools.workspace", "handle_write_file"): _load_ralph_mcp_tools_workspace_handle_write_file,
}


class LazyToolHandler:
    __slots__ = ("_extra_kwargs", "_loader", "_session", "_workspace")

    def __init__(
        self,
        *,
        module_name: str,
        handler_name: str,
        session: object,
        workspace: object,
        extra_kwargs: dict[str, object] | None = None,
    ) -> None:
        key = (module_name, handler_name)
        loader = _HANDLER_LOADERS.get(key)
        if loader is None:
            raise KeyError(f"no static loader for {module_name}.{handler_name}")
        self._loader = loader
        self._session = session
        self._workspace = workspace
        self._extra_kwargs: dict[str, object] = extra_kwargs if extra_kwargs is not None else {}

    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: JsonObject,
    ) -> object:
        del host_session, workspace
        handler = self._loader()
        return handler(self._session, self._workspace, params, **self._extra_kwargs)

