"""Utility helpers for dealing with the local filesystem."""
from __future__ import annotations

import mimetypes
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from flask import current_app

NATURAL_PATTERN = re.compile(r"(\d+)")

ICON_MAP = {
    "directory": "bi bi-folder-fill",
    "default": "bi bi-file-earmark",
    "pdf": "bi bi-filetype-pdf",
    "doc": "bi bi-filetype-doc",
    "docx": "bi bi-filetype-doc",
    "xls": "bi bi-filetype-xls",
    "xlsx": "bi bi-filetype-xls",
    "csv": "bi bi-filetype-csv",
    "ppt": "bi bi-filetype-ppt",
    "pptx": "bi bi-filetype-ppt",
    "png": "bi bi-filetype-png",
    "jpg": "bi bi-filetype-jpg",
    "jpeg": "bi bi-filetype-jpg",
    "gif": "bi bi-filetype-gif",
    "txt": "bi bi-filetype-txt",
    "zip": "bi bi-file-zip",
    "rar": "bi bi-file-zip",
    "json": "bi bi-filetype-json",
    "xml": "bi bi-filetype-xml",
    "html": "bi bi-filetype-html",
    "py": "bi bi-filetype-py",
    "js": "bi bi-filetype-js",
    "css": "bi bi-filetype-css",
}


def human_readable_size(size_bytes: int | None) -> str:
    """Return a human friendly size string."""

    if size_bytes is None:
        return "--"

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}" if unit == "B" else f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def natural_key(text: str) -> Sequence[object]:
    """Return a key that enables natural sorting of strings."""

    parts = NATURAL_PATTERN.split(text)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def determine_icon(path: Path, is_directory: bool) -> str:
    """Return the icon CSS class for the given item."""

    if is_directory:
        return ICON_MAP["directory"]

    extension = path.suffix.lower().lstrip(".")
    if extension in ICON_MAP:
        return ICON_MAP[extension]

    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("image"):
        return "bi bi-file-earmark-image"
    if mime and mime.startswith("video"):
        return "bi bi-file-earmark-play"
    if mime and mime.startswith("audio"):
        return "bi bi-file-earmark-music"
    return ICON_MAP["default"]


def normalize_path(path: str) -> Path:
    """Normalise and resolve a path, ensuring it is accessible."""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        base_paths = current_app.config["APP_CONFIG"].base_paths
        base = base_paths[0] if base_paths else Path.cwd()
        candidate = (base / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def is_path_allowed(path: Path) -> bool:
    """Check if the given path falls within the configured base directories."""

    allowed_roots = current_app.config["APP_CONFIG"].base_paths
    if not allowed_roots:
        return True
    return any(path.is_relative_to(root.resolve()) for root in allowed_roots)


def iter_directory(path: Path) -> Iterable[Path]:
    """Yield entries from a directory, ignoring inaccessible items."""

    with os.scandir(path) as entries:
        for entry in entries:
            try:
                yield Path(entry.path)
            except OSError:
                continue
