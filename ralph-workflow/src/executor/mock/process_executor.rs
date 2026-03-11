use super::super::{
    AgentChildHandle, AgentCommandResult, AgentSpawnConfig, ChildProcessInfo, ProcessExecutor,
    ProcessOutput,
};
use super::agent_child::MockAgentChild;
use super::agent_output::generate_mock_agent_output;
use super::ExecuteCall;
use std::collections::HashMap;
use std::io::{self, Cursor};
use std::path::Path;
use std::process::ExitStatus;
use std::sync::Mutex;

/// Clonable representation of an `io::Result`.
///
/// Since `io::Error` doesn't implement `Clone`, we store error info as strings
/// and reconstruct the error on demand.
#[derive(Debug, Clone)]
pub enum MockResult<T: Clone> {
    Ok(T),
    Err {
        kind: io::ErrorKind,
        message: String,
    },
}

impl<T: Clone> MockResult<T> {
    pub(crate) fn to_io_result(&self) -> io::Result<T> {
        match self {
            Self::Ok(v) => Ok(v.clone()),
            Self::Err { kind, message } => Err(io::Error::new(*kind, message.clone())),
        }
    }

    pub(crate) fn from_io_result(result: io::Result<T>) -> Self {
        match result {
            Ok(v) => Self::Ok(v),
            Err(e) => Self::Err {
                kind: e.kind(),
                message: e.to_string(),
            },
        }
    }
}

impl<T: Clone + Default> Default for MockResult<T> {
    fn default() -> Self {
        Self::Ok(T::default())
    }
}

/// Mock process executor for testing.
///
/// Captures all calls and allows tests to control what each execution returns.
#[derive(Debug)]
pub struct MockProcessExecutor {
    execute_calls: Mutex<Vec<ExecuteCall>>,
    results: Mutex<HashMap<String, MockResult<ProcessOutput>>>,
    default_result: Mutex<MockResult<ProcessOutput>>,
    agent_calls: Mutex<Vec<AgentSpawnConfig>>,
    agent_results: Mutex<HashMap<String, MockResult<AgentCommandResult>>>,
    default_agent_result: Mutex<MockResult<AgentCommandResult>>,
    active_children: Mutex<HashMap<u32, ChildProcessInfo>>,
}

impl Default for MockProcessExecutor {
    fn default() -> Self {
        #[cfg(unix)]
        use std::os::unix::process::ExitStatusExt;

        Self {
            execute_calls: Mutex::new(Vec::new()),
            results: Mutex::new(HashMap::new()),
            #[cfg(unix)]
            default_result: Mutex::new(MockResult::Ok(ProcessOutput {
                status: ExitStatus::from_raw(0),
                stdout: String::new(),
                stderr: String::new(),
            })),
            #[cfg(not(unix))]
            default_result: Mutex::new(MockResult::Ok(ProcessOutput {
                status: std::process::ExitStatus::default(),
                stdout: String::new(),
                stderr: String::new(),
            })),
            agent_calls: Mutex::new(Vec::new()),
            agent_results: Mutex::new(HashMap::new()),
            default_agent_result: Mutex::new(MockResult::Ok(AgentCommandResult::success())),
            active_children: Mutex::new(HashMap::new()),
        }
    }
}

impl MockProcessExecutor {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn new_error() -> Self {
        fn err_result<T: Clone>(msg: &str) -> MockResult<T> {
            MockResult::Err {
                kind: io::ErrorKind::Other,
                message: msg.to_string(),
            }
        }

        Self {
            execute_calls: Mutex::new(Vec::new()),
            results: Mutex::new(HashMap::new()),
            default_result: Mutex::new(err_result("mock process error")),
            agent_calls: Mutex::new(Vec::new()),
            agent_results: Mutex::new(HashMap::new()),
            default_agent_result: Mutex::new(err_result("mock agent error")),
            active_children: Mutex::new(HashMap::new()),
        }
    }

    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    #[must_use]
    pub fn with_result(self, command: &str, result: io::Result<ProcessOutput>) -> Self {
        self.results
            .lock()
            .unwrap()
            .insert(command.to_string(), MockResult::from_io_result(result));
        self
    }

