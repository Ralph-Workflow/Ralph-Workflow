pub fn get_current_dir() -> Option<String> {
    std::env::current_dir()
        .ok()
        .map(|path| path.to_string_lossy().to_string())
}
