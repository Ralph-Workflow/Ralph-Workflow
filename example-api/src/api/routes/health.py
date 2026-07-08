"""Health-check blueprint exposing GET /health."""

from flask import Blueprint, jsonify

# A Blueprint groups related routes and keeps registration isolated.
health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health() -> tuple:
    """Return a small JSON payload used by load balancers and uptime monitors.

    ``flask.jsonify`` sets Content-Type to ``application/json`` and returns a
    ``Response`` object, so we hand it back directly without any tuple wrapping
    that could conflate the status code.
    """
    return jsonify({"status": "ok"})