# Parallel Worker Bootstrap Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Ralph fan-out workers execute as truly unit-specific parallel workers instead of concurrent copies of the shared top-level pipeline.

**Architecture:** Replace the current `python -m ralph` shared-pipeline worker bootstrap with a dedicated parallel-worker entry path that short-circuits before the normal config/preflight/run-loop pipeline bootstrap. Each worker will receive an explicit work-unit manifest, a worker-specific prompt, and worker-local runtime state paths for prompt dumps, current-prompt mirrors, system prompts, multimodal sidecars, and checkpoints, while keeping the existing same-workspace edit-area restrictions and MCP artifact isolation.

**Tech Stack:** Python 3.12, Typer CLI, asyncio TaskGroup, existing `SubprocessAgentExecutor`, MCP session plan/runtime, pytest, mypy, Ruff.

---

## Root cause this plan addresses

Today the fan-out layer launches real concurrent subprocesses, but those subprocesses are bootstrapped with the generic Ralph CLI command:

```python
def _parallel_worker_command() -> tuple[str, ...]:
    return (sys.executable, "-m", "ralph")
```

That means workers still enter the normal pipeline startup path and continue to use shared singleton files like:

- `.agent/checkpoint.json`
- `.agent/PRODUCT_CRITERIA.md`
- `.agent/tmp/<phase>_prompt.md`

The codebase already contains a unit-specific prompt renderer:

```python
def render_worker_prompt(unit: WorkUnit, base_prompt: str, policy: PipelinePolicy) -> str:
    ...
```

but it is only referenced in tests today, not in the production fan-out bootstrap path.

---

## File structure

- **Modify:** `ralph-workflow/ralph/pipeline/fan_out.py`
  - Change worker launch command construction.
  - Persist a worker manifest / worker prompt path for each unit.
- **Modify:** `ralph-workflow/ralph/pipeline/parallel/parallel_coordinator.py`
  - Pass worker-manifest / worker-state env into subprocess workers.
- **Modify:** `ralph-workflow/ralph/prompts/materialize.py`
  - Reuse `render_worker_prompt(...)` from production code.
  - Add explicit `WorkUnit` input support and worker-local prompt dump support.
- **Modify:** `ralph-workflow/ralph/prompts/debug_dump.py`
  - Add worker-aware prompt dump and multimodal sidecar path helpers.
- **Modify:** `ralph-workflow/ralph/prompts/master_prompt.py`
  - Stop worker mode from writing shared `.agent/PRODUCT_CRITERIA.md` and shared system-prompt files.
- **Modify:** `ralph-workflow/ralph/prompts/developer/__init__.py`
  - Stop worker mode from hardcoding shared `.agent/PRODUCT_CRITERIA.md` and shared `.agent/tmp/prompt_payloads`.
- **Modify:** `ralph-workflow/ralph/pipeline/checkpoint.py`
  - Support worker-local checkpoint paths.
- **Create:** `ralph-workflow/ralph/pipeline/parallel/worker_manifest.py`
  - Typed manifest for a single worker run.
- **Create:** `ralph-workflow/ralph/pipeline/parallel/worker_runtime.py`
  - Dedicated worker execution path that bypasses the outer shared pipeline loop.
- **Modify:** `ralph-workflow/ralph/cli/main.py`
  - Register hidden/internal worker entry option.
- **Modify:** `ralph-workflow/ralph/cli/commands/run.py`
  - Dispatch into worker runtime when worker mode is requested.
  - Short-circuit before the normal shared config/preflight/pipeline path.
- **Test:** `ralph-workflow/tests/test_prompt_materialize_worker.py`
  - Extend prompt-specific production wiring coverage.
- **Create:** `ralph-workflow/tests/test_parallel_worker_runtime.py`
  - Unit tests for manifest loading, worker-local state paths, and single-unit invocation.
- **Create:** `ralph-workflow/tests/integration/test_parallel_worker_bootstrap.py`
  - Integration test proving concurrent workers no longer share singleton runtime files.
- **Modify:** `ralph-workflow/tests/integration/test_runner_fanout_wiring.py`
  - Preserve fan-out runner invariants while changing worker bootstrap.
