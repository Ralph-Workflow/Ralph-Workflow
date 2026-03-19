#!/usr/bin/env python3
"""
Unified agent dispatch preparation script.

This script does EVERYTHING needed to prepare agent instructions:
1. Runs `cargo xtask dylint-report` to generate dylint reports
2. Runs `cargo build -p ralph-workflow --lib` to compile app code
3. Runs `cargo test -p ralph-workflow --lib` to compile test code and run tests
4. Parses compilation errors and filters them by agent
5. Parses test failures and filters them by agent
6. Generates per-agent instruction files with their specific errors only

Orchestrator just runs: python3 .opencode/prepare_agent_dispatch.py

Output:
- tmp/build-current.txt - Full app compilation output
- tmp/test-output.txt - Full test compilation and execution output
- tmp/compilation-errors/{agent}.txt - Per-agent filtered errors
- tmp/agent-instructions-{agent}.txt - Per-agent complete instructions
- tmp/dylint-*.txt - Dylint reports per module
"""

import re
from pathlib import Path
from collections import defaultdict


def count_errors_in_dylint_file(filepath: str) -> int:
    bare = re.sub(r'\s*\(\d+\)\s*$', '', filepath).strip()
    try:
        with open(bare) as f:
            for line in f:
                m = re.match(r'^Total:\s+(\d+)\s+errors', line)
                if m:
                    return int(m.group(1))
        return 0
    except FileNotFoundError:
        return -1


def get_actual_dylint_info(config: dict) -> tuple:
    total = 0
    display_strings = []
    for entry in config['dylint_files']:
        bare = re.sub(r'\s*\(\d+\)\s*$', '', entry).strip()
        count = count_errors_in_dylint_file(bare)
        if count < 0:
            count = 0
        total += count
        display_strings.append(f"{bare} ({count})")
    return total, display_strings


def get_effective_dylint_info(config: dict, dylint_success: bool = True) -> tuple:
    if not dylint_success:
        return 0, ["Skipped: dylint data is stale; fix compilation errors first."], True

    total, display_strings = get_actual_dylint_info(config)
    return total, display_strings, False


ERROR_CODE_HINTS = {
    "E0515": (
        "BORROW-THEN-MOVE: A value is created locally, borrowed to construct another object, "
        "then both are returned. Rust forbids this because the borrow outlives the local.\n"
        "  Fix: Move the value's creation to the *call site* scope so it outlives the borrowing struct.\n"
        "  Example: instead of creating `timer` inside `setup_X()` and returning `(timer, ctx_that_borrows_timer)`,\n"
        "  have the caller create `timer`, pass `&mut timer` to `setup_X()`, and return only the ctx."
    ),
    "E0505": (
        "MOVE-WHILE-BORROWED: Same root cause as E0515 — a local value is borrowed and also moved.\n"
        "  Fix: same as E0515 — move the owned value's creation to the call site."
    ),
    "E0382": (
        "USE-AFTER-MOVE: A value was moved (into a function call or closure) and then used again.\n"
        "  Fix options:\n"
        "  (a) Clone before moving: `x.clone()` if cloning is acceptable.\n"
        "  (b) Reorder: compute what you need from `x` *before* moving it.\n"
        "  (c) Change the function to borrow instead of taking ownership."
    ),
    "E0502": (
        "BORROW-CONFLICT: A value has an active mutable borrow when an immutable borrow is attempted.\n"
        "  Common cause: function signature uses the same lifetime for `&'a mut T<'a>` (invariant),\n"
        "  which extends the mutable borrow for the entire `'a` lifetime.\n"
        "  Fix: use two distinct lifetimes: `fn foo<'r, 'ctx>(x: &'r mut T<'ctx>)`."
    ),
    "E0621": (
        "LIFETIME-REQUIRED: A reference in a return type needs an explicit lifetime annotation.\n"
        "  The compiler hint shows the exact fix — add the named lifetime to the parameter type.\n"
        "  Example: `config: &CommitGenerationConfig<'a>` → `config: &'a CommitGenerationConfig<'a>`."
    ),
    "E0277": (
        "SIZED-BOUND: A generic type parameter `E` requires `Sized` but the call site uses `&E` (a trait object).\n"
        "  Fix: relax the bound with `+ ?Sized`: `fn foo<E: SomeTrait + ?Sized>(x: &E)`."
    ),
    "E0308": (
        "TYPE-MISMATCH: Expected one type, found another. Common causes:\n"
        "  (a) A function return type was changed (e.g., tuple gained/lost an element) — update all callers.\n"
        "  (b) `&str` vs `String` — use `.to_string()` or `.as_str()` to convert.\n"
        "  (c) `Option<&T>` vs `Option<T>` — use `.cloned()` or `.copied()`."
    ),
    "E0425": (
        "NOT-IN-SCOPE: A variable name is used but not declared in this scope.\n"
        "  Common cause: a refactor deleted the `let mut x = ...` declaration that this code depended on.\n"
        "  Fix: read the full function body to find where `x` should be declared, then re-introduce it."
    ),
    "E0282": (
        "TYPE-ANNOTATION-NEEDED: The compiler cannot infer the type.\n"
        "  Fix: add an explicit type annotation. Common locations:\n"
        "  - Closure parameters: `|r: EffectResult, ev|`\n"
        "  - Struct literal fields: annotate the struct type or the field with a turbofish.\n"
        "  - `.clone()` on ambiguous types: annotate the variable being cloned."
    ),
    "E0061": (
        "WRONG-ARG-COUNT: A function was called with the wrong number of arguments.\n"
        "  Common cause: the function signature was updated to require an additional parameter,\n"
        "  but the call site was not updated.\n"
        "  Fix: read the function definition to see what the new parameter is, then pass it."
    ),
    "E0596": (
        "NOT-MUTABLE: A variable is borrowed as mutable but was not declared with `mut`.\n"
        "  Fix: add `mut` to the binding: `let mut x = ...`."
    ),
    "unused import": (
        "UNUSED-IMPORT: A `use` statement imports a name that is never referenced in this file.\n"
        "  Fix: remove the `use` line."
    ),
    "unused variable": (
        "UNUSED-VARIABLE: A variable binding is never read.\n"
        "  Fix: prefix with `_` (e.g., `_foo`) to suppress, or remove if truly unneeded."
    ),
}


