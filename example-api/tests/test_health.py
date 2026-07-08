"""Tests for the /health endpoint."""

from src.api.app import create_app


def test_health_returns_ok() -> None:
    """GET /health returns 200, JSON body {status: ok}, and application/json."""
    app = create_app()
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.content_type == "application/json"
    assert response.get_json() == {"status": "ok"}