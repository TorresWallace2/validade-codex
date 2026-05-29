"""Domain services implementing document management operations."""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from flask import current_app

from .. import db
from ..db import DEFAULT_LAST_PATH_KEY, WARNING_DAYS_KEY
from ..file_utils import (
    determine_icon,
    human_readable_size,
    is_path_allowed,
    iter_directory,
    natural_key,
    normalize_path,
)
from ..models import DocumentItem, DocumentMetadata
from ..status import compute_status, format_display_date, normalise_validity_input, parse_validity_date


class DocumentServiceError(RuntimeError):
    """Base class for service level errors."""


class InvalidPathError(DocumentServiceError):
    """Raised when a path is not accessible or outside the allowed scope."""


class OperationNotPermittedError(DocumentServiceError):
    """Raised when an operation on a path is not permitted."""


ALLOWED_SORT_FIELDS = {"name", "size", "modified", "status", "validity"}
VALIDITY_IN_FILENAME_RE = re.compile(
    r"\bVAL(?:IDADE)?\.?\s*([0-3]?\d[\/\-.][0-1]?\d[\/\-.]\d{4})\b",
    re.IGNORECASE,
)


def _default_root() -> Path:
    base_paths = current_app.config["APP_CONFIG"].base_paths
    if base_paths:
        return base_paths[0].resolve()
    return Path.home().resolve()


def _extract_validity_from_filename(filename: str) -> Optional[date]:
    match = VALIDITY_IN_FILENAME_RE.search(filename or "")
    if not match:
        return None

    raw_value = match.group(1).strip().replace(".", "-").replace("/", "-")
    parts = raw_value.split("-")
    if len(parts) != 3:
        return None

    day_str, month_str, year_str = parts
    if len(year_str) != 4:
        return None

    try:
        return date(int(year_str), int(month_str), int(day_str))
    except ValueError:
        return None


def _ensure_accessible(path: Path) -> Path:
    if not path.exists():
        raise InvalidPathError(f"O caminho {path} não existe.")
    if not is_path_allowed(path):
        raise OperationNotPermittedError("Caminho fora do escopo permitido.")
    return path


def _load_last_path() -> Path:
    rows = db.query("SELECT value FROM settings WHERE key = ?", (DEFAULT_LAST_PATH_KEY,))
    if rows:
        candidate_str = rows[0]["value"]
        try:
            path = normalize_path(candidate_str)
        except DocumentServiceError:
            path = _default_root()
    else:
        path = _default_root()

    if not path.exists() or not path.is_dir():
        path = _default_root()
    return _ensure_accessible(path)


def _store_last_path(path: Path) -> None:
    db.execute(
        "REPLACE INTO settings(key, value) VALUES(?, ?)",
        (DEFAULT_LAST_PATH_KEY, str(path)),
    )


def _require_user_id(username: str) -> int:
    cleaned = (username or '').strip().upper()
    if not cleaned:
        raise DocumentServiceError('Usuario invalido.')
    rows = db.query(
        "SELECT id FROM users WHERE username = ?",
        (cleaned,),
    )
    if not rows:
        raise DocumentServiceError('Usuario nao encontrado.')
    return rows[0]['id']


def _fetch_metadata_map(paths: Sequence[Path]) -> dict[str, DocumentMetadata]:
    if not paths:
        return {}
    rows = db.query(
        f"SELECT path, validity_type, validity_date, warning_days, notes FROM documents WHERE path IN ({','.join(['?']*len(paths))})",
        [str(p) for p in paths],
    )
    metadata: dict[str, DocumentMetadata] = {}
    for row in rows:
        metadata[row["path"]] = DocumentMetadata(
            path=Path(row["path"]),
            validity_type=row["validity_type"],
            validity_date=parse_validity_date(row["validity_date"]),
            warning_days=row["warning_days"],
            notes=row["notes"] or "",
        )
    return metadata


