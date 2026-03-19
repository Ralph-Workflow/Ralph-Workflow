//! Imperative command builder utilities.
//!
//! Process command construction is inherently imperative.

use std::collections::HashMap;
use std::path::Path;
use std::process::Command;

/// Build a Command with all configuration applied.
pub fn build_command(
    command: &str,
    args: &[&str],
    env: &[(String, String)],
    workdir: Option<&Path>,
) -> Command {
    let mut cmd = Command::new(command);
    cmd.args(args);
    for (key, value) in env {
        cmd.env(key, value);
    }
    if let Some(dir) = workdir {
        cmd.current_dir(dir);
    }
    cmd
}

/// Build an agent command with default environment variables.
pub fn build_agent_command(
    command: &str,
    args: &[String],
    env: &HashMap<String, String>,
    prompt: &str,
) -> Command {
    let mut cmd = Command::new(command);
    cmd.args(args.iter().map(String::as_str));
    for (k, v) in env {
        cmd.env(k, v);
    }
    cmd.arg(prompt);
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.env("NODE_ENV", "production");
    cmd
}

/// Internal agent command builder for trait default implementation.
pub fn build_agent_command_internal(
    command: &str,
    args: &[String],
    env: &HashMap<String, String>,
    prompt: &str,
) -> Command {
    build_agent_command(command, args, env, prompt)
}
