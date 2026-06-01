"""REST API blueprint for document management operations."""

from __future__ import annotations

import io
import json
import os
import time
from http import HTTPStatus
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    g,
    jsonify,
    make_response,
    request,
    send_file,
    session,
)

from ..services import auth_service
from ..services import document_service as svc
from ..services import drive_metadata_service as drive_meta_svc
from ..services import google_drive_service as drive_svc

api_bp = Blueprint("api", __name__)


def _json_success(
    payload: dict[str, Any] | None = None, status: HTTPStatus = HTTPStatus.OK
) -> Response:
    data = {"success": True}
    if payload:
        data.update(payload)
    return make_response(jsonify(data), status)


def _json_error(message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> Response:
    return make_response(jsonify({"success": False, "error": message}), status)


def _current_user_dict() -> dict[str, Any] | None:
    user = getattr(g, "current_user", None)
    if isinstance(user, dict):
        return user
    return None


def _json_unauthorised(message: str = "Autenticacao requerida.") -> Response:
    return _json_error(message, HTTPStatus.UNAUTHORIZED)


def _drive_error(exc: Exception) -> Response:
    return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@api_bp.post("/auth/login")
def auth_login() -> Response:
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    remember = bool(payload.get("remember"))
    if not username or not password:
        return _json_error("Informe usuario e senha.")
    try:
        user = auth_service.authenticate(username, password)
    except auth_service.AuthServiceError as exc:
        return _json_error(str(exc), HTTPStatus.UNAUTHORIZED)
    session["user"] = {
        "username": user.username,
        "role": user.role,
        "is_admin": user.is_admin,
    }
    session["remember"] = remember
    session.permanent = True
    return _json_success({"data": session["user"]})


@api_bp.post("/auth/logout")
def auth_logout() -> Response:
    session.pop("user", None)
    session.pop("remember", None)
    return _json_success()


@api_bp.get("/auth/session")
def auth_session() -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    return _json_success({"data": user})


@api_bp.get("/users")
def list_all_users() -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    if not user.get("is_admin"):
        return _json_error(
            "Somente administradores podem listar usuarios.", HTTPStatus.FORBIDDEN
        )
    records = auth_service.list_users()
    data = [
        {
            "id": item.id,
            "username": item.username,
            "role": item.role,
            "is_active": item.is_active,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
        for item in records
    ]
    return _json_success({"data": data})


@api_bp.post("/users")
def create_user() -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    if not user.get("is_admin"):
        return _json_error(
            "Somente administradores podem criar usuarios.", HTTPStatus.FORBIDDEN
        )
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    role = str(payload.get("role") or "user").strip().lower()
    if not username or not password:
        return _json_error("Informe usuario e senha.")
    if role not in {"admin", "user"}:
        return _json_error("Perfil invalido.")
    try:
        new_user = auth_service.create_user(
            username, password, role, created_by=user["username"]
        )
    except auth_service.AuthServiceError as exc:
        return _json_error(str(exc))
    data = {
        "id": new_user.id,
        "username": new_user.username,
        "role": new_user.role,
        "is_active": new_user.is_active,
        "created_at": new_user.created_at,
        "updated_at": new_user.updated_at,
    }
    return _json_success({"data": data}, HTTPStatus.CREATED)


@api_bp.post("/users/<username>/status")
def update_user_status(username: str) -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    if not user.get("is_admin"):
        return _json_error(
            "Somente administradores podem gerenciar usuarios.", HTTPStatus.FORBIDDEN
        )
    payload = request.get_json(silent=True) or {}
    active = bool(payload.get("active", True))
    try:
        auth_service.set_active(username, active, updated_by=user["username"])
    except auth_service.AuthServiceError as exc:
        return _json_error(str(exc))
    return _json_success({"data": {"username": username.upper(), "active": active}})


@api_bp.post("/users/<username>/password")
def update_user_password(username: str) -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    if not user.get("is_admin"):
        return _json_error(
            "Somente administradores podem gerenciar usuarios.", HTTPStatus.FORBIDDEN
        )
    payload = request.get_json(silent=True) or {}
    new_password = str(payload.get("password") or "")
    if len(new_password) < 6:
        return _json_error("Senha deve ter ao menos 6 caracteres.")
    try:
        auth_service.update_password(
            username, new_password, updated_by=user["username"]
        )
    except auth_service.AuthServiceError as exc:
        return _json_error(str(exc))
    return _json_success()


@api_bp.get("/list_items")
def list_items() -> Response:
    started_at = time.perf_counter()
    path = request.args.get("path")
    sort_by = request.args.get("sort_by", "name")
    sort_direction = request.args.get("direction", "asc")
    page = int(request.args.get("page", 1))
    page_size = request.args.get("page_size")
    page_size_int = int(page_size) if page_size else None
    search = request.args.get("search")
    status_filter_param = request.args.get("status")
    status_filter = status_filter_param.split(",") if status_filter_param else None
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.list_items(
                path,
                page=page,
                page_size=page_size_int or 50,
                search=search,
                status_filter=status_filter,
            )
            perf_enabled = str(os.environ.get("APP_DEBUG_PERF", "")).strip().lower() in {"1", "true", "yes", "on"}
            if perf_enabled:
                perf_payload = dict(data.get("perf") or {})
                perf_payload["api_handler_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
                serialize_probe_started = time.perf_counter()
                json.dumps({"success": True, "data": data}, default=str, ensure_ascii=False)
                perf_payload["response_serialize_probe_ms"] = round(
                    (time.perf_counter() - serialize_probe_started) * 1000, 2
                )
                data["perf"] = perf_payload
                current_app.logger.info(
                    "api.list_items drive request_id=%s total_ms=%.2f response_serialize_probe_ms=%.2f",
                    perf_payload.get("request_id"),
                    perf_payload.get("api_handler_ms", 0.0),
                    perf_payload.get("response_serialize_probe_ms", 0.0),
                )
            return _json_success({"data": data})
        data = svc.list_directory_items(
            path if path else None,
            sort_by=sort_by,
            sort_direction=sort_direction,
            page=page,
            page_size=page_size_int,
            search=search,
            status_filter=status_filter,
        )
        return _json_success({"data": data})
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)
    except Exception as exc:  # pragma: no cover
        return _json_error(f"Erro inesperado: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)


@api_bp.post("/navigate")
def navigate() -> Response:
    payload = request.get_json(silent=True) or {}
    path = payload.get("path")
    if not path:
        return _json_error("Informe o caminho.")
    sort_by = payload.get("sort_by", "name")
    sort_direction = payload.get("direction", "asc")
    search = payload.get("search")
    status_filter = payload.get("status")
    page_size = payload.get("page_size")
    page_size_int = int(page_size) if page_size else None
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.list_items(
                path,
                page=1,
                page_size=page_size_int or 50,
                search=search,
                status_filter=status_filter,
            )
            return _json_success({"data": data})
        data = svc.navigate_to_path(
            path,
            sort_by=sort_by,
            sort_direction=sort_direction,
            search=search,
            status_filter=status_filter,
            page_size=page_size_int,
        )
        return _json_success({"data": data})
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.get("/details")
def details() -> Response:
    path = request.args.get("path")
    if not path:
        return _json_error("Informe o caminho.")
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.get_details(path)
            return _json_success({"data": data})
        data = svc.get_details(path)
        return _json_success({"data": data})
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@api_bp.post("/set_validity")
def set_validity() -> Response:
    payload = request.get_json(silent=True) or {}
    path = payload.get("path")
    validity_type = payload.get("validity_type")
    validity_value = payload.get("validity")
    warning_days = payload.get("warning_days")
    warning_days_int = int(warning_days) if warning_days else None
    if not path or not validity_type:
        return _json_error("Caminho e tipo de validade sao obrigatorios.")
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.set_validity(
                path, validity_type, validity_value, warning_days_int
            )
            return _json_success({"data": data})
        data = svc.set_validity(path, validity_type, validity_value, warning_days_int)
        return _json_success({"data": data})
    except ValueError as exc:
        return _json_error(str(exc))
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/set_notes")
def set_notes() -> Response:
    payload = request.get_json(silent=True) or {}
    path = payload.get("path")
    notes = payload.get("notes", "")
    if not path:
        return _json_error("Informe o caminho.")
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.set_notes(path, notes)
            return _json_success({"data": data})
        data = svc.set_notes(path, notes)
        return _json_success({"data": data})
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/set_auctions")
@api_bp.post("/set_pregoes")
def set_auctions() -> Response:
    payload = request.get_json(silent=True) or {}
    path = payload.get("path")
    auctions = payload.get("auctions", payload.get("pregoes", ""))
    if not path:
        return _json_error("Informe o caminho.")
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.set_auctions(path, auctions)
            return _json_success({"data": data})
        return _json_error(
            "Pregoes persistentes estao implementados para Google Drive nesta versao."
        )
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)


@api_bp.post("/rename")
def rename() -> Response:
    payload = request.get_json(silent=True) or {}
    path = payload.get("path")
    new_name = payload.get("new_name")
    if not path or not new_name:
        return _json_error("Informe caminho e novo nome.")
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.rename_item(path, new_name)
            return _json_success({"data": data})
        data = svc.rename_item(path, new_name)
        return _json_success({"data": data})
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/delete")
def delete() -> Response:
    payload = request.get_json(silent=True) or {}
    paths_payload = payload.get("paths")
    paths: list[str] = []
    if paths_payload is not None:
        if not isinstance(paths_payload, list):
            return _json_error("Informe caminhos validos.")
        paths = [str(item).strip() for item in paths_payload if item]
    else:
        path = payload.get("path")
        if path:
            paths.append(str(path).strip())
    paths = [p for p in paths if p]
    if not paths:
        return _json_error("Informe ao menos um caminho.")
    try:
        if all(drive_svc.is_drive_path(path) for path in paths):
            deleted = drive_svc.delete_items(paths)
            return _json_success({"data": {"deleted": deleted}})
        if any(drive_svc.is_drive_path(path) for path in paths):
            return _json_error(
                "Nao misture itens locais e Google Drive na mesma exclusao."
            )
        deleted = svc.delete_items(paths)
        return _json_success({"data": {"deleted": deleted}})
    except drive_svc.GoogleDriveError as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/create_folder")
def create_folder() -> Response:
    payload = request.get_json(silent=True) or {}
    parent = payload.get("parent")
    name = payload.get("name")
    if not parent or not name:
        return _json_error("Informe pasta base e nome.")
    try:
        if drive_svc.is_drive_path(parent):
            data = drive_svc.create_folder(parent, name)
            return _json_success({"data": data}, HTTPStatus.CREATED)
        data = svc.create_directory(parent, name)
        return _json_success({"data": data}, HTTPStatus.CREATED)
    except drive_svc.GoogleDriveError as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/create_file")
def create_file() -> Response:
    payload = request.get_json(silent=True) or {}
    parent = payload.get("parent")
    name = payload.get("name")

    if not parent or not name:
        return _json_error("Informe pasta base e nome.")

    try:
        if drive_svc.is_drive_path(parent):
            data = drive_svc.create_file(parent, name)
            return _json_success({"data": data}, HTTPStatus.CREATED)

        data = svc.create_file(parent, name)
        return _json_success({"data": data}, HTTPStatus.CREATED)

    except drive_svc.GoogleDriveError as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/upload")
def upload() -> Response:
    target = request.form.get("path")
    if not target:
        return _json_error("Informe o caminho da pasta.")
    files = request.files.getlist("files")
    if drive_svc.is_drive_path(target):
        if not files:
            return _json_error("Nenhum arquivo enviado.")
        try:
            data = drive_svc.upload_files(target, files)
            return _json_success({"data": data}, HTTPStatus.CREATED)
        except drive_svc.GoogleDriveError as exc:
            return _drive_error(exc)
    if not files:
        return _json_error("Nenhum arquivo enviado.")
    try:
        data = svc.save_upload(target, files)
        return _json_success({"data": data}, HTTPStatus.CREATED)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/items/move")
def move_items() -> Response:
    payload = request.get_json(silent=True) or {}
    paths = payload.get("paths")
    destination = payload.get("destination")
    if not paths or not destination:
        return _json_error("Informe os itens e a pasta de destino.")
    try:
        if all(
            drive_svc.is_drive_path(path) for path in paths
        ) and drive_svc.is_drive_path(destination):
            data = drive_svc.move_items(paths, destination)
            return _json_success({"data": data})
        if any(
            drive_svc.is_drive_path(path) for path in paths
        ) or drive_svc.is_drive_path(destination):
            return _json_error(
                "Mover entre local e Google Drive nao e suportado nesta versao."
            )
        data = svc.move_items(paths, destination)
        return _json_success({"data": data})
    except drive_svc.GoogleDriveError as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/items/copy")
def copy_items() -> Response:
    payload = request.get_json(silent=True) or {}
    paths = payload.get("paths")
    destination = payload.get("destination")
    if not paths or not destination:
        return _json_error("Informe os itens e a pasta de destino.")
    try:
        if all(
            drive_svc.is_drive_path(path) for path in paths
        ) and drive_svc.is_drive_path(destination):
            data = drive_svc.copy_items(paths, destination)
            return _json_success({"data": data})
        if any(
            drive_svc.is_drive_path(path) for path in paths
        ) or drive_svc.is_drive_path(destination):
            return _json_error(
                "Copiar entre local e Google Drive nao e suportado nesta versao."
            )
        data = svc.copy_items(paths, destination)
        return _json_success({"data": data})
    except drive_svc.GoogleDriveError as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.get("/export")
def export() -> Response:
    path = request.args.get("path")
    if not path:
        return _json_error("Informe o caminho.")
    try:
        if drive_svc.is_drive_path(path):
            filename, csv_bytes = drive_svc.export_directory_snapshot(path)
        else:
            filename, csv_bytes = svc.export_directory_snapshot(path)
        return send_file(
            io.BytesIO(csv_bytes),
            mimetype="text/csv",
            as_attachment=True,
            download_name=filename,
        )
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/open_file")
def open_file() -> Response:
    payload = request.get_json(silent=True) or {}
    path = payload.get("path")
    if not path:
        return _json_error("Informe o caminho.")
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.get_details(path)
            return _json_success({"data": {"url": data.get("web_url")}})
        svc.open_with_system(path)
        return _json_success()
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/open_folder")
def open_folder() -> Response:
    payload = request.get_json(silent=True) or {}
    path = payload.get("path")
    if not path:
        return _json_error("Informe o caminho.")
    try:
        if drive_svc.is_drive_path(path):
            data = drive_svc.get_details(path)
            return _json_success({"data": {"url": data.get("web_url")}})
        svc.open_in_explorer(path)
        return _json_success()
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.get("/presets")
def get_presets() -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    local_presets = svc.list_presets(user["username"])
    try:
        drive_presets = drive_meta_svc.list_presets(user["username"])
    except drive_meta_svc.DriveMetadataError:
        drive_presets = []
    return _json_success({"data": local_presets + drive_presets})


@api_bp.post("/presets")
def create_preset() -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    path = payload.get("path")
    if not name or not path:
        return _json_error("Informe nome e caminho.")
    try:
        if drive_svc.is_drive_path(path):
            file_id = drive_svc.path_to_id(path)
            data = drive_meta_svc.add_preset(user["username"], file_id, name, path)
            return _json_success({"data": data}, HTTPStatus.CREATED)
        data = svc.add_preset(user["username"], name, path)
        return _json_success({"data": data}, HTTPStatus.CREATED)
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.delete("/presets/<preset_id>")
def remove_preset(preset_id: str) -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    try:
        if preset_id.startswith("gdrive://"):
            drive_meta_svc.delete_preset(
                user["username"], drive_svc.path_to_id(preset_id)
            )
        elif not preset_id.isdigit():
            drive_meta_svc.delete_preset(user["username"], preset_id)
        else:
            svc.delete_preset(user["username"], int(preset_id))
        return _json_success()
    except drive_meta_svc.DriveMetadataError as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc), HTTPStatus.NOT_FOUND)


