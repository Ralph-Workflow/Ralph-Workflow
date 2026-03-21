use std::time::Duration;

pub fn fetch_api_catalog_json(url: &str) -> Result<String, String> {
    let agent = ureq::Agent::new_with_config(
        ureq::config::Config::builder()
            .timeout_global(Some(Duration::from_secs(10)))
            .http_status_as_error(false)
            .build(),
    );

    let mut response = agent
        .get(url)
        .call()
        .map_err(|e: ureq::Error| e.to_string())?;

    let status = response.status();
    let body = response
        .body_mut()
        .read_to_string()
        .map_err(|e| e.to_string())?;

    if status.is_client_error() || status.is_server_error() {
        if body.is_empty() {
            return Err(format!("status {}", status.as_u16()));
        }

        return Err(format!("status {}: {}", status.as_u16(), body));
    }

    Ok(body)
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
