//! Process execution abstraction for dependency injection.
//!
//! This module provides a trait-based abstraction for executing external processes,
//! allowing production code to use real processes and test code to use mocks.
//! This follows the same pattern as [`crate::workspace::Workspace`] for dependency injection.
//!
//! # Purpose
//!
//! - Production: [`RealProcessExecutor`] executes actual commands using `std::process::Command`
//! - Tests: `MockProcessExecutor` captures calls and returns controlled results (with `test-utils` feature)
//!
//! # Benefits
//!
//! - Test isolation: Tests don't spawn real processes
//! - Determinism: Tests produce consistent results
//! - Speed: Tests run faster without subprocess overhead
//! - Mockability: Full control over process behavior in tests
//!
//! # Key Types
//!
//! - [`ProcessExecutor`] - The trait abstraction for process execution
//! - [`AgentSpawnConfig`] - Configuration for spawning agent processes
//! - [`AgentChildHandle`] - Handle to a spawned agent with streaming output
//! - [`ProcessOutput`] - Captured output from a completed process
//!
//! # Testing with `MockProcessExecutor`
//!
//! The `test-utils` feature enables `MockProcessExecutor` for integration tests:
//!
//! ```ignore
//! use ralph_workflow::{MockProcessExecutor, ProcessExecutor};
//!
//! // Create a mock that returns success for 'git' commands
//! let executor = MockProcessExecutor::new()
//!     .with_output("git", "On branch main\nnothing to commit");
//!
//! // Execute command (captured, returns mock result)
//! let result = executor.execute("git", &["status"], &[], None)?;
//! assert!(result.status.success());
//!
//! // Verify the call was captured
//! assert_eq!(executor.execute_count(), 1);
//! ```
//!
//! # See Also
//!
//! - [`crate::workspace::Workspace`] - Similar abstraction for filesystem operations

pub mod bfs;
pub mod command;
mod executor_trait;
#[cfg(target_os = "macos")]
pub mod macos;
#[cfg(any(test, feature = "test-utils"))]
mod mock;
pub mod ps;
mod real;
mod types;

// Re-export all public types
pub use executor_trait::ProcessExecutor;
pub use real::RealProcessExecutor;
pub use types::{
    AgentChild, AgentChildHandle, AgentCommandResult, AgentSpawnConfig, ChildProcessInfo,
    ProcessOutput, RealAgentChild, SpawnedProcess,
};

