use serde::{Deserialize, Serialize};
use std::path::Path;

/// Information about a git worktree.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorktreeInfo {
    pub path: String,
    pub branch: String,
    pub name: String,
    pub has_active_run: bool,
    pub is_main: bool,
}

/// Result of creating a new worktree.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateWorktreeResult {
    pub worktree: WorktreeInfo,
}

/// List all worktrees for a repository.
///
/// # Errors
///
/// Returns an error string if the path is not a git repository or cannot be read.
#[tauri::command]
pub fn list_worktrees(repo_path: String) -> Result<Vec<WorktreeInfo>, String> {
    let repo_path_buf = std::path::PathBuf::from(repo_path);
    let repo = git2::Repository::open(&repo_path_buf)
        .map_err(|e| format!("Not a valid git repository: {e}"))?;

    let mut worktrees = Vec::new();

    // Add the main worktree first
    let main_path = repo.workdir().map_or_else(
        || repo_path_buf.to_string_lossy().into_owned(),
        |p| p.to_string_lossy().to_string(),
    );

    let main_branch = get_current_branch(&repo).unwrap_or_else(|| "HEAD".to_string());

    // Check if main worktree has an active run
    let main_has_run = check_has_active_run(&main_path);

    worktrees.push(WorktreeInfo {
        path: main_path,
        branch: main_branch,
        name: "main".to_string(),
        has_active_run: main_has_run,
        is_main: true,
    });

    // List linked worktrees
    let linked = repo
        .worktrees()
        .map_err(|e| format!("Failed to list worktrees: {e}"))?;

    for name in linked.iter().flatten() {
        if let Ok(wt) = repo.find_worktree(name) {
            let path = wt.path();
            let wt_path = path.to_string_lossy().to_string();
            let has_run = check_has_active_run(&wt_path);

            // Try to get branch from the worktree's HEAD
            let branch = get_worktree_branch(path).unwrap_or_else(|| "unknown".to_string());

            worktrees.push(WorktreeInfo {
                path: wt_path,
                branch,
                name: name.to_string(),
                has_active_run: has_run,
                is_main: false,
            });
        }
    }

    Ok(worktrees)
}

/// Create a new git worktree with the given branch and name.
///
/// The name must match the convention `wt-[number]-[name]`.
///
/// # Errors
///
/// Returns an error if the name does not match the convention, the repo is invalid,
/// or git worktree creation fails.
#[tauri::command]
pub fn create_worktree(
    repo_path: String,
    branch: String,
    name: String,
    base_path: Option<String>,
) -> Result<CreateWorktreeResult, String> {
    // Validate naming convention: wt-<number>-<name>
    let naming_re =
        regex::Regex::new(r"^wt-\d+-[\w-]+$").map_err(|e| format!("Regex error: {e}"))?;
    if !naming_re.is_match(&name) {
        return Err(format!(
            "Worktree name '{name}' does not match convention 'wt-[number]-[name]'"
        ));
    }

    let repo_path_buf = std::path::PathBuf::from(repo_path);
    let repo = git2::Repository::open(&repo_path_buf)
        .map_err(|e| format!("Not a valid git repository: {e}"))?;

    // Determine worktree directory location — convert base_path to PathBuf to consume it
    let base_path_owned = base_path.map(std::path::PathBuf::from);
    let parent_dir = base_path_owned
        .as_deref()
        .and_then(|p| p.parent())
        .or_else(|| repo.workdir().and_then(|p| p.parent()));

    let wt_path = parent_dir.map_or_else(
        || {
            // Fallback: sibling of repo root
            repo_path_buf
                .parent()
                .map_or_else(|| std::path::PathBuf::from(&name), |p| p.join(&name))
        },
        |parent| parent.join(&name),
    );

    // Create the worktree using git2
    // We use `git2::Repository::worktree()` which adds a linked worktree
    let wt_path_str = wt_path.to_string_lossy().to_string();

    // Check if branch exists; if not, create it from HEAD
    let branch_ref = format!("refs/heads/{branch}");
    let commit = repo
        .head()
        .and_then(|h| h.peel_to_commit())
        .map_err(|e| format!("Failed to get HEAD commit: {e}"))?;

    // Create branch if it doesn't exist
    if repo.find_branch(&branch, git2::BranchType::Local).is_err() {
        repo.branch(&branch, &commit, false)
            .map_err(|e| format!("Failed to create branch '{branch}': {e}"))?;
    }

    let reference = repo
        .find_reference(&branch_ref)
        .map_err(|e| format!("Failed to find branch reference: {e}"))?;

    let mut opts = git2::WorktreeAddOptions::new();
    opts.reference(Some(&reference));

    repo.worktree(&name, &wt_path, Some(&opts))
        .map_err(|e| format!("Failed to create worktree: {e}"))?;

    Ok(CreateWorktreeResult {
        worktree: WorktreeInfo {
            path: wt_path_str,
            branch,
            name,
            has_active_run: false,
            is_main: false,
        },
    })
}

