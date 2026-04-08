pub fn read_to_string(path: impl AsRef<std::path::Path>) -> std::io::Result<String> {
    std::fs::read_to_string(path)
}

pub fn write(path: impl AsRef<std::path::Path>, content: impl AsRef<[u8]>) -> std::io::Result<()> {
    std::fs::write(path, content)
}

pub fn create_dir_all(path: impl AsRef<std::path::Path>) -> std::io::Result<()> {
    std::fs::create_dir_all(path)
}

pub fn remove_file(path: impl AsRef<std::path::Path>) -> std::io::Result<()> {
    std::fs::remove_file(path)
}

pub fn read_dir_paths(
    path: impl AsRef<std::path::Path>,
) -> std::io::Result<Vec<std::path::PathBuf>> {
    std::fs::read_dir(path).map(|entries| {
        entries
            .flatten()
            .map(|entry| entry.path())
            .collect::<Vec<_>>()
    })
}

pub fn env_var(name: &str) -> Option<String> {
    std::env::var(name).ok()
}

pub fn post_anthropic_messages(
    api_key: &str,
    body: serde_json::Value,
) -> Result<serde_json::Value, String> {
    ureq::post("https://api.anthropic.com/v1/messages")
        .set("x-api-key", api_key)
        .set("anthropic-version", "2023-06-01")
        .set("content-type", "application/json")
        .send_json(body)
        .map_err(|e| format!("API call failed: {e}"))?
        .into_json()
        .map_err(|e| format!("Failed to parse API response: {e}"))
}
