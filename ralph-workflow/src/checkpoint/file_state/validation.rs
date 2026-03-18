impl FileSystemState {
    /// Validate the current file system state against this snapshot using a workspace.
    ///
    /// Returns a list of validation errors. Empty list means all checks passed.
    pub fn validate_with_workspace(
        &self,
        workspace: &dyn Workspace,
        executor: Option<&dyn ProcessExecutor>,
    ) -> Vec<ValidationError> {
        let errors: Vec<ValidationError> = self
            .files
            .iter()
            .filter_map(|(path, snapshot)| {
                Self::validate_file_with_workspace(workspace, path, snapshot).err()
            })
            .chain(executor.and_then(|exec| self.validate_git_state_with_executor(exec).err()))
            .collect();

        errors
    }

    /// Validate a single file against its snapshot using a workspace.
    fn validate_file_with_workspace(
        workspace: &dyn Workspace,
        path: &str,
        snapshot: &FileSnapshot,
    ) -> Result<(), ValidationError> {
        let path_ref = Path::new(path);

        // Check existence
        if snapshot.exists && !workspace.exists(path_ref) {
            return Err(ValidationError::FileMissing {
                path: path.to_string(),
            });
        }

        if !snapshot.exists && workspace.exists(path_ref) {
            return Err(ValidationError::FileUnexpectedlyExists {
                path: path.to_string(),
            });
        }

        // Verify checksum for existing files
        if snapshot.exists && !snapshot.verify_with_workspace(workspace) {
            return Err(ValidationError::FileContentChanged {
                path: path.to_string(),
            });
        }

        Ok(())
    }

    /// Validate git state against the snapshot with a provided process executor.
    fn validate_git_state_with_executor(
        &self,
        executor: &dyn ProcessExecutor,
    ) -> Result<(), ValidationError> {
        if let Some(expected_oid) = &self.git_head_oid {
            if let Some(current_oid) = crate::checkpoint::git_capture::git_head_oid(executor) {
                if current_oid != *expected_oid {
                    return Err(ValidationError::GitHeadChanged {
                        expected: expected_oid.clone(),
                        actual: current_oid,
                    });
                }
            }
        }

        Ok(())
    }
}