def _auto_apply_validity_from_filename(path: Path, metadata: DocumentMetadata | None) -> DocumentMetadata | None:
    if path.is_dir():
        return metadata

    current_type = (metadata.validity_type if metadata else "not_defined").lower()
    if current_type in {"defined", "indeterminate"}:
        return metadata

    inferred_date = _extract_validity_from_filename(path.name)
    if inferred_date is None:
        return metadata

    warning = _default_warning_days(metadata)
    warning_value = metadata.warning_days if metadata and metadata.warning_days else warning
    timestamp = datetime.utcnow().isoformat(timespec="seconds")

    db.touch_document(str(path))
    db.execute(
        """
        UPDATE documents
        SET validity_type = ?,
            validity_date = ?,
            warning_days = COALESCE(warning_days, ?),
            updated_at = ?
        WHERE path = ?
        """,
        (
            "defined",
            inferred_date.strftime("%Y-%m-%d"),
            warning_value,
            timestamp,
            str(path),
        ),
    )
    db.record_audit(
        str(path),
        "auto_validity_from_filename",
        None,
        f"Data={inferred_date.strftime('%d/%m/%Y')}",
    )

    return DocumentMetadata(
        path=path,
        validity_type="defined",
        validity_date=inferred_date,
        warning_days=warning_value,
        notes=metadata.notes if metadata else "",
    )


def _default_warning_days(metadata: DocumentMetadata | None) -> int:
    if metadata and metadata.warning_days:
        return metadata.warning_days
    return current_app.config["APP_CONFIG"].warning_days


def _build_item(path: Path, metadata: DocumentMetadata | None) -> DocumentItem:
    is_dir = path.is_dir()
    size = None
    modified = datetime.fromtimestamp(0)
    try:
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime)
        if not is_dir:
            size = stat.st_size
    except OSError:
        pass

    warning_days = _default_warning_days(metadata)
    validity_type = metadata.validity_type if metadata else "not_defined"
    validity_date = metadata.validity_date if metadata else None
    status = compute_status(validity_type, validity_date, warning_days)

    if validity_type == "indeterminate":
        validity_display = "Indeterminada"
    elif validity_type == "not_defined":
        validity_display = "Não definido"
    else:
        validity_display = format_display_date(validity_date)

    return DocumentItem(
        name=path.name or str(path),
        path=path,
        is_directory=is_dir,
        size_bytes=size,
        modified=modified,
        icon=determine_icon(path, is_dir),
        status=status,
        display_size=human_readable_size(size),
        display_modified=modified.strftime("%d/%m/%Y %H:%M"),
        validity_display=validity_display,
        extension=path.suffix.lower().lstrip("."),
    )


def _sort_items(items: list[DocumentItem], sort_by: str, sort_direction: str) -> None:
    reverse = sort_direction == "desc"

    def base_key(item: DocumentItem):
        return not item.is_directory, natural_key(item.name)

    items.sort(key=base_key)

    if sort_by == "name":
        items.sort(key=base_key, reverse=reverse)
    elif sort_by == "size":
        items.sort(
            key=lambda item: (not item.is_directory, item.size_bytes or 0),
            reverse=reverse,
        )
    elif sort_by == "modified":
        items.sort(
            key=lambda item: (not item.is_directory, item.modified.timestamp()),
            reverse=reverse,
        )
    elif sort_by == "validity":
        def validity_key(item: DocumentItem) -> tuple:
            validity_rank = {
                "expired": 0,
                "expiring": 1,
                "ok": 2,
                "indeterminate": 3,
                "not_defined": 4,
            }.get(item.status.code, 5)
            validity_date = item.modified.timestamp()
            if item.validity_display not in {"Não definido", "Indeterminada"}:
                try:
                    dt = datetime.strptime(item.validity_display, "%d/%m/%Y")
                    validity_date = dt.timestamp()
                except ValueError:
                    pass
            return not item.is_directory, validity_rank, validity_date

        items.sort(key=validity_key, reverse=reverse)
    elif sort_by == "status":
        status_order = {
            code: index
            for index, code in enumerate(["ok", "expiring", "expired", "indeterminate", "not_defined"])
        }
        items.sort(
            key=lambda item: (not item.is_directory, status_order.get(item.status.code, 99), natural_key(item.name)),
            reverse=reverse,
        )


def _build_breadcrumbs(path: Path) -> list[dict[str, str]]:
    parts: list[dict[str, str]] = []
    if path.drive:
        root = Path(path.drive + "\\")
        parts.append({"label": path.drive, "path": str(root)})
        cumulative = root
        for component in path.parts[1:]:
            cumulative = cumulative / component
            parts.append({"label": component, "path": str(cumulative)})
    else:
        cumulative = Path("/")
        parts.append({"label": "/", "path": str(cumulative)})
        for component in path.parts[1:]:
            cumulative = cumulative / component
            parts.append({"label": component, "path": str(cumulative)})
    return parts


