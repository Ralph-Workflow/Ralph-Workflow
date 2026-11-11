
from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.git.subprocess_runner import run_git

__all__ = ["is_ort_residue_name", "label_names_a_git_ref", "remove_ort_residue"]


def is_ort_residue_name(conflicted_name: str, candidate_name: str) -> bool:
    label = candidate_name[len(conflicted_name) + 1 :]
    if not label or label.endswith("~"):
        return False
    return bool(label.strip("0123456789"))


def label_names_a_git_ref(root: Path, label: str) -> bool:
    candidate = label.split(" ", maxsplit=1)[0]
    if not candidate:
        return False
    resolved = run_git(
        ("rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"),
        cwd=root,
        label="git-ort-residue-label",
    )
    return resolved.returncode == 0


def remove_ort_residue(root: Path, paths: tuple[str, ...]) -> bool:
    for path in paths:
        candidate_parent = (root / path).parent
        try:
            candidates = tuple(
                candidate
                for candidate in candidate_parent.glob(f"{Path(path).name}~*")
                if is_ort_residue_name(Path(path).name, candidate.name)
                and label_names_a_git_ref(root, candidate.name[len(Path(path).name) + 1 :])
            )
        except OSError:
            return False
        for candidate in candidates:
            relative = candidate.relative_to(root).as_posix()
            tracked = run_git(
                ("ls-files", "--error-unmatch", "--", relative),
                cwd=root,
                label="git-ort-residue-tracked",
            )
            if tracked.returncode == 0:
                continue
            try:
                if candidate.is_dir():
                    logger.info(
                        "conflict_resolution: leaving directory '{}' in place",
                        candidate.name,
                    )
                    continue
                candidate.unlink()
            except OSError:
                return False
    return True