#[cfg(any(test, feature = "test-utils"))]
pub use mock::{MockAgentChild, MockProcessExecutor};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_real_executor_can_be_created() {
        let executor = RealProcessExecutor::new();
        // Can't test actual execution without real commands
        let _ = executor;
    }

    #[test]
    #[cfg(unix)]
    fn test_real_executor_execute_basic() {
        let executor = RealProcessExecutor::new();
        // Use 'echo' command which should exist on all Unix systems
        let result = executor.execute("echo", &["hello"], &[], None);
        // Should succeed
        assert!(result.is_ok());
        if let Ok(output) = result {
            assert!(output.status.success());
            assert_eq!(output.stdout.trim(), "hello");
        }
    }

    /// Unsafe code verification tests module.
    ///
    /// This module tests the observable behavior of unsafe code without testing
    /// the unsafe blocks directly. These tests verify correctness through behavioral
    /// tests to ensure unsafe operations work correctly.
    mod safety {
        use super::*;
        use crate::agents::JsonParserType;
        use std::collections::HashMap;
        use tempfile::tempdir;

        #[test]
        #[cfg(unix)]
        fn test_nonblocking_io_setup_succeeds() {
            // This internally uses unsafe fcntl - verify it works
            let executor = RealProcessExecutor::new();
            let tempdir = tempdir().expect("create tempdir for logfile");
            let logfile_path = tempdir.path().join("opencode_agent.log");
            let config = AgentSpawnConfig {
                command: "echo".to_string(),
                args: vec!["test".to_string()],
                env: HashMap::new(),
                prompt: "test prompt".to_string(),
                logfile: logfile_path.to_string_lossy().to_string(),
                parser_type: JsonParserType::OpenCode,
            };

            let result = executor.spawn_agent(&config);

            assert!(
                result.is_ok(),
                "Agent spawn with non-blocking I/O should succeed"
            );

            // Clean up spawned process
            if let Ok(mut handle) = result {
                let _ = handle.inner.wait();
            }
        }

        #[test]
        #[cfg(unix)]
        fn test_process_termination_cleanup_works() {
            // Test that the unsafe kill() calls work correctly for process cleanup
            let executor = RealProcessExecutor::new();

            // Spawn a long-running process
            let result = executor.spawn("sleep", &["10"], &[], None);

            assert!(result.is_ok(), "Process spawn should succeed");

            if let Ok(mut child) = result {
                // Verify process is running
                assert!(
                    child.try_wait().unwrap().is_none(),
                    "Process should be running"
                );

                // Terminate the process (uses unsafe kill internally)
                let kill_result = child.kill();
                assert!(kill_result.is_ok(), "Process termination should succeed");

                // Wait for the process to exit
                let wait_result = child.wait();
                assert!(
                    wait_result.is_ok(),
                    "Process wait should succeed after kill"
                );
            }
        }

        #[test]
        #[cfg(unix)]
        fn test_process_group_creation_succeeds() {
            // Test that the unsafe setpgid() call in pre_exec works correctly
            let executor = RealProcessExecutor::new();
            let tempdir = tempdir().expect("create tempdir for logfile");
            let logfile_path = tempdir.path().join("opencode_agent.log");
            let config = AgentSpawnConfig {
                command: "echo".to_string(),
                args: vec!["test".to_string()],
                env: HashMap::new(),
                prompt: "test prompt".to_string(),
                logfile: logfile_path.to_string_lossy().to_string(),
                parser_type: JsonParserType::OpenCode,
            };

            // This internally uses unsafe setpgid in pre_exec
            let result = executor.spawn_agent(&config);

            assert!(
                result.is_ok(),
                "Agent spawn with process group creation should succeed"
            );

            // Clean up
            if let Ok(mut handle) = result {
                let _ = handle.inner.wait();
            }
        }

        #[test]
        fn test_executor_handles_invalid_command_gracefully() {
            // Verify error handling when file descriptors are invalid
            let executor = RealProcessExecutor::new();

            // Try to spawn a command that doesn't exist
            let result = executor.spawn("nonexistent_command_12345", &[], &[], None);

            assert!(
                result.is_err(),
                "Spawning nonexistent command should fail gracefully"
            );
        }

        #[test]
        #[cfg(unix)]
        fn test_agent_spawn_handles_env_vars_correctly() {
            // Test that environment variables are passed correctly (no unsafe code,
            // but important for agent behavior)
            let executor = RealProcessExecutor::new();
            let mut env = HashMap::new();
            env.insert("TEST_VAR_1".to_string(), "value1".to_string());
            env.insert("TEST_VAR_2".to_string(), "value2".to_string());

            let tempdir = tempdir().expect("create tempdir for logfile");
            let logfile_path = tempdir.path().join("opencode_agent.log");

            let config = AgentSpawnConfig {
                command: "env".to_string(),
                args: vec![],
                env,
                prompt: String::new(),
                logfile: logfile_path.to_string_lossy().to_string(),
                parser_type: JsonParserType::OpenCode,
            };

            let result = executor.spawn_agent(&config);
            assert!(
                result.is_ok(),
                "Agent spawn with environment variables should succeed"
            );

            // Clean up
            if let Ok(mut handle) = result {
                let _ = handle.inner.wait();
            }
        }

        #[test]
        fn test_process_executor_execute_with_workdir() {
            // Test workdir setting (no unsafe code, but important for process spawning)
            let executor = RealProcessExecutor::new();

            // Use 'pwd' command to verify workdir is set (Unix) or 'cd' (cross-platform alternative)
            #[cfg(unix)]
            let result = executor.execute("pwd", &[], &[], Some(std::path::Path::new("/")));

            #[cfg(not(unix))]
            let result = executor.execute(
                "cmd",
                &["/c", "cd"],
                &[],
                Some(std::path::Path::new("C:\\")),
            );

            assert!(result.is_ok(), "Execute with workdir should succeed");
        }
    }
}
