use std::path::PathBuf;
use std::sync::{Arc, Mutex};

/// Active repository and worktree context for the GUI.
#[derive(Debug, Default)]
pub struct ActiveContext {
    pub repo_path: Option<PathBuf>,
    pub worktree_path: Option<PathBuf>,
}

pub type SharedState = Arc<Mutex<ActiveContext>>;

/// Create a new shared state instance with no active context.
#[must_use]
pub fn new_shared_state() -> SharedState {
    Arc::new(Mutex::new(ActiveContext::default()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_state_has_no_context() {
        let state = new_shared_state();
        let (repo_path, worktree_path) = {
            let locked = state.lock().unwrap();
            (locked.repo_path.clone(), locked.worktree_path.clone())
        };
        assert!(repo_path.is_none());
        assert!(worktree_path.is_none());
    }

    #[test]
    fn test_state_context_can_be_set() {
        let state = new_shared_state();
        {
            let mut locked = state.lock().unwrap();
            locked.repo_path = Some(PathBuf::from("/tmp/my-repo"));
        }
        let repo_path = state.lock().unwrap().repo_path.clone();
        assert_eq!(repo_path, Some(PathBuf::from("/tmp/my-repo")));
    }
}
