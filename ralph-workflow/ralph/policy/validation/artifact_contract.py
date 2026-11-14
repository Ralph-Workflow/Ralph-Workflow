from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.policy.validation._policy_validation_error import PolicyValidationError
from ralph.skills._content import get_skill_content

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.phases.required_artifacts import RequiredArtifact


class ArtifactContractMismatchError(PolicyValidationError):
    def __init__(
        self,
        phase: str,
        prompted_type: str | None,
        graded_type: str,
        message: str | None = None,
    ) -> None:
        self.phase = phase
        self.prompted_type = prompted_type
        self.graded_type = graded_type
        detail = message or (
            f"Phase '{phase}' artifact contract mismatch: prompted type is "
            f"'{prompted_type}' but graded type is '{graded_type}'"
        )
        super().__init__(detail)


_PROMPTED_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"render_artifact_submission\(\s*['\"]([a-z_]+)['\"]"),
    re.compile(r"## Submit a `([a-z_]+)` artifact"),
    re.compile(r"`artifact_type`\s*set\s*to\s*[\"']([a-z_]+)[\"']"),
    re.compile(r"artifact_type=[\"']([a-z_]+)[\"']"),
)

_SKILL_POINTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"render_optional_artifact_skill_pointer\(\s*['\"]([a-z0-9_-]+)['\"]"),
    re.compile(r"(?:Optional aid:\s*)?use `([a-z0-9_-]+)` when available"),
)

_SUPPORTED_TYPES_PATTERN = re.compile(r"Supported `artifact_type` values:\s*([^.]+)\.")
_CONCRETE_SKILL_PATTERN = re.compile(r"^submit-([a-z0-9_-]+)-artifact$")
_PAYLOAD_SECTION_PATTERN = re.compile(
    r"^(?P<label>[A-Z][A-Z ]*) BEGIN$.*^(?P=label) END$",
    re.MULTILINE | re.DOTALL,
)


def _without_payload_sections(prompt_text: str) -> str:
    return _PAYLOAD_SECTION_PATTERN.sub("", prompt_text)


def extract_prompted_artifact_types(prompt_text: str) -> set[str]:
    types: set[str] = set()
    for pattern in _PROMPTED_ARTIFACT_PATTERNS:
        for match in pattern.finditer(prompt_text):
            types.add(f"{match.group(1)}")
    return types


def extract_referenced_skill_pointers(prompt_text: str) -> set[str]:
    pointers: set[str] = set()
    for pattern in _SKILL_POINTER_PATTERNS:
        for match in pattern.finditer(prompt_text):
            val: str = f"{match.group(1)}"
            if val.startswith("submit-"):
                pointers.add(val)
    return pointers


def _umbrella_supported_types(content: str) -> set[str]:
    m = _SUPPORTED_TYPES_PATTERN.search(content)
    if not m:
        return set()
    supported: set[str] = set()
    for match in re.finditer(r"`([a-z_]+)`", m.group(1)):
        supported.add(f"{match.group(1)}")
    return supported