def _resolve_filters(status_filter: Sequence[str] | None) -> set[str]:
    if not status_filter:
        return set()
    return {code.lower() for code in status_filter}


def list_directory_items(
    path_str: str | None,
    sort_by: str = "name",
    sort_direction: str = "asc",
    page: int = 1,
    page_size: int | None = None,
    search: str | None = None,
    status_filter: Sequence[str] | None = None,
) -> dict[str, object]:
    """Return paginated directory listing with metadata."""

    if path_str:
        base_path = normalize_path(path_str)
    else:
        base_path = _load_last_path()

    _ensure_accessible(base_path)
    if not base_path.is_dir():
        raise InvalidPathError("O caminho informado não é uma pasta.")

    _store_last_path(base_path)

    if sort_by not in ALLOWED_SORT_FIELDS:
        sort_by = "name"

    config = current_app.config["APP_CONFIG"]
    page_size = page_size or config.default_page_size
    page_size = max(1, min(page_size, config.max_page_size))
    page = max(page, 1)

    entries = list(iter_directory(base_path))
    metadata_map = _fetch_metadata_map(entries)

    items: list[DocumentItem] = []
    for entry in entries:
        metadata = _auto_apply_validity_from_filename(entry, metadata_map.get(str(entry)))
        if metadata:
            metadata_map[str(entry)] = metadata
        items.append(_build_item(entry, metadata))

    if search:
        lowered = search.lower()
        items = [item for item in items if lowered in item.name.lower()]

    status_allowed = _resolve_filters(status_filter)
    if status_allowed:
        items = [item for item in items if item.status.code in status_allowed]

    _sort_items(items, sort_by, sort_direction)

    total_items = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]

    breadcrumbs = _build_breadcrumbs(base_path)
    parent = str(base_path.parent) if base_path != base_path.parent else str(base_path)

    return {
        "items": [
            {
                "name": item.name,
                "path": str(item.path),
                "type": "directory" if item.is_directory else "file",
                "size": item.display_size,
                "size_bytes": item.size_bytes,
                "modified": item.display_modified,
                "modified_ts": item.modified.isoformat(),
                "icon": item.icon,
                "status": asdict(item.status),
                "validity": item.validity_display,
                "extension": item.extension,
            }
            for item in page_items
        ],
        "total": total_items,
        "page": page,
        "page_size": page_size,
        "has_more": end < total_items,
        "breadcrumbs": breadcrumbs,
        "current_path": str(base_path),
        "parent_path": parent,
    }


def get_details(path_str: str) -> dict[str, object]:
    path = normalize_path(path_str)
    _ensure_accessible(path)

    metadata_rows = db.query(
        "SELECT validity_type, validity_date, warning_days, notes FROM documents WHERE path = ?",
        (str(path),),
    )
    metadata = None
    if metadata_rows:
        row = metadata_rows[0]
        metadata = DocumentMetadata(
            path=path,
            validity_type=row["validity_type"],
            validity_date=parse_validity_date(row["validity_date"]),
            warning_days=row["warning_days"],
            notes=row["notes"] or "",
        )
    else:
        metadata = DocumentMetadata(path=path)

    metadata = _auto_apply_validity_from_filename(path, metadata) or metadata

    warning_days = _default_warning_days(metadata)
    status = compute_status(metadata.validity_type, metadata.validity_date, warning_days)

    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime)
    size = stat.st_size if path.is_file() else None

    validity_display = "Não definido"
    if metadata.validity_type == "indeterminate":
        validity_display = "Indeterminada"
    elif metadata.validity_type == "defined" and metadata.validity_date:
        validity_display = format_display_date(metadata.validity_date)

    days_remaining: Optional[int]
    if metadata.validity_type == "defined" and metadata.validity_date:
        days_remaining = (metadata.validity_date - date.today()).days
    else:
        days_remaining = None

    return {
        "name": path.name or str(path),
        "path": str(path),
        "size": human_readable_size(size),
        "modified": modified.strftime("%d/%m/%Y %H:%M"),
        "validity_type": metadata.validity_type,
        "validity": validity_display,
        "warning_days": warning_days,
        "status": asdict(status),
        "notes": metadata.notes,
        "validity_days_remaining": days_remaining,
    }


