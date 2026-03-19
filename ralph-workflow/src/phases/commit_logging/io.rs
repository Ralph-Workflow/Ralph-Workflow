#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_session_increments_attempt_number() {
        let workspace = MemoryWorkspace::new_test();

        let (n1, n2, n3) = {
            let mut session =
                CommitLogSession::new(".agent/logs/commit_generation", &workspace).unwrap();
            (
                session.next_attempt_number(),
                session.next_attempt_number(),
                session.next_attempt_number(),
            )
        };
        assert_eq!(n1, 1);
        assert_eq!(n2, 2);
        assert_eq!(n3, 3);
    }

    #[test]
    fn test_session_new_attempt() {
        let workspace = MemoryWorkspace::new_test();

        let log1;
        let log2;
        {
            let mut session =
                CommitLogSession::new(".agent/logs/commit_generation", &workspace).unwrap();
            log1 = session.new_attempt("claude", "initial");
            log2 = session.new_attempt("glm", "strict_json");
        }
        assert_eq!(log1.attempt_number, 1);
        assert_eq!(log2.attempt_number, 2);
    }

    #[test]
    fn test_noop_session_attempt_counter() {
        let (n1, n2, n3) = {
            let mut session = CommitLogSession::noop();
            (
                session.next_attempt_number(),
                session.next_attempt_number(),
                session.next_attempt_number(),
            )
        };
        assert_eq!(n1, 1);
        assert_eq!(n2, 2);
        assert_eq!(n3, 3);
    }
}
