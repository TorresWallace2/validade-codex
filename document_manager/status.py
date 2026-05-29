"""Logic for computing document validity statuses."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from .models import StatusInfo

STATUS_MAP = {
    "ok": StatusInfo(code="ok", label="OK", color="success", icon="✅"),
    "expiring": StatusInfo(code="expiring", label="A vencer", color="warning", icon="⚠️"),
    "expired": StatusInfo(code="expired", label="Vencido", color="danger", icon="❌"),
    "indeterminate": StatusInfo(code="indeterminate", label="Indeterminada", color="info", icon="♾️"),
    "not_defined": StatusInfo(code="not_defined", label="Não definido", color="secondary", icon="❓"),
}


def compute_status(
    validity_type: str,
    validity_date: Optional[date],
    warning_days: int,
) -> StatusInfo:
    """Return a status info object given validity metadata."""

    validity_type = validity_type.lower()
    if validity_type == "indeterminate":
        return STATUS_MAP["indeterminate"]
    if validity_type == "not_defined":
        return STATUS_MAP["not_defined"]

    if validity_date is None:
        return STATUS_MAP["not_defined"]

    today = date.today()
    if validity_date < today:
        return STATUS_MAP["expired"]
    if validity_date <= today + timedelta(days=warning_days):
        return STATUS_MAP["expiring"]
    return STATUS_MAP["ok"]


def parse_validity_date(value: str | None) -> Optional[date]:
    """Parse an ISO stored date string into a date."""

    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def normalise_validity_input(value: str | None) -> Optional[date]:
    """Parse a dd/mm/yyyy or compact string into a date object."""

    if not value:
        return None

    cleaned = value.replace("/", "").strip()
    if len(cleaned) != 8 or not cleaned.isdigit():
        raise ValueError("Data inválida. Use o formato dd/mm/aaaa.")

    day = int(cleaned[0:2])
    month = int(cleaned[2:4])
    year = int(cleaned[4:8])
    return date(year, month, day)


def format_display_date(value: Optional[date]) -> str:
    """Return the dd/mm/yyyy string used in the UI."""

    if value is None:
        return "Não definido"
    return value.strftime("%d/%m/%Y")
