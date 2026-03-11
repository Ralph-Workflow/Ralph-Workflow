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
fn wait_for_descendant_snapshot_matching(
    executor: &RealProcessExecutor,
    parent_pid: u32,
    timeout: Duration,
    matches: impl Fn(ChildProcessInfo) -> bool,
) -> ChildProcessInfo {
    let deadline = Instant::now() + timeout;
    let mut last_info = executor.get_child_process_info(parent_pid);

    loop {
        if matches(last_info) {
            return last_info;
        }
        if Instant::now() >= deadline {
            return last_info;
        }
        std::thread::sleep(Duration::from_millis(25));
        last_info = executor.get_child_process_info(parent_pid);
    }
}

#[test]
#[cfg(unix)]
fn detached_descendant_process_group_does_not_qualify_as_active_child_work() {
    let executor = RealProcessExecutor::new();
    let script = "python3 -c 'import os,time; os.setpgid(0,0); time.sleep(1.0)' & wait";
    let mut shell = spawn_shell_in_own_process_group(script);

    let info = wait_for_descendant_snapshot_matching(
        &executor,
        shell.id(),
        Duration::from_millis(400),
        |info| info.active_child_count == 0,
    );

    shell.wait().expect("wait for shell");

    assert_eq!(
        info.active_child_count, 0,
        "detached descendants in a different process group must not count as currently active child work"
    );
    assert!(
        info.child_count <= 1,
        "at most one transient detached descendant should remain observable"
    );
}

#[test]
#[cfg(unix)]
fn same_process_group_sleeping_descendants_with_only_historical_cpu_do_not_qualify() {
    let executor = RealProcessExecutor::new();
    let script = "python3 -c 'import time\nfor _ in range(5):\n    start=time.time()\n    while time.time()-start < 0.03:\n        pass\n    time.sleep(0.2)' & sleep 1.5";
    let mut shell = spawn_shell_in_own_process_group(script);

    std::thread::sleep(Duration::from_millis(350));
    let info = executor.get_child_process_info(shell.id());

    shell.wait().expect("wait for shell");

    assert!(
        info.child_count > 0,
        "stalled descendants should remain observable"
    );
    assert_eq!(
        info.active_child_count, 0,
        "same-process-group descendants that are only sleeping after brief CPU bursts must not count as currently active child work"
    );
}

#[test]
#[cfg(unix)]
fn same_process_group_busy_descendant_qualifies_as_active_child_work() {
    let executor = RealProcessExecutor::new();
    let script = "python3 -c 'import time\nend=time.time()+1.0\nwhile time.time()<end:\n    pass' & sleep 1.2";
    let mut shell = spawn_shell_in_own_process_group(script);

    let info = wait_for_descendant_snapshot_matching(
        &executor,
        shell.id(),
        Duration::from_secs(1),
        |info| info.active_child_count > 0,
    );

    shell.wait().expect("wait for shell");

    assert!(
        info.child_count > 0,
        "busy descendants should remain observable while the shell waits"
    );
    assert!(
        info.active_child_count > 0,
        "same-process-group descendants doing current CPU work must qualify as active child work"
    );
}
