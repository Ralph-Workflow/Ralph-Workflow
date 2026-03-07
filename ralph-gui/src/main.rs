#![deny(unsafe_code)]
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use ralph_gui::commands::{config, run_management, session, worktree};
use ralph_gui::state::new_shared_state;

fn main() {
    tauri::Builder::default()
        .manage(new_shared_state())
        .invoke_handler(tauri::generate_handler![
            session::get_sessions,
            session::create_session,
            session::get_session_detail,
            session::resume_session,
            worktree::list_worktrees,
            worktree::create_worktree,
            worktree::switch_context,
            config::get_global_config,
            config::get_project_config,
            config::get_effective_config,
            config::save_global_config,
            config::save_project_config,
            run_management::get_run_status,
            run_management::get_resumable_runs,
            run_management::get_run_detail,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