    #[must_use]
    pub fn with_output(self, command: &str, stdout: &str) -> Self {
        #[cfg(unix)]
        use std::os::unix::process::ExitStatusExt;

        #[cfg(unix)]
        let result = Ok(ProcessOutput {
            status: ExitStatus::from_raw(0),
            stdout: stdout.to_string(),
            stderr: String::new(),
        });
        #[cfg(not(unix))]
        let result = Ok(ProcessOutput {
            status: std::process::ExitStatus::default(),
            stdout: stdout.to_string(),
            stderr: String::new(),
        });
        self.with_result(command, result)
    }

    #[must_use]
    pub fn with_error(self, command: &str, stderr: &str) -> Self {
        #[cfg(unix)]
        use std::os::unix::process::ExitStatusExt;

        #[cfg(unix)]
        let result = Ok(ProcessOutput {
            status: ExitStatus::from_raw(1),
            stdout: String::new(),
            stderr: stderr.to_string(),
        });
        #[cfg(not(unix))]
        let result = Ok(ProcessOutput {
            status: std::process::ExitStatus::default(),
            stdout: String::new(),
            stderr: stderr.to_string(),
        });
        self.with_result(command, result)
    }

    #[must_use]
    pub fn with_io_error(self, command: &str, kind: io::ErrorKind, message: &str) -> Self {
        self.with_result(command, Err(io::Error::new(kind, message)))
    }

    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn execute_count(&self) -> usize {
        self.execute_calls.lock().unwrap().len()
    }

    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn execute_calls(&self) -> Vec<ExecuteCall> {
        self.execute_calls.lock().unwrap().clone()
    }

    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn execute_calls_for(&self, command: &str) -> Vec<ExecuteCall> {
        self.execute_calls
            .lock()
            .unwrap()
            .iter()
            .filter(|(cmd, _, _, _)| cmd == command)
            .cloned()
            .collect()
    }

    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn reset_calls(&self) {
        self.execute_calls.lock().unwrap().clear();
        self.agent_calls.lock().unwrap().clear();
    }

    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    #[must_use]
    pub fn with_agent_result(
        self,
        command_pattern: &str,
        result: io::Result<AgentCommandResult>,
    ) -> Self {
        self.agent_results.lock().unwrap().insert(
            command_pattern.to_string(),
            MockResult::from_io_result(result),
        );
        self
    }

    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn agent_calls(&self) -> Vec<AgentSpawnConfig> {
        self.agent_calls.lock().unwrap().clone()
    }

    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn agent_calls_for(&self, command_pattern: &str) -> Vec<AgentSpawnConfig> {
        self.agent_calls
            .lock()
            .unwrap()
            .iter()
            .filter(|config| config.command.contains(command_pattern))
            .cloned()
            .collect()
    }

    /// Configure this mock to report active child processes for the given parent PID.
    ///
    /// When `get_child_process_info` is called with `parent_pid`, returns a
    /// `ChildProcessInfo` with `child_count: 1` and `cpu_time_ms: 0`.
    ///
    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    #[must_use]
    pub fn with_active_children_for(self, parent_pid: u32) -> Self {
        self.active_children.lock().unwrap().insert(
            parent_pid,
            ChildProcessInfo {
                child_count: 1,
                cpu_time_ms: 0,
                descendant_pid_signature: u64::from(parent_pid),
            },
        );
        self
    }

    /// Configure this mock with specific child process info for a parent PID.
    ///
    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    #[must_use]
    pub fn with_active_children_info(self, parent_pid: u32, info: ChildProcessInfo) -> Self {
        self.active_children
            .lock()
            .unwrap()
            .insert(parent_pid, info);
        self
    }

