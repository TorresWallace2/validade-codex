"""Application data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class StatusInfo:
    """User-facing representation of a document status."""

    code: str
    label: str
    color: str
    icon: str


@dataclass(slots=True)
class DocumentMetadata:
    """Persisted metadata for a document or folder."""

    path: Path
    validity_type: str = "not_defined"
    validity_date: Optional[datetime] = None
    warning_days: Optional[int] = None
    notes: str = ""


@dataclass(slots=True)
class DocumentItem:
    """Representation of an item for listing in the UI."""

    name: str
    path: Path
    is_directory: bool
    size_bytes: Optional[int]
    modified: datetime
    icon: str
    status: StatusInfo
    display_size: str
    display_modified: str
    validity_display: str
    extension: str = ""
    children_count: Optional[int] = None


@dataclass(slots=True)
class Preset:
    """Quick access preset for commonly used directories."""

    id: int
    name: str
    path: Path
    created_at: datetime


@dataclass(slots=True)
class AuditLogEntry:
    """Record of a significant user action."""

    id: int
    path: Path
    action: str
    username: Optional[str]
    details: Optional[str]
    created_at: datetime


def default_status() -> StatusInfo:
    """Return the default status used when no metadata is defined."""

    return StatusInfo(code="unknown", label="Não definido", color="secondary", icon="❓")
