"""Blueprint serving the HTML interface."""
from __future__ import annotations

from flask import Blueprint, render_template

ui_bp = Blueprint("ui", __name__)


@ui_bp.get("/")
def index() -> str:
    """Render the main dashboard."""

    return render_template("index.html")