def set_validity(path_str: str, validity_type: str, validity_value: str | None, warning_days: int | None) -> dict[str, object]:
    path = normalize_path(path_str)
    _ensure_accessible(path)

    validity_type = validity_type.lower()
    if validity_type not in {"defined", "indeterminate", "not_defined"}:
        raise DocumentServiceError("Tipo de validade inválido.")

    validity_date: Optional[date]
    validity_date = None
    if validity_type == "defined":
        validity_date = normalise_validity_input(validity_value)

    warning = warning_days or current_app.config["APP_CONFIG"].warning_days

    db.touch_document(str(path))
    db.execute(
        """
        UPDATE documents
        SET validity_type = ?,
            validity_date = ?,
            warning_days = ?,
            updated_at = ?
        WHERE path = ?
        """,
        (
            validity_type,
            validity_date.strftime("%Y-%m-%d") if validity_date else None,
            warning,
            datetime.utcnow().isoformat(timespec="seconds"),
            str(path),
        ),
    )
    db.record_audit(str(path), "set_validity", None, f"Tipo={validity_type}")

    warning_days = warning
    status = compute_status(validity_type, validity_date, warning_days)
    validity_display = "Não definido"
    if validity_type == "indeterminate":
        validity_display = "Indeterminada"
    elif validity_type == "defined" and validity_date:
        validity_display = format_display_date(validity_date)

    return {
        "status": asdict(status),
        "validity": validity_display,
        "validity_type": validity_type,
    }


def set_notes(path_str: str, notes: str) -> dict[str, object]:
    path = normalize_path(path_str)
    _ensure_accessible(path)

    db.touch_document(str(path))
    db.execute(
        "UPDATE documents SET notes = ?, updated_at = ? WHERE path = ?",
        (notes, datetime.utcnow().isoformat(timespec="seconds"), str(path)),
    )
    db.record_audit(str(path), "set_notes", None, "Notas atualizadas")
    return {"notes": notes}


def rename_item(path_str: str, new_name: str) -> dict[str, object]:
    path = normalize_path(path_str)
    _ensure_accessible(path)

    destination = path.with_name(new_name)
    if destination.exists():
        raise DocumentServiceError("Já existe um item com esse nome.")

    path.rename(destination)

    db.execute(
        "UPDATE documents SET path = ?, updated_at = ? WHERE path = ?",
        (
            str(destination),
            datetime.utcnow().isoformat(timespec="seconds"),
            str(path),
        ),
    )
    db.record_audit(str(destination), "rename", None, f"Anterior={path}")

    return {"path": str(destination), "name": destination.name}



def _delete_path(path: Path) -> None:
    _ensure_accessible(path)

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()

    db.execute("DELETE FROM documents WHERE path = ?", (str(path),))
    db.record_audit(str(path), "delete", None, None)


def delete_item(path_str: str) -> None:
    _delete_path(normalize_path(path_str))


def delete_items(paths: Iterable[str] | None) -> int:
    items = list(paths or [])
    if not items:
        raise DocumentServiceError("Informe ao menos um caminho.")

    seen: set[str] = set()
    normalized: list[Path] = []
    for raw in items:
        value = str(raw).strip() if raw is not None else ''
        if not value:
            continue
        candidate = normalize_path(value)
        key = str(candidate)
        if key in seen:
            continue
        normalized.append(candidate)
        seen.add(key)

    if not normalized:
        raise DocumentServiceError("Nenhum caminho válido informado.")

    for path in normalized:
        _delete_path(path)

    return len(normalized)



def create_directory(parent_str: str, folder_name: str) -> dict[str, object]:
    parent = normalize_path(parent_str)
    _ensure_accessible(parent)
    if not parent.is_dir():
        raise DocumentServiceError("O caminho base não é uma pasta.")

    destination = parent / folder_name
    destination.mkdir(parents=False, exist_ok=False)
    db.record_audit(str(destination), "create_directory", None, None)
    return {"path": str(destination), "name": destination.name}


