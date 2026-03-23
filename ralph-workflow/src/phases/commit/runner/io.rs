pub fn create_session_and_get_attempt_number(
    log_dir: &Path,
    workspace: &dyn Workspace,
) -> (CommitLogSession, usize) {
    let mut session = CommitLogSession::new(
        log_dir
            .to_str()
            .expect("Path contains invalid UTF-8 - all paths in this codebase should be UTF-8"),
        workspace,
    )
    .unwrap_or_else(|_| CommitLogSession::noop());
    let attempt_number = session.next_attempt_number();
    (session, attempt_number)
}
