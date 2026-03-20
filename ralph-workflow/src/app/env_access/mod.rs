#[must_use]
pub fn check_no_resume_prompt() -> bool {
    crate::app::io::effect_io::check_no_resume_prompt()
}

#[must_use]
pub fn is_terminal_io() -> bool {
    crate::app::io::effect_io::is_terminal_io()
}

#[must_use]
pub fn get_current_dir() -> std::path::PathBuf {
    std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."))
}

pub fn set_current_dir(path: &std::path::Path) -> std::io::Result<()> {
    std::env::set_current_dir(path)
}

#[must_use]
pub fn get_args() -> Vec<String> {
    std::env::args().collect()
}

#[must_use]
pub fn get_program_args() -> Vec<String> {
    std::env::args().skip(1).collect()
}

#[must_use]
pub fn get_process_id() -> u32 {
    std::process::id()
}

pub fn exit_with_code(code: i32) -> ! {
    std::process::exit(code)
}
