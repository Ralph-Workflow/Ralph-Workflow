// Test fixture for boundary exit-code policy detection lint
//
// Exit-code interpretation (deciding what exit_code==0 or status.success() means)
// is a domain policy decision that should live in reducers, not boundary modules.

pub struct ProcessOutput {
    pub exit_code: i32,
    pub status: ExitStatus,
    pub stdout: String,
    pub stderr: String,
}

pub struct ExitStatus {
    code: Option<i32>,
}

impl ExitStatus {
    pub fn success(&self) -> bool {
        self.code == Some(0)
    }
    
    pub fn code(&self) -> Option<i32> {
        self.code
    }
}

pub fn is_success(output: &ProcessOutput) -> bool {
    output.exit_code == 0
}

// Pattern 1: Match guard with is_success helper
// Should trigger lint: boundary deciding success vs failure via helper
pub mod io {
    use super::*;
    
    pub fn handle_push(result: Result<ProcessOutput, String>) -> String {
        //~^ ERROR branching on exit code or process status is a policy decision forbidden in boundary modules
        match result {
            Ok(output) if is_success(&output) => "completed".to_string(),
            Ok(output) => format!("failed: {}", output.stderr),
            Err(e) => format!("error: {e}"),
        }
    }
}

// Pattern 2: Direct exit_code comparison in if statement
// Should trigger lint: boundary interpreting exit code
pub mod runtime {
    use super::*;
    
    pub fn handle_conflict_resolution(result: ProcessOutput) -> &'static str {
        //~^ ERROR branching on exit code or process status is a policy decision forbidden in boundary modules
        if result.exit_code != 0 {
            return "failed";
        }
        "success"
    }
}

// Pattern 3: status.success() method call branching
// Should trigger lint: boundary deciding success meaning
pub mod ffi {
    use super::*;
    
    pub fn verify_preconditions(output: ProcessOutput) -> Result<(), String> {
        //~^ ERROR branching on exit code or process status is a policy decision forbidden in boundary modules
        if output.status.success() {
            Ok(())
        } else {
            Err("status check failed".to_string())
        }
    }
}

// Pattern 4: Tri-way branching with exit_code == 0
// Should trigger lint: three-way decision on exit code
pub mod executor {
    use super::*;
    
    pub fn classify_result(result: ProcessOutput) -> &'static str {
        //~^ ERROR branching on exit code or process status is a policy decision forbidden in boundary modules
        if result.exit_code == 0 {
            "success"
        } else if result.exit_code == 1 {
            "retriable_error"
        } else {
            "fatal_error"
        }
    }
}

// Pattern 5: Match on status.code() with multiple arms
// Should trigger lint: multi-way branching on exit code
pub mod boundary {
    use super::*;
    
    pub fn handle_bfs_result(output: ProcessOutput) -> Result<Option<String>, String> {
        //~^ ERROR branching on exit code or process status is a policy decision forbidden in boundary modules
        if output.status.success() {
            Ok(None)
        } else if output.status.code() == Some(1) {
            Ok(Some("partial".to_string()))
        } else {
            Err("failed".to_string())
        }
    }
}

// NEGATIVE CASE: Pure execution without decision
// Should NOT trigger: just executing command, not interpreting result
pub mod executor_pure {
    use super::*;
    
    pub fn execute_command() -> ProcessOutput {
        ProcessOutput {
            exit_code: 0,
            status: ExitStatus { code: Some(0) },
            stdout: String::new(),
            stderr: String::new(),
        }
    }
}

// NEGATIVE CASE: Passing result through without interpretation
// Should NOT trigger: no branching on exit code
pub mod boundary_passthrough {
    use super::*;
    
    pub fn run_and_return(_cmd: &str) -> ProcessOutput {
        ProcessOutput {
            exit_code: 0,
            status: ExitStatus { code: Some(0) },
            stdout: String::new(),
            stderr: String::new(),
        }
    }
}

fn main() {}
