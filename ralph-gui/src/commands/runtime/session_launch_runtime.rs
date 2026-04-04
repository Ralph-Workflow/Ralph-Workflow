pub(super) fn is_dry_run_enabled() -> bool {
    std::env::var("RALPH_GUI_DRY_RUN").as_deref() == Ok("1")
}

pub(super) fn spawn_launch_process(
    ralph: &std::path::Path,
    target_path: &std::path::Path,
    prompt_path: &str,
    developer_iterations: u32,
    reviewer_passes: u32,
    developer_agent: Option<&str>,
    reviewer_agent: Option<&str>,
) -> Result<u32, String> {
    let optional_args = developer_agent
        .map(|dev_agent| vec!["--developer-agent".to_string(), dev_agent.to_string()])
        .into_iter()
        .chain(
            reviewer_agent
                .map(|rev_agent| vec!["--reviewer-agent".to_string(), rev_agent.to_string()]),
        )
        .flatten();

    let command_args = [
        "--prompt".to_string(),
        prompt_path.to_string(),
        "--developer-iters".to_string(),
        developer_iterations.to_string(),
        "--reviewer-passes".to_string(),
        reviewer_passes.to_string(),
    ]
    .into_iter()
    .chain(optional_args)
    .collect::<Vec<_>>();

    let child = std::process::Command::new(ralph)
        .current_dir(target_path)
        .args(command_args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to spawn ralph: {e}"))?;

    Ok(child.id())
}

pub(super) fn store_pid_file(target_path: &std::path::Path, pid: u32) {
    let pid_dir = target_path.join(".agent").join("tmp");
    if let Err(e) = std::fs::create_dir_all(&pid_dir) {
        eprintln!("Warning: could not create .agent/tmp for PID file: {e}");
    } else {
        let _ = std::fs::write(pid_dir.join("gui-pid"), pid.to_string());
    }
}

pub(super) fn spawn_resume_process(
    ralph: &std::path::Path,
    target_path: &std::path::Path,
) -> Result<(), String> {
    std::process::Command::new(ralph)
        .current_dir(target_path)
        .arg("--resume")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to spawn ralph --resume: {e}"))?;

    Ok(())
}
