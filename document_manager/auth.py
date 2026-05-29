
"""Application level authentication utilities."""
from __future__ import annotations

from flask import Flask, g, jsonify, redirect, request, session, url_for

API_EXEMPT = {
    'api.auth_login',
    'api.auth_session',
    'api.auth_logout',
}
PAGE_EXEMPT = {
    'auth.login',
    'auth.perform_logout',
}
PATH_EXEMPT_PREFIXES = (
    '/static/',
    '/favicon.ico',
)


def init_auth(app: Flask) -> None:
    """Register request hooks that enforce login."""

    @app.before_request
    def load_user() -> None:  # type: ignore[override]
        user = session.get('user')
        if user:
            g.current_user = user
        else:
            g.current_user = None

    @app.before_request
    def enforce_login():  # type: ignore[override]
        if request.path.startswith(PATH_EXEMPT_PREFIXES):
            return None
        endpoint = request.endpoint or ''
        if endpoint.startswith('static'):
            return None
        if endpoint in PAGE_EXEMPT or endpoint in API_EXEMPT:
            return None
        user = session.get('user')
        if user:
            return None
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'Autenticacao requerida.'}), 401
        return redirect(url_for('auth.login', next=request.full_path or request.path))

    @app.after_request
    def no_cache(response):  # type: ignore[override]
        """Prevent caching of authenticated pages."""

        if session.get('user'):
            response.headers.setdefault('Cache-Control', 'no-store')
        return response