- **Modify:** `ralph-workflow/tests/integration/test_parallel_resume.py`
  - Preserve resume/requeue behavior with worker-local runtime state.
- **Modify:** `ralph-workflow/tests/test_process_exit_code_not_trusted.py`
  - Preserve artifact-evidence success/failure invariants.
- **Modify:** `ralph-workflow/tests/integration/test_parallel_multimodal_runtime_e2e.py`
  - Preserve multimodal worker isolation after namespacing sidecars and payload files.

---

### Task 1: Lock in the failing worker-bootstrap behavior

**Files:**
- Modify: `ralph-workflow/tests/test_prompt_materialize_worker.py`
- Create: `ralph-workflow/tests/test_parallel_worker_runtime.py`
- Create: `ralph-workflow/tests/integration/test_parallel_worker_bootstrap.py`
- Modify: `ralph-workflow/tests/integration/test_parallel_multimodal_runtime_e2e.py`

- [ ] **Step 1: Add a failing unit test proving production worker bootstrap does not currently use unit-specific prompt content**

```python
def test_worker_runtime_uses_unit_specific_prompt_payload(tmp_path: Path) -> None:
    unit = WorkUnit(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
    )

    rendered = render_worker_prompt(
        unit=unit,
        base_prompt="Base development prompt",
        policy=load_policy(tmp_path / ".agent").pipeline,
    )

    assert "Implement only unit A" in rendered
    assert json.dumps(["src/a"], indent=2) in rendered
```

- [ ] **Step 2: Add a failing runtime test proving worker-local files must not use shared singleton `.agent` paths**

```python
def test_worker_runtime_paths_are_namespaced(tmp_path: Path) -> None:
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"

    runtime = build_worker_runtime_paths(
        workspace_root=tmp_path,
        worker_namespace=worker_ns,
        phase="development",
    )

    assert runtime.checkpoint_path == worker_ns / "tmp" / "checkpoint.json"
    assert runtime.product_criteria_path == worker_ns / "tmp" / "PRODUCT_CRITERIA.md"
    assert runtime.prompt_dump_path == worker_ns / "tmp" / "development_prompt.md"
    assert runtime.master_prompt_path == worker_ns / "tmp" / "development_master_prompt.md"
    assert runtime.multimodal_sidecar_path == worker_ns / "tmp" / "development_multimodal_handoff.json"
```

- [ ] **Step 3: Add a failing integration test proving two workers currently collide on shared pipeline files unless isolated**

```python
def test_parallel_workers_do_not_share_global_prompt_or_checkpoint_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unit_a = WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"])
    unit_b = WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"])

    written_paths: list[Path] = []

    def _record_write(path: Path, content: str) -> None:
        written_paths.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr("ralph.prompts.debug_dump.dump_rendered_prompt", _record_prompt_dump)
    monkeypatch.setattr("ralph.pipeline.checkpoint.save", _record_checkpoint_save)

    # Execute two workers through the parallel bootstrap path.
    ...

    assert tmp_path / ".agent" / "checkpoint.json" not in written_paths
    assert tmp_path / ".agent" / "tmp" / "development_prompt.md" not in written_paths
```

- [ ] **Step 4: Add a failing integration test proving worker mode must bypass the normal shared pipeline bootstrap**

```python
def test_parallel_worker_mode_does_not_call_shared_pipeline_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: list[str] = []

    monkeypatch.setattr(
        "ralph.cli.commands.run._load_configuration",
        lambda *args, **kwargs: called.append("load_configuration"),
    )
    monkeypatch.setattr(
        "ralph.cli.commands.run._run_preflight_checks",
        lambda *args, **kwargs: called.append("preflight"),
    )

    ...

    assert called == []
```

- [ ] **Step 5: Run the focused tests and confirm they fail before implementation**

Run:

```bash
cd ralph-workflow
uv run pytest -q tests/test_prompt_materialize_worker.py tests/test_parallel_worker_runtime.py tests/integration/test_parallel_worker_bootstrap.py
```

