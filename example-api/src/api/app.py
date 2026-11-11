
from flask import Blueprint, Flask

from src.api.routes.health import health_bp

_BLUEPRINTS: tuple[Blueprint, ...] = (health_bp,)


def create_app() -> Flask:
    app = Flask(__name__)
    for blueprint in _BLUEPRINTS:
        app.register_blueprint(blueprint, url_prefix="/")
    return app