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

#[cfg(test)]
mod tests {
    use super::*;
    use mockito::Server;

    #[test]
    fn fetch_api_catalog_json_returns_mocked_body() {
        let mut server = Server::new();
        let _mock = server
            .mock("GET", "/catalog")
            .with_status(200)
            .with_body("{\"ok\":true}")
            .create();

        let url = format!("{}/catalog", server.url());
        let result = fetch_api_catalog_json(&url).unwrap();

        assert_eq!(result, "{\"ok\":true}");
    }

    #[test]
    fn fetch_api_catalog_json_propagates_errors() {
        let mut server = Server::new();
        let _mock = server
            .mock("GET", "/catalog")
            .with_status(500)
            .with_body("internal")
            .create();

        let url = format!("{}/catalog", server.url());
        let error = fetch_api_catalog_json(&url).unwrap_err();

        assert!(error.contains("500"));
        assert!(error.contains("internal"));
    }
}
