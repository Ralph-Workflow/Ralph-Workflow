// Pipeline Event Loop Execution
//
// This module contains the core pipeline execution logic using the reducer-based event loop.
//
// Architecture:
//
// The pipeline follows the reducer pattern:
// State → Orchestrator → Effect → Handler → Event → Reducer → State
//
// Execution Flow:
//
// 1. Resume Handling: Check for existing checkpoint and offer interactive resume
// 2. State Initialization: Create or restore pipeline state from checkpoint
// 3. Context Setup: Configure interrupt handlers, git helpers, monitoring
// 4. Event Loop: Run the reducer event loop until completion
// 5. Finalization: Write completion checkpoint, cleanup, restore PROMPT.md
//
// Checkpoint and Resume:
//
// - Fresh run: Creates new `RunContext` with UUID, initializes state
// - Resume: Restores state from checkpoint, applies config overrides, restores env vars
// - Completion: Saves final checkpoint with Complete phase for idempotent resume
//
// Event Loop Result Handling:
//
// The event loop returns `EventLoopResult`:
// - `completed=true`: Normal completion (Complete or Interrupted phase)
// - `completed=false`: Abnormal exit (bug in event loop or reducer)
//
// When `completed=false`, we write a defensive completion marker to ensure
// external orchestrators can detect termination.

include!("execution_core_resume.rs");
include!("execution_core_phases.rs");
include!("execution_core_finish.rs");

use crate::app::detection::detect_project_stack;
use crate::app::runner::setup_helpers::{
    defer_clear_interrupt_context, setup_interrupt_context_for_pipeline,
    update_interrupt_context_from_phase,
};
use crate::banner::print_welcome_banner;
use crate::checkpoint::PipelinePhase;

// `run_pipeline_with_default_handler` lives in boundary.rs because it needs mutable
// process-level handles (git helpers, agent-phase guard, timer, phase context) that
// demand &mut bindings. Keeping it in a boundary-path file is architecturally correct:
// this function IS the IMPURE→PURE→IMPURE seam — it gathers OS-level capabilities,
// drives the pure event loop, then finalises effects. See boundary.rs for details.
include!("boundary.rs");
