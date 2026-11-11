
from __future__ import annotations

import importlib.metadata
import importlib.util
from datetime import UTC, datetime
from importlib import import_module
from typing import TYPE_CHECKING

from ralph.config.mcp_loader import McpConfigError, load_mcp_config
from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._docs_mcp_probe import is_supported_docs_mcp_url, probe_docs_mcp
from ralph.skills._installer import (
    check_skills_update_available,
    install_baseline_skills,
)
from ralph.skills._recheck_policy import DEFAULT_POLICY, RecheckPolicy, needs_recheck
from ralph.skills._state_store import load_capability_state, save_capability_state
from ralph.workspace.scope import resolve_workspace_scope

_DDGS_AVAILABLE = importlib.util.find_spec("ralph.mcp.websearch.backends.ddgs") is not None

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.skills._capability_state import CapabilityState


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _get_ralph_version() -> str:
    try:
        return importlib.metadata.version("ralph-workflow")
    except Exception:
        return ""


def _find_configured_docs_mcp_url(workspace_root: Path | None = None) -> str | None:
    try:
        scope = (
            resolve_workspace_scope(workspace_root) if workspace_root else resolve_workspace_scope()
        )
        mcp_config = load_mcp_config(workspace_scope=scope)
    except (McpConfigError, Exception):
        return None
    for server in mcp_config.mcp_servers.values():
        if server.url and is_supported_docs_mcp_url(server.url):
            return server.url
    return None


def _web_search_is_available() -> bool:
    return _DDGS_AVAILABLE


def _visit_url_is_available() -> bool:
    try:
        import_module("ralph.mcp.webvisit.extractor")
    except ImportError:
        return False
    return True


