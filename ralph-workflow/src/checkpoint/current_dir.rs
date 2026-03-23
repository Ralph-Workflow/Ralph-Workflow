pub fn get_current_dir() -> Option<String> {
    crate::checkpoint::io::current_dir::get_current_dir()
}
