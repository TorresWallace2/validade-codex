"""Google Drive integration helpers with metadata parity for document manager."""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import date, datetime
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import quote, unquote

from flask import current_app, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from .. import db
from ..status import (
    compute_status,
    format_display_date,
    normalise_validity_input,
    parse_validity_date,
)

# Drive precisa de escopo de escrita para criar, renomear, mover, excluir e upload.
# Se o token antigo foi gerado como readonly, desconecte e conecte novamente.
SCOPES = ["https://www.googleapis.com/auth/drive"]

DRIVE_PREFIX = "gdrive://"
ROOT_PATH = "gdrive://root"
FOLDER_MIME = "application/vnd.google-apps.folder"

VALIDITY_IN_FILENAME_RE = re.compile(
    r"\bVAL(?:IDADE)?\.?\s*([0-3]?\d[\/\-.][0-1]?\d[\/\-.]\d{4})\b",
    re.IGNORECASE,
)


class GoogleDriveError(Exception):
    """Raised when the Google Drive integration cannot complete an operation."""


def _redirect_uri() -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI", "").strip()


def _client_config() -> dict[str, Any]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = _redirect_uri()
    if not client_id or not client_secret or not redirect_uri:
        raise GoogleDriveError(
            "Google Drive nao configurado. Configure GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET e GOOGLE_REDIRECT_URI no Render."
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def create_flow() -> Flow:
    if current_app.debug:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    return Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=_redirect_uri())


def authorization_url() -> str:
    flow = create_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["google_oauth_state"] = state
    code_verifier = getattr(flow, "code_verifier", None)
    if code_verifier:
        session["google_oauth_code_verifier"] = code_verifier
    return auth_url


def finish_authorization(authorization_response: str, state: str | None) -> None:
    expected_state = session.get("google_oauth_state")
    if not expected_state or state != expected_state:
        raise GoogleDriveError("Sessao OAuth invalida. Tente conectar novamente.")
    flow = create_flow()
    code_verifier = session.get("google_oauth_code_verifier")
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(authorization_response=authorization_response)
    session["google_drive_credentials"] = credentials_to_dict(flow.credentials)
    session.pop("google_oauth_state", None)
    session.pop("google_oauth_code_verifier", None)


def credentials_to_dict(credentials: Credentials) -> dict[str, Any]:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }


def credentials_from_session() -> Credentials | None:
    data = session.get("google_drive_credentials")
    if not data:
        return None
    return Credentials(**data)


def is_connected() -> bool:
    return credentials_from_session() is not None


def disconnect() -> None:
    session.pop("google_drive_credentials", None)
    session.pop("google_oauth_state", None)
    session.pop("google_oauth_code_verifier", None)


def service():
    credentials = credentials_from_session()
    if not credentials:
        raise GoogleDriveError("Google Drive nao conectado.")
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def is_drive_path(path: str | None) -> bool:
    return bool(path and str(path).startswith(DRIVE_PREFIX))


def id_to_path(file_id: str) -> str:
    return f"{DRIVE_PREFIX}{quote(file_id, safe='')}"


def path_to_id(path: str | None) -> str:
    if not path or path == ROOT_PATH:
        return "root"
    if not str(path).startswith(DRIVE_PREFIX):
        raise GoogleDriveError("Caminho do Google Drive invalido.")
    file_id = unquote(str(path)[len(DRIVE_PREFIX):])
    return file_id or "root"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _default_warning_days() -> int:
    try:
        return int(current_app.config["APP_CONFIG"].warning_days)
    except Exception:
        return 15


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


def _ensure_metadata_row(path: str) -> None:
    db.touch_document(path)


def _metadata_for_path(path: str) -> dict[str, Any]:
    rows = db.query(
        "SELECT validity_type, validity_date, warning_days, notes FROM documents WHERE path = ?",
        (path,),
    )
    if not rows:
        return {
            "validity_type": "not_defined",
            "validity_date": None,
            "warning_days": _default_warning_days(),
            "notes": "",
        }
    row = rows[0]
    return {
        "validity_type": row["validity_type"] or "not_defined",
        "validity_date": parse_validity_date(row["validity_date"]),
        "warning_days": row["warning_days"] or _default_warning_days(),
        "notes": row["notes"] or "",
    }


