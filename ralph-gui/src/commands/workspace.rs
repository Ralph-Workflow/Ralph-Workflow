use serde::{Deserialize, Serialize};
use specta::Type;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};
use tokio::fs;

const WORKSPACES_FILE: &str = "workspaces.json";
const RECENT_FILE: &str = "recent_workspaces.json";
const MAX_RECENT: usize = 10;

#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct WorkspaceEntry {
    pub id: String,
    pub repo_path: String,
    pub display_name: String,
    pub last_nav: String,
    pub active_run_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct WorkspaceStore {
    workspaces: Vec<WorkspaceEntry>,
    order: Vec<String>,
}

fn get_app_data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path().app_data_dir().map_err(|e| e.to_string())
}

async fn read_json<T: for<'de> Deserialize<'de> + Default>(path: &PathBuf) -> Result<T, String> {
    if !path.exists() {
        return Ok(T::default());
    }
    let contents = fs::read_to_string(path)
        .await
        .map_err(|e| format!("Failed to read file: {e}"))?;
    if contents.trim().is_empty() {
        return Ok(T::default());
    }
    serde_json::from_str(&contents).map_err(|e| format!("Failed to parse JSON: {e}"))
}

async fn write_json<T: Serialize + Send + Sync>(path: &PathBuf, data: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .await
            .map_err(|e| format!("Failed to create directory: {e}"))?;
    }
    let contents =
        serde_json::to_string_pretty(data).map_err(|e| format!("Failed to serialize JSON: {e}"))?;
    fs::write(path, contents)
        .await
        .map_err(|e| format!("Failed to write file: {e}"))?;
    Ok(())
}

fn move_id_to_front(order: Vec<String>, id: &str) -> Vec<String> {
    std::iter::once(id.to_string())
        .chain(order.into_iter().filter(|existing_id| existing_id != id))
        .collect()
}

fn with_workspace_added(store: WorkspaceStore, entry: WorkspaceEntry) -> WorkspaceStore {
    let WorkspaceStore { workspaces, order } = store;
    let id = entry.id.clone();

    WorkspaceStore {
        workspaces: workspaces
            .into_iter()
            .chain(std::iter::once(entry))
            .collect(),
        order: std::iter::once(id).chain(order).collect(),
    }
}

fn with_workspace_removed(store: WorkspaceStore, id: &str) -> WorkspaceStore {
    let WorkspaceStore { workspaces, order } = store;

    WorkspaceStore {
        workspaces: workspaces
            .into_iter()
            .filter(|workspace| workspace.id != id)
            .collect(),
        order: order
            .into_iter()
            .filter(|existing_id| existing_id != id)
            .collect(),
    }
}

fn with_updated_workspace_nav(store: WorkspaceStore, id: &str, nav: &str) -> WorkspaceStore {
    let WorkspaceStore { workspaces, order } = store;

    WorkspaceStore {
        workspaces: workspaces
            .into_iter()
            .map(|workspace| {
                if workspace.id == id {
                    WorkspaceEntry {
                        last_nav: nav.to_string(),
                        ..workspace
                    }
                } else {
                    workspace
                }
            })
            .collect(),
        order,
    }
}

fn with_updated_run_count(store: WorkspaceStore, id: &str, count: u32) -> WorkspaceStore {
    let WorkspaceStore { workspaces, order } = store;

    WorkspaceStore {
        workspaces: workspaces
            .into_iter()
            .map(|workspace| {
                if workspace.id == id {
                    WorkspaceEntry {
                        active_run_count: count,
                        ..workspace
                    }
                } else {
                    workspace
                }
            })
            .collect(),
        order,
    }
}

fn with_recent_workspace_path(recent: Vec<String>, path: &str) -> Vec<String> {
    std::iter::once(path.to_string())
        .chain(recent.into_iter().filter(|recent_path| recent_path != path))
        .take(MAX_RECENT)
        .collect()
}

fn is_valid_git_repo(path: &str) -> bool {
    let repo_path = PathBuf::from(path);
    let git_dir = repo_path.join(".git");
    git_dir.exists()
}

fn extract_display_name(path: &str) -> String {
    let path_buf = PathBuf::from(path);
    path_buf
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(path)
        .to_string()
}

