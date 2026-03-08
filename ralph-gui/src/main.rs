#![deny(unsafe_code)]
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use ralph_gui::commands::{
    config, run_management, session, session_launch, session_prompt, worktree,
};
use ralph_gui::state::new_shared_state;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .manage(new_shared_state())
        .invoke_handler(tauri::generate_handler![
            session::get_sessions,
            session::create_session,
            session::get_session_detail,
            worktree::list_worktrees,
            worktree::create_worktree,
            worktree::switch_context,
            config::get_global_config,
            config::get_project_config,
            config::get_effective_config,
            config::save_global_config,
            config::save_project_config,
            config::get_raw_global_config_toml,
            config::get_raw_project_config_toml,
            config::list_agent_profiles,
            config::get_ai_api_key,
            config::save_ai_api_key,
            config::validate_config_toml,
            run_management::get_run_status,
            run_management::get_resumable_runs,
            run_management::get_run_detail,
            run_management::get_run_logs,
            run_management::notify_run_status_change,
            session_prompt::read_prompt_file,
            session_prompt::save_prompt_file,
            session_prompt::review_prompt_with_ai,
            session_launch::launch_ralph_session,
            session_launch::resume_ralph_session,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