@api_bp.get("/favorites/list")
def get_favorites() -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    local_favorites = svc.list_favorites(user["username"])
    try:
        drive_favorites = drive_meta_svc.list_favorites(user["username"])
    except drive_meta_svc.DriveMetadataError:
        drive_favorites = []
    return _json_success({"data": local_favorites + drive_favorites})


@api_bp.post("/favorites/add")
def add_favorite() -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    path = payload.get("path")
    if not name or not path:
        return _json_error("Informe nome e caminho.")
    try:
        if drive_svc.is_drive_path(path):
            file_id = drive_svc.path_to_id(path)
            data = drive_meta_svc.add_favorite(user["username"], file_id, name, path)
            return _json_success({"data": data}, HTTPStatus.CREATED)
        data = svc.add_favorite(user["username"], name, path)
        return _json_success({"data": data}, HTTPStatus.CREATED)
    except (drive_svc.GoogleDriveError, drive_meta_svc.DriveMetadataError) as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.post("/favorites/delete")
def delete_favorite() -> Response:
    user = _current_user_dict()
    if not user:
        return _json_unauthorised()
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    path = payload.get("path")
    try:
        if path and drive_svc.is_drive_path(path):
            drive_meta_svc.delete_favorite(
                user["username"], file_id=drive_svc.path_to_id(path)
            )
            return _json_success()
        if not name:
            return _json_error("Informe o nome do favorito.")
        try:
            svc.delete_favorite(user["username"], name)
        except svc.DocumentServiceError:
            drive_meta_svc.delete_favorite(user["username"], name=name)
        return _json_success()
    except drive_meta_svc.DriveMetadataError as exc:
        return _drive_error(exc)
    except svc.DocumentServiceError as exc:
        return _json_error(str(exc))


@api_bp.get("/settings/warning_days")
def get_warning_days() -> Response:
    value = svc.get_warning_days()
    return _json_success({"data": {"warning_days": value}})


@api_bp.post("/settings/warning_days")
def set_warning_days() -> Response:
    payload = request.get_json(silent=True) or {}
    value = payload.get("warning_days")
    if value is None:
        return _json_error("Informe o valor de dias de aviso.")
    try:
        numeric = int(value)
        updated = svc.update_warning_days(numeric)
        return _json_success({"data": {"warning_days": updated}})
    except (ValueError, TypeError):
        return _json_error("Valor invalido.")
