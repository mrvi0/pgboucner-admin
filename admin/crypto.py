from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

def pgbouncer_md5_password(username: str, password: str) -> str:
    """PgBouncer userlist: md5 + md5(password + username) — только для старых клиентов."""
    inner = hashlib.md5((password + username).encode()).hexdigest()
    return "md5" + inner


def pgbouncer_scram_secret(password: str, iterations: int = 4096) -> str:
    """SCRAM-SHA-256 для userlist.txt (DataGrip, JDBC 42+, psql 14+)."""
    salt = secrets.token_bytes(16)
    salted_password = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()
    salt_b64 = base64.b64encode(salt).decode("ascii")
    return (
        f"SCRAM-SHA-256${iterations}:{salt_b64}$"
        f"{base64.b64encode(stored_key).decode('ascii')}:"
        f"{base64.b64encode(server_key).decode('ascii')}"
    )


def pgbouncer_auth_secret(username: str, password: str) -> str:
    """SCRAM-секрет для проверки пароля (username в SCRAM не участвует)."""
    _ = username
    return pgbouncer_scram_secret(password)


def scram_secret_matches_password(password: str, secret: str) -> bool:
    """Пароль совпадает с SCRAM-записью из userlist / auth_md5."""
    import re

    m = re.match(r"SCRAM-SHA-256\$(\d+):([^$]+)\$([^:]+):", secret.strip())
    if not m:
        return False
    iterations = int(m.group(1))
    salt = base64.b64decode(m.group(2))
    stored_key = base64.b64decode(m.group(3))
    salted = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    return hashlib.sha256(client_key).digest() == stored_key


def encrypt_secret(plain: str, key: bytes) -> str:
    from cryptography.fernet import Fernet

    f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))
    return f.encrypt(plain.encode()).decode()


def decrypt_secret(token: str, key: bytes) -> str:
    from cryptography.fernet import Fernet

    f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))
    # убрать \r и пробелы — иначе SCRAM к PostgreSQL с «верным» паролем падает
    return f.decrypt(token.encode()).decode().strip().strip("\r")


def derive_storage_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def random_password(length: int = 24) -> str:
    return secrets.token_urlsafe(length)[:length]
