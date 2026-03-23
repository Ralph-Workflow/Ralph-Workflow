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
    crate::app::runtime::get_current_dir()
}

pub fn set_current_dir(path: &std::path::Path) -> std::io::Result<()> {
    crate::app::runtime::set_current_dir(path)
}

#[must_use]
pub fn get_args() -> Vec<String> {
    crate::app::runtime::get_args()
}

#[must_use]
pub fn get_program_args() -> Vec<String> {
    crate::app::runtime::get_program_args()
}

#[must_use]
pub fn get_process_id() -> u32 {
    crate::app::runtime::get_process_id()
}

pub fn exit_with_code(code: i32) -> ! {
    crate::app::runtime::exit_with_code(code)
}