    /// Add or replace child process info for a parent PID after construction.
    ///
    /// Unlike [`with_active_children_info`](Self::with_active_children_info), this
    /// takes `&self` so it can be called on an already-constructed executor (e.g.
    /// after [`remove_active_children_for`](Self::remove_active_children_for)).
    ///
    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn add_active_children_info(&self, parent_pid: u32, info: ChildProcessInfo) {
        self.active_children
            .lock()
            .unwrap()
            .insert(parent_pid, info);
    }

    /// Update the CPU time reported for a parent PID's children.
    ///
    /// If the parent PID is not present (e.g. after removal), inserts a new
    /// entry with `child_count: 1` and the given CPU time. This makes the API
    /// forgiving for tests that remove and re-add children.
    ///
    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn set_child_cpu_time(&self, parent_pid: u32, cpu_time_ms: u64) {
        let mut children = self.active_children.lock().unwrap();
        match children.get_mut(&parent_pid) {
            Some(info) => info.cpu_time_ms = cpu_time_ms,
            None => {
                children.insert(
                    parent_pid,
                    ChildProcessInfo {
                        child_count: 1,
                        cpu_time_ms,
                        descendant_pid_signature: u64::from(parent_pid),
                    },
                );
            }
        }
    }

    /// Remove a PID from the set of active children.
    ///
    /// Call this to simulate a child process completing while the monitor is running.
    ///
    /// # Panics
    ///
    /// Panics if the mutex is poisoned.
    pub fn remove_active_children_for(&self, parent_pid: u32) {
        self.active_children.lock().unwrap().remove(&parent_pid);
    }

    fn find_agent_result(&self, command: &str) -> AgentCommandResult {
        if let Some(result) = self.agent_results.lock().unwrap().get(command) {
            return result
                .clone()
                .to_io_result()
                .unwrap_or_else(|_| AgentCommandResult::success());
        }

        for (pattern, result) in self.agent_results.lock().unwrap().iter() {
            if command.contains(pattern) {
                return result
                    .clone()
                    .to_io_result()
                    .unwrap_or_else(|_| AgentCommandResult::success());
            }
        }

        self.default_agent_result
            .lock()
            .unwrap()
            .clone()
            .to_io_result()
            .unwrap_or_else(|_| AgentCommandResult::success())
    }
}

impl ProcessExecutor for MockProcessExecutor {
    fn get_child_process_info(&self, parent_pid: u32) -> ChildProcessInfo {
        self.active_children
            .lock()
            .unwrap()
            .get(&parent_pid)
            .copied()
            .unwrap_or(ChildProcessInfo::NONE)
    }

    fn spawn(
        &self,
        _command: &str,
        _args: &[&str],
        _env: &[(String, String)],
        _workdir: Option<&Path>,
    ) -> io::Result<std::process::Child> {
        Err(io::Error::other(
            "MockProcessExecutor doesn't support spawn() - use execute() instead",
        ))
    }

    fn spawn_agent(&self, config: &AgentSpawnConfig) -> io::Result<AgentChildHandle> {
        self.agent_calls.lock().unwrap().push(config.clone());

        let result = self.find_agent_result(&config.command);
        let mock_output = generate_mock_agent_output(config.parser_type, &config.command);

        Ok(AgentChildHandle {
            stdout: Box::new(Cursor::new(mock_output)),
            stderr: Box::new(Cursor::new(result.stderr)),
            inner: Box::new(MockAgentChild::new(result.exit_code)),
        })
    }

    fn execute(
        &self,
        command: &str,
        args: &[&str],
        env: &[(String, String)],
        workdir: Option<&Path>,
    ) -> io::Result<ProcessOutput> {
        let workdir_str = workdir.map(|p| p.display().to_string());
        self.execute_calls.lock().unwrap().push((
            command.to_string(),
            args.iter().map(std::string::ToString::to_string).collect(),
            env.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
            workdir_str,
        ));

        self.results.lock().unwrap().get(command).map_or_else(
            || self.default_result.lock().unwrap().to_io_result(),
            MockResult::to_io_result,
        )
    }
}
