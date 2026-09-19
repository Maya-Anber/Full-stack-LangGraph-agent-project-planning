import logging
import os

from flask import Flask

from app.config import BASE_DIR, Config
from app.extensions import db


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Log INFO and above to the console so webhook activity is visible.
    _setup_logging(app)

    # Say so at boot instead of letting the first chat request fail: the provider
    # picked by LLM_PROVIDER needs its own key, and .env is only read once, at
    # import -- editing it without restarting changes nothing.
    from app.agent.llm import missing_credentials_message

    problem = missing_credentials_message(app.config.get("LLM_PROVIDER", "gemini"), app.config)
    if problem:
        app.logger.warning("The agent is not ready: %s", problem)

    os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)

    db.init_app(app)

    from app.routes.chat import chat_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.webhook import webhook_bp

    app.register_blueprint(chat_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(webhook_bp)

    with app.app_context():
        db.create_all()

        from app.seed import seed_if_empty

        seed_if_empty()

        from app.rag.ingest import refresh_index

        refresh_index()

    return app


def _setup_logging(app: Flask) -> None:
    """Make application and webhook logs visible at INFO level."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("app.routes.webhook").setLevel(logging.INFO)
    logging.getLogger("app.agent").setLevel(logging.INFO)
    logging.getLogger("requests").setLevel(logging.WARNING)