def _resolve_prompt_text(
    phase: str,
    target_type: str,
    prompt_text: str | None,
    prompt_file: str | Path | None,
    workspace_root: Path | None,
    file_backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    if prompt_text is not None:
        return prompt_text
    if prompt_file is None:
        msg = f"Phase '{phase}' requires artifact '{target_type}' but no prompt was provided"
        raise ArtifactContractMismatchError(
            phase=phase,
            prompted_type=None,
            graded_type=target_type,
            message=msg,
        )
    prompt_path = Path(prompt_file)
    if not prompt_path.is_absolute() and workspace_root is not None:
        prompt_path = workspace_root / prompt_path
    if not file_backend.exists(prompt_path):
        return None
    return file_backend.read_text(prompt_path, encoding="utf-8")


def _validate_prompted_types(
    phase: str,
    target_type: str,
    prompt_text: str,
    *,
    require_prompted_type: bool = True,
) -> None:
    prompted_types = extract_prompted_artifact_types(prompt_text)
    if not prompted_types:
        if require_prompted_type:
            msg = (
                f"Phase '{phase}' requires artifact '{target_type}' but rendered prompt does not name "
                "any submission artifact type"
            )
            raise ArtifactContractMismatchError(
                phase=phase,
                prompted_type=None,
                graded_type=target_type,
                message=msg,
            )
        return

    if len(prompted_types) == 1:
        prompted_type = next(iter(prompted_types))
        if prompted_type != target_type:
            msg = (
                f"Phase '{phase}' artifact contract mismatch: rendered prompt instructs submission "
                f"of '{prompted_type}' but graded type is '{target_type}'"
            )
            raise ArtifactContractMismatchError(
                phase=phase,
                prompted_type=prompted_type,
                graded_type=target_type,
                message=msg,
            )
        return

    prompted_type_summary = ", ".join(sorted(prompted_types))
    msg = (
        f"Phase '{phase}' artifact contract mismatch: rendered prompt names conflicting "
        f"artifact types [{prompted_type_summary}] for required artifact '{target_type}'"
    )
    raise ArtifactContractMismatchError(
        phase=phase,
        prompted_type=prompted_type_summary,
        graded_type=target_type,
        message=msg,
    )


def _validate_skill_pointer(
    phase: str,
    target_type: str,
    skill: str,
    get_skill: Callable[[str], str],
) -> None:
    if skill == "submit-artifact":
        try:
            umbrella_content = get_skill("submit-artifact")
        except Exception as exc:
            msg = f"Umbrella skill 'submit-artifact' referenced in phase '{phase}' could not be loaded: {exc}"
            raise ArtifactContractMismatchError(
                phase=phase,
                prompted_type=target_type,
                graded_type=target_type,
                message=msg,
            ) from exc
        supported = _umbrella_supported_types(umbrella_content)
        if target_type not in supported:
            msg = (
                f"Umbrella skill 'submit-artifact' does not support required artifact "
                f"type '{target_type}'"
            )
            raise ArtifactContractMismatchError(
                phase=phase,
                prompted_type=target_type,
                graded_type=target_type,
                message=msg,
            )
        return

    match = _CONCRETE_SKILL_PATTERN.match(skill)
    if not match:
        msg = f"Invalid artifact skill pointer '{skill}' referenced in phase '{phase}'"
        raise ArtifactContractMismatchError(
            phase=phase,
            prompted_type=target_type,
            graded_type=target_type,
            message=msg,
        )

    concrete_type = f"{match.group(1)}".replace("-", "_")
    if concrete_type != target_type:
        msg = (
            f"Referenced skill '{skill}' targets '{concrete_type}' but required "
            f"artifact is '{target_type}'"
        )
        raise ArtifactContractMismatchError(
            phase=phase,
            prompted_type=concrete_type,
            graded_type=target_type,
            message=msg,
        )

    try:
        skill_content = get_skill(skill)
    except Exception as exc:
        msg = f"Skill '{skill}' referenced in phase '{phase}' could not be loaded: {exc}"
        raise ArtifactContractMismatchError(
            phase=phase,
            prompted_type=target_type,
            graded_type=target_type,
            message=msg,
        ) from exc

    if target_type not in skill_content:
        msg = f"Skill '{skill}' does not name required artifact type '{target_type}' in its content"
        raise ArtifactContractMismatchError(
            phase=phase,
            prompted_type=target_type,
            graded_type=target_type,
            message=msg,
        )


def validate_artifact_contract(
    *,
    phase: str,
    prompt_text: str | None = None,
    prompt_file: str | Path | None = None,
    workspace_root: Path | None = None,
    required_artifact: RequiredArtifact | None = None,
    graded_type: str | None = None,
    skill_content_provider: Callable[[str], str] | None = None,
    require_prompted_type: bool = True,
    file_backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    target_type: str | None = None
    if required_artifact is not None:
        target_type = f"{required_artifact.artifact_type}"
    elif graded_type is not None:
        target_type = f"{graded_type}"

    if target_type is None:
        return

    resolved_text = _resolve_prompt_text(
        phase=phase,
        target_type=target_type,
        prompt_text=prompt_text,
        prompt_file=prompt_file,
        workspace_root=workspace_root,
        file_backend=file_backend,
    )
    if resolved_text is None:
        return
    instruction_text = _without_payload_sections(resolved_text)

    _validate_prompted_types(
        phase,
        target_type,
        instruction_text,
        require_prompted_type=require_prompted_type,
    )

    get_skill = skill_content_provider or get_skill_content
    for skill in extract_referenced_skill_pointers(instruction_text):
        _validate_skill_pointer(phase, target_type, skill, get_skill)
