use axum::{extract::State, routing::post, Json, Router};
use ralph_gui::commands::{config, preferences, session, workspace, worktree};
use ralph_gui::state::new_shared_state;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tower_http::cors::{Any, CorsLayer};

type SharedState = Arc<std::sync::Mutex<ralph_gui::state::ActiveContext>>;

#[derive(Serialize, Deserialize)]
struct InvokePayload {
    cmd: String,
    args: Value,
}

#[derive(Serialize)]
struct InvokeResponse {
    ok: bool,
    data: Option<Value>,
    error: Option<String>,
}

impl InvokeResponse {
    fn ok(data: Value) -> Self {
        Self {
            ok: true,
            data: Some(data),
            error: None,
        }
    }

    fn err(error: String) -> Self {
        Self {
            ok: false,
            data: None,
            error: Some(error),
        }
    }
}

async fn invoke_handler(
    State(state): State<SharedState>,
    Json(payload): Json<InvokePayload>,
) -> Json<InvokeResponse> {
    let result: Result<Value, String> = match payload.cmd.as_str() {
        "get_sessions" => Ok(json!([
            {
                "run_id": "e2e-test-run-1",
                "status": "completed",
                "description": "Test run 1",
                "worktree": "main",
                "agent": "claude-sonnet",
                "phase": "commit",
                "started_at": "2024-01-15T10:00:00Z",
                "updated_at": "2024-01-15T10:30:00Z",
                "iterations": 5,
                "reviews": 2
            },
            {
                "run_id": "e2e-test-run-2",
                "status": "running",
                "description": "Test run 2",
                "worktree": "wt-1-test",
                "agent": "claude-sonnet",
                "phase": "development",
                "started_at": "2024-01-15T11:00:00Z",
                "updated_at": "2024-01-15T11:15:00Z",
                "iterations": 3,
                "reviews": 1
            }
        ])),
        "create_session" => {
            let request = payload
                .args
                .get("request")
                .and_then(|v| {
                    serde_json::from_value::<session::CreateSessionRequest>(v.clone()).ok()
                })
                .ok_or_else(|| "Invalid request".to_string());
            match request {
                Ok(req) => session::create_session(req)
                    .map(|v| serde_json::to_value(v).unwrap_or(json!({}))),
                Err(e) => Err(e),
            }
        }
        "get_session_detail" => {
            let run_id = payload
                .args
                .get("run_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            Ok(json!({
                "run_id": run_id,
                "status": "completed",
                "description": "Test session detail",
                "worktree": "main",
                "agent": "claude-sonnet",
                "phase": "commit",
                "started_at": "2024-01-15T10:00:00Z",
                "updated_at": "2024-01-15T10:30:00Z",
                "completed_at": "2024-01-15T10:30:00Z",
                "iterations": 5,
                "reviews": 2,
                "total_duration_seconds": 1800
            }))
        }
        "batch_resume_sessions" => {
            let run_ids = payload
                .args
                .get("run_ids")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            session::batch_resume_sessions_impl(&state, run_ids)
                .map(|v| serde_json::to_value(v).unwrap_or(json!({})))
        }
        "batch_cancel_sessions" => {
            let run_ids = payload
                .args
                .get("run_ids")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            session::batch_cancel_sessions_impl(&state, run_ids)
                .map(|v| serde_json::to_value(v).unwrap_or(json!({})))
        }
        "batch_delete_sessions" => {
            let run_ids = payload
                .args
                .get("run_ids")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            session::batch_delete_sessions_impl(&state, run_ids)
                .map(|v| serde_json::to_value(v).unwrap_or(json!({})))
        }
        "list_worktrees" => Ok(json!([
            {
                "name": "main",
                "path": "/tmp/e2e-test-workspace",
                "branch": "main",
                "is_main": true,
                "has_runs": false
            },
            {
                "name": "wt-1-test",
                "path": "/tmp/e2e-test-workspace/wt-1-test",
                "branch": "feature/test-branch",
                "is_main": false,
                "has_runs": false
            }
        ])),
        "create_worktree" => {
            let repo_path = payload
                .args
                .get("repo_path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let branch = payload
                .args
                .get("branch")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let name = payload
                .args
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let base_path = payload
                .args
                .get("base_path")
                .and_then(|v| v.as_str())
                .map(String::from);
            worktree::create_worktree(repo_path, branch, name, base_path)
                .map(|v| serde_json::to_value(v).unwrap_or(json!({})))
        }
        "switch_context" => {
            let repo_path = payload
                .args
                .get("repo_path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let worktree_path = payload
                .args
                .get("worktree_path")
                .and_then(|v| v.as_str())
                .map(String::from);
            worktree::switch_context_impl(&state, &repo_path, worktree_path.as_deref())
                .map(|_| json!(null))
        }
        "get_global_config" => Ok(json!({
            "version": "1.0",
            "agents": {
                "default": {
                    "model": "claude-sonnet-4-20250514",
                    "max_iterations": 10,
                    "max_reviews": 3
                }
            }
        })),
        "get_project_config" => Ok(json!(null)),
        "get_effective_config" => Ok(json!({
            "version": "1.0",
            "agents": {
                "default": {
                    "model": "claude-sonnet-4-20250514",
                    "max_iterations": 10,
                    "max_reviews": 3
                }
            },
            "drains": {
                "planning": "default",
                "development": "default",
                "analysis": "default",
                "review": "default",
                "fix": "default",
                "commit": "default"
            }
        })),
        "get_effective_config_with_sources" => Ok(json!({
            "version": "1.0",
            "agents": {
                "default": {
                    "model": "claude-sonnet-4-20250514",
                    "max_iterations": 10,
                    "max_reviews": 3
                }
            },
            "sources": {
                "version": "default",
                "agents": "default"
            }
        })),
        "get_effective_chains_config" => Ok(json!({
            "chains": {
                "default": {
                    "planning": ["planner"],
                    "development": ["developer"],
                    "analysis": ["analyzer"],
                    "review": ["reviewer"],
                    "fix": ["fixer"],
                    "commit": ["committer"]
                }
            }
        })),
        "save_global_config" => {
            let config_toml = payload
                .args
                .get("config_toml")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            config::save_global_config(config_toml).map(|_| json!(null))
        }
        "save_project_config" => {
            let repo_path = payload
                .args
                .get("repo_path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let config_toml = payload
                .args
                .get("config_toml")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            config::save_project_config(repo_path, config_toml).map(|_| json!(null))
        }
        "get_raw_global_config_toml" => config::get_raw_global_config_toml()
            .map(|v| serde_json::to_value(v).unwrap_or(json!(""))),
        "get_raw_project_config_toml" => {
            let repo_path = payload
                .args
                .get("repo_path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            config::get_raw_project_config_toml(repo_path)
                .map(|v| serde_json::to_value(v).unwrap_or(json!("")))
        }
        "validate_config_toml" => {
            let config_toml = payload
                .args
                .get("config_toml")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            config::validate_config_toml(config_toml)
                .map(|v| serde_json::to_value(v).unwrap_or(json!(null)))
        }
        "get_config_schema" => {
            config::get_config_schema().map(|v| serde_json::to_value(v).unwrap_or(json!([])))
        }
        "get_run_status" => Ok(json!({
            "has_active_runs": true,
            "active_runs": [
                {
                    "run_id": "e2e-test-run-2",
                    "status": "running",
                    "phase": "development",
                    "iteration": 3
                }
            ]
        })),
        "get_resumable_runs" => Ok(json!([
            {
                "run_id": "e2e-paused-run-1",
                "status": "paused",
                "description": "Paused test run",
                "worktree": "wt-1-test",
                "agent": "claude-sonnet",
                "phase": "development",
                "started_at": "2024-01-15T09:00:00Z",
                "updated_at": "2024-01-15T09:45:00Z",
                "iterations": 2,
                "reviews": 0,
                "checkpoint": {
                    "iteration": 2,
                    "phase": "development"
                }
            }
        ])),
        "get_run_detail" => {
            let run_id = payload
                .args
                .get("run_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            Ok(json!({
                "run_id": run_id,
                "status": "completed",
                "description": "Test run detail",
                "worktree": "main",
                "agent": "claude-sonnet",
                "phase": "commit",
                "started_at": "2024-01-15T10:00:00Z",
                "updated_at": "2024-01-15T10:30:00Z",
                "completed_at": "2024-01-15T10:30:00Z",
                "iterations": 5,
                "reviews": 2,
                "total_duration_seconds": 1800
            }))
        }
        "get_run_logs" => Ok(json!([
            {"timestamp": "2024-01-15T10:00:00Z", "level": "info", "message": "Starting run e2e-test-run-1"},
            {"timestamp": "2024-01-15T10:01:00Z", "level": "info", "message": "Phase: planning"},
            {"timestamp": "2024-01-15T10:02:00Z", "level": "info", "message": "Phase: development"},
            {"timestamp": "2024-01-15T10:05:00Z", "level": "info", "message": "Iteration 1 complete"},
            {"timestamp": "2024-01-15T10:10:00Z", "level": "info", "message": "Phase: review"},
            {"timestamp": "2024-01-15T10:15:00Z", "level": "info", "message": "Review complete"},
            {"timestamp": "2024-01-15T10:20:00Z", "level": "info", "message": "Phase: commit"},
            {"timestamp": "2024-01-15T10:30:00Z", "level": "info", "message": "Run completed successfully"}
        ])),
        "get_run_changes" => Ok(json!({
            "files": [
                {"path": "src/index.ts", "additions": 10, "deletions": 2},
                {"path": "src/feature.ts", "additions": 25, "deletions": 0}
            ],
            "total_additions": 35,
            "total_deletions": 2,
            "iteration": 1
        })),
        "cancel_run" => Ok(json!(null)),
        "get_iteration_history" => Ok(json!([
            {"iteration": 1, "phase": "development", "duration_seconds": 300, "files_changed": 5},
            {"iteration": 2, "phase": "development", "duration_seconds": 280, "files_changed": 8},
            {"iteration": 3, "phase": "development", "duration_seconds": 320, "files_changed": 3}
        ])),
        "get_review_history" => Ok(json!([
            {"review": 1, "findings": 2, "duration_seconds": 120, "passed": false},
            {"review": 2, "findings": 0, "duration_seconds": 90, "passed": true}
        ])),
        "open_in_file_manager" => Ok(json!(null)),
        "open_in_terminal" => Ok(json!(null)),
        "list_agent_profiles" => {
            let repo_path = payload
                .args
                .get("repo_path")
                .and_then(|v| v.as_str())
                .map(String::from);
            config::list_agent_profiles(repo_path)
                .map(|v| serde_json::to_value(v).unwrap_or(json!([])))
        }
        "get_agent_tools" => {
            config::get_agent_tools().map(|v| serde_json::to_value(v).unwrap_or(json!([])))
        }
        "test_agent_tool_connection" => {
            let name = payload
                .args
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            Ok(json!(format!("[E2E] {} connected", name)))
        }
        "check_tool_updates" => {
            config::check_tool_updates().map(|v| serde_json::to_value(v).unwrap_or(json!([])))
        }
        "refresh_tool_models" => {
            let name = payload
                .args
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            Ok(json!(vec![
                format!("model-1-{}", name),
                format!("model-2-{}", name)
            ]))
        }
        "install_agent_tool" => Ok(json!(null)),
        "open_tool_settings" => Ok(json!(null)),
        "get_ai_api_key" => Ok(json!("")),
        "save_ai_api_key" => Ok(json!(null)),
        "get_gui_preferences" => Ok(json!(preferences::GuiPreferences::default())),
        "save_gui_preferences" => Ok(json!(null)),
        "get_workspaces" => Ok(json!([workspace::WorkspaceEntry {
            id: "ws-e2e".to_string(),
            repo_path: "/tmp/e2e-test-workspace".to_string(),
            display_name: "E2E Workspace".to_string(),
            last_nav: String::new(),
            active_run_count: 0,
        }])),
        "open_workspace" => {
            let path = payload
                .args
                .get("path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            Ok(json!(workspace::WorkspaceEntry {
                id: "ws-e2e".to_string(),
                repo_path: path,
                display_name: "E2E Workspace".to_string(),
                last_nav: String::new(),
                active_run_count: 0,
            }))
        }
        "close_workspace" => Ok(json!(null)),
        "reorder_workspaces" => Ok(json!(null)),
        "set_workspace_nav" => Ok(json!(null)),
        "get_recent_workspaces" => Ok(json!([])),
        "update_workspace_run_count" => Ok(json!(null)),
        "launch_ralph_session" => Ok(json!("e2e-run-id")),
        "resume_ralph_session" => Ok(json!(null)),
        "read_prompt_file" => Ok(json!("")),
        "save_prompt_file" => Ok(json!(null)),
        "review_prompt_with_ai" => Ok(json!(
            ralph_gui::commands::session_prompt::PromptReviewResult {
                suggestions: vec!["Suggestion 1".to_string()],
                improved_prompt: None,
            }
        )),
        "assist_prompt_describe" => Ok(json!("Generated prompt from AI")),
        "assist_prompt_refine" => Ok(json!(ralph_gui::commands::session_prompt::PromptAnalysis {
            issues: vec![],
            suggestions: vec!["Refine suggestion".to_string()],
            quality_rating: 7,
            improved_prompt: Some("Refined prompt".to_string()),
        })),
        "get_planning_drain_agent" => Ok(json!(Option::<String>::None)),
        "list_templates" => Ok(json!([])),
        "save_template" => Ok(json!(null)),
        "delete_template" => Ok(json!(null)),
        "subscribe_run_logs" => Ok(json!(null)),
        "unsubscribe_run_logs" => Ok(json!(null)),
        "notify_run_status_change" => Ok(json!(null)),
        "open_directory_dialog" => Ok(json!(Option::<String>::None)),
        "get_resumable_runs_for_path" => Ok(json!([
            {
                "run_id": "e2e-paused-run-1",
                "status": "paused",
                "description": "Paused test run",
                "worktree": "wt-1-test",
                "agent": "claude-sonnet",
                "phase": "development",
                "started_at": "2024-01-15T09:00:00Z",
                "updated_at": "2024-01-15T09:45:00Z",
                "iterations": 2,
                "reviews": 0,
                "checkpoint": {
                    "iteration": 2,
                    "phase": "development"
                }
            }
        ])),
        _ => Err(format!("Unknown command: {}", payload.cmd)),
    };

    match result {
        Ok(data) => Json(InvokeResponse::ok(data)),
        Err(e) => Json(InvokeResponse::err(e)),
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let state = new_shared_state();

    if let Ok(repo_path) = std::env::var("E2E_REPO_PATH") {
        if let Ok(mut locked) = state.lock() {
            locked.repo_path = Some(std::path::PathBuf::from(&repo_path));
            locked
                .known_repos
                .push(std::path::PathBuf::from(&repo_path));
        }
    }
    if let Ok(worktree_path) = std::env::var("E2E_WORKTREE_PATH") {
        if let Ok(mut locked) = state.lock() {
            locked.worktree_path = Some(std::path::PathBuf::from(&worktree_path));
        }
    }

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/invoke", post(invoke_handler))
        .layer(cors)
        .with_state(state);

    let port = std::env::var("E2E_SERVER_PORT")
        .unwrap_or_else(|_| "3001".to_string())
        .parse::<u16>()
        .unwrap_or(3001);

    let addr = format!("127.0.0.1:{}", port);

    eprintln!("E2E test server listening on http://{}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