def _metadata_map(paths: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not paths:
        return {}
    placeholders = ",".join(["?"] * len(paths))
    rows = db.query(
        f"SELECT path, validity_type, validity_date, warning_days, notes FROM documents WHERE path IN ({placeholders})",
        paths,
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        result[row["path"]] = {
            "validity_type": row["validity_type"] or "not_defined",
            "validity_date": parse_validity_date(row["validity_date"]),
            "warning_days": row["warning_days"] or _default_warning_days(),
            "notes": row["notes"] or "",
        }
    return result


def _auto_apply_validity_from_filename(path: str, name: str, is_folder: bool, metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {
        "validity_type": "not_defined",
        "validity_date": None,
        "warning_days": _default_warning_days(),
        "notes": "",
    }
    if is_folder:
        return metadata
    if str(metadata.get("validity_type") or "not_defined").lower() in {"defined", "indeterminate"}:
        return metadata
    inferred = _extract_validity_from_filename(name)
    if inferred is None:
        return metadata
    warning = metadata.get("warning_days") or _default_warning_days()
    _ensure_metadata_row(path)
    db.execute(
        """
        UPDATE documents
           SET validity_type = ?, validity_date = ?, warning_days = COALESCE(warning_days, ?), updated_at = ?
         WHERE path = ?
        """,
        ("defined", inferred.strftime("%Y-%m-%d"), warning, _now(), path),
    )
    db.record_audit(path, "auto_validity_from_filename", None, f"Data={inferred.strftime('%d/%m/%Y')}")
    return {
        "validity_type": "defined",
        "validity_date": inferred,
        "warning_days": warning,
        "notes": metadata.get("notes", ""),
    }


def _validity_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    validity_type = metadata.get("validity_type") or "not_defined"
    validity_date = metadata.get("validity_date")
    warning_days = metadata.get("warning_days") or _default_warning_days()
    status = compute_status(validity_type, validity_date, warning_days)
    if validity_type == "indeterminate":
        validity = "Indeterminada"
    elif validity_type == "defined" and validity_date:
        validity = format_display_date(validity_date)
    else:
        validity = "Não definido"
    return {
        "validity": validity,
        "validity_type": validity_type,
        "warning_days": warning_days,
        "status": {
            "code": status.code,
            "key": status.code,
            "label": status.label,
            "color": status.color,
            "icon": status.icon,
        },
    }


def _format_modified(value: str | None) -> str:
    if not value:
        return "--"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def _format_size(value: str | int | None) -> str:
    if value in (None, ""):
        return "--"
    size = int(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(size)
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    if index == 0:
        return f"{int(amount)} {units[index]}"
    return f"{amount:.1f} {units[index]}"


def _item_to_dict(item: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    is_folder = item.get("mimeType") == FOLDER_MIME
    path = id_to_path(item["id"])
    metadata = _auto_apply_validity_from_filename(path, item.get("name", ""), is_folder, metadata)
    data = {
        "name": item.get("name", "Sem nome"),
        "path": path,
        "type": "directory" if is_folder else "file",
        "size": "--" if is_folder else _format_size(item.get("size")),
        "size_bytes": None if is_folder else int(item.get("size") or 0),
        "modified": _format_modified(item.get("modifiedTime")),
        "modified_ts": item.get("modifiedTime"),
        "icon": "bi bi-folder-fill" if is_folder else "bi bi-file-earmark-text",
        "drive_id": item.get("id"),
        "mime_type": item.get("mimeType"),
        "web_url": item.get("webViewLink"),
        "extension": os.path.splitext(item.get("name", ""))[1].lower().lstrip("."),
    }
    data.update(_validity_payload(metadata))
    return data


def _breadcrumbs_for(file_id: str) -> list[dict[str, str]]:
    breadcrumbs = [{"label": "Google Drive", "path": ROOT_PATH}]
    if file_id == "root":
        return breadcrumbs
    try:
        drive = service()
        current = drive.files().get(fileId=file_id, fields="id,name,parents", supportsAllDrives=True).execute()
        chain = []
        guard = 0
        while current and current.get("id") != "root" and guard < 40:
            chain.append({"label": current.get("name", "Sem nome"), "path": id_to_path(current["id"])})
            parents = current.get("parents") or []
            if not parents or parents[0] == "root":
                break
            current = drive.files().get(fileId=parents[0], fields="id,name,parents", supportsAllDrives=True).execute()
            guard += 1
        breadcrumbs.extend(reversed(chain))
    except HttpError:
        breadcrumbs.append({"label": file_id, "path": id_to_path(file_id)})
    return breadcrumbs


def list_items(path: str | None, *, page: int = 1, page_size: int = 50, search: str | None = None) -> dict[str, Any]:
    file_id = path_to_id(path or ROOT_PATH)
    drive = service()
    query_parts = [f"'{file_id}' in parents", "trashed = false"]
    if search:
        safe_search = search.replace("'", "\\'")
        query_parts.append(f"name contains '{safe_search}'")
    query = " and ".join(query_parts)
    token_key = f"google_drive_page_tokens:{file_id}:{search or ''}:{page_size}"
    tokens = session.get(token_key, {})
    page_token = tokens.get(str(page)) if page > 1 else None
    try:
        response = drive.files().list(
            q=query,
            fields="nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,parents)",
            orderBy="folder,name_natural",
            pageSize=min(max(page_size, 1), 200),
            pageToken=page_token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao listar Google Drive: {exc}") from exc
    next_token = response.get("nextPageToken")
    if next_token:
        tokens[str(page + 1)] = next_token
        session[token_key] = tokens
    raw_items = response.get("files", [])
    paths = [id_to_path(item["id"]) for item in raw_items]
    metadata = _metadata_map(paths)
    items = [_item_to_dict(item, metadata.get(id_to_path(item["id"]))) for item in raw_items]
    current_path = id_to_path(file_id)
    parent_path = ROOT_PATH
    if file_id != "root":
        try:
            current = drive.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
            parents = current.get("parents") or []
            if parents:
                parent_path = id_to_path(parents[0])
        except HttpError:
            parent_path = ROOT_PATH
    return {
        "items": items,
        "current_path": current_path,
        "parent_path": parent_path,
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "has_more": bool(next_token),
        "breadcrumbs": _breadcrumbs_for(file_id),
        "source": "google_drive",
    }


def get_details(path: str) -> dict[str, Any]:
    file_id = path_to_id(path)
    drive = service()
    try:
        item = drive.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime,webViewLink,webContentLink,parents,capabilities",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao abrir detalhes do Google Drive: {exc}") from exc
    data = _item_to_dict(item, _metadata_for_path(id_to_path(file_id)))
    data.update({
        "notes": _metadata_for_path(id_to_path(file_id)).get("notes", ""),
        "web_url": item.get("webViewLink") or item.get("webContentLink"),
        "capabilities": item.get("capabilities", {}),
    })
    metadata = _metadata_for_path(id_to_path(file_id))
    if metadata["validity_type"] == "defined" and metadata["validity_date"]:
        data["validity_days_remaining"] = (metadata["validity_date"] - date.today()).days
    else:
        data["validity_days_remaining"] = None
    return data


def set_validity(path: str, validity_type: str, validity_value: str | None, warning_days: int | None) -> dict[str, Any]:
    path_to_id(path)  # valida
    validity_type = (validity_type or "").lower()
    if validity_type not in {"defined", "indeterminate", "not_defined"}:
        raise GoogleDriveError("Tipo de validade invalido.")
    validity_date: Optional[date] = None
    if validity_type == "defined":
        validity_date = normalise_validity_input(validity_value)
        if not validity_date:
            raise GoogleDriveError("Informe a data de validade.")
    warning = warning_days or _default_warning_days()
    _ensure_metadata_row(path)
    db.execute(
        """
        UPDATE documents
           SET validity_type = ?, validity_date = ?, warning_days = ?, updated_at = ?
         WHERE path = ?
        """,
        (validity_type, validity_date.strftime("%Y-%m-%d") if validity_date else None, warning, _now(), path),
    )
    db.record_audit(path, "set_validity", None, f"Tipo={validity_type}")
    metadata = {"validity_type": validity_type, "validity_date": validity_date, "warning_days": warning, "notes": ""}
    return _validity_payload(metadata)


def set_notes(path: str, notes: str) -> dict[str, Any]:
    path_to_id(path)
    _ensure_metadata_row(path)
    db.execute("UPDATE documents SET notes = ?, updated_at = ? WHERE path = ?", (notes or "", _now(), path))
    db.record_audit(path, "set_notes", None, "Notas atualizadas")
    return {"notes": notes or ""}


def rename_item(path: str, new_name: str) -> dict[str, Any]:
    file_id = path_to_id(path)
    if not new_name or not new_name.strip():
        raise GoogleDriveError("Informe o novo nome.")
    try:
        item = service().files().update(
            fileId=file_id,
            body={"name": new_name.strip()},
            fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao renomear no Google Drive: {exc}") from exc
    db.record_audit(path, "rename", None, f"Novo nome={new_name}")
    return {"path": id_to_path(item["id"]), "name": item.get("name", new_name)}


def delete_items(paths: Iterable[str] | None) -> int:
    items = [p for p in (paths or []) if p]
    if not items:
        raise GoogleDriveError("Informe ao menos um caminho.")
    drive = service()
    deleted = 0
    for path in items:
        file_id = path_to_id(path)
        try:
            drive.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Erro ao excluir no Google Drive: {exc}") from exc
        db.execute("DELETE FROM documents WHERE path = ?", (path,))
        db.record_audit(path, "delete", None, None)
        deleted += 1
    return deleted


def create_directory(parent: str, folder_name: str) -> dict[str, Any]:
    parent_id = path_to_id(parent)
    if not folder_name or not folder_name.strip():
        raise GoogleDriveError("Informe o nome da pasta.")
    try:
        item = service().files().create(
            body={"name": folder_name.strip(), "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao criar pasta no Google Drive: {exc}") from exc
    path = id_to_path(item["id"])
    db.record_audit(path, "create_directory", None, None)
    return {"path": path, "name": item.get("name", folder_name)}


def create_file(parent: str, file_name: str) -> dict[str, Any]:
    parent_id = path_to_id(parent)
    if not file_name or not file_name.strip():
        raise GoogleDriveError("Informe o nome do arquivo.")
    media = MediaIoBaseUpload(io.BytesIO(b""), mimetype="application/octet-stream", resumable=False)
    try:
        item = service().files().create(
            body={"name": file_name.strip(), "parents": [parent_id]},
            media_body=media,
            fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao criar arquivo no Google Drive: {exc}") from exc
    path = id_to_path(item["id"])
    db.record_audit(path, "create_file", None, None)
    return {"path": path, "name": item.get("name", file_name)}


def save_upload(parent: str, storage_objects: Iterable) -> list[dict[str, Any]]:
    parent_id = path_to_id(parent)
    saved = []
    drive = service()
    for storage in storage_objects:
        filename = os.path.basename(storage.filename or "")
        if not filename:
            continue
        stream = storage.stream
        if hasattr(stream, "seek"):
            stream.seek(0)
        mimetype = storage.mimetype or "application/octet-stream"
        media = MediaIoBaseUpload(stream, mimetype=mimetype, resumable=True)
        try:
            item = drive.files().create(
                body={"name": filename, "parents": [parent_id]},
                media_body=media,
                fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Erro ao enviar arquivo para o Google Drive: {exc}") from exc
        path = id_to_path(item["id"])
        auto_date = _extract_validity_from_filename(filename)
        auto_display = None
        if auto_date:
            auto_display = auto_date.strftime("%d/%m/%Y")
            set_validity(path, "defined", auto_display, None)
        db.record_audit(path, "upload", None, None)
        saved.append({"path": path, "name": filename, "auto_validity": auto_display})
    return saved


def move_items(paths: Sequence[str], destination: str) -> dict[str, Any]:
    destination_id = path_to_id(destination)
    moved = []
    drive = service()
    for path in paths or []:
        file_id = path_to_id(path)
        try:
            current = drive.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
            previous_parents = ",".join(current.get("parents", []))
            item = drive.files().update(
                fileId=file_id,
                addParents=destination_id,
                removeParents=previous_parents,
                fields="id,name",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Erro ao mover no Google Drive: {exc}") from exc
        db.record_audit(path, "move", None, f"destino={destination}")
        moved.append(id_to_path(item["id"]))
    return {"items": moved}


def copy_items(paths: Sequence[str], destination: str) -> dict[str, Any]:
    destination_id = path_to_id(destination)
    copied = []
    drive = service()
    for path in paths or []:
        file_id = path_to_id(path)
        try:
            source = drive.files().get(fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True).execute()
            if source.get("mimeType") == FOLDER_MIME:
                raise GoogleDriveError("Copia de pastas do Google Drive ainda nao e suportada pela API.")
            item = drive.files().copy(
                fileId=file_id,
                body={"name": source.get("name"), "parents": [destination_id]},
                fields="id,name",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Erro ao copiar no Google Drive: {exc}") from exc
        new_path = id_to_path(item["id"])
        meta = _metadata_for_path(path)
        _ensure_metadata_row(new_path)
        db.execute(
            "UPDATE documents SET validity_type=?, validity_date=?, warning_days=?, notes=?, updated_at=? WHERE path=?",
            (
                meta["validity_type"],
                meta["validity_date"].strftime("%Y-%m-%d") if meta["validity_date"] else None,
                meta["warning_days"],
                meta["notes"],
                _now(),
                new_path,
            ),
        )
        db.record_audit(new_path, "copy", None, f"origem={path}")
        copied.append(new_path)
    return {"items": copied}


def export_directory_snapshot(path: str) -> tuple[str, bytes]:
    collected: list[dict[str, Any]] = []
    page = 1
    while True:
        result = list_items(path, page=page, page_size=200)
        collected.extend(result["items"])
        if not result.get("has_more"):
            break
        page += 1
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Nome", "Caminho", "Tipo", "Validade", "Status", "Observações", "Origem", "Link"])
    for item in collected:
        meta = _metadata_for_path(item["path"])
        writer.writerow([
            item["name"],
            item["path"],
            item["type"],
            item["validity"],
            item["status"]["label"],
            meta.get("notes", ""),
            "Google Drive",
            item.get("web_url") or "",
        ])
    csv_bytes = output.getvalue().encode("utf-8-sig")
    output.close()
    filename = f"relatorio_google_drive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return filename, csv_bytes
