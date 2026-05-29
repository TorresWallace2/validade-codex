
"""Authentication and user management services."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from .. import db

ISO_FORMAT = db.ISO_FORMAT


class AuthServiceError(RuntimeError):
    """Base class for authentication related errors."""


@dataclass(slots=True)
class User:
    """Lightweight user representation."""

    id: int
    username: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str
    password_hash: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role.lower() == 'admin'


def _row_to_user(row, *, include_password: bool = False) -> User:
    password_hash = row['password_hash'] if include_password and 'password_hash' in row.keys() else None
    return User(
        id=row['id'],
        username=row['username'],
        role=row['role'],
        is_active=bool(row['is_active']),
        created_at=row['created_at'],
        updated_at=row['updated_at'],
        password_hash=password_hash,
    )


def get_user_by_username(username: str) -> User | None:
    rows = db.query(
        "SELECT id, username, password_hash, role, is_active, created_at, updated_at FROM users WHERE username = ?",
        (username.upper(),),
    )
    if not rows:
        return None
    return _row_to_user(rows[0], include_password=True)


def authenticate(username: str, password: str) -> User:
    user = get_user_by_username(username)
    if not user:
        raise AuthServiceError('Usuario ou senha invalidos.')
    if not user.password_hash or not check_password_hash(user.password_hash, password):
        raise AuthServiceError('Usuario ou senha invalidos.')
    if not user.is_active:
        raise AuthServiceError('Usuario inativo.')
    user.password_hash = None
    return user


def list_users() -> list[User]:
    rows = db.query(
        "SELECT id, username, role, is_active, created_at, updated_at FROM users ORDER BY username ASC"
    )
    return [_row_to_user(row) for row in rows]


def create_user(username: str, password: str, role: str, *, created_by: str) -> User:
    username_clean = username.strip().upper()
    if len(username_clean) < 3:
        raise AuthServiceError('Informe um usuario com pelo menos 3 caracteres.')
    if role not in {'admin', 'user'}:
        raise AuthServiceError('Perfil invalido.')
    if len(password) < 6:
        raise AuthServiceError('Senha deve ter ao menos 6 caracteres.')

    existing = get_user_by_username(username_clean)
    if existing:
        raise AuthServiceError('Usuario ja existe.')

    timestamp = datetime.utcnow().strftime(ISO_FORMAT)
    password_hash = generate_password_hash(password)
    db.execute(
        """
        INSERT INTO users(username, password_hash, role, is_active, created_at, updated_at)
        VALUES(?, ?, ?, 1, ?, ?)
        """,
        (username_clean, password_hash, role, timestamp, timestamp),
    )
    db.record_audit('-', 'create_user', created_by, f'Criado usuario {username_clean} com perfil {role}')
    user = get_user_by_username(username_clean)
    if not user:
        raise AuthServiceError('Falha ao criar o usuario.')
    return user


def update_password(username: str, new_password: str, *, updated_by: str) -> None:
    username_clean = username.strip().upper()
    if len(new_password) < 6:
        raise AuthServiceError('Senha deve ter ao menos 6 caracteres.')
    password_hash = generate_password_hash(new_password)
    timestamp = datetime.utcnow().strftime(ISO_FORMAT)
    affected = db.execute(
        """
        UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?
        """,
        (password_hash, timestamp, username_clean),
    )
    if affected == 0:
        raise AuthServiceError('Usuario nao encontrado.')
    db.record_audit('-', 'password_reset', updated_by, f'Redefiniu senha do usuario {username_clean}')


def set_active(username: str, active: bool, *, updated_by: str) -> None:
    username_clean = username.strip().upper()
    timestamp = datetime.utcnow().strftime(ISO_FORMAT)
    affected = db.execute(
        """
        UPDATE users SET is_active = ?, updated_at = ? WHERE username = ?
        """,
        (1 if active else 0, timestamp, username_clean),
    )
    if affected == 0:
        raise AuthServiceError('Usuario nao encontrado.')
    status = 'ativou' if active else 'desativou'
    db.record_audit('-', 'toggle_user', updated_by, f'{status} usuario {username_clean}')