Expected: FAIL with missing worker-runtime path helpers and/or production bootstrap still writing shared `.agent` files.

---

### Task 2: Introduce a dedicated parallel worker runtime instead of generic `python -m ralph`

**Files:**
- Create: `ralph-workflow/ralph/pipeline/parallel/worker_manifest.py`
- Create: `ralph-workflow/ralph/pipeline/parallel/worker_runtime.py`
- Modify: `ralph-workflow/ralph/cli/main.py`
- Modify: `ralph-workflow/ralph/cli/commands/run.py`
- Modify: `ralph-workflow/ralph/pipeline/fan_out.py`
- Modify: `ralph-workflow/ralph/cli/commands/_execute_pipeline_request.py`
- Modify: `ralph-workflow/ralph/cli/commands/_preflight_request.py`

- [ ] **Step 1: Add a typed worker manifest model**

```python
class ParallelWorkerManifest(RalphBaseModel):
    unit_id: str
    description: str
    allowed_directories: list[str]
    phase: str
    drain: str
    config_path: str | None = None
    cli_overrides: dict[str, object] = {}
    worker_namespace: str
    worker_artifact_dir: str
    prompt_file: str
    workspace_root: str
```

- [ ] **Step 2: Add a hidden CLI worker mode that runs one explicit manifest instead of the outer pipeline loop**

```python
def main(
    ctx: typer.Context,
    ...,
    parallel_worker_manifest: Annotated[
        str | None,
        typer.Option("--parallel-worker-manifest", hidden=True),
    ] = None,
) -> None:
    ...
```

- [ ] **Step 3: Thread the worker-manifest option through request types instead of hiding it in ad-hoc kwargs**

```python
class RunPipelineRequest(NamedTuple):
    ...
    parallel_worker_manifest: Path | None = None
```

- [ ] **Step 4: Add worker-runtime dispatch near the top of `run_pipeline(...)`, before normal load/preflight logic**

```python
if effective_request.parallel_worker_manifest is not None:
    return run_parallel_worker_from_manifest(
        manifest_path=effective_request.parallel_worker_manifest,
        display_context=ctx,
    )
```

- [ ] **Step 5: Replace the generic worker command in fan-out with a dedicated worker command**

```python
def _parallel_worker_command(manifest_path: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        "ralph",
        "--parallel-worker-manifest",
        str(manifest_path),
    )
```

- [ ] **Step 6: Persist one worker manifest per unit before subprocess launch, keeping phase and drain separate**

```python
manifest = ParallelWorkerManifest(
    unit_id=unit.unit_id,
    description=unit.description,
    allowed_directories=unit.allowed_directories,
    phase=effect.phase,
    drain=same_workspace.session_drain,
    config_path=str(ctx.config_path) if ctx.config_path is not None else None,
    cli_overrides=serialized_cli_overrides,
    worker_namespace=str(worker_namespace),
    worker_artifact_dir=str(worker_artifact_dir),
    prompt_file=str(worker_prompt_path),
    workspace_root=str(same_workspace.repo_root),
)
manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
```

- [ ] **Step 7: Run the new unit tests for worker runtime construction**

Run:

```bash
cd ralph-workflow
uv run pytest -q tests/test_parallel_worker_runtime.py -k "manifest or runtime"
```

Expected: PASS.

---

### Task 3: Make worker prompts and runtime state truly unit-specific

**Files:**
- Modify: `ralph-workflow/ralph/prompts/materialize.py`
- Modify: `ralph-workflow/ralph/prompts/debug_dump.py`
- Modify: `ralph-workflow/ralph/prompts/master_prompt.py`
- Modify: `ralph-workflow/ralph/prompts/developer/__init__.py`
- Modify: `ralph-workflow/ralph/pipeline/checkpoint.py`
- Modify: `ralph-workflow/ralph/pipeline/parallel/parallel_coordinator.py`
- Modify: `ralph-workflow/ralph/pipeline/prompt_prep.py`
- Modify: `ralph-workflow/ralph/pipeline/parallel/worker_runtime.py`

- [ ] **Step 1: Add worker-aware prompt dump helpers**