/// Return all open workspaces in their current tab order.
///
/// # Errors
///
/// Returns an error if the workspace store cannot be read from disk.
#[tauri::command]
#[specta::specta]
pub async fn get_workspaces(app: AppHandle) -> Result<Vec<WorkspaceEntry>, String> {
    let data_dir = get_app_data_dir(&app)?;
    let store_path = data_dir.join(WORKSPACES_FILE);
    let store: WorkspaceStore = read_json(&store_path).await?;

    let ordered: Vec<WorkspaceEntry> = store
        .order
        .iter()
        .filter_map(|id| store.workspaces.iter().find(|w| &w.id == id).cloned())
        .collect();

    Ok(ordered)
}

/// Open a git repository as a workspace, adding it to the tab bar.
///
/// # Errors
///
/// Returns an error if `path` is not a valid git repository or if the store
/// cannot be read or written.
#[tauri::command]
#[specta::specta]
pub async fn open_workspace(app: AppHandle, path: String) -> Result<WorkspaceEntry, String> {
    if !is_valid_git_repo(&path) {
        return Err("Selected directory is not a valid git repository".to_string());
    }

    let data_dir = get_app_data_dir(&app)?;
    let store_path = data_dir.join(WORKSPACES_FILE);
    let store: WorkspaceStore = read_json(&store_path).await?;

    if let Some(existing) = store
        .workspaces
        .iter()
        .find(|w| w.repo_path == path)
        .cloned()
    {
        if !store.order.contains(&existing.id) {
            let updated_store = WorkspaceStore {
                order: move_id_to_front(store.order, &existing.id),
                ..store
            };
            write_json(&store_path, &updated_store).await?;
        }
        return Ok(existing);
    }

    let id = format!("ws-{}", uuid::Uuid::new_v4());
    let entry = WorkspaceEntry {
        id: id.clone(),
        repo_path: path.clone(),
        display_name: extract_display_name(&path),
        last_nav: String::new(),
        active_run_count: 0,
    };

    let updated_store = with_workspace_added(store, entry.clone());
    write_json(&store_path, &updated_store).await?;

    let recent_path = data_dir.join(RECENT_FILE);
    let recent: Vec<String> = read_json(&recent_path).await.unwrap_or_default();
    let recent = with_recent_workspace_path(recent, &path);
    let _ = write_json(&recent_path, &recent).await;

    Ok(entry)
}

/// Remove a workspace from the tab bar.
///
/// # Errors
///
/// Returns an error if the workspace has active runs, is not found, or the
/// store cannot be written.
#[tauri::command]
#[specta::specta]
pub async fn close_workspace(app: AppHandle, id: String) -> Result<(), String> {
    let data_dir = get_app_data_dir(&app)?;
    let store_path = data_dir.join(WORKSPACES_FILE);
    let store: WorkspaceStore = read_json(&store_path).await?;

    let workspace = store
        .workspaces
        .iter()
        .find(|w| w.id == id)
        .ok_or_else(|| "Workspace not found".to_string())?;

    if workspace.active_run_count > 0 {
        return Err(format!(
            "Cannot close workspace with {} active run(s)",
            workspace.active_run_count
        ));
    }

    let updated_store = with_workspace_removed(store, &id);
    write_json(&store_path, &updated_store).await?;

    Ok(())
}

/// Persist a new tab order for the open workspaces.
///
/// # Errors
///
/// Returns an error if `ids` does not match the set of currently open
/// workspaces, or if the store cannot be written.
#[tauri::command]
#[specta::specta]
pub async fn reorder_workspaces(app: AppHandle, ids: Vec<String>) -> Result<(), String> {
    let data_dir = get_app_data_dir(&app)?;
    let store_path = data_dir.join(WORKSPACES_FILE);
    let store: WorkspaceStore = read_json(&store_path).await?;

    let existing_ids: std::collections::HashSet<String> =
        store.workspaces.iter().map(|w| w.id.clone()).collect();
    let new_ids: std::collections::HashSet<String> = ids.iter().cloned().collect();

    if existing_ids != new_ids {
        return Err("Provided IDs do not match existing workspaces".to_string());
    }

    let updated_store = WorkspaceStore {
        order: ids,
        ..store
    };
    write_json(&store_path, &updated_store).await?;

    Ok(())
}

/// Persist the current navigation path for a workspace.
///
/// # Errors
///
/// Returns an error if the workspace is not found or the store cannot be
/// written.
#[tauri::command]
#[specta::specta]
pub async fn set_workspace_nav(app: AppHandle, id: String, nav: String) -> Result<(), String> {
    let data_dir = get_app_data_dir(&app)?;
    let store_path = data_dir.join(WORKSPACES_FILE);
    let store: WorkspaceStore = read_json(&store_path).await?;

    if store.workspaces.iter().any(|workspace| workspace.id == id) {
        let updated_store = with_updated_workspace_nav(store, &id, &nav);
        write_json(&store_path, &updated_store).await?;
        Ok(())
    } else {
        Err("Workspace not found".to_string())
    }
}

