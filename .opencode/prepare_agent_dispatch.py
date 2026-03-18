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
AGENTS = {
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
        "total_dylint": 51
    },
    "workflow-cloud": {
        "write": "ralph-workflow/src/cloud/**",
        "search": "tmp/**, ralph-workflow/src/cloud/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/cloud/*, etc.",
        "modules": ["ralph-workflow/src/cloud/**"],
        "dylint_files": ["tmp/dylint-cloud.txt (6)"],
        "total_dylint": 6
    },
    "workflow-app": {
        "write": "ralph-workflow/src/app/**",
        "search": "tmp/**, ralph-workflow/src/app/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/app/*, etc.",
        "modules": ["ralph-workflow/src/app/**"],
        "dylint_files": ["tmp/dylint-app.txt (52)"],
        "total_dylint": 52
    },
    "workflow-config": {
        "write": "ralph-workflow/src/config/**, ralph-workflow/src/cli/**",
        "search": "tmp/**, ralph-workflow/src/{config,cli}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/config/*, etc.",
        "modules": ["ralph-workflow/src/config/**", "ralph-workflow/src/cli/**"],
        "dylint_files": ["tmp/dylint-config.txt (16)", "tmp/dylint-cli.txt (41)"],
        "total_dylint": 57
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
        "total_dylint": 234
    },
    "workflow-json": {
        "write": "ralph-workflow/src/json_parser/**",
        "search": "tmp/**, ralph-workflow/src/json_parser/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/json_parser/*, etc.",
        "modules": ["ralph-workflow/src/json_parser/**"],
        "dylint_files": ["tmp/dylint-json_parser.txt (162)"],
        "total_dylint": 162
    },
    "workflow-prompts": {
        "write": "ralph-workflow/src/guidelines/**, ralph-workflow/src/prompts/**",
        "search": "tmp/**, ralph-workflow/src/{guidelines,prompts}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/prompts/*, etc.",
        "modules": ["ralph-workflow/src/guidelines/**", "ralph-workflow/src/prompts/**"],
        "dylint_files": ["tmp/dylint-prompts.txt (96)", "tmp/dylint-guidelines.txt (32)"],
        "total_dylint": 128
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
        "total_dylint": 47
    },
    "workflow-agents": {
        "write": "ralph-workflow/src/agents/**",
        "search": "tmp/**, ralph-workflow/src/agents/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/agents/*, etc.",
        "modules": ["ralph-workflow/src/agents/**"],
        "dylint_files": ["tmp/dylint-agents.txt (8)"],
        "total_dylint": 8
    },
    "workflow-logging": {
        "write": "ralph-workflow/src/logging/**, ralph-workflow/src/logger/**",
        "search": "tmp/**, ralph-workflow/src/{logging,logger}/**, docs/**",
        "bash": "ls, cat, head, tail, grep, cargo check/clippy/test, mkdir -p ralph-workflow/src/logging/*, etc.",
        "modules": ["ralph-workflow/src/logging/**", "ralph-workflow/src/logger/**"],
        "dylint_files": ["tmp/dylint-logging.txt (2)", "tmp/dylint-logger.txt (2)"],
        "total_dylint": 4
    }
}


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


def generate_agent_instructions(errors_by_agent):
    """Generate agent instruction files with compilation error context."""
    print("\nStep 2: Generating agent instruction files...")
    
    template = """# Agent: {agent_name}

⚠️ **RE-READ THIS FILE BEFORE EVERY ACTION. DO NOT WORK FROM MEMORY.**

## Your Goal

Fix ALL {dylint_total} dylint errors in your modules. Keep working until done.

## Your Modules
{modules}

## Your Errors

### Dylint Issues: {dylint_total}
Files to read: {dylint_files}

### Compilation Errors: {compilation_count}
{compilation_list}
{compilation_file_ref}

## Instructions (RE-READ THESE BEFORE EACH FIX)

### STEP 1: Read Style Guide (if you haven't yet)
```bash
cat docs/code-style/functional-transformations.md
cat docs/code-style/boundaries.md
```

The style guide is the source of truth. Dylint just enforces it.

### STEP 2: Read Your Error Files (RE-READ to see what's left)

Read each dylint file listed above. Read your compilation errors.

### STEP 3: Fix ONE Issue (then loop back to Step 2)

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
# Example: I/O boundary for json_parser
mkdir -p ralph-workflow/src/json_parser/io
echo "pub mod streaming;" > ralph-workflow/src/json_parser/io/mod.rs
# Update json_parser/mod.rs to add: pub mod io;
```

Valid boundary names: `io/`, `runtime/`, `ffi/`, `boundary/`, `executor/`

### STEP 4: Verify Your Work
```bash
.opencode/verify_agent_work.sh
```

This runs clippy to check style guide compliance.

### STEP 5: Report

```
FILES CHANGED: [list]
DYLINT FIXED: X/{dylint_total}
CLIPPY: pass/fail
```

## Important Rules

- **Style guide > compilation**: If your refactoring breaks compilation, that's OK. Report it.
- **Don't re-run cargo build/test**: Results already provided above
- **Only edit your modules**: {write}
- **Do hard work first**: Don't avoid complex refactors
- **Boundary modules are real work**: Create them when legitimately needed, not as escape hatch

The orchestrator will handle compilation fixes separately if needed. Your job is style guide compliance."""

    for agent_name, config in AGENTS.items():
        modules_str = '\n'.join(f"- {m}" for m in config['modules'])
        dylint_files_str = ', '.join(config['dylint_files'])
        
        # Get compilation error info for this agent
        if agent_name in errors_by_agent:
            error_count = len(errors_by_agent[agent_name])
            compilation_list = f"See detailed errors in: `tmp/compilation-errors/{agent_name}.txt`"
            compilation_file_ref = f"\n**Full error details:** Read `tmp/compilation-errors/{agent_name}.txt` for complete error messages and context."
        else:
            error_count = 0
            compilation_list = "- No compilation errors (focus on dylint)"
            compilation_file_ref = ""
        
        content = template.format(
            agent_name=agent_name,
            write=config['write'],
            search=config['search'],
            bash=config['bash'],
            modules=modules_str,
            compilation_count=error_count,
            compilation_list=compilation_list,
            compilation_file_ref=compilation_file_ref,
            dylint_files=dylint_files_str,
            dylint_total=config['total_dylint']
        )
        
        filename = f"tmp/agent-instructions-{agent_name}.txt"
        with open(filename, 'w') as f:
            f.write(content)
        
        print(f"  Created {filename}")
    
    print(f"\nGenerated {len(AGENTS)} agent instruction files!")


