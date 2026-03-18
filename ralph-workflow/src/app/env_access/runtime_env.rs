pub fn check_no_resume_prompt() -> bool {
    std::env::var("RALPH_NO_RESUME_PROMPT").is_ok()
}

use std::io::IsTerminal;

pub fn is_terminal_io() -> bool {
    std::io::stdin().is_terminal()
        && (std::io::stdout().is_terminal() || std::io::stderr().is_terminal())
}

pub fn get_current_dir() -> std::path::PathBuf {
    std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."))
}

pub fn set_current_dir(path: &std::path::Path) -> std::io::Result<()> {
    std::env::set_current_dir(path)
}

pub fn get_args() -> Vec<String> {
    std::env::args().collect()
}

pub fn get_program_args() -> Vec<String> {
    std::env::args().skip(1).collect()
}

pub fn get_process_id() -> u32 {
    std::process::id()
}

pub fn exit_with_code(code: i32) -> ! {
    std::process::exit(code)
}
