use std::{io, time::Duration};

use thiserror::Error;

#[derive(Debug, Error)]
pub enum CatalogFetchError {
    #[error("request failed: {0}")]
    Request(#[from] ureq::Error),
    #[error("failed to read catalog body: {0}")]
    ReadBody(#[from] io::Error),
    #[error("{message}")]
    HttpStatus {
        status: u16,
        body: Option<String>,
        message: String,
    },
}

impl CatalogFetchError {
    fn http_status(status: u16, body: String) -> Self {
        let body_for_message = body.clone();
        let message = if body.is_empty() {
            format!("status {}", status)
        } else {
            format!("status {}: {}", status, body_for_message)
        };

        let body = if body.is_empty() { None } else { Some(body) };

        CatalogFetchError::HttpStatus {
            status,
            body,
            message,
        }
    }
}

pub fn fetch_api_catalog_json(url: &str) -> Result<String, CatalogFetchError> {
    let agent = ureq::Agent::new_with_config(
        ureq::config::Config::builder()
            .timeout_global(Some(Duration::from_secs(10)))
            .http_status_as_error(false)
            .build(),
    );

    let mut response = agent.get(url).call().map_err(CatalogFetchError::Request)?;

    let status = response.status();
    let body = response
        .body_mut()
        .read_to_string()
        .map_err(CatalogFetchError::ReadBody)?;

    if status.is_client_error() || status.is_server_error() {
        return Err(CatalogFetchError::http_status(status.as_u16(), body));
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

        match error {
            CatalogFetchError::HttpStatus {
                status,
                body,
                message,
            } => {
                assert_eq!(status, 500);
                assert_eq!(body.as_deref(), Some("internal"));
                assert!(message.contains("500"));
                assert!(message.contains("internal"));
            }
            other => panic!("unexpected error variant: {other:?}"),
        }
    }
}