def run_compilation_and_tests():
    """
    Run cargo build and cargo test to compile both app and test code.
    This runs BEFORE parsing so we have fresh output to parse.
    """
    import subprocess
    
    print("\n" + "=" * 60)
    print("STEP 1: Running dylint report...")
    print("=" * 60)
    result = subprocess.run(
        ["cargo", "xtask", "dylint-report"],
        capture_output=False,
        text=True
    )
    if result.returncode != 0:
        print("⚠️  Warning: dylint-report failed, but continuing...")
    
    print("\n" + "=" * 60)
    print("STEP 2: Compiling app code (cargo build)...")
    print("=" * 60)
    result = subprocess.run(
        ["cargo", "build", "-p", "ralph-workflow", "--lib"],
        capture_output=True,
        text=True
    )
    
    # Write build output to tmp/build-current.txt
    Path("tmp").mkdir(exist_ok=True)
    with open("tmp/build-current.txt", "w") as f:
        f.write(result.stdout)
        f.write(result.stderr)
    
    if result.returncode == 0:
        print("✓ App compilation successful")
    else:
        print(f"✗ App compilation failed with {result.returncode} errors")
    
    print("\n" + "=" * 60)
    print("STEP 3: Compiling tests and running them (cargo test)...")
    print("=" * 60)
    result = subprocess.run(
        ["cargo", "test", "-p", "ralph-workflow", "--lib"],
        capture_output=True,
        text=True
    )
    
    # Write test output to tmp/test-output.txt
    with open("tmp/test-output.txt", "w") as f:
        f.write(result.stdout)
        f.write(result.stderr)
    
    if result.returncode == 0:
        print("✓ Tests compiled and passed")
    else:
        print(f"✗ Tests failed (compilation or execution)")
    
    print("\n" + "=" * 60)
    print("STEP 4: Parsing compilation errors and generating instructions...")
    print("=" * 60)


def print_dispatch_summary(errors_by_agent):
    """Print summary of which agents need to be dispatched."""
    print("\n" + "=" * 60)
    print("DISPATCH THESE AGENTS:")
    print("=" * 60)
    
    for agent_name, config in AGENTS.items():
        dylint_total = config['total_dylint']
        compilation_errors = len(errors_by_agent.get(agent_name, []))
        
        if dylint_total > 0 or compilation_errors > 0:
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
            print(f"  Command: Read and execute: {instruction_file}")
    
    print("\n" + "=" * 60)
    print("NOTES:")
    print("=" * 60)
    print("- Use '{agent}-cargo' variant for agents with compilation errors")
    print("- Use '{agent}' (regular) variant for agents with only dylint errors")
    print("- Instruction file is always: tmp/agent-instructions-{agent}.txt (without -cargo)")


def main():
    """Main entry point."""
    print("=" * 60)
    print("AGENT DISPATCH PREPARATION")
    print("=" * 60)
    
    # Step 1: Run compilation and tests
    run_compilation_and_tests()
    
    # Step 2: Extract compilation errors from the generated files
    errors_by_agent = extract_compilation_errors()
    
    # Step 3: Generate agent instructions with error context
    generate_agent_instructions(errors_by_agent)
    
    # Step 4: Print dispatch summary
    print_dispatch_summary(errors_by_agent)
    
    print("\n" + "=" * 60)
    print("READY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