```python
def worker_prompt_dump_path(worker_namespace: Path, phase: str) -> Path:
    normalized = phase.replace("/", "_").replace(" ", "_")
    return worker_namespace / "tmp" / f"{normalized}_prompt.md"


def worker_multimodal_sidecar_path(worker_namespace: Path, phase: str) -> Path:
    normalized = phase.replace("/", "_").replace(" ", "_")
    return worker_namespace / "tmp" / f"{normalized}_multimodal_handoff.json"
```

- [ ] **Step 2: Add worker-aware current-prompt and checkpoint path helpers**

```python
def worker_product_criteria_path(worker_namespace: Path) -> Path:
    return worker_namespace / "tmp" / "PRODUCT_CRITERIA.md"


def worker_checkpoint_path(worker_namespace: Path) -> Path:
    return worker_namespace / "tmp" / "checkpoint.json"


def worker_master_prompt_path(worker_namespace: Path, phase: str) -> Path:
    return worker_namespace / "tmp" / f"{phase}_master_prompt.md"
```

- [ ] **Step 3: Thread explicit `WorkUnit` context into production prompt materialization**

```python
@dataclass(frozen=True)
class PromptPhaseOptions:
    ...
    work_unit: WorkUnit | None = None
```

- [ ] **Step 4: Materialize the base development prompt, then wrap it with `render_worker_prompt(...)` in production**

```python
base_prompt = _render_developer_prompt(...)
if options.work_unit is not None:
    return render_worker_prompt(
        unit=options.work_unit,
        base_prompt=base_prompt,
        policy=pipeline_policy,
    )
return base_prompt
```

- [ ] **Step 5: Write worker prompt dumps and multimodal sidecars into the worker namespace, not shared `.agent/tmp/`**

```python
path = (
    worker_prompt_dump_path(worker_namespace, phase)
    if worker_namespace is not None
    else Path(prompt_dump_path(phase))
)
workspace.write(str(path), prompt)
```

- [ ] **Step 6: Make worker runtime save checkpoints only under its own namespace, and do not invent worker resume semantics yet**

```python
save(worker_state, path=worker_checkpoint_path(worker_namespace))
```

- [ ] **Step 7: Stop worker mode from hardcoding shared `PRODUCT_CRITERIA.md`, shared prompt payloads, and shared system-prompt paths**

```python
product_criteria_path = worker_product_criteria_path(worker_namespace)
payload_root = worker_namespace / "tmp" / "prompt_payloads"
master_prompt_path = worker_master_prompt_path(worker_namespace, phase)
```

- [ ] **Step 8: Make worker runtime reload the same config source and CLI overrides as the parent run**

```python
config = load_config(
    Path(manifest.config_path) if manifest.config_path is not None else None,
    manifest.cli_overrides,
    workspace_scope=workspace_scope,
)
```

- [ ] **Step 9: Thread worker manifest data through coordinator/executor env**

```python
extra_env={
    str(MCP_ENDPOINT_ENV): bundle.mcp_handle.endpoint,
    str(WORKER_ID_ENV): unit.unit_id,
    str(WORKER_NAMESPACE_ENV): str(worker_namespace),
    "RALPH_PARALLEL_WORKER_MANIFEST": str(manifest_path),
}
```

- [ ] **Step 10: Run focused tests for worker prompt and path isolation**

Run:

```bash
cd ralph-workflow
uv run pytest -q tests/test_prompt_materialize_worker.py tests/test_parallel_worker_runtime.py -k "worker"
```

Expected: PASS.

---

### Task 4: Prove the live fan-out path is parallel and isolated end-to-end

**Files:**
- Modify: `ralph-workflow/tests/integration/test_parallel_worker_bootstrap.py`
- Modify: `ralph-workflow/tests/integration/test_parallel_happy.py`
- Modify: `ralph-workflow/tests/integration/test_parallel_serialized_verification.py`
- Modify: `ralph-workflow/tests/integration/test_runner_fanout_wiring.py`
- Modify: `ralph-workflow/tests/integration/test_parallel_resume.py`
- Modify: `ralph-workflow/tests/test_process_exit_code_not_trusted.py`
- Modify: `ralph-workflow/tests/integration/test_parallel_multimodal_runtime_e2e.py`

