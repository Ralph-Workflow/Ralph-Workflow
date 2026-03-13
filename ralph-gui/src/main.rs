#![deny(unsafe_code)]
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use ralph_gui::commands::{
    config, preferences, run_management, session, session_launch, session_prompt, workspace,
    worktree,
};
use ralph_gui::state::new_shared_state;
use tauri_specta::collect_commands;

/// Build and return the tauri-specta builder with all commands and types registered.
fn build_specta_builder() -> tauri_specta::Builder<tauri::Wry> {
    let builder = tauri_specta::Builder::<tauri::Wry>::new().commands(collect_commands![
        // Session commands
        session::get_sessions,
        session::create_session,
        session::get_session_detail,
        session::batch_resume_sessions,
        session::batch_cancel_sessions,
        session::batch_delete_sessions,
        // Worktree commands
        worktree::list_worktrees,
        worktree::create_worktree,
        worktree::switch_context,
        // Config commands
        config::get_global_config,
        config::get_project_config,
        config::get_effective_config,
        config::get_effective_config_with_sources,
        config::save_global_config,
        config::save_project_config,
        config::get_raw_global_config_toml,
        config::get_raw_project_config_toml,
        config::list_agent_profiles,
        config::get_ai_api_key,
        config::save_ai_api_key,
        config::validate_config_toml,
        config::get_agent_tools,
        config::test_agent_tool_connection,
        config::get_config_schema,
        config::check_tool_updates,
        config::install_agent_tool,
        config::open_tool_settings,
        config::refresh_tool_models,
        // Run management commands
        run_management::get_run_status,
        run_management::get_run_detail,
        run_management::get_run_logs,
        run_management::notify_run_status_change,
        run_management::get_resumable_runs,
        run_management::subscribe_run_logs,
        run_management::unsubscribe_run_logs,
        run_management::get_run_changes,
        run_management::cancel_run,
        run_management::get_iteration_history,
        run_management::get_review_history,
        // Session prompt commands
        session_prompt::read_prompt_file,
        session_prompt::save_prompt_file,
        session_prompt::review_prompt_with_ai,
        session_prompt::assist_prompt_describe,
        session_prompt::assist_prompt_refine,
        session_prompt::get_planning_drain_agent,
        session_prompt::list_templates,
        session_prompt::save_template,
        session_prompt::delete_template,
        // Session launch commands
        session_launch::launch_ralph_session,
        session_launch::resume_ralph_session,
        // Preferences commands
        preferences::get_gui_preferences,
        preferences::save_gui_preferences,
        // Workspace commands
        workspace::get_workspaces,
        workspace::open_workspace,
        workspace::close_workspace,
        workspace::reorder_workspaces,
        workspace::set_workspace_nav,
        workspace::get_recent_workspaces,
        workspace::update_workspace_run_count,
    ]);

    register_extra_types(builder)
}

/// Register additional Specta types not directly attached to commands.
fn register_extra_types(
    builder: tauri_specta::Builder<tauri::Wry>,
) -> tauri_specta::Builder<tauri::Wry> {
    builder
        .typ::<ralph_gui::commands::config::ConfigView>()
        .typ::<ralph_gui::commands::config::ConfigSource>()
        .typ::<ralph_gui::commands::config::ConfigFieldWithSource>()
        .typ::<ralph_gui::commands::config::EffectiveConfigWithSources>()
        .typ::<ralph_gui::commands::config::AgentProfile>()
        .typ::<ralph_gui::commands::config::AgentToolInfo>()
        .typ::<ralph_gui::commands::config::ConfigFieldSchema>()
        .typ::<ralph_gui::commands::config::ConfigSection>()
        .typ::<ralph_gui::commands::config::ToolUpdateInfo>()
        .typ::<ralph_gui::commands::preferences::GuiPreferences>()
        .typ::<ralph_gui::commands::run_management::RunStatus>()
        .typ::<ralph_gui::commands::run_management::RunDetail>()
        .typ::<ralph_gui::commands::run_management::RunLogLine>()
        .typ::<ralph_gui::commands::run_management::FileDiff>()
        .typ::<ralph_gui::commands::run_management::RunChanges>()
        .typ::<ralph_gui::commands::run_management::IterationSummary>()
        .typ::<ralph_gui::commands::run_management::IterationStatus>()
        .typ::<ralph_gui::commands::run_management::ReviewSummary>()
        .typ::<ralph_gui::commands::run_management::ReviewStatus>()
        .typ::<ralph_gui::commands::run_management::PhaseDuration>()
        .typ::<ralph_gui::commands::run_management::DegradedInfo>()
        .typ::<ralph_gui::commands::session::SessionSummary>()
        .typ::<ralph_gui::commands::session::CreateSessionRequest>()
        .typ::<ralph_gui::commands::session::BatchOperationResult>()
        .typ::<ralph_gui::commands::session_launch::LaunchSessionArgs>()
        .typ::<ralph_gui::commands::session_prompt::PromptReviewResult>()
        .typ::<ralph_gui::commands::session_prompt::TemplateInfo>()
        .typ::<ralph_gui::commands::session_prompt::PromptAssistantMessage>()
        .typ::<ralph_gui::commands::session_prompt::PromptAnalysis>()
        .typ::<ralph_gui::commands::worktree::WorktreeInfo>()
        .typ::<ralph_gui::commands::worktree::CreateWorktreeResult>()
        .typ::<ralph_gui::commands::workspace::WorkspaceEntry>()
}

fn main() {
    let builder = build_specta_builder();

    // Export TypeScript bindings in debug builds for frontend type safety
    #[cfg(debug_assertions)]
    {
        let output_path = std::path::PathBuf::from("ui/src/app/types/generated.ts");

        builder
            .export(
                specta_typescript::Typescript::default()
                    .header("// Auto-Generated - DO NOT EDIT\n// Generated by tauri-specta from Rust commands in ralph-gui/src/commands/\n\n"),
                &output_path,
            )
            .expect("Failed to export TypeScript bindings");
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .manage(new_shared_state())
        .invoke_handler(builder.invoke_handler())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
