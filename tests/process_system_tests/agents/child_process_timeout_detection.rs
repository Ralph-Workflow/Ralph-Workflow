use ralph_workflow::{ChildProcessInfo, ProcessExecutor, RealProcessExecutor};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

#[cfg(unix)]
fn spawn_shell_in_own_process_group(script: &str) -> Child {
    use std::os::unix::process::CommandExt;

    let mut command = Command::new("sh");
    command
        .arg("-c")
        .arg(script)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    // Safety: this mirrors production agent spawning. The closure only invokes
    // `setpgid(0, 0)` before exec to isolate the shell in its own process group.
    unsafe {
        command.pre_exec(|| {
            if libc::setpgid(0, 0) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }

    command.spawn().expect("spawn test shell")
}

#[cfg(unix)]
fn wait_for_descendant_snapshot(
    executor: &RealProcessExecutor,
    parent_pid: u32,
    timeout: Duration,
) -> ChildProcessInfo {
    let deadline = Instant::now() + timeout;
    loop {
        let info = executor.get_child_process_info(parent_pid);
        if Instant::now() >= deadline || info != ChildProcessInfo::NONE {
            return info;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

#[test]
#[cfg(unix)]
fn detached_descendant_process_group_does_not_qualify_as_active_child_work() {
    let executor = RealProcessExecutor::new();
    let script = "python3 -c 'import os,time; os.setpgid(0,0); time.sleep(1.0)' & sleep 1.0";
    let mut shell = spawn_shell_in_own_process_group(script);

    let info = wait_for_descendant_snapshot(&executor, shell.id(), Duration::from_millis(400));

    shell.wait().expect("wait for shell");

    assert_eq!(
        info,
        ChildProcessInfo::NONE,
        "detached descendants in a different process group must not suppress idle timeout"
    );
}
