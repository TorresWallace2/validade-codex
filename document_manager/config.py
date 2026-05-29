"""Application configuration module."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass(slots=True)
class AppConfig:
    """Container for runtime configuration values."""

    database_path: Path = Path("instance") / "documents.db"
    base_paths: List[Path] = field(default_factory=list)
    default_page_size: int = 50
    max_page_size: int = 200
    warning_days: int = 15


def load_config() -> AppConfig:
    """Return default application configuration.

    The function can later be expanded to load values from environment
    variables or configuration files.
    """

    config = AppConfig()
    # Ensure database directory exists.
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    return config
