use crate::workspace::Workspace;
use std::path::PathBuf;

pub fn create_run_dir_with_collision_handling(
    workspace: &dyn Workspace,
    base_run_id: &crate::logging::RunId,
) -> Result<(crate::logging::RunId, PathBuf), anyhow::Error> {
    let candidates: Vec<(crate::logging::RunId, PathBuf)> = (0..=99)
        .map(|counter| {
            let run_id = if counter == 0 {
                base_run_id.clone()
            } else {
                base_run_id.with_collision_counter(counter)
            };
            let run_dir = PathBuf::from(format!(".agent/logs-{run_id}"));
            (run_id, run_dir)
        })
        .collect();

    candidates
        .into_iter()
        .find(|(_run_id, run_dir)| {
            let agents_dir = run_dir.join("agents");

            if workspace.exists(&agents_dir) {
                return false;
            }

            if workspace.create_dir_all(run_dir).is_err() {
                return false;
            }
            if workspace.create_dir_all(&agents_dir).is_err() {
                return false;
            }
            if workspace.create_dir_all(&run_dir.join("provider")).is_err() {
                return false;
            }
            if workspace.create_dir_all(&run_dir.join("debug")).is_err() {
                return false;
            }

            workspace.exists(&agents_dir)
        })
        .ok_or_else(|| {
            anyhow::anyhow!(
                "Too many collisions creating run log directory (tried base + 99 variants). \
         This is extremely rare (100+ runs in the same millisecond). \
         Possible causes: clock skew, or filesystem issues. \
         Suggestion: Wait 1ms and retry, or check system clock."
            )
        })
}
