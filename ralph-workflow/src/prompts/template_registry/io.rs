// prompts/template_registry/io.rs — boundary module for filesystem and env I/O.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

fn get_xdg_config_home() -> Option<PathBuf> {
    std::env::var("XDG_CONFIG_HOME")
        .ok()
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var("HOME")
                .ok()
                .map(|h| PathBuf::from(h).join(".config"))
        })
}

fn template_exists(path: &std::path::Path) -> bool {
    path.exists()
}

#[derive(Debug, thiserror::Error)]
enum LoadTemplateError {
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

fn load_template(path: &std::path::Path) -> Result<String, LoadTemplateError> {
    std::fs::read_to_string(path).map_err(LoadTemplateError::from)
}
