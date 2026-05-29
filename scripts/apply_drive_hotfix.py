from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Trecho nao encontrado para patch: {label}")
    return text.replace(old, new, 1)


def patch_requirements() -> None:
    p = ROOT / "requirements.txt"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if "psycopg2-binary" not in text:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "psycopg2-binary\n"
        p.write_text(text, encoding="utf-8")


def patch_app_js() -> None:
    path = "static/js/app.js"
    text = read(path)

    text = text.replace(
        "detail: null, notesSnapshot:",
        "detail: null, detailsRequestSeq: 0, detailsInFlightPath: null, detailsInFlightPromise: null, detailsCache: new Map(), notesSnapshot:",
        1,
    )

    text = replace_once(
        text,
        "function selectRow(row) { if (!row) { return; } elements.tableBody.querySelectorAll('tr').forEach((tr) => tr.classList.remove('active')); row.classList.add('active'); state.selectedPath = row.dataset.path; state.selectedType = row.dataset.type; loadDetails(); updateActionButtons(); }",
        "function selectRow(row) { if (!row) { return; } const nextPath = row.dataset.path; const nextType = row.dataset.type; const sameSelection = state.selectedPath === nextPath && state.selectedType === nextType; elements.tableBody.querySelectorAll('tr').forEach((tr) => tr.classList.remove('active')); row.classList.add('active'); if (sameSelection && state.detail && state.detail.path === nextPath) { updateActionButtons(); return; } state.selectedPath = nextPath; state.selectedType = nextType; loadDetails(nextPath); updateActionButtons(); }",
        "selectRow duplicate details guard",
    )

    text = replace_once(
        text,
        "async function loadDetails() { if (!state.selectedPath) { clearDetails(); return; } try { const params = new URLSearchParams({ path: state.selectedPath }); const response = await fetch(`/api/details?${params.toString()}`); if (!response.ok) { throw new Error('Não foi possível obter os detalhes.'); } const payload = await response.json(); if (!payload.success) { throw new Error(payload.error || 'Erro ao obter detalhes.'); } state.detail = payload.data; fillDetails(payload.data); } catch (error) { console.error(error); showToast(error.message, 'danger'); } }",
        "async function loadDetails(pathOverride = null, options = {}) { const targetPath = pathOverride || state.selectedPath; if (!targetPath) { clearDetails(); return; } const now = Date.now(); const cached = state.detailsCache && state.detailsCache.get(targetPath); if (!options.force && cached && now - cached.timestamp < 30000) { state.detail = cached.data; fillDetails(cached.data); return; } if (state.detailsInFlightPath === targetPath && state.detailsInFlightPromise) { return state.detailsInFlightPromise; } const requestSeq = (state.detailsRequestSeq || 0) + 1; state.detailsRequestSeq = requestSeq; const promise = (async () => { try { const params = new URLSearchParams({ path: targetPath }); const response = await fetch(`/api/details?${params.toString()}`); if (!response.ok) { throw new Error('Não foi possível obter os detalhes.'); } const payload = await response.json(); if (!payload.success) { throw new Error(payload.error || 'Erro ao obter detalhes.'); } if (requestSeq !== state.detailsRequestSeq || state.selectedPath !== targetPath) { return payload.data; } state.detail = payload.data; if (state.detailsCache) { state.detailsCache.set(targetPath, { timestamp: Date.now(), data: payload.data }); } fillDetails(payload.data); return payload.data; } catch (error) { console.error(error); showToast(error.message, 'danger'); return null; } finally { if (state.detailsInFlightPath === targetPath) { state.detailsInFlightPath = null; state.detailsInFlightPromise = null; } } })(); state.detailsInFlightPath = targetPath; state.detailsInFlightPromise = promise; return promise; }",
        "loadDetails in-flight/cache",
    )

    text = text.replace("if (isGoogleDrivePath()) { showToast('Observacoes em arquivos do Google Drive ainda nao estao habilitadas.', 'info'); return; } ", "", 1)
    text = text.replace("if (isGoogleDrivePath()) { showToast('Validade em arquivos do Google Drive ainda nao esta habilitada.', 'info'); return; } ", "", 1)
    text = text.replace("if (isGoogleDrivePath()) { showToast('Validade em arquivos do Google Drive ainda nao esta habilitada.', 'info'); return; } ", "", 1)
    text = text.replace("if (isGoogleDrivePath()) { showToast('Exportar CSV do Google Drive ainda nao esta habilitado.', 'info'); return; } ", "", 1)

    text = text.replace("await loadDetails();", "await loadDetails(null, { force: true });")

    # Keep Drive metadata actions enabled. Heavy file operations remain controlled by backend permissions.
    text = text.replace(
        "elements.btnSaveNotes, elements.btnResetNotes, elements.btnSetValidity, elements.btnMarkIndeterminate, elements.btnClearValidity, elements.btnRename, elements.btnMove, elements.btnCopy, elements.btnDelete, elements.btnUpload, elements.btnNewFolder, elements.btnNewFile, elements.btnExport,",
        "elements.btnRename, elements.btnMove, elements.btnCopy, elements.btnDelete, elements.btnUpload, elements.btnNewFolder, elements.btnNewFile,",
        1,
    )

    # Invalidate details cache after metadata changes.
    text = text.replace("state.notesSnapshot = payload.data.notes;", "if (state.detailsCache) state.detailsCache.delete(state.selectedPath); state.notesSnapshot = payload.data.notes;", 1)
    text = text.replace("modals.validity.hide();", "if (state.detailsCache) state.detailsCache.delete(state.selectedPath); modals.validity.hide();", 1)

    write(path, text)


