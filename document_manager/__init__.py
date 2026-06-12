"""Application factory for the document manager."""
from __future__ import annotations

from pathlib import Path
from datetime import timedelta
import os

from dotenv import load_dotenv
from flask import Flask


load_dotenv()

from .config import load_config
from .db import init_app as init_db
from .auth import init_auth
from .blueprints.auth import auth_bp
from .blueprints.api import api_bp
from .blueprints.ui import ui_bp
from .blueprints.google_drive import google_drive_bp


def create_app() -> Flask:
    """Create and configure the Flask application instance."""

    package_dir = Path(__file__).resolve().parent
    project_root = package_dir.parent
    app = Flask(
        __name__,
        static_folder=str(project_root / "static"),
        template_folder=str(project_root / "templates"),
    )
    app.config['SECRET_KEY'] = app.config.get('SECRET_KEY') or os.environ.get('DOCMGR_SECRET_KEY', 'docmgr-secret-key')
    app.permanent_session_lifetime = timedelta(hours=24)
    config = load_config()
    app.config["APP_CONFIG"] = config

    init_db(app)
    init_auth(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(google_drive_bp)

    return app