def create_file(parent_str: str, file_name: str) -> dict[str, object]:
    parent = normalize_path(parent_str)
    _ensure_accessible(parent)
    if not parent.is_dir():
        raise DocumentServiceError("O caminho base não é uma pasta.")

    destination = parent / file_name
    destination.touch(exist_ok=False)
    db.record_audit(str(destination), "create_file", None, None)
    return {"path": str(destination), "name": destination.name}


def save_upload(parent_str: str, storage_objects: Iterable) -> list[dict[str, object]]:
    parent = normalize_path(parent_str)
    _ensure_accessible(parent)
    if not parent.is_dir():
        raise DocumentServiceError("O caminho base não é uma pasta.")

    saved = []
    for storage in storage_objects:
        filename = Path(storage.filename).name
        destination = parent / filename
        storage.save(str(destination))
        db.record_audit(str(destination), "upload", None, None)
        auto_validity_date = _extract_validity_from_filename(filename)
        auto_validity_display = None
        if auto_validity_date is not None:
            auto_validity_display = auto_validity_date.strftime("%d/%m/%Y")
            set_validity(
                str(destination),
                "defined",
                auto_validity_display,
                None,
            )

        saved.append(
            {
                "path": str(destination),
                "name": filename,
                "auto_validity": auto_validity_display,
            }
        )
    return saved


def _normalise_transfer_sources(paths: Sequence[str]) -> list[Path]:
    if not paths:
        raise DocumentServiceError("Selecione ao menos um item.")

    normalised: list[Path] = [normalize_path(path_str) for path_str in paths]
    normalised.sort(key=lambda candidate: len(str(candidate)))

    unique: list[Path] = []
    for candidate in normalised:
        resolved = _ensure_accessible(candidate)
        if any(resolved == existing or resolved.is_relative_to(existing) for existing in unique):
            continue
        unique.append(resolved)
    return unique


def _prepare_transfer_targets(paths: Sequence[str], destination_str: str) -> list[tuple[Path, Path]]:
    sources = _normalise_transfer_sources(paths)

    destination = normalize_path(destination_str)
    destination = _ensure_accessible(destination)
    if not destination.is_dir():
        raise DocumentServiceError("O destino precisa ser uma pasta.")

    prepared: list[tuple[Path, Path]] = []
    seen_targets: set[str] = set()
    for source in sources:
        target = (destination / source.name).resolve()
        if target == source:
            raise DocumentServiceError("Selecione um destino diferente do local atual.")
        if source.is_dir() and destination.is_relative_to(source):
            raise DocumentServiceError("Não é possível transferir uma pasta para dentro dela mesma.")
        if target.exists():
            raise DocumentServiceError(f"Já existe '{target.name}' no destino.")
        if not is_path_allowed(target):
            raise OperationNotPermittedError("Destino fora do escopo permitido.")

        key = str(target).lower() if os.name == "nt" else str(target)
        if key in seen_targets:
            raise DocumentServiceError(f"Destino duplicado detectado para '{target.name}'.")
        seen_targets.add(key)

        prepared.append((source, target))

    return prepared


def _update_metadata_on_move(source: Path, target: Path) -> None:
    src_str = str(source)
    prefix = src_str + os.sep
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    rows = db.query(
        "SELECT path FROM documents WHERE path = ? OR path LIKE ?",
        (src_str, f"{prefix}%"),
    )
    for row in rows:
        current = Path(row["path"])
        if current == source:
            new_path = target
        else:
            try:
                relative = current.relative_to(source)
            except ValueError:
                continue
            new_path = target / relative
        db.execute(
            "UPDATE documents SET path = ?, updated_at = ? WHERE path = ?",
            (str(new_path), timestamp, str(current)),
        )


