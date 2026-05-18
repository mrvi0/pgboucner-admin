from __future__ import annotations

import base64
import hashlib
import secrets

def pgbouncer_md5_password(username: str, password: str) -> str:
    """PgBouncer userlist format: md5 + md5(password + username)."""
    inner = hashlib.md5((password + username).encode()).hexdigest()
    return "md5" + inner


def encrypt_secret(plain: str, key: bytes) -> str:
    from cryptography.fernet import Fernet

    f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))
    return f.encrypt(plain.encode()).decode()


def decrypt_secret(token: str, key: bytes) -> str:
    from cryptography.fernet import Fernet

    f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))
    return f.decrypt(token.encode()).decode()


def derive_storage_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def random_password(length: int = 24) -> str:
    return secrets.token_urlsafe(length)[:length]
