from __future__ import annotations

from ralph.project_policy import analysis, markers, preflight, reset
from ralph.project_policy.models import ReadinessStatus
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack


def _seeded() -> MemoryWorkspace:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    ws.mkdirs(".agent/tmp")
    ws.write(markers.CACHE_REL_PATH, "{}")
    ws.write(preflight.REMEDIATION_PROMPT_REL_PATH, "stale prompt")
    ws.write(analysis.ANALYSIS_PROMPT_REL_PATH, "stale analysis prompt")
    ws.mkdirs(".agent/artifacts")
    ws.write(analysis.ANALYSIS_ARTIFACT_REL_PATH, '{"stale": true}')
    return ws


def test_reset_deletes_the_canonical_policy_directory() -> None:
    ws = _seeded()

    reset.reset_policy_state(ws)

    for filename in markers.CORE_POLICY_FILES:
        assert not ws.exists(f"{markers.CANONICAL_DIR}{filename}"), filename


def test_reset_deletes_every_scratch_path() -> None:
    ws = _seeded()

    reset.reset_policy_state(ws)

    assert not ws.exists(markers.CACHE_REL_PATH)
    assert not ws.exists(preflight.REMEDIATION_PROMPT_REL_PATH)
    assert not ws.exists(analysis.ANALYSIS_PROMPT_REL_PATH)
    assert not ws.exists(analysis.ANALYSIS_ARTIFACT_REL_PATH)


def test_reset_strips_the_managed_block_but_keeps_the_users_own_content() -> None:
    ws = _seeded()
    ws.write(
        markers.AGENTS_MD,
        "# My project\n\nMy own house rules.\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN}\nRalph wrote this.\n{markers.AGENTS_BLOCK_END}\n"
        "\nMore of my own rules.\n",
    )

    reset.reset_policy_state(ws)

    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN not in content
    assert markers.AGENTS_BLOCK_END not in content
    assert "Ralph wrote this." not in content
    assert "My own house rules." in content, "the user's content is theirs"
    assert "More of my own rules." in content


def test_reset_strips_a_legacy_v1_managed_block() -> None:
    ws = _seeded()
    ws.write(
        markers.AGENTS_MD,
        "# My project\n\nMy own house rules.\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN_V1}\nLegacy Ralph wrote this.\n{markers.AGENTS_BLOCK_END}\n"
        "\nMore of my own rules.\n",
    )

    reset.reset_policy_state(ws)

    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN_V1 not in content
    assert markers.AGENTS_BLOCK_END not in content
    assert "Legacy Ralph wrote this." not in content
    assert "My own house rules." in content
    assert "More of my own rules." in content


def test_reset_strips_a_v2_managed_block() -> None:
    ws = _seeded()
    ws.write(
        markers.AGENTS_MD,
        "# My project\n\nMy own house rules.\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN}\nCurrent Ralph wrote this.\n{markers.AGENTS_BLOCK_END}\n"
        "\nMore of my own rules.\n",
    )

    reset.reset_policy_state(ws)

    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN not in content
    assert markers.AGENTS_BLOCK_END not in content
    assert "Current Ralph wrote this." not in content
    assert "My own house rules." in content
    assert "More of my own rules." in content


def test_reset_is_idempotent_for_a_stripped_agents_md() -> None:
    ws = _seeded()
    ws.write(
        markers.AGENTS_MD,
        "# My project\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN_V1}\nLegacy Ralph wrote this.\n{markers.AGENTS_BLOCK_END}\n"
        "\nMy own house rules.\n",
    )

    reset.reset_policy_state(ws)
    once = ws.read(markers.AGENTS_MD)
    reset.reset_policy_state(ws)
    twice = ws.read(markers.AGENTS_MD)

    assert once == twice


def test_reset_strips_the_opt_out_marker() -> None:
    ws = _seeded()
    ws.write(markers.AGENTS_MD, f"# Rules\n\n{markers.OPT_OUT_MARKER}\n")

    reset.reset_policy_state(ws)

    assert markers.OPT_OUT_MARKER not in ws.read(markers.AGENTS_MD)


def test_reset_strips_migrated_markers_from_migration_candidates() -> None:
    ws = _seeded()
    candidate = markers.MIGRATION_CANDIDATE_PATHS[0]
    marker = markers.MIGRATED_MARKER_TEMPLATE.format(target="testing-policy.md")
    ws.write(candidate, f"# Testing\n{marker}\nSome real content.\n")

    reset.reset_policy_state(ws)

    content = ws.read(candidate)
    assert markers.MIGRATED_MARKER_PREFIX not in content
    assert "Some real content." in content


def test_reset_is_idempotent() -> None:
    ws = _seeded()

    reset.reset_policy_state(ws)
    second = reset.reset_policy_state(ws)

    assert second == [], "a second reset on a clean workspace changes nothing"


def test_reset_is_not_a_no_op_the_way_a_cache_drop_would_be() -> None:
    ws = _seeded()

    ws.delete(markers.CACHE_REL_PATH)
    cache_only = preflight.run_policy_readiness_preflight(ws, stack())
    assert cache_only.status is ReadinessStatus.READY, (
        "a cache drop alone leaves the old policy intact -- this is the bug"
    )

    reset.reset_policy_state(ws)
    after = preflight.run_policy_readiness_preflight(ws, stack())

    assert after.status is ReadinessStatus.REMEDIATION_REQUIRED
    assert after.changed_files, "fresh starters must have been seeded"
