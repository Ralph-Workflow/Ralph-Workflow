pub fn run_external_command() -> std::process::Output {
    std::process::Command::new("echo")
        .arg("hello")
        .output()
        .unwrap()
}

pub fn get_command_output() -> std::process::Output {
    std::process::Command::new("pwd").output().unwrap()
}

fn private_helper() -> std::process::Output {
    std::process::Command::new("date").output().unwrap()
}
