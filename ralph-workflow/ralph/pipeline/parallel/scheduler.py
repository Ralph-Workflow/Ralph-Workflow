"""Conflict-safe wave scheduler for parallel work-unit execution."""

from pathlib import PurePosixPath

from ralph.pipeline.work_units import WorkUnit


def _contains(directory: str, path: str) -> bool:
    directory_parts = PurePosixPath(directory).parts
    path_parts = PurePosixPath(path).parts
    return path_parts[: len(directory_parts)] == directory_parts


def _conflicts(left: WorkUnit, right: WorkUnit) -> bool:
    """Return whether two declared ownership scopes can touch the same path."""
    left_dirs, right_dirs = left.allowed_directories, right.allowed_directories
    left_paths, right_paths = left.paths, right.paths
    return (
        any(_contains(a, b) or _contains(b, a) for a in left_dirs for b in right_dirs)
        or any(_contains(directory, path) for directory in left_dirs for path in right_paths)
        or any(_contains(directory, path) for directory in right_dirs for path in left_paths)
        or bool(set(left_paths) & set(right_paths))
    )


def schedule_next_wave(
    completed: set[str],
    all_units: tuple[WorkUnit, ...],
    currently_running: set[str],
    max_workers: int,
) -> list[WorkUnit]:
    """Return ready work units that can be launched in the next wave."""
    available_slots = max_workers - len(currently_running)
    if available_slots <= 0:
        return []

    ready = [
        unit
        for unit in all_units
        if unit.unit_id not in completed
        and unit.unit_id not in currently_running
        and all(dep in completed for dep in unit.dependencies)
    ]
    ready.sort(key=lambda u: u.unit_id)
    running_units = [unit for unit in all_units if unit.unit_id in currently_running]
    selected: list[WorkUnit] = []
    for unit in ready:
        if len(selected) == available_slots:
            break
        if any(_conflicts(unit, active) for active in (*running_units, *selected)):
            continue
        selected.append(unit)
    return selected
