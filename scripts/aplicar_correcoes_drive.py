from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

api_path = ROOT / 'document_manager' / 'blueprints' / 'api.py'
app_js_path = ROOT / 'static' / 'js' / 'app.js'
service_src = ROOT / 'document_manager' / 'services' / 'google_drive_service.py'


def patch_api() -> None:
    text = api_path.read_text(encoding='utf-8')
    text = text.replace(
        'if drive_svc.is_drive_path(parent): return _json_error("Criar arquivo vazio no Google Drive nao e suportado nesta versao.") data = svc.create_file(parent, name)',
        'if drive_svc.is_drive_path(parent): data = drive_svc.create_file(parent, name); return _json_success({"data": data}, HTTPStatus.CREATED) data = svc.create_file(parent, name)',
    )
    text = text.replace(
        'if drive_svc.is_drive_path(target): return _json_error("Upload direto para Google Drive ainda nao esta implementado nesta versao.") files = request.files.getlist("files")',
        'files = request.files.getlist("files") if drive_svc.is_drive_path(target):\n        if not files: return _json_error("Nenhum arquivo enviado.")\n        try: data = drive_svc.upload_files(target, files); return _json_success({"data": data}, HTTPStatus.CREATED)\n        except drive_svc.GoogleDriveError as exc: return _drive_error(exc)',
    )
    api_path.write_text(text, encoding='utf-8')


def patch_app_js() -> None:
    text = app_js_path.read_text(encoding='utf-8')

    # Libera botoes de acoes dentro do Google Drive. Antes o app desabilitava tudo em modo Drive.
    text = re.sub(
        r"if \(driveMode\) \{ \[ elements\.btnSaveNotes, elements\.btnResetNotes, elements\.btnSetValidity, elements\.btnMarkIndeterminate, elements\.btnClearValidity, elements\.btnRename, elements\.btnMove, elements\.btnCopy, elements\.btnDelete, elements\.btnUpload, elements\.btnNewFolder, elements\.btnNewFile, elements\.btnExport, \]\.forEach\(\(button\) => \{ if \(button\) button\.disabled = true; \}\); \} else \{ \[ elements\.btnMove, elements\.btnCopy, elements\.btnUpload, elements\.btnNewFolder, elements\.btnNewFile, elements\.btnExport, \]\.forEach\(\(button\) => \{ if \(button\) button\.disabled = false; \}\); \}",
        "if (driveMode) { [ elements.btnSaveNotes, elements.btnResetNotes, elements.btnSetValidity, elements.btnMarkIndeterminate, elements.btnClearValidity, elements.btnRename, ].forEach((button) => { if (button) button.disabled = !enabled; }); [ elements.btnUpload, elements.btnNewFolder, elements.btnNewFile, elements.btnExport, ].forEach((button) => { if (button) button.disabled = false; }); [ elements.btnMove, elements.btnCopy, elements.btnDelete, ].forEach((button) => { if (button) button.disabled = state.selectedPaths.size === 0; }); } else { [ elements.btnMove, elements.btnCopy, elements.btnUpload, elements.btnNewFolder, elements.btnNewFile, elements.btnExport, ].forEach((button) => { if (button) button.disabled = false; }); }",
        text,
    )

    # Remove bloqueios antigos no front-end; o backend agora decide e executa as acoes do Drive.
    replacements = [
        ("if (isGoogleDrivePath()) { showToast('Criar pasta no Google Drive ainda nao esta habilitado.', 'info'); return; } ", ""),
        ("if (isGoogleDrivePath()) { showToast('Criar arquivo no Google Drive ainda nao esta habilitado.', 'info'); return; } ", ""),
        ("if (isGoogleDrivePath()) { showToast('Upload direto para Google Drive ainda nao esta habilitado.', 'info'); return; } ", ""),
        ("if (isGoogleDrivePath()) { showToast('Exportar CSV do Google Drive ainda nao esta habilitado.', 'info'); return; } ", ""),
        ("if (isGoogleDrivePath()) { showToast('Renomear no Google Drive ainda nao esta habilitado.', 'info'); return; } ", ""),
        ("if (isGoogleDrivePath()) { showToast('Observacoes em arquivos do Google Drive ainda nao estao habilitadas.', 'info'); return; } ", ""),
        ("if (isGoogleDrivePath()) { showToast('Validade em arquivos do Google Drive ainda nao esta habilitada.', 'info'); return; } ", ""),
    ]
    for old, new in replacements:
        text = text.replace(old, new)

    app_js_path.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    # Este script deve ser copiado para a RAIZ do projeto antes de rodar.
    if not api_path.exists() or not app_js_path.exists() or not service_src.exists():
        raise SystemExit('Execute este script na raiz do projeto validade-codex.')
    patch_api()
    patch_app_js()
    print('Correcoes aplicadas em api.py e static/js/app.js.')