def _copy_metadata_records(source: Path, target: Path) -> None:
    src_str = str(source)
    prefix = src_str + os.sep
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    rows = db.query(
        "SELECT path, validity_type, validity_date, warning_days, notes FROM documents WHERE path = ? OR path LIKE ?",
        (src_str, f"{prefix}%"),
    )
    for row in rows:
        current = Path(row["path"])
        if current == source:
            new_path = target
        else:
            try:
                relative = current.relative_to(source)
            except ValueError:
                continue
            new_path = target / relative
        db.execute(
            """
            INSERT OR REPLACE INTO documents(path, validity_type, validity_date, warning_days, notes, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(new_path),
                row["validity_type"],
                row["validity_date"],
                row["warning_days"],
                row["notes"],
                timestamp,
                timestamp,
            ),
        )


def move_items(paths: Sequence[str], destination_str: str) -> dict[str, object]:
    prepared = _prepare_transfer_targets(paths, destination_str)
    moved: list[str] = []

    for source, target in prepared:
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:  # pragma: no cover - OS dependent
            raise DocumentServiceError(f"Falha ao mover '{source.name}': {exc}") from exc

        _update_metadata_on_move(source, target)
        db.record_audit(str(target), "move", None, f"origem={source}")
        moved.append(str(target))

    return {"items": moved}


def copy_items(paths: Sequence[str], destination_str: str) -> dict[str, object]:
    prepared = _prepare_transfer_targets(paths, destination_str)
    copied: list[str] = []

    for source, target in prepared:
        try:
            if source.is_dir():
                shutil.copytree(str(source), str(target))
            else:
                shutil.copy2(str(source), str(target))
        except OSError as exc:  # pragma: no cover - OS dependent
            raise DocumentServiceError(f"Falha ao copiar '{source.name}': {exc}") from exc

        _copy_metadata_records(source, target)
        db.record_audit(str(target), "copy", None, f"origem={source}")
        copied.append(str(target))

    return {"items": copied}


def export_directory_snapshot(path_str: str, sort_by: str = "name", sort_direction: str = "asc") -> tuple[str, bytes]:
    result = list_directory_items(
        path_str,
        sort_by=sort_by,
        sort_direction=sort_direction,
        page=1,
        page_size=current_app.config["APP_CONFIG"].max_page_size,
    )

    items = result["items"]
    total = result["total"]
    collected = items.copy()
    while len(collected) < total and result["has_more"]:
        page = result["page"] + 1
        result = list_directory_items(
            path_str,
            sort_by=sort_by,
            sort_direction=sort_direction,
            page=page,
            page_size=current_app.config["APP_CONFIG"].max_page_size,
        )
        collected.extend(result["items"])

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Nome", "Caminho", "Tipo", "Validade", "Status", "Observações"])
    for item in collected:
        metadata_rows = db.query(
            "SELECT notes FROM documents WHERE path = ?",
            (item["path"],),
        )
        notes = metadata_rows[0]["notes"] if metadata_rows else ""
        writer.writerow([
            item["name"],
            item["path"],
            item["type"],
            item["validity"],
            item["status"]["label"],
            notes,
        ])
    csv_bytes = output.getvalue().encode("utf-8-sig")
    output.close()
    filename = f"relatorio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return filename, csv_bytes


def open_with_system(path_str: str) -> None:
    path = normalize_path(path_str)
    _ensure_accessible(path)

    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

    db.record_audit(str(path), "open", None, None)


def open_in_explorer(path_str: str) -> None:
    path = normalize_path(path_str)
    _ensure_accessible(path)

    directory = path if path.is_dir() else path.parent
    if os.name == "nt":
        try:
            subprocess.Popen(["explorer", str(directory)])
        except FileNotFoundError:
            os.startfile(str(directory))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(directory)])
    else:
        subprocess.Popen(["xdg-open", str(directory)])

    db.record_audit(str(directory), "open_folder", None, None)


def list_presets(username: str) -> list[dict[str, object]]:
    user_id = _require_user_id(username)
    rows = db.query(
        "SELECT id, name, path, created_at FROM presets WHERE user_id = ? ORDER BY name ASC",
        (user_id,),
    )
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "path": row["path"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _is_google_drive_virtual_path(raw_path: str) -> bool:
    return raw_path.startswith('gdrive://') or raw_path.startswith('gdrive:/')


def _normalize_google_drive_virtual_path(raw_path: str) -> str:
    if raw_path.startswith('gdrive://'):
        return raw_path
    if raw_path.startswith('gdrive:/'):
        return 'gdrive://' + raw_path[len('gdrive:/'):].lstrip('/')
    return raw_path


def add_preset(username: str, name: str, path_str: str) -> dict[str, object]:
    if not name.strip():
        raise DocumentServiceError('Informe um nome para o pregao.')

    raw_path = (path_str or '').strip()
    if not raw_path:
        raise DocumentServiceError('Informe o caminho do pregao.')

    # Pregoes do Google Drive usam caminhos virtuais, exemplo:
    # gdrive://root ou gdrive://<id-da-pasta>. Eles nao existem no disco
    # do Render, entao nao podem passar pela validacao de Path local.
    if _is_google_drive_virtual_path(raw_path):
        preset_path = _normalize_google_drive_virtual_path(raw_path)
    else:
        path = normalize_path(raw_path)
        _ensure_accessible(path)
        if not path.is_dir():
            raise DocumentServiceError('Somente pastas podem ser salvas como preset.')
        preset_path = str(path)

    user_id = _require_user_id(username)
    try:
        db.execute(
            "INSERT INTO presets(user_id, name, path, created_at) VALUES(?, ?, ?, ?)",
            (user_id, name, preset_path, datetime.utcnow().isoformat(timespec="seconds")),
        )
    except sqlite3.IntegrityError as exc:
        raise DocumentServiceError('Preset ja existe para este usuario.') from exc
    return {"name": name, "path": preset_path}


def delete_preset(username: str, preset_id: int) -> None:
    user_id = _require_user_id(username)
    deleted = db.execute("DELETE FROM presets WHERE id = ? AND user_id = ?", (preset_id, user_id))
    if deleted == 0:
        raise DocumentServiceError('Preset nao encontrado.')


def list_favorites(username: str) -> list[dict[str, object]]:
    user_id = _require_user_id(username)
    rows = db.query(
        "SELECT id, name, path, created_at FROM favorites WHERE user_id = ? ORDER BY name ASC",
        (user_id,),
    )
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "path": row["path"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def add_favorite(username: str, name: str, path_str: str) -> dict[str, object]:
    if not name.strip():
        raise DocumentServiceError('Informe um nome para o favorito.')

    raw_path = (path_str or '').strip()
    if not raw_path:
        raise DocumentServiceError('Informe o caminho do favorito.')

    # Favoritos do Google Drive usam caminhos virtuais, exemplo:
    # gdrive://root ou gdrive://<id-da-pasta>. Eles nao existem no disco
    # do Render, entao nao podem passar pela validacao de Path local.
    if _is_google_drive_virtual_path(raw_path):
        favorite_path = _normalize_google_drive_virtual_path(raw_path)
    else:
        path = normalize_path(raw_path)
        _ensure_accessible(path)
        if not path.is_dir():
            raise DocumentServiceError('Somente pastas podem ser adicionadas aos favoritos.')
        favorite_path = str(path)

    user_id = _require_user_id(username)
    try:
        db.execute(
            "INSERT INTO favorites(user_id, name, path, created_at) VALUES(?, ?, ?, ?)",
            (user_id, name, favorite_path, datetime.utcnow().isoformat(timespec="seconds")),
        )
    except sqlite3.IntegrityError as exc:
        raise DocumentServiceError('Favorito ja existe para este usuario.') from exc

    return {"id": None, "name": name, "path": favorite_path}


def delete_favorite(username: str, name: str) -> None:
    user_id = _require_user_id(username)
    deleted = db.execute("DELETE FROM favorites WHERE user_id = ? AND name = ?", (user_id, name))
    if deleted == 0:
        raise DocumentServiceError('Favorito nao encontrado.')


def get_warning_days() -> int:
    rows = db.query("SELECT value FROM settings WHERE key = ?", (WARNING_DAYS_KEY,))
    if rows:
        return int(rows[0]["value"])
    return current_app.config["APP_CONFIG"].warning_days


def update_warning_days(value: int) -> int:
    db.execute("REPLACE INTO settings(key, value) VALUES(?, ?)", (WARNING_DAYS_KEY, str(value)))
    current_app.config["APP_CONFIG"].warning_days = value
    return value


def navigate_to_path(
    path_str: str,
    sort_by: str = "name",
    sort_direction: str = "asc",
    search: str | None = None,
    status_filter: Sequence[str] | None = None,
    page_size: int | None = None,
) -> dict[str, object]:
    """Navigate to the requested directory and return the listing payload."""

    return list_directory_items(
        path_str=path_str,
        sort_by=sort_by,
        sort_direction=sort_direction,
        page=1,
        page_size=page_size,
        search=search,
        status_filter=status_filter,
    )


def get_last_path() -> str:
    """Return the last persisted directory path."""

    path = _load_last_path()
    return str(path)