def generate_error_hints(error_blocks: list) -> str:
    """
    Given a list of error block strings for one agent, produce a targeted hints section
    that maps each error code found to a concrete fix strategy.
    """
    seen_codes = {}
    for block in error_blocks:
        m = re.search(r'error\[(\w+)\]', block)
        if m:
            code = m.group(1)
            if code not in seen_codes:
                seen_codes[code] = block[:200]
        else:
            for keyword in ("unused import", "unused variable"):
                if keyword in block and keyword not in seen_codes:
                    seen_codes[keyword] = block[:200]

    if not seen_codes:
        return ""

    lines = ["## ⚡ TARGETED HINTS (read before touching any file)\n"]
    lines.append(
        "These hints are generated from YOUR specific errors. Read them first — they may let you\n"
        "fix an error in 1-2 tool calls instead of 10+.\n"
    )
    for code, _sample in seen_codes.items():
        hint = ERROR_CODE_HINTS.get(code)
        if hint:
            lines.append(f"### {code}\n{hint}\n")

    return "\n".join(lines)


# Module to agent mapping - based on opencode.json write permissions
# Each agent only has access to specific top-level modules
MODULE_TO_AGENT = {
    # workflow-json: only json_parser/**
    "json_parser": "workflow-json",
    
    # workflow-reducer: reducer/**, pipeline/**, checkpoint/**, phases/**
    "reducer": "workflow-reducer",
    "pipeline": "workflow-reducer",
    "checkpoint": "workflow-reducer",
    "phases": "workflow-reducer",
    
    # workflow-config: config/**, cli/**
    "cli": "workflow-config",
    "config": "workflow-config",
    
    # workflow-prompts: guidelines/**, prompts/**
    "prompts": "workflow-prompts",
    "guidelines": "workflow-prompts",
    
    # workflow-app: ONLY app/** (can create app/io, app/runtime internally)
    "app": "workflow-app",
    
    # workflow-misc: common/**, platform/**, rendering/**, templates/**
    "platform": "workflow-misc",
    "common": "workflow-misc",
    "rendering": "workflow-misc",
    "templates": "workflow-misc",
    
    # workflow-future: benchmarks/**, interrupt/**, language_detector/**, review_metrics/**
    "language_detector": "workflow-future",
    "interrupt": "workflow-future",
    "benchmarks": "workflow-future",
    "review_metrics": "workflow-future",
    
    # workflow-core: only root-level files (lib.rs, main.rs, banner.rs, exit_pause.rs)
    # and can create/access src/io/**, src/boundary/**, src/runtime/** at TOP level
    "lib": "workflow-core",
    "main": "workflow-core",
    "banner": "workflow-core",
    "exit_pause": "workflow-core",
    
    # workflow-io: ONLY top-level io/**, files/**
    "io": "workflow-io",
    "files": "workflow-io",
    
    # workflow-agents: ONLY agents/**
    "agents": "workflow-agents",
    
    # workflow-cloud: ONLY cloud/**
    "cloud": "workflow-cloud",
    
    # workflow-logging: ONLY logging/**, logger/**
    "logging": "workflow-logging",
    "logger": "workflow-logging",
    
    # workflow-workspace: ONLY workspace/**, boundary/**
    "workspace": "workflow-workspace",
    "boundary": "workflow-workspace",
    
    # workflow-git: ONLY git_helpers/**
    "git_helpers": "workflow-git",
    
    # workflow-execution: ONLY executor/**, runtime/**
    "executor": "workflow-execution",
    "runtime": "workflow-execution",
    
    # workflow-monitoring: ONLY monitoring/**, diagnostics/**
    "monitoring": "workflow-monitoring",
    "diagnostics": "workflow-monitoring",
}