- [ ] **Step 1: Add an integration test proving two workers get different prompt content at the same phase**

```python
def test_two_workers_receive_distinct_worker_prompts(tmp_path: Path) -> None:
    prompt_a = (tmp_path / ".agent" / "workers" / "unit-a" / "tmp" / "development_prompt.md")
    prompt_b = (tmp_path / ".agent" / "workers" / "unit-b" / "tmp" / "development_prompt.md")

    assert prompt_a.read_text(encoding="utf-8") != prompt_b.read_text(encoding="utf-8")
    assert "unit-a" in prompt_a.read_text(encoding="utf-8")
    assert "unit-b" in prompt_b.read_text(encoding="utf-8")
```

- [ ] **Step 2: Add an integration test proving worker checkpoints stay isolated**

```python
def test_two_workers_do_not_write_shared_checkpoint(tmp_path: Path) -> None:
    shared_checkpoint = tmp_path / ".agent" / "checkpoint.json"
    worker_checkpoint_a = tmp_path / ".agent" / "workers" / "unit-a" / "tmp" / "checkpoint.json"
    worker_checkpoint_b = tmp_path / ".agent" / "workers" / "unit-b" / "tmp" / "checkpoint.json"

    assert not shared_checkpoint.exists()
    assert worker_checkpoint_a.exists()
    assert worker_checkpoint_b.exists()
```

- [ ] **Step 3: Keep serialized post-fanout verification explicit, but assert it happens only after isolated workers finish**

```python
assert call_order == ["fan_out", "verify"]
```

- [ ] **Step 4: Add regression coverage proving worker mode does not re-enter the normal outer pipeline loop**

```python
assert outer_run_loop_called is False
assert shared_checkpoint_path.exists() is False
```

- [ ] **Step 5: Run the parallel integration suite and existing invariant suites**

Run:

```bash
cd ralph-workflow
uv run pytest -q tests/integration/test_parallel_happy.py tests/integration/test_parallel_worker_bootstrap.py tests/integration/test_parallel_serialized_verification.py tests/integration/test_runner_fanout_wiring.py tests/integration/test_parallel_resume.py tests/test_process_exit_code_not_trusted.py tests/integration/test_parallel_multimodal_runtime_e2e.py
```

Expected: PASS.

---

### Task 5: Run full verification and document the contract

**Files:**
- Modify: `ralph-workflow/docs/sphinx/agents.md`
- Modify: `docs/agents/verification.md`

- [ ] **Step 1: Document the new worker contract**

```markdown
Parallel fan-out workers now use a dedicated worker bootstrap path.
Each worker receives:
- one explicit work-unit manifest
- one worker-local prompt dump
- one worker-local checkpoint path
- one isolated worker namespace under `.agent/workers/<unit_id>/`
```

- [ ] **Step 2: Add verification commands for the new worker runtime tests**

```markdown
uv run pytest -q tests/test_parallel_worker_runtime.py
uv run pytest -q tests/integration/test_parallel_worker_bootstrap.py
```

- [ ] **Step 3: Run canonical verification**

Run:

```bash
cd ralph-workflow
make verify
```

Expected: PASS with no ERROR/WARNING diagnostics.

---

## Self-review

- This plan directly addresses the real cause found in the codebase: shared singleton pipeline bootstrap plus missing production wiring for `render_worker_prompt(...)`.
- It now also addresses the Oracle-found bootstrap gaps: early worker-mode short-circuit, phase-vs-drain separation, shared current-prompt/system-prompt helpers, and missing production `WorkUnit` data flow.
- It now explicitly covers parent config/CLI override propagation into worker runtime so child workers cannot silently diverge from the spawning run.
- It does **not** waste time changing `max_workers` defaults, because the code already defaults development parallelization to `max_parallel_workers = 8`.
- It preserves intentional serialization only where required: post-fanout workspace verification.
- It adds tests for the missing production guarantees instead of relying on existing fake-executor coverage.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-parallel-worker-bootstrap-isolation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
