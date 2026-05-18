from __future__ import annotations

import secrets

_credentials: tuple[str, str] | None = None


def generate_credentials() -> tuple[str, str]:
    """Новый логин и пароль на каждый запуск serve."""
    username = f"admin_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(18)
    return username, password


def activate(username: str, password: str) -> None:
    global _credentials
    _credentials = (username, password)


def verify(username: str, password: str) -> bool:
    if _credentials is None:
        return False
    expected_user, expected_pass = _credentials
    user_ok = secrets.compare_digest(username.encode(), expected_user.encode())
    pass_ok = secrets.compare_digest(password.encode(), expected_pass.encode())
    return user_ok and pass_ok


def is_active() -> bool:
    return _credentials is not None
