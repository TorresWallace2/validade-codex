"""Google Drive OAuth and API routes."""
from __future__ import annotations

from http import HTTPStatus

from flask import Blueprint, Response, jsonify, redirect, request, session, url_for

from ..services import google_drive_service as drive

google_drive_bp = Blueprint("google_drive", __name__)


def _json_success(payload: dict | None = None, status: HTTPStatus = HTTPStatus.OK) -> Response:
    data = {"success": True}
    if payload:
        data.update(payload)
    return Response(jsonify(data).get_data(), status=status, mimetype="application/json")


def _json_error(message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> Response:
    return Response(jsonify({"success": False, "error": message}).get_data(), status=status, mimetype="application/json")


@google_drive_bp.get("/auth/google/connect")
def connect_google_drive():
    try:
        return redirect(drive.authorization_url())
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.get("/auth/google/callback")
def google_drive_callback():
    try:
        drive.finish_authorization(request.url, request.args.get("state"))
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)
    return redirect(url_for("ui.index", drive="connected"))


@google_drive_bp.post("/api/google-drive/disconnect")
def disconnect_google_drive() -> Response:
    drive.disconnect()
    return _json_success()


@google_drive_bp.get("/api/google-drive/status")
def google_drive_status() -> Response:
    return _json_success({"data": {"connected": drive.is_connected(), "root_path": drive.ROOT_PATH}})


@google_drive_bp.post("/api/google-drive/open")
def open_google_drive_file() -> Response:
    payload = request.get_json(silent=True) or {}
    url = payload.get("url")
    if not url:
        return _json_error("URL do Google Drive nao informada.")
    return _json_success({"data": {"url": url}})
