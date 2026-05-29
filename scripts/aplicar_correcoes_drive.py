from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
api_path = ROOT / 'document_manager' / 'blueprints' / 'api.py'
app_js_path = ROOT / 'static' / 'js' / 'app.js'
service_path = ROOT / 'document_manager' / 'services' / 'google_drive_service.py'


def patch_api() -> None:
    text = api_path.read_text(encoding='utf-8')
    original = text

    # Habilita create_file no Google Drive quando o api.py antigo ainda bloqueia.
    text = text.replace(
        'if drive_svc.is_drive_path(parent): return _json_error("Criar arquivo vazio no Google Drive nao e suportado nesta versao.") data = svc.create_file(parent, name)',
        'if drive_svc.is_drive_path(parent): data = drive_svc.create_file(parent, name); return _json_success({"data": data}, HTTPStatus.CREATED) data = svc.create_file(parent, name)',
    )

    # Habilita upload direto no Google Drive quando o api.py antigo ainda bloqueia.
    text = text.replace(
        'if drive_svc.is_drive_path(target): return _json_error("Upload direto para Google Drive ainda nao esta implementado nesta versao.") files = request.files.getlist("files")',
        'files = request.files.getlist("files") if drive_svc.is_drive_path(target):\n        if not files: return _json_error("Nenhum arquivo enviado.")\n        try: data = drive_svc.upload_files(target, files); return _json_success({"data": data}, HTTPStatus.CREATED)\n        except drive_svc.GoogleDriveError as exc: return _drive_error(exc)',
    )

    # Caso o arquivo esteja formatado em multiplas linhas, remove mensagens de bloqueio conhecidas.
    text = re.sub(
        r'if\s+drive_svc\.is_drive_path\(parent\):\s*\n\s*return\s+_json_error\([^\n]*Criar arquivo vazio no Google Drive[^\n]*\)\s*\n\s*data\s*=\s*svc\.create_file\(parent,\s*name\)',
        'if drive_svc.is_drive_path(parent):\n        data = drive_svc.create_file(parent, name)\n        return _json_success({"data": data}, HTTPStatus.CREATED)\n    data = svc.create_file(parent, name)',
        text,
    )
    text = re.sub(
        r'if\s+drive_svc\.is_drive_path\(target\):\s*\n\s*return\s+_json_error\([^\n]*Upload direto para Google Drive[^\n]*\)\s*\n\s*files\s*=\s*request\.files\.getlist\("files"\)',
        'files = request.files.getlist("files")\n    if drive_svc.is_drive_path(target):\n        if not files:\n            return _json_error("Nenhum arquivo enviado.")\n        try:\n            data = drive_svc.upload_files(target, files)\n            return _json_success({"data": data}, HTTPStatus.CREATED)\n        except drive_svc.GoogleDriveError as exc:\n            return _drive_error(exc)',
        text,
    )

    if text != original:
        api_path.write_text(text, encoding='utf-8')
        print('api.py atualizado.')
    else:
        print('api.py sem alteracoes automáticas. Verifique se ja esta atualizado.')


def patch_app_js() -> None:
    text = app_js_path.read_text(encoding='utf-8')
    original = text

    # Remove bloqueios antigos dentro das funcoes dos botoes.
    messages = [
        'Criar pasta no Google Drive ainda nao esta habilitado.',
        'Criar arquivo no Google Drive ainda nao esta habilitado.',
        'Upload direto para Google Drive ainda nao esta habilitado.',
        'Exportar CSV do Google Drive ainda nao esta habilitado.',
        'Renomear no Google Drive ainda nao esta habilitado.',
        'Observacoes em arquivos do Google Drive ainda nao estao habilitadas.',
        'Validade em arquivos do Google Drive ainda nao esta habilitada.',
    ]
    for msg in messages:
        text = text.replace(f"if (isGoogleDrivePath()) {{ showToast('{msg}', 'info'); return; }} ", '')
        text = text.replace(f'if (isGoogleDrivePath()) {{ showToast("{msg}", "info"); return; }} ', '')

    # Libera os botoes no bloco antigo que desabilitava tudo quando era Google Drive.
    text = re.sub(
        r"if \(driveMode\) \{ \[ elements\.btnSaveNotes, elements\.btnResetNotes, elements\.btnSetValidity, elements\.btnMarkIndeterminate, elements\.btnClearValidity, elements\.btnRename, elements\.btnMove, elements\.btnCopy, elements\.btnDelete, elements\.btnUpload, elements\.btnNewFolder, elements\.btnNewFile, elements\.btnExport, \]\.forEach\(\(button\) => \{ if \(button\) button\.disabled = true; \}\); \} else \{ \[ elements\.btnMove, elements\.btnCopy, elements\.btnUpload, elements\.btnNewFolder, elements\.btnNewFile, elements\.btnExport, \]\.forEach\(\(button\) => \{ if \(button\) button\.disabled = false; \}\); \}",
        "if (driveMode) { [ elements.btnSaveNotes, elements.btnResetNotes, elements.btnSetValidity, elements.btnMarkIndeterminate, elements.btnClearValidity, elements.btnRename, ].forEach((button) => { if (button) button.disabled = !enabled; }); [ elements.btnUpload, elements.btnNewFolder, elements.btnNewFile, elements.btnExport, ].forEach((button) => { if (button) button.disabled = false; }); [ elements.btnMove, elements.btnCopy, elements.btnDelete, ].forEach((button) => { if (button) button.disabled = state.selectedPaths.size === 0; }); } else { [ elements.btnMove, elements.btnCopy, elements.btnUpload, elements.btnNewFolder, elements.btnNewFile, elements.btnExport, ].forEach((button) => { if (button) button.disabled = false; }); }",
        text,
    )

    # Garantia extra: se alguma rotina posterior voltar a desabilitar, esta rotina relibera no Drive.
    marker = 'function forceEnableGoogleDriveActions()'
    if marker not in text:
        text += """

// Correção: manter ações principais disponíveis dentro do Google Drive.
function forceEnableGoogleDriveActions() {
  try {
    if (typeof isGoogleDrivePath !== 'function' || !isGoogleDrivePath()) return;
    [elements.btnUpload, elements.btnNewFolder, elements.btnNewFile, elements.btnExport].forEach((button) => {
      if (!button) return;
      button.disabled = false;
      button.classList.remove('disabled');
      button.removeAttribute('aria-disabled');
    });
  } catch (error) {
    console.warn('Nao foi possivel atualizar botoes do Google Drive.', error);
  }
}
setInterval(forceEnableGoogleDriveActions, 500);
document.addEventListener('DOMContentLoaded', forceEnableGoogleDriveActions);
document.addEventListener('click', () => setTimeout(forceEnableGoogleDriveActions, 0));
"""

    if text != original:
        app_js_path.write_text(text, encoding='utf-8')
        print('static/js/app.js atualizado.')
    else:
        print('static/js/app.js sem alteracoes automáticas. Verifique se ja esta atualizado.')


if __name__ == '__main__':
    if not api_path.exists():
        raise SystemExit(f'Nao encontrei {api_path}')
    if not app_js_path.exists():
        raise SystemExit(f'Nao encontrei {app_js_path}')
    if not service_path.exists():
        raise SystemExit(f'Nao encontrei {service_path}')
    patch_api()
    patch_app_js()
    print('Concluido. Agora rode o sistema localmente e teste os botoes no Google Drive.')
