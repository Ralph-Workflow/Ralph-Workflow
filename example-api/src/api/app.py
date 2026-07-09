"""Flask application factory for the example API."""

from flask import Blueprint, Flask

from src.api.routes.health import health_bp

# Tuple of every Blueprint this app exposes. Adding a new blueprint is a
# single-line change here; no other module in this package needs editing.
_BLUEPRINTS: tuple[Blueprint, ...] = (health_bp,)


def create_app() -> Flask:
    """Build and return a configured Flask application.

    Using the factory pattern keeps module-level state empty so tests can
    construct isolated app instances without inheriting globals.
    """
    app = Flask(__name__)
    for blueprint in _BLUEPRINTS:
        app.register_blueprint(blueprint, url_prefix="/")
    return app