# Agent configurations with dylint files and module mappings
# module_filter: grep pattern used to filter `cargo check` output to this agent's errors only
AGENTS = {
    "workflow-core": {
        "write": "ralph-workflow/src/lib.rs, ralph-workflow/src/main.rs, ralph-workflow/src/banner.rs, ralph-workflow/src/exit_pause.rs",
        "search": "tmp/**, ralph-workflow/src/lib.rs, ralph-workflow/src/main.rs, ralph-workflow/src/banner.rs, ralph-workflow/src/exit_pause.rs, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test",
        "modules": [
            "ralph-workflow/src/lib.rs",
            "ralph-workflow/src/main.rs",
            "ralph-workflow/src/banner.rs",
            "ralph-workflow/src/exit_pause.rs",
        ],
        "dylint_files": [],
        "total_dylint": 0,
        "module_filter": "src/main\\.rs|src/lib\\.rs|src/banner|src/exit_pause",
    },
    "workflow-future": {
        "write": "ralph-workflow/src/benchmarks/**, ralph-workflow/src/interrupt/**, ralph-workflow/src/language_detector/**, ralph-workflow/src/review_metrics/**",
        "search": "tmp/**, ralph-workflow/src/{benchmarks,interrupt,language_detector,review_metrics}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/benchmarks/*, mkdir -p ralph-workflow/src/interrupt/*, etc.",
        "modules": [
            "ralph-workflow/src/benchmarks/**",
            "ralph-workflow/src/interrupt/**",
            "ralph-workflow/src/language_detector/**",
            "ralph-workflow/src/review_metrics/**"
        ],
        "dylint_files": ["tmp/dylint-language_detector.txt (46)", "tmp/dylint-interrupt.txt (5)"],
        "total_dylint": 51,
        "module_filter": "benchmarks|interrupt|language_detector|review_metrics",
    },
    "workflow-cloud": {
        "write": "ralph-workflow/src/cloud/**",
        "search": "tmp/**, ralph-workflow/src/cloud/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/cloud/*, etc.",
        "modules": ["ralph-workflow/src/cloud/**"],
        "dylint_files": ["tmp/dylint-cloud.txt (6)"],
        "total_dylint": 6,
        "module_filter": "cloud",
    },
    "workflow-app": {
        "write": "ralph-workflow/src/app/**",
        "search": "tmp/**, ralph-workflow/src/app/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/app/*, etc.",
        "modules": ["ralph-workflow/src/app/**"],
        "dylint_files": ["tmp/dylint-app.txt (52)"],
        "total_dylint": 52,
        "module_filter": "src/app",
    },
    "workflow-config": {
        "write": "ralph-workflow/src/config/**, ralph-workflow/src/cli/**",
        "search": "tmp/**, ralph-workflow/src/{config,cli}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/config/*, etc.",
        "modules": ["ralph-workflow/src/config/**", "ralph-workflow/src/cli/**"],
        "dylint_files": ["tmp/dylint-config.txt (16)", "tmp/dylint-cli.txt (41)"],
        "total_dylint": 57,
        "module_filter": "src/config|src/cli",
    },
    "workflow-reducer": {
        "write": "ralph-workflow/src/reducer/**, ralph-workflow/src/pipeline/**, ralph-workflow/src/checkpoint/**, ralph-workflow/src/phases/**",
        "search": "tmp/**, ralph-workflow/src/{reducer,pipeline,checkpoint,phases}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/reducer/*, etc.",
        "modules": [
            "ralph-workflow/src/reducer/**",
            "ralph-workflow/src/pipeline/**",
            "ralph-workflow/src/checkpoint/**",
            "ralph-workflow/src/phases/**"
        ],
        "dylint_files": [
            "tmp/dylint-reducer.txt (109)",
            "tmp/dylint-pipeline.txt (55)",
            "tmp/dylint-checkpoint.txt (38)",
            "tmp/dylint-phases.txt (32)"
        ],
        "total_dylint": 234,
        "module_filter": "src/reducer|src/pipeline|src/checkpoint|src/phases",
    },
    "workflow-json": {
        "write": "ralph-workflow/src/json_parser/**",
        "search": "tmp/**, ralph-workflow/src/json_parser/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/json_parser/*, etc.",
        "modules": ["ralph-workflow/src/json_parser/**"],
        "dylint_files": ["tmp/dylint-json_parser.txt (162)"],
        "total_dylint": 162,
        "module_filter": "json_parser",
    },
    "workflow-prompts": {
        "write": "ralph-workflow/src/guidelines/**, ralph-workflow/src/prompts/**",
        "search": "tmp/**, ralph-workflow/src/{guidelines,prompts}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/prompts/*, etc.",
        "modules": ["ralph-workflow/src/guidelines/**", "ralph-workflow/src/prompts/**"],
        "dylint_files": ["tmp/dylint-prompts.txt (96)", "tmp/dylint-guidelines.txt (32)"],
        "total_dylint": 128,
        "module_filter": "src/prompts|src/guidelines",
    },
    "workflow-misc": {
        "write": "ralph-workflow/src/common/**, ralph-workflow/src/platform/**, ralph-workflow/src/rendering/**, ralph-workflow/src/templates/**",
        "search": "tmp/**, ralph-workflow/src/{common,platform,rendering,templates}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/common/*, etc.",
        "modules": [
            "ralph-workflow/src/common/**",
            "ralph-workflow/src/platform/**",
            "ralph-workflow/src/rendering/**",
            "ralph-workflow/src/templates/**"
        ],
        "dylint_files": ["tmp/dylint-rendering.txt (46)", "tmp/dylint-common.txt (1)"],
        "total_dylint": 47,
        "module_filter": "src/common|src/platform|src/rendering|src/templates",
    },
    "workflow-agents": {
        "write": "ralph-workflow/src/agents/**",
        "search": "tmp/**, ralph-workflow/src/agents/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/agents/*, etc.",
        "modules": ["ralph-workflow/src/agents/**"],
        "dylint_files": ["tmp/dylint-agents.txt (8)"],
        "total_dylint": 8,
        "module_filter": "src/agents",
    },
    "workflow-logging": {
        "write": "ralph-workflow/src/logging/**, ralph-workflow/src/logger/**",
        "search": "tmp/**, ralph-workflow/src/{logging,logger}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/logging/*, etc.",
        "modules": ["ralph-workflow/src/logging/**", "ralph-workflow/src/logger/**"],
        "dylint_files": ["tmp/dylint-logging.txt (2)", "tmp/dylint-logger.txt (2)"],
        "total_dylint": 4,
        "module_filter": "src/logging|src/logger",
    },
    "workflow-git": {
        "write": "ralph-workflow/src/git_helpers/**",
        "search": "tmp/**, ralph-workflow/src/git_helpers/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/git_helpers/*, etc.",
        "modules": ["ralph-workflow/src/git_helpers/**"],
        "dylint_files": ["tmp/dylint-git_helpers.txt"],
        "total_dylint": 0,
        "module_filter": "git_helpers",
    },
    "workflow-execution": {
        "write": "ralph-workflow/src/executor/**",
        "search": "tmp/**, ralph-workflow/src/executor/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/executor/*, etc.",
        "modules": ["ralph-workflow/src/executor/**"],
        "dylint_files": ["tmp/dylint-executor.txt"],
        "total_dylint": 0,
        "module_filter": "src/executor",
    },
    "workflow-workspace": {
        "write": "ralph-workflow/src/workspace/**, ralph-workflow/src/boundary/**",
        "search": "tmp/**, ralph-workflow/src/{workspace,boundary}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/workspace/*, etc.",
        "modules": ["ralph-workflow/src/workspace/**", "ralph-workflow/src/boundary/**"],
        "dylint_files": ["tmp/dylint-workspace.txt", "tmp/dylint-boundary.txt"],
        "total_dylint": 0,
        "module_filter": "src/workspace|src/boundary",
    },
    "workflow-io": {
        "write": "ralph-workflow/src/io/**, ralph-workflow/src/files/**",
        "search": "tmp/**, ralph-workflow/src/{io,files}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/io/*, etc.",
        "modules": ["ralph-workflow/src/io/**", "ralph-workflow/src/files/**"],
        "dylint_files": ["tmp/dylint-io.txt", "tmp/dylint-files.txt"],
        "total_dylint": 0,
        "module_filter": "src/io|src/files",
    },
}


def extract_clippy_warnings():
    """Extract clippy warnings from clippy-output.txt and organize by agent."""
    print("Extracting clippy warnings...")
    
    output_dir = Path("tmp/clippy-warnings")
    output_dir.mkdir(exist_ok=True)
    
    # Clear existing files
    for f in output_dir.glob("*.txt"):
        f.unlink()
    
    warnings_by_agent = defaultdict(list)
    
    try:
        with open("tmp/clippy-output.txt") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print("  WARNING: tmp/clippy-output.txt not found. Skipping clippy warnings.")
        return {}
    
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        
        # Match warning start
        if line.startswith("warning:"):
            warning_block = [line]
            i += 1
            
            # Collect warning block (next ~10-20 lines until empty line or next warning)
            while i < len(lines):
                next_line = lines[i].rstrip()
                if not next_line:  # Empty line
                    break
                if next_line.startswith("warning:") or next_line.startswith("error:"):
                    # Next warning/error, don't consume this line
                    break
                warning_block.append(next_line)
                i += 1
            
            # Extract module from warning block
            module = None
            for block_line in warning_block:
                match = re.search(r'--> ralph-workflow/src/(\w+)', block_line)
                if match:
                    module = match.group(1)
                    break
            
            if module and module in MODULE_TO_AGENT:
                agent = MODULE_TO_AGENT[module]
                warnings_by_agent[agent].append("\n".join(warning_block))
        else:
            i += 1
    
    # Write warnings to files
    for agent, warnings in warnings_by_agent.items():
        output_file = output_dir / f"{agent}.txt"
        with open(output_file, "w") as f:
            f.write("\n\n".join(warnings))
            f.write("\n")
        print(f"  Created {output_file} ({len(warnings)} warnings)")
    
    total = sum(len(w) for w in warnings_by_agent.values())
    print(f"  Summary: {total} total warnings across {len(warnings_by_agent)} agents")
    
    return warnings_by_agent


def extract_test_failures():
    """Extract test failures from test-output.txt into a single file."""
    print("Extracting test failures...")
    
    try:
        with open("tmp/test-output.txt") as f:
            content = f.read()
    except FileNotFoundError:
        print("  WARNING: tmp/test-output.txt not found. Skipping test failures.")
        return False
    
    # Check if there are any test failures
    if "test result: ok" in content or "running 0 tests" in content:
        print("  No test failures found")
        with open("tmp/test-failures.txt", "w") as f:
            f.write("# Test Failures\n\nNo test failures - all tests passed!\n")
        return False
    
    # Extract failure sections
    failures = []
    lines = content.split("\n")
    
    # Look for "failures:" section which lists all failed tests
    in_failures_section = False
    failure_block = []
    
    for line in lines:
        if line.startswith("failures:"):
            in_failures_section = True
            failure_block.append(line)
        elif in_failures_section:
            if line.startswith("test result:"):
                # End of failures section
                failure_block.append(line)
                break
            failure_block.append(line)
    
    # Write all failures to one file
    with open("tmp/test-failures.txt", "w") as f:
        f.write("# Test Failures\n\n")
        f.write("⚠️ **IMPORTANT**: Only fix tests in YOUR modules. Do NOT fix tests outside your boundary.\n\n")
        f.write("Read this file and check if any failures are in your modules:\n")
        for agent_name, config in AGENTS.items():
            f.write(f"- {agent_name}: {', '.join(config['modules'])}\n")
        f.write("\n" + "=" * 60 + "\n\n")
        f.write("\n".join(failure_block))
    
    print(f"  Created tmp/test-failures.txt")
    return True


def extract_compilation_errors():
    """Extract compilation errors from build-current.txt and organize by agent."""
    print("Step 1: Extracting compilation errors...")
    
    output_dir = Path("tmp/compilation-errors")
    output_dir.mkdir(exist_ok=True)
    
    # Clear existing files
    for f in output_dir.glob("*.txt"):
        f.unlink()
    
    errors_by_agent = defaultdict(list)
    
    try:
        with open("tmp/build-current.txt") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print("  WARNING: tmp/build-current.txt not found. Skipping compilation error extraction.")
        return {}
    
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        
        # Match error start
        if line.startswith("error[") or line.startswith("error:"):
            error_block = [line]
            i += 1
            
            # Collect error block (next ~10-20 lines until empty line or next error)
            while i < len(lines):
                next_line = lines[i].rstrip()
                if not next_line:  # Empty line
                    break
                if next_line.startswith("error[") or next_line.startswith("error:"):
                    # Next error, don't consume this line
                    break
                error_block.append(next_line)
                i += 1
            
            # Extract module from error block
            module = None
            for block_line in error_block:
                match = re.search(r'--> ralph-workflow/src/(\w+)', block_line)
                if match:
                    module = match.group(1)
                    break
            
            if module and module in MODULE_TO_AGENT:
                agent = MODULE_TO_AGENT[module]
                errors_by_agent[agent].append("\n".join(error_block))
        else:
            i += 1
    
    # Write errors to files
    for agent, errors in errors_by_agent.items():
        output_file = output_dir / f"{agent}.txt"
        with open(output_file, "w") as f:
            f.write("\n\n".join(errors))
            f.write("\n")
        print(f"  Created {output_file} ({len(errors)} errors)")
    
    # Generate summary
    summary_lines = ["# Compilation Errors by Agent", ""]
    total = 0
    for agent in sorted(errors_by_agent.keys()):
        count = len(errors_by_agent[agent])
        total += count
        summary_lines.append(f"{agent}: {count} errors")
    summary_lines.append(f"\nTotal: {total} errors")
    
    summary_file = output_dir / "summary.txt"
    with open(summary_file, "w") as f:
        f.write("\n".join(summary_lines))
    
    print(f"  Summary: {total} total errors across {len(errors_by_agent)} agents")
    
    return errors_by_agent


def _agent_permissions_block(agent_name: str, config: dict) -> str:
    """
    Generate an explicit PERMISSIONS section for an agent instruction file.
    Derived from the opencode.json permission rules for the given agent.
    """
    modules = config['modules']

    # mkdir is allowed one level deep within each module (for creating boundary submodules)
    mkdir_lines = []
    for m in modules:
        base = m.replace('/**', '')
        mkdir_lines.append(f"    - `mkdir -p {base}/<subdir>` — create a boundary submodule (e.g., io/, runtime/, ffi/)")
    mkdir_lines.append("    - `mkdir -p tmp/<name>` — create scratch/temp directories")
    mkdir_str = "\n".join(mkdir_lines)

    # rmdir follows the same paths as mkdir
    rmdir_lines = []
    for m in modules:
        base = m.replace('/**', '')
        rmdir_lines.append(f"    - `rmdir {base}/<subdir>` — remove empty subdirectory you created")
    rmdir_lines.append("    - `rmdir tmp/<name>` — remove temp directory")
    rmdir_str = "\n".join(rmdir_lines)

    # rm is allowed recursively within modules
    rm_lines = [f"    - `rm {m}` — delete files within your module" for m in modules]
    rm_str = "\n".join(rm_lines)

    read_paths = ", ".join(modules) + ", tmp/**, docs/**"
    write_paths = ", ".join(modules) + ", tmp/**"

    cargo_note = f"""
### ✅ Cargo Commands (for compiling and testing your changes)

| Command | Purpose |
|---------|---------|
| `cargo build <flags>` | Compile — verify your changes are syntactically correct |
| `cargo check <flags>` | Fast type-check without linking — cheaper than build |
| `cargo test <flags>` | Run tests — verify behaviour is correct |

> **Note:** The orchestrator tells you whether you are running as `{agent_name}` (no compile access)
> or `{agent_name}-cargo` (compile access). If you have compilation errors in your instruction file
> you are a `-cargo` variant and these commands are available to you."""

    return f"""## ⚠️ PERMISSIONS — Read Before Using Any Tool

This agent runs with a **deny-by-default** permission system. Every tool call is checked against
an allowlist. Anything not listed below **will be blocked silently or raise an error.**

---

### ⛔ STRICTLY FORBIDDEN

**Git — ALL git operations are forbidden without exception:**
```
git add, git commit, git push, git pull, git reset, git stash,
git rebase, git merge, git cherry-pick, git checkout, git branch -D, git clean
```
**Ralph is the ONLY entity allowed to commit. Do not attempt any git operation.**
The protection system reinstalls on every run. Bypass is futile and is a security violation.

**Other forbidden commands:**
- `cargo add` / `cargo install` / `cargo update` — do NOT modify Cargo.toml or install crates
- `cargo run` — do NOT run the application
- `webfetch` / `websearch` — no internet access (both tools are fully disabled)
- `question` — you cannot ask the user questions (tool is disabled)
- `task` — you cannot spawn sub-agents (tool is disabled)
- `todoread` / `todowrite` — no task list management (tools are disabled)
- Any bash command not explicitly listed in the allowlist below

---

### ✅ Allowed Bash Commands

| Command | Purpose |
|---------|---------|
| `ls [path]` | Explore directory structure |
| `cat <file>` | Read file contents |
| `head <file>` | Read first N lines of a file |
| `tail <file>` | Read last N lines of a file |
| `grep <pattern> <files>` | Search within file contents |
| `find <path>` | Locate files by name or pattern |
| `wc <file>` | Count lines/words in a file |
| `echo <text>` | Print output to stdout |
| `pwd` | Show current working directory |
| `mkdir -p <path>` | Create directories — **ONLY the paths listed below** |
| `rmdir <path>` | Remove empty directories — **ONLY the paths listed below** |
| `rm <path>` | Delete files — **ONLY within your modules** |
{cargo_note}

**`mkdir -p` is allowed ONLY for these paths** (use this to create boundary submodules like `io/`, `runtime/`):
{mkdir_str}

**`rmdir` is allowed ONLY for these paths:**
{rmdir_str}

**`rm` is allowed ONLY for these paths:**
{rm_str}

---

### ✅ File Tool Access

| Tool | Access |
|------|--------|
| **Read** | `{read_paths}` |
| **Write** (create new files) | `{write_paths}` |
| **Edit** (modify existing files) | `{", ".join(modules)}` only |
| **Glob / Grep / List** | Allowed everywhere EXCEPT `.opencode/**` |
| **`.opencode/**`** | ⛔ DENIED — security boundary, never touch |

---"""