class SkillManager:

    def __init__(
        self,
        state_path: Path | None = None,
        policy: RecheckPolicy = DEFAULT_POLICY,
    ) -> None:
        self._state_path = state_path
        self._policy = policy

    def _load_state(self) -> CapabilityState:
        return load_capability_state(self._state_path)

    def _save_state(self, state: CapabilityState) -> None:
        save_capability_state(state, self._state_path)

    def ensure_baseline_capabilities(
        self, *, workspace_root: Path
    ) -> tuple[CapabilityState, list[str]]:
        state = self._load_state()
        current_version = _get_ralph_version()

        skills_entry, failures = install_baseline_skills()
        if failures:
            skills_entry = CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=_now_iso(),
            )

        docs_mcp_url = _find_configured_docs_mcp_url(workspace_root)
        if docs_mcp_url:
            reachable = probe_docs_mcp(docs_mcp_url)
            docs_mcp_entry = CapabilityEntry(
                status=(
                    CapabilityStatus.INSTALLED_HEALTHY
                    if reachable
                    else CapabilityStatus.CONFIGURED_UNREACHABLE
                ),
                last_check_ok_iso=_now_iso() if reachable else "",
                last_check_fail_iso="" if reachable else _now_iso(),
            )
        else:
            docs_mcp_entry = CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED)

        web_search_ok = _web_search_is_available()
        web_search_entry = CapabilityEntry(
            status=(
                CapabilityStatus.INSTALLED_HEALTHY
                if web_search_ok
                else CapabilityStatus.INSTALLED_DEGRADED
            ),
            last_check_ok_iso=_now_iso() if web_search_ok else "",
            last_check_fail_iso="" if web_search_ok else _now_iso(),
            ralph_version=current_version,
        )
        visit_url_ok = _visit_url_is_available()
        visit_url_entry = CapabilityEntry(
            status=(
                CapabilityStatus.INSTALLED_HEALTHY
                if visit_url_ok
                else CapabilityStatus.NEEDS_REPAIR
            ),
            last_check_ok_iso=_now_iso() if visit_url_ok else "",
            last_check_fail_iso="" if visit_url_ok else _now_iso(),
            ralph_version=current_version,
        )

        updated = state.model_copy(
            update={
                "web_search": web_search_entry,
                "visit_url": visit_url_entry,
                "docs_mcp": docs_mcp_entry,
                "skills": skills_entry,
            }
        )
        self._save_state(updated)
        return updated, failures

    def check_baseline_health(self) -> dict[str, bool]:
        state = self._load_state()
        current_version = _get_ralph_version()
        updates: dict[str, object] = {}
        for field_name in ("web_search", "visit_url"):
            entry: CapabilityEntry = getattr(state, field_name)
            if (
                entry.status == CapabilityStatus.INSTALLED_HEALTHY
                and entry.ralph_version
                and entry.ralph_version != current_version
            ):
                updates[field_name] = entry.model_copy(
                    update={
                        "status": CapabilityStatus.INSTALLED_OUTDATED,
                        "update_available": True,
                    }
                )

        if updates:
            updated_state = state.model_copy(update=updates)
            self._save_state(updated_state)
            state_to_report = updated_state
        else:
            state_to_report = state

        healthy = CapabilityStatus.INSTALLED_HEALTHY
        return {
            "web_search": state_to_report.web_search.status == healthy,
            "visit_url": state_to_report.visit_url.status == healthy,
            "docs_mcp": state_to_report.docs_mcp.status == healthy,
            "skills": state_to_report.skills.status == healthy,
        }

    def reinstall_baseline_skills(self, workspace_root: Path) -> tuple[CapabilityState, list[str]]:
        try:
            from ralph.git.operations import (
                create_commit,
            )
            from ralph.git.scoped_auto_commit import ScopedCommitStatus
            from ralph.skills._auto_commit import commit_skill_writes
            from ralph.skills._installer import (
                install_project_baseline_skills_with_diff,
            )

            user_entry, user_failures = install_baseline_skills()
            project_outcome = install_project_baseline_skills_with_diff(workspace_root)
            project_entry = project_outcome.entry
            project_failures = project_outcome.failures
            if project_outcome.written_paths:
                try:
                    result = commit_skill_writes(
                        workspace_root,
                        written_paths=project_outcome.written_paths,
                        pre_contents=project_outcome.pre_contents,
                        create_commit_fn=create_commit,
                        intentional_transitions=project_outcome.intentional_transitions,
                    )
                    if result.status is ScopedCommitStatus.FAILED:
                        from loguru import logger as _logger

                        _logger.warning(
                            "Skill auto-commit during --force-init-skills failed (non-fatal): {}",
                            result.error,
                        )
                except Exception as exc:
                    from loguru import logger as _logger

                    _logger.warning(
                        "Skill auto-commit during --force-init-skills raised (non-fatal): {}",
                        exc,
                    )
        except Exception:
            return self._load_state(), ["reinstall-exception"]

        merged_failures: list[str] = list(user_failures) + list(project_failures)

        def _rank(status: CapabilityStatus) -> int:
            order: dict[CapabilityStatus, int] = {
                CapabilityStatus.NEEDS_REPAIR: 5,
                CapabilityStatus.INSTALLED_DEGRADED: 4,
                CapabilityStatus.INSTALLED_OUTDATED: 3,
                CapabilityStatus.CONFIGURED_UNREACHABLE: 2,
                CapabilityStatus.INSTALLED_HEALTHY: 1,
                CapabilityStatus.NOT_INSTALLED: 0,
            }
            return order.get(status, 0)

        worst = (
            user_entry.status
            if _rank(user_entry.status) >= _rank(project_entry.status)
            else project_entry.status
        )
        now_iso = _now_iso()
        if worst == CapabilityStatus.NEEDS_REPAIR:
            merged_entry = CapabilityEntry(
                status=CapabilityStatus.NEEDS_REPAIR,
                last_check_fail_iso=now_iso,
            )
        else:
            merged_entry = CapabilityEntry(
                status=CapabilityStatus.INSTALLED_HEALTHY,
                last_check_ok_iso=now_iso,
            )

        state = self._load_state()
        updated = state.model_copy(update={"skills": merged_entry})
        self._save_state(updated)
        return updated, merged_failures

    def check_skills_for_updates(self) -> bool:
        state = self._load_state()
        entry = state.skills
        if not needs_recheck(entry, self._policy):
            return entry.update_available

        update_available = check_skills_update_available()
        now_iso = _now_iso()
        if update_available:
            updated_entry = entry.model_copy(
                update={
                    "update_available": True,
                    "last_check_ok_iso": now_iso,
                }
            )
        else:
            updated_entry = entry.model_copy(
                update={
                    "status": CapabilityStatus.INSTALLED_HEALTHY,
                    "update_available": False,
                    "last_check_ok_iso": now_iso,
                    "last_check_fail_iso": "",
                }
            )
        updated_state = state.model_copy(update={"skills": updated_entry})
        self._save_state(updated_state)
        return update_available

    def get_docs_mcp_available(self, *, workspace_root: Path) -> bool:
        state = self._load_state()
        entry = state.docs_mcp
        if not needs_recheck(entry, self._policy):
            return entry.status == CapabilityStatus.INSTALLED_HEALTHY

        docs_mcp_url = _find_configured_docs_mcp_url(workspace_root)
        if not docs_mcp_url:
            updated_entry = CapabilityEntry(status=CapabilityStatus.NOT_INSTALLED)
            updated_state = state.model_copy(update={"docs_mcp": updated_entry})
            self._save_state(updated_state)
            return False

        reachable = probe_docs_mcp(docs_mcp_url)
        updated_entry = CapabilityEntry(
            status=(
                CapabilityStatus.INSTALLED_HEALTHY
                if reachable
                else CapabilityStatus.CONFIGURED_UNREACHABLE
            ),
            last_check_ok_iso=_now_iso() if reachable else "",
            last_check_fail_iso="" if reachable else _now_iso(),
        )
        updated_state = state.model_copy(update={"docs_mcp": updated_entry})
        self._save_state(updated_state)
        return reachable


__all__ = ["SkillManager"]