/// Switch the active GUI context to a different worktree.
///
/// This is a state mutation only — no git operations are performed.
///
/// # Errors
///
/// Returns an error if the state lock cannot be acquired.
#[tauri::command]
pub fn switch_context(
    repo_path: String,
    worktree_path: Option<String>,
    state: tauri::State<'_, crate::state::SharedState>,
) -> Result<(), String> {
    let mut locked = state
        .lock()
        .map_err(|e| format!("Failed to acquire state lock: {e}"))?;
    let repo_pb = std::path::PathBuf::from(repo_path);
    if !locked.known_repos.contains(&repo_pb) {
        locked.known_repos.push(repo_pb.clone());
    }
    locked.repo_path = Some(repo_pb);
    locked.worktree_path = worktree_path.map(std::path::PathBuf::from);
    drop(locked);
    Ok(())
}

fn get_current_branch(repo: &git2::Repository) -> Option<String> {
    repo.head()
        .ok()
        .filter(git2::Reference::is_branch)
        .and_then(|h| h.shorthand().map(std::borrow::ToOwned::to_owned))
}

fn get_worktree_branch(wt_path: &Path) -> Option<String> {
    git2::Repository::open(wt_path)
        .ok()
        .and_then(|r| get_current_branch(&r))
}

fn check_has_active_run(path: &str) -> bool {
    let lock_path = Path::new(path).join(".agent").join("tmp").join("run.lock");
    lock_path.exists()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn init_git_repo(dir: &TempDir) -> git2::Repository {
        let repo = git2::Repository::init(dir.path()).unwrap();
        // Create an initial commit so HEAD is valid
        let sig = git2::Signature::now("Test", "test@test.com").unwrap();
        let tree_id = {
            let mut index = repo.index().unwrap();
            index.write_tree().unwrap()
        };
        {
            let tree = repo.find_tree(tree_id).unwrap();
            repo.commit(Some("HEAD"), &sig, &sig, "Initial commit", &tree, &[])
                .unwrap();
        }
        repo
    }

    #[test]
    fn test_list_worktrees_errors_for_non_git_directory() {
        let dir = TempDir::new().unwrap();
        let result = list_worktrees(dir.path().to_string_lossy().to_string());
        assert!(result.is_err());
        assert!(
            result.unwrap_err().contains("Not a valid git repository"),
            "Expected git error"
        );
    }

    #[test]
    fn test_list_worktrees_returns_main_for_git_repo() {
        let dir = TempDir::new().unwrap();
        init_git_repo(&dir);
        let result = list_worktrees(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok(), "Expected Ok but got: {result:?}");
        let worktrees = result.unwrap();
        assert!(!worktrees.is_empty(), "Expected at least main worktree");
        assert!(
            worktrees.iter().any(|wt| wt.is_main),
            "Expected a main worktree entry"
        );
    }

    #[test]
    fn test_create_worktree_validates_naming_convention() {
        let dir = TempDir::new().unwrap();
        init_git_repo(&dir);
        let result = create_worktree(
            dir.path().to_string_lossy().to_string(),
            "feature-branch".to_string(),
            "invalid-name".to_string(),
            None,
        );
        assert!(result.is_err());
        assert!(
            result.unwrap_err().contains("does not match convention"),
            "Expected naming convention error"
        );
    }

    #[test]
    fn test_create_worktree_rejects_missing_number() {
        let dir = TempDir::new().unwrap();
        init_git_repo(&dir);
        let result = create_worktree(
            dir.path().to_string_lossy().to_string(),
            "feature-branch".to_string(),
            "wt-feature".to_string(),
            None,
        );
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("does not match convention"));
    }

    #[test]
    fn test_create_worktree_valid_name_format() {
        let dir = TempDir::new().unwrap();
        init_git_repo(&dir);
        // This will attempt to create but may fail on filesystem — just verify name passes
        let result = create_worktree(
            dir.path().to_string_lossy().to_string(),
            "wt-50-my-feature".to_string(),
            "wt-50-my-feature".to_string(),
            None,
        );
        // Either Ok (worktree created) or Err (git error, not naming error)
        if let Err(e) = &result {
            assert!(
                !e.contains("does not match convention"),
                "Should not fail on naming: {e}"
            );
        }
    }

    #[test]
    fn test_list_worktrees_has_active_run_reflects_run_lock() {
        let dir = TempDir::new().unwrap();
        init_git_repo(&dir);

        // Without run.lock, has_active_run should be false.
        let result = list_worktrees(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        let worktrees = result.unwrap();
        let main = worktrees.iter().find(|wt| wt.is_main).unwrap();
        assert!(
            !main.has_active_run,
            "has_active_run should be false when run.lock does not exist"
        );

        // Create the run.lock file — has_active_run should now be true.
        let lock_path = dir.path().join(".agent").join("tmp").join("run.lock");
        std::fs::create_dir_all(lock_path.parent().unwrap()).unwrap();
        std::fs::write(&lock_path, "locked").unwrap();

        let result = list_worktrees(dir.path().to_string_lossy().to_string());
        assert!(result.is_ok());
        let worktrees = result.unwrap();
        let main = worktrees.iter().find(|wt| wt.is_main).unwrap();
        assert!(
            main.has_active_run,
            "has_active_run should be true when run.lock exists"
        );
    }
}
