//! Pipeline finalization I/O tests.
//!
//! These tests perform real git I/O and must remain in a boundary module.

use crate::config::Config;
use crate::finalization::{finalize_pipeline, FinalizeContext};
use crate::git_helpers::{
    agent_phase_test_lock, clear_agent_phase_global_state, get_agent_phase_paths_for_test,
    set_agent_phase_paths_for_test, GitHelpers,
};
use crate::logger::{Colors, Logger};
use crate::pipeline::{AgentPhaseGuard, Timer};
use crate::reducer::state::PipelineState;
use crate::workspace::WorkspaceFs;

#[test]
fn test_finalize_pipeline_keeps_global_cleanup_state_when_guard_stays_armed() {
    let _lock = agent_phase_test_lock().lock().unwrap();
    let tempdir = tempfile::tempdir().unwrap();
    let repo_root = tempdir.path();
    let _repo = git2::Repository::init(repo_root).unwrap();

    let ralph_dir = repo_root.join(".git/ralph");
    std::fs::create_dir_all(&ralph_dir).unwrap();
    std::fs::write(ralph_dir.join("quarantine.bin"), "keep").unwrap();
    let hooks_dir = repo_root.join(".git/hooks");
    std::fs::create_dir_all(&hooks_dir).unwrap();

    set_agent_phase_paths_for_test(
        Some(repo_root.to_path_buf()),
        Some(ralph_dir.clone()),
        Some(hooks_dir.clone()),
    );

    let workspace = WorkspaceFs::new(repo_root.to_path_buf());
    let logger = Logger::new(Colors::with_enabled(false));
    let config = Config::test_default();
    let timer = Timer::new();
    let final_state = PipelineState::initial(1, 1);
    let mut helpers = GitHelpers::default();
    let mut guard = AgentPhaseGuard::new(&mut helpers, &logger, &workspace);

    finalize_pipeline(
        &mut guard,
        FinalizeContext {
            logger: &logger,
            colors: Colors::with_enabled(false),
            config: &config,
            timer: &timer,
            workspace: &workspace,
        },
        &final_state,
        None,
    );

    let actual = get_agent_phase_paths_for_test();
    assert_eq!(
        actual,
        (
            Some(repo_root.to_path_buf()),
            Some(ralph_dir),
            Some(hooks_dir),
        ),
        "finalize_pipeline must leave fallback cleanup paths intact when cleanup fails and the guard remains armed"
    );

    drop(guard);
    clear_agent_phase_global_state();
}