def generate_agent_instructions(errors_by_agent, warnings_by_agent, has_test_failures, dylint_success=True, cargo_available=True):
    """Generate agent instruction files with compilation error context."""
    print("\nGenerating agent instruction files...")

    template = """# Agent: {agent_name}

⚠️ **RE-READ THIS FILE BEFORE EVERY ACTION. DO NOT WORK FROM MEMORY.**

{cargo_warning}
{permissions_block}

## Tool-Call Budget

You have a hard limit of **400 tool calls**. Allocate ~5-10 calls per error.

**DO:**
- Read `docs/code-style/functional-transformations.md` and `docs/code-style/boundaries.md` first.
- Follow the style guide — Dylint just enforces it. Understand the WHY, not just the lint line.
- Fix directly from the error message — it has file, line, and context.
- Stop reading and start editing as soon as you have enough context.
- Make large refactors fearlessly — compilation errors from refactoring are expected and OK.

**DO NOT:**
- Run ANY cargo command mid-work. Cargo is intentionally disabled to allow large refactors.
- Chase individual lint lines without understanding the style guide principle behind them.
- Read 10 files before making a single edit.
- Loop: read → read → read → give up. Read once, understand, refactor boldly.

## Your Goal
{stale_warning}
{goal_text}

## Your Modules
{modules}

## Your Errors

### Compilation Errors: {compilation_count}

{embedded_errors}

### Dylint Issues: {dylint_total}
Files to read: {dylint_files}

### Clippy Warnings: {clippy_count}
{clippy_list}

### Test Failures
{test_failures_section}

{targeted_hints}

## Instructions (RE-READ THESE BEFORE EACH FIX)

### STEP 1: Read Style Guide (if you haven't yet)
```bash
cat docs/code-style/functional-transformations.md
cat docs/code-style/boundaries.md
```

The style guide is the source of truth. Dylint just enforces it.

### STEP 2: Plan your fixes

{step_2_text}

### STEP 3: Fix ALL Issues (do NOT verify between fixes)

**Priority order:**
1. **Hard stuff first**: Complex imperative loops/mutations → functional
2. **Architectural**: Create boundary modules when truly needed
3. **Easy stuff last**: Imports, simple mechanical fixes

**Refactoring patterns:**
```rust
// BEFORE: let mut
let mut results = Vec::new();
for item in items {{
    results.push(transform(item));
}}

// AFTER: functional
let results: Vec<_> = items.into_iter()
    .map(transform)
    .collect();

// BEFORE: for loop with side effects
for item in items {{
    process(item);
}}

// AFTER: iterator
items.into_iter().for_each(process);

// BEFORE: while loop
while let Some(item) = queue.pop() {{
    process(item);
}}

// AFTER: recursion OR boundary module
fn process_queue(mut queue: Queue) {{
    match queue.pop() {{
        Some(item) => {{
            process(item);
            process_queue(queue)
        }}
        None => ()
    }}
}}
```

**When to create boundary modules:**
- I/O operations (file, network, process)
- Runtime state (event loops, thread pools)
- FFI, executors
- Truly imperative code that refactoring would break

**How to create boundary:**
```bash
mkdir -p ralph-workflow/src/json_parser/io
echo "pub mod streaming;" > ralph-workflow/src/json_parser/io/mod.rs
```

Valid boundary names: `io/`, `runtime/`, `ffi/`, `boundary/`, `executor/`

### STEP 4: Report your changes (NO cargo — it is disabled)

{step_4_verify}

### STEP 5: Report

```
FILES CHANGED: [list]
DYLINT FIXED: {report_dylint_fixed}
CLIPPY: pass/fail
```

## Important Rules

- **Style guide > compilation**: If your refactoring breaks compilation, that's OK. Report it.
- **COMPLEXITY IS THE POINT, LARGE ARCHITECTURAL CHANGE IS THE POINT DO NOT SLACK OFF
- **Only edit your modules**: {write}
- **Do hard work first**: Don't avoid complex refactors
- **Boundary modules are real work**: Create them when legitimately needed, not as escape hatch
- **COMPLEXITY IS THE POINT, LARGE ARCHITECTURAL CHANGE IS THE POINT DO NOT SLACK OFF
- **TO EMPTHESIZE ONE MORE TIME COMPLEXITY AND LARGE CHANGE IS THE POINT!!! DO NOT SAY ITS TOO COMPLEX!!** 

{final_note}"""

    for agent_name, config in AGENTS.items():
        modules_str = '\n'.join(f"- {m}" for m in config['modules'])
        actual_total, actual_file_strings, dylint_is_stale = get_effective_dylint_info(
            config,
            dylint_success,
        )
        dylint_files_str = ', '.join(actual_file_strings)
        permissions_block = _agent_permissions_block(agent_name, config)
        module_filter = config.get('module_filter', agent_name.replace('workflow-', ''))

        agent_errors = errors_by_agent.get(agent_name, [])
        error_count = len(agent_errors)

        if agent_errors:
            error_text_blocks = "\n\n".join(agent_errors)
            embedded_errors = (
                f"**{error_count} error(s) — full text below (no need to read a separate file):**\n\n"
                f"```\n{error_text_blocks}\n```"
            )
            targeted_hints = generate_error_hints(agent_errors)
        else:
            embedded_errors = "- No compilation errors (focus on dylint)"
            targeted_hints = ""

        if agent_name in warnings_by_agent:
            clippy_count = len(warnings_by_agent[agent_name])
            clippy_list = f"Read: `tmp/clippy-warnings/{agent_name}.txt`"
        else:
            clippy_count = 0
            clippy_list = "- No clippy warnings"

        if has_test_failures:
            test_failures_section = (
                "⚠️ **Read `tmp/test-failures.txt` and check if any failures are in YOUR modules.**\n\n"
                "**CRITICAL**: DO NOT fix tests outside your boundary. Only fix tests in your modules listed above."
            )
        else:
            test_failures_section = "✓ No test failures"

        stale_warning = ""
        if dylint_is_stale:
            stale_warning = (
                "\n⚠️  **DYLINT SKIPPED**: dylint-report did not produce trustworthy current data. "
                "Fix compilation errors first, then re-run prepare_agent_dispatch.py.\n"
            )

        if dylint_is_stale:
            goal_text = "Fix compilation errors in your modules first. Do not work on dylint until compilation succeeds."
            step_2_text = (
                "Your compilation errors are listed above. Fix ALL of them, then run the filtered check in STEP 4 — not before.\n"
                "Ignore dylint until prepare_agent_dispatch.py is re-run successfully."
            )
            report_dylint_fixed = "skipped until compilation succeeds"
            final_note = "The orchestrator will rerun dylint after compilation is fixed. Your job right now is compilation recovery."
        else:
            goal_text = f"Fix ALL {actual_total} dylint errors in your modules. Keep working until done."
            step_2_text = "Read each dylint file listed above. Run the filtered check to see current compilation state."
            report_dylint_fixed = f"X/{actual_total}"
            final_note = "The orchestrator will handle compilation fixes separately if needed. Your job is style guide compliance."

        cargo_warning = (
            "\n```\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  🚫  DO NOT RUN CARGO — EVER — DURING YOUR WORK  🚫        ║\n"
            "║                                                              ║\n"
            "║  Cargo is intentionally OFF for agents. This is by design   ║\n"
            "║  so you can perform LARGE refactors without distraction.     ║\n"
            "║                                                              ║\n"
            "║  FORBIDDEN: cargo build, cargo check, cargo test, clippy    ║\n"
            "║  FORBIDDEN: .opencode/verify_agent_work.sh                  ║\n"
            "║  FORBIDDEN: any shell command that invokes cargo             ║\n"
            "║                                                              ║\n"
            "║  Compilation errors from refactoring are EXPECTED and OK.   ║\n"
            "║  The orchestrator runs verification after ALL agents done.   ║\n"
            "║  Your job: follow the style guide, refactor the code.       ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n"
            "```\n"
        )
        step_4_verify = (
            "**Cargo is disabled for agents. Do NOT run any cargo command.**\n\n"
            "The orchestrator verifies everything after all agents complete. "
            "Simply report what files you changed and what patterns you fixed."
        )

        content = template.format(
            agent_name=agent_name,
            write=config['write'],
            search=config['search'],
            bash=config['bash'],
            modules=modules_str,
            permissions_block=permissions_block,
            module_filter=module_filter,
            compilation_count=error_count,
            embedded_errors=embedded_errors,
            clippy_count=clippy_count,
            clippy_list=clippy_list,
            test_failures_section=test_failures_section,
            dylint_files=dylint_files_str,
            dylint_total=actual_total,
            stale_warning=stale_warning,
            goal_text=goal_text,
            step_2_text=step_2_text,
            report_dylint_fixed=report_dylint_fixed,
            final_note=final_note,
            cargo_warning=cargo_warning,
            step_4_verify=step_4_verify,
            targeted_hints=targeted_hints,
        )

        filename = f"tmp/agent-instructions-{agent_name}.txt"
        with open(filename, 'w') as f:
            f.write(content)

        print(f"  Created {filename}")

    print(f"\nGenerated {len(AGENTS)} agent instruction files!")


