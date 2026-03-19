use std::time::Duration;

pub fn fetch_api_catalog_json(url: &str) -> Result<String, String> {
    let agent = ureq::Agent::new_with_config(
        ureq::config::Config::builder()
            .timeout_global(Some(Duration::from_secs(10)))
            .build(),
    );

    agent
        .get(url)
        .call()
        .map_err(|e: ureq::Error| e.to_string())?
        .body_mut()
        .read_to_string()
        .map_err(|e: ureq::Error| e.to_string())
}

pub fn get_env_var(name: &str) -> Option<String> {
    std::env::var(name).ok()
}
