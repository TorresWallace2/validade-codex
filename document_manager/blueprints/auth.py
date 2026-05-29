
"""Authentication blueprint for login views."""
from __future__ import annotations

from flask import Blueprint, redirect, render_template, session, url_for

auth_bp = Blueprint('auth', __name__)


@auth_bp.get('/login')
def login() -> str:
    if session.get('user'):
        return redirect(url_for('ui.index'))
    return render_template('login.html')


@auth_bp.get('/logout')
def perform_logout():
    session.pop('user', None)
    return redirect(url_for('auth.login'))