def check_cargo_available():
    """Check if cargo is available on the system."""
    import subprocess
    try:
        result = subprocess.run(
            ["cargo", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return False
        if "command not found" in result.stderr.lower() or "not found" in result.stderr.lower():
            return False
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def run_compilation_and_tests():
    """
    Run cargo build, and conditionally run tests and clippy.
    This runs BEFORE parsing so we have fresh output to parse.
    
    Returns: (dylint_success, build_success, test_success, cargo_available) tuple of booleans
    """
    import subprocess
    
    cargo_available = check_cargo_available()
    
    if not cargo_available:
        print("\n" + "=" * 60)
        print("⚠️  CARGO IS DELIBERATELY TURN OFF")
        print("=" * 60)
        print("Cargo is deliberately turned OFF to allow complex refactors without")
        print("verification distraction. Focus on IMPLEMENTING, not verifying.")
        print("The verification script (.opencode/verify_agent_work.sh) will NOT work")
        print("until cargo is re-enabled.")
        print("=" * 60)
        Path("tmp").mkdir(exist_ok=True)
        Path("tmp/build-current.txt").write_text("CARGO NOT AVAILABLE\n")
        Path("tmp/test-output.txt").write_text("CARGO NOT AVAILABLE\n")
        Path("tmp/clippy-output.txt").write_text("CARGO NOT AVAILABLE\n")
        return (False, False, False, False)
    
    print("\n" + "=" * 60)
    print("STEP 1: Running dylint report...")
    print("=" * 60)
    result = subprocess.run(
        ["cargo", "xtask", "dylint-report"],
        capture_output=False,
        text=True
    )
    dylint_success = result.returncode == 0
    if not dylint_success:
        print("✗ dylint-report failed — tmp/dylint-*.txt files are STALE from a previous run")
        print("  Agents will be warned. Fix compilation errors first to get fresh dylint data.")
    
    print("\n" + "=" * 60)
    print("STEP 2: Compiling all targets (lib + tests) ...")
    print("=" * 60)
    build_result = subprocess.run(
        ["cargo", "check", "-p", "ralph-workflow", "--all-targets"],
        capture_output=True,
        text=True
    )
    
    # Write build output to tmp/build-current.txt
    Path("tmp").mkdir(exist_ok=True)
    with open("tmp/build-current.txt", "w") as f:
        f.write(build_result.stdout)
        f.write(build_result.stderr)
    
    build_success = build_result.returncode == 0
    
    if build_success:
        print("✓ All targets compile successfully")
    else:
        print("✗ Compilation failed (lib or tests) - skipping test run and clippy")
        return (dylint_success, False, False, cargo_available)
    
    # Only run tests if build succeeded
    print("\n" + "=" * 60)
    print("STEP 3: Compiling tests and running them (cargo test)...")
    print("=" * 60)
    test_result = subprocess.run(
        ["cargo", "test", "-p", "ralph-workflow", "--lib"],
        capture_output=True,
        text=True
    )
    
    # Write test output to tmp/test-output.txt
    with open("tmp/test-output.txt", "w") as f:
        f.write(test_result.stdout)
        f.write(test_result.stderr)
    
    test_success = test_result.returncode == 0
    
    if test_success:
        print("✓ Tests passed")
    else:
        print("✗ Tests failed (runtime failures — compilation already verified in step 2)")
    
    # Only run clippy if build succeeded
    print("\n" + "=" * 60)
    print("STEP 4: Running clippy for warnings...")
    print("=" * 60)
    clippy_result = subprocess.run(
        ["cargo", "clippy", "-p", "ralph-workflow", "--lib", "--all-targets", "--", "-W", "clippy::all"],
        capture_output=True,
        text=True
    )
    
    # Write clippy output to tmp/clippy-output.txt
    with open("tmp/clippy-output.txt", "w") as f:
        f.write(clippy_result.stdout)
        f.write(clippy_result.stderr)
    
    if clippy_result.returncode == 0:
        print("✓ Clippy found no warnings")
    else:
        print(f"⚠️  Clippy found warnings")
    
    print("\n" + "=" * 60)
    print("STEP 5: Parsing errors and generating instructions...")
    print("=" * 60)
    
    return (dylint_success, build_success, test_success, cargo_available)


def print_dispatch_summary(errors_by_agent, warnings_by_agent, has_test_failures, dylint_success=True, cargo_available=True):
    """Print summary of which agents need to be dispatched."""
    print("\n" + "=" * 60)
    print("DISPATCH THESE AGENTS:")
    print("=" * 60)
    
    if not dylint_success:
        print("⚠️  WARNING: dylint is skipped because dylint-report failed.")
        print("   Fix compilation errors first, then re-run prepare_agent_dispatch.py.")
        print()

    for agent_name, config in AGENTS.items():
        dylint_total, _, _ = get_effective_dylint_info(config, dylint_success)
        compilation_errors = len(errors_by_agent.get(agent_name, []))
        clippy_warnings = len(warnings_by_agent.get(agent_name, []))
        
        if dylint_total > 0 or compilation_errors > 0 or clippy_warnings > 0:
            # Determine which agent variant to use
            if compilation_errors > 0:
                agent_variant = f"{agent_name}-cargo"
                instruction_file = f"tmp/agent-instructions-{agent_name}.txt"
            else:
                agent_variant = agent_name
                instruction_file = f"tmp/agent-instructions-{agent_name}.txt"
            
            status = "✗" if compilation_errors > 0 else "✓"
            print(f"\n{status} Agent: {agent_variant}")
            print(f"  Dylint: {dylint_total} errors")
            print(f"  Compilation: {compilation_errors} errors")
            print(f"  Clippy: {clippy_warnings} warnings")
            print(f"  Command: Read and execute: {instruction_file}")
    
    if has_test_failures:
        print(f"\n⚠️  TEST FAILURES DETECTED")
        print(f"  All agents: Read tmp/test-failures.txt and fix failures in YOUR modules only")
    
    print("\n" + "=" * 60)
    print("NOTES:")
    print("=" * 60)
    
    if not cargo_available:
        print("🚨 CARGO IS DELIBERATELY TURNED OFF")
        print("- DO NOT use -cargo variants — cargo is not available for verification")
        print("- Focus on IMPLEMENTING, not verifying")
        print("- Verification will work when cargo is re-enabled")
    else:
        print("- Use '{agent}-cargo' variant for agents with compilation errors")
        print("- Use '{agent}' (regular) variant for agents with only dylint errors")


def main():
    """Main entry point."""
    print("=" * 60)
    print("AGENT DISPATCH PREPARATION")
    print("=" * 60)
    
    # Step 1: Run compilation, tests (if build succeeds), and clippy (if build succeeds)
    dylint_success, build_success, test_success, cargo_available = run_compilation_and_tests()
    
    # Step 2: Extract compilation errors from the generated files
    errors_by_agent = extract_compilation_errors()
    
    # Step 3: Extract clippy warnings (only if build succeeded)
    warnings_by_agent = {}
    if build_success:
        warnings_by_agent = extract_clippy_warnings()
    
    # Step 4: Extract test failures (only if tests ran)
    has_test_failures = False
    if build_success:
        has_test_failures = extract_test_failures()
    
    # Step 5: Generate agent instructions with all error context
    generate_agent_instructions(errors_by_agent, warnings_by_agent, has_test_failures, dylint_success, cargo_available)
    
    # Step 6: Print dispatch summary
    print_dispatch_summary(errors_by_agent, warnings_by_agent, has_test_failures, dylint_success, cargo_available)
    
    print("\n" + "=" * 60)
    print("READY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
