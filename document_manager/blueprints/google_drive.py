"""Google Drive OAuth and account routes."""
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
        return redirect(drive.connect_new_authorization_url())
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.get("/auth/google/callback")
def google_drive_callback():
    try:
        account = drive.finish_authorization(request.url, request.args.get("state"))
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)
    return redirect(url_for("ui.index", drive="connected", account_id=account["account_id"]))


@google_drive_bp.get("/auth/google/reconnect/<account_id>")
def reconnect_google_drive(account_id: str):
    try:
        return redirect(drive.reconnect_authorization_url(account_id))
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.get("/api/google-drive/status")
def google_drive_status() -> Response:
    try:
        return _json_success({"data": drive.accounts_status()})
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.get("/api/google-drive/accounts")
def google_drive_accounts() -> Response:
    try:
        return _json_success({"data": drive.list_accounts()})
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.post("/api/google-drive/accounts/connect")
def connect_google_drive_account() -> Response:
    try:
        return _json_success({"data": {"auth_url": drive.connect_new_authorization_url()}})
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.post("/api/google-drive/accounts/<account_id>/activate")
def activate_google_drive_account(account_id: str) -> Response:
    try:
        account = drive.activate_account(account_id)
        return _json_success({"data": account})
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.post("/api/google-drive/accounts/<account_id>/disconnect")
def disconnect_google_drive_account(account_id: str) -> Response:
    try:
        drive.disconnect(account_id)
        return _json_success()
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.post("/api/google-drive/accounts/<account_id>/reconnect")
def reconnect_google_drive_account(account_id: str) -> Response:
    try:
        return _json_success({"data": {"auth_url": drive.reconnect_authorization_url(account_id)}})
    except drive.GoogleDriveError as exc:
        return _json_error(str(exc), HTTPStatus.BAD_REQUEST)


@google_drive_bp.post("/api/google-drive/open")
def open_google_drive_file() -> Response:
    payload = request.get_json(silent=True) or {}
    url = payload.get("url")
    if not url:
        return _json_error("URL do Google Drive nao informada.")
    return _json_success({"data": {"url": url}})