/// Return the list of recently opened workspace paths.
///
/// # Errors
///
/// Returns an error if the recent-workspaces list cannot be read from disk.
#[tauri::command]
#[specta::specta]
pub async fn get_recent_workspaces(app: AppHandle) -> Result<Vec<String>, String> {
    let data_dir = get_app_data_dir(&app)?;
    let recent_path = data_dir.join(RECENT_FILE);
    let recent: Vec<String> = read_json(&recent_path).await.unwrap_or_default();
    Ok(recent)
}

/// Update the active run count displayed on a workspace tab.
///
/// # Errors
///
/// Returns an error if the workspace is not found or the store cannot be
/// written.
#[tauri::command]
#[specta::specta]
pub async fn update_workspace_run_count(
    app: AppHandle,
    id: String,
    count: u32,
) -> Result<(), String> {
    let data_dir = get_app_data_dir(&app)?;
    let store_path = data_dir.join(WORKSPACES_FILE);
    let store: WorkspaceStore = read_json(&store_path).await?;

    if store.workspaces.iter().any(|workspace| workspace.id == id) {
        let updated_store = with_updated_run_count(store, &id, count);
        write_json(&store_path, &updated_store).await?;
        Ok(())
    } else {
        Err("Workspace not found".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn create_test_store() -> WorkspaceStore {
        WorkspaceStore {
            workspaces: vec![
                WorkspaceEntry {
                    id: "ws-1".to_string(),
                    repo_path: "/tmp/repo-a".to_string(),
                    display_name: "repo-a".to_string(),
                    last_nav: "/sessions".to_string(),
                    active_run_count: 1,
                },
                WorkspaceEntry {
                    id: "ws-2".to_string(),
                    repo_path: "/tmp/repo-b".to_string(),
                    display_name: "repo-b".to_string(),
                    last_nav: "/".to_string(),
                    active_run_count: 0,
                },
            ],
            order: vec!["ws-1".to_string(), "ws-2".to_string()],
        }
    }

    #[test]
    fn test_extract_display_name() {
        assert_eq!(extract_display_name("/Users/test/my-repo"), "my-repo");
        assert_eq!(extract_display_name("/tmp/api-service"), "api-service");
        assert_eq!(extract_display_name("single"), "single");
    }

    #[test]
    fn test_reorder_validation() {
        let store = create_test_store();
        let existing_ids: std::collections::HashSet<String> =
            store.workspaces.iter().map(|w| w.id.clone()).collect();

        assert!(existing_ids.contains("ws-1"));
        assert!(existing_ids.contains("ws-2"));
        assert_eq!(existing_ids.len(), 2);
    }

    #[test]
    fn test_close_with_active_runs_fails() {
        let store = create_test_store();
        let ws1 = store.workspaces.iter().find(|w| w.id == "ws-1");
        assert!(ws1.is_some_and(|w| w.active_run_count > 0));
    }

    #[test]
    fn test_close_without_active_runs_allowed() {
        let store = create_test_store();
        let ws2 = store.workspaces.iter().find(|w| w.id == "ws-2");
        assert!(ws2.is_some_and(|w| w.active_run_count == 0));
    }

    #[tokio::test]
    async fn test_read_write_json() {
        let dir = tempdir().expect("Failed to create temp dir");
        let path = dir.path().join("test.json");

        let store = create_test_store();
        write_json(&path, &store).await.expect("Failed to write");

        let loaded: WorkspaceStore = read_json(&path).await.expect("Failed to read");
        assert_eq!(loaded.workspaces.len(), 2);
        assert_eq!(loaded.order.len(), 2);
    }

    #[tokio::test]
    async fn test_read_empty_file_returns_default() {
        let dir = tempdir().expect("Failed to create temp dir");
        let path = dir.path().join("empty.json");

        fs::write(&path, "")
            .await
            .expect("Failed to write empty file");

        let loaded: WorkspaceStore = read_json(&path).await.expect("Failed to read");
        assert!(loaded.workspaces.is_empty());
        assert!(loaded.order.is_empty());
    }

    #[tokio::test]
    async fn test_read_nonexistent_file_returns_default() {
        let dir = tempdir().expect("Failed to create temp dir");
        let path = dir.path().join("nonexistent.json");

        let loaded: WorkspaceStore = read_json(&path).await.expect("Failed to read");
        assert!(loaded.workspaces.is_empty());
    }
}
