//! Agent phase state - process-global state for git wrapper enforcement.

pub static AGENT_PHASE_HOOKS_DIR: &std::sync::Mutex<Option<std::path::PathBuf>> =
    &super::runtime::AGENT_PHASE_HOOKS_DIR;
pub static AGENT_PHASE_RALPH_DIR: &std::sync::Mutex<Option<std::path::PathBuf>> =
    &super::runtime::AGENT_PHASE_RALPH_DIR;
pub static AGENT_PHASE_REPO_ROOT: &std::sync::Mutex<Option<std::path::PathBuf>> =
    &super::runtime::AGENT_PHASE_REPO_ROOT;