def patch_api_py() -> None:
    path = "document_manager/blueprints/api.py"
    text = read(path)

    if "drive_metadata_service as drive_meta_svc" not in text:
        text = text.replace(
            "from ..services import google_drive_service as drive_svc",
            "from ..services import google_drive_service as drive_svc\nfrom ..services import drive_metadata_service as drive_meta_svc",
            1,
        )

    text = replace_once(
        text,
        "@api_bp.get(\"/presets\") def get_presets() -> Response: user = _current_user_dict() if not user: return _json_unauthorised() return _json_success({\"data\": svc.list_presets(user['username'])})",
        "@api_bp.get(\"/presets\") def get_presets() -> Response: user = _current_user_dict() if not user: return _json_unauthorised() local_items = svc.list_presets(user['username']) drive_items = [] try: drive_items = drive_meta_svc.list_presets(user['username']) except Exception: drive_items = [] return _json_success({\"data\": [*local_items, *drive_items]})",
        "get_presets merge postgres drive presets",
    )

    text = replace_once(
        text,
        "try: data = svc.add_preset(user['username'], name, path) return _json_success({\"data\": data}, HTTPStatus.CREATED) except svc.DocumentServiceError as exc: return _json_error(str(exc))",
        "try: data = drive_meta_svc.add_preset(user['username'], drive_svc.extract_file_id(path), name, path) if _is_drive(path) else svc.add_preset(user['username'], name, path) return _json_success({\"data\": data}, HTTPStatus.CREATED) except (drive_meta_svc.DriveMetadataError, drive_svc.GoogleDriveError, svc.DocumentServiceError) as exc: return _json_error(str(exc))",
        "create_preset drive postgres",
    )

    text = replace_once(
        text,
        "@api_bp.delete(\"/presets/<int:preset_id>\") def remove_preset(preset_id: int) -> Response: user = _current_user_dict() if not user: return _json_unauthorised() try: svc.delete_preset(user['username'], preset_id) return _json_success() except svc.DocumentServiceError as exc: return _json_error(str(exc), HTTPStatus.NOT_FOUND)",
        "@api_bp.delete(\"/presets/<path:preset_id>\") def remove_preset(preset_id: str) -> Response: user = _current_user_dict() if not user: return _json_unauthorised() try: if str(preset_id).startswith('gdrive://') or not str(preset_id).isdigit(): drive_meta_svc.delete_preset(user['username'], drive_svc.extract_file_id(preset_id) if str(preset_id).startswith('gdrive://') else preset_id) else: svc.delete_preset(user['username'], int(preset_id)) return _json_success() except (drive_meta_svc.DriveMetadataError, drive_svc.GoogleDriveError, svc.DocumentServiceError) as exc: return _json_error(str(exc), HTTPStatus.NOT_FOUND)",
        "delete preset supports drive file_id",
    )

    text = replace_once(
        text,
        "@api_bp.get(\"/favorites/list\") def get_favorites() -> Response: user = _current_user_dict() if not user: return _json_unauthorised() return _json_success({\"data\": svc.list_favorites(user['username'])})",
        "@api_bp.get(\"/favorites/list\") def get_favorites() -> Response: user = _current_user_dict() if not user: return _json_unauthorised() local_items = svc.list_favorites(user['username']) drive_items = [] try: drive_items = drive_meta_svc.list_favorites(user['username']) except Exception: drive_items = [] return _json_success({\"data\": [*local_items, *drive_items]})",
        "get_favorites merge postgres drive favorites",
    )

    text = replace_once(
        text,
        "try: data = svc.add_favorite(user['username'], name, path) return _json_success({\"data\": data}, HTTPStatus.CREATED) except svc.DocumentServiceError as exc: return _json_error(str(exc))",
        "try: data = drive_meta_svc.add_favorite(user['username'], drive_svc.extract_file_id(path), name, path) if _is_drive(path) else svc.add_favorite(user['username'], name, path) return _json_success({\"data\": data}, HTTPStatus.CREATED) except (drive_meta_svc.DriveMetadataError, drive_svc.GoogleDriveError, svc.DocumentServiceError) as exc: return _json_error(str(exc))",
        "add_favorite drive postgres",
    )

    text = replace_once(
        text,
        "@api_bp.post(\"/favorites/delete\") def delete_favorite() -> Response: user = _current_user_dict() if not user: return _json_unauthorised() payload = request.get_json(silent=True) or {} name = payload.get(\"name\") if not name: return _json_error(\"Informe o nome do favorito.\") try: svc.delete_favorite(user['username'], name) return _json_success() except svc.DocumentServiceError as exc: return _json_error(str(exc))",
        "@api_bp.post(\"/favorites/delete\") def delete_favorite() -> Response: user = _current_user_dict() if not user: return _json_unauthorised() payload = request.get_json(silent=True) or {} name = payload.get(\"name\") path = payload.get(\"path\") file_id = payload.get(\"file_id\") if not name and not path and not file_id: return _json_error(\"Informe o favorito.\") try: if path and _is_drive(path): drive_meta_svc.delete_favorite(user['username'], file_id=drive_svc.extract_file_id(path)) elif file_id and not str(file_id).isdigit(): drive_meta_svc.delete_favorite(user['username'], file_id=str(file_id)) else: svc.delete_favorite(user['username'], name) return _json_success() except (drive_meta_svc.DriveMetadataError, drive_svc.GoogleDriveError, svc.DocumentServiceError) as exc: return _json_error(str(exc))",
        "delete_favorite supports drive",
    )

    write(path, text)


def main() -> None:
    patch_requirements()
    patch_app_js()
    patch_api_py()
    print("Hotfix aplicado com sucesso.")


if __name__ == "__main__":
    main()
