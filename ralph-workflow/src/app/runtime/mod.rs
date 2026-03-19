pub fn check_no_resume_prompt() -> bool {
    crate::app::io::effect_io::check_no_resume_prompt()
}

pub fn is_terminal_io() -> bool {
    crate::app::io::effect_io::is_terminal_io()
}

pub fn get_current_dir() -> std::path::PathBuf {
    crate::app::io::effect_io::get_current_dir()
}

pub fn set_current_dir(path: &std::path::Path) -> std::io::Result<()> {
    crate::app::io::effect_io::set_current_dir(path)
}

pub fn get_args() -> Vec<String> {
    crate::app::io::effect_io::get_args()
}

pub fn get_program_args() -> Vec<String> {
    crate::app::io::effect_io::get_program_args()
}

pub fn get_process_id() -> u32 {
    crate::app::io::effect_io::get_process_id()
}

pub fn exit_with_code(code: i32) -> ! {
    crate::app::io::effect_io::exit_with_code(code)
}
