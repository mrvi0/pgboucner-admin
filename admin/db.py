from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from admin import crypto
from admin.settings import DATA_DIR, DB_PATH, SESSION_SECRET


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS postgres_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 5432,
                database TEXT NOT NULL,
                user TEXT NOT NULL,
                password_enc TEXT NOT NULL,
                sslmode TEXT NOT NULL DEFAULT 'disable',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pgbouncer_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                pool_name TEXT NOT NULL UNIQUE,
                auth_md5 TEXT NOT NULL,
                postgres_server_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (postgres_server_id) REFERENCES postgres_servers(id)
                    ON DELETE RESTRICT
            );
            """
        )
        cols = {r[1] for r in conn.execute("PRAGMA table_info(postgres_servers)")}
        if "sslmode" not in cols:
            conn.execute(
                "ALTER TABLE postgres_servers ADD COLUMN sslmode TEXT NOT NULL DEFAULT 'disable'"
            )
        ucols = {r[1] for r in conn.execute("PRAGMA table_info(pgbouncer_users)")}
        if "password_enc" not in ucols:
            conn.execute("ALTER TABLE pgbouncer_users ADD COLUMN password_enc TEXT")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def storage_key() -> bytes:
    return crypto.derive_storage_key(SESSION_SECRET)


def list_postgres_servers() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT id, name, host, port, database, user, created_at
            FROM postgres_servers ORDER BY name
            """
        ).fetchall()


def get_postgres_server(server_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM postgres_servers WHERE id = ?", (server_id,)
        ).fetchone()


def create_postgres_server(
    name: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sslmode: str = "disable",
) -> int:
    password = password.strip().strip("\r")
    enc = crypto.encrypt_secret(password, storage_key())
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO postgres_servers
                (name, host, port, database, user, password_enc, sslmode, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, host, port, database, user, enc, sslmode, _now()),
        )
        return cur.lastrowid


def update_postgres_password_by_name(name: str, password: str) -> None:
    password = password.strip().strip("\r")
    enc = crypto.encrypt_secret(password, storage_key())
    with connect() as conn:
        cur = conn.execute(
            "UPDATE postgres_servers SET password_enc = ? WHERE name = ?",
            (enc, name),
        )
        if cur.rowcount == 0:
            raise ValueError(f"сервер PostgreSQL «{name}» не найден")


def delete_postgres_server(server_id: int) -> bool:
    with connect() as conn:
        used = conn.execute(
            "SELECT 1 FROM pgbouncer_users WHERE postgres_server_id = ? LIMIT 1",
            (server_id,),
        ).fetchone()
        if used:
            return False
        conn.execute("DELETE FROM postgres_servers WHERE id = ?", (server_id,))
        return True


def postgres_password(server: sqlite3.Row) -> str:
    return crypto.decrypt_secret(server["password_enc"], storage_key())


def list_pgbouncer_users() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.pool_name, u.created_at,
                   s.name AS server_name, s.host, s.port
            FROM pgbouncer_users u
            JOIN postgres_servers s ON s.id = u.postgres_server_id
            ORDER BY u.username
            """
        ).fetchall()
    return [dict(r) for r in rows]


def create_pgbouncer_user(
    username: str, password: str, postgres_server_id: int
) -> tuple[int, str]:
    password = password.strip().strip("\r")
    pool_name = f"pool_{username}"
    auth_secret = crypto.pgbouncer_auth_secret(username, password)
    pwd_enc = crypto.encrypt_secret(password, storage_key())
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO pgbouncer_users
                (username, pool_name, auth_md5, password_enc, postgres_server_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, pool_name, auth_secret, pwd_enc, postgres_server_id, _now()),
        )
        return cur.lastrowid, pool_name


def reset_pgbouncer_password(user_id: int, password: str | None = None) -> tuple[str, str, str]:
    """Новый пароль и pool_name. Возвращает (username, password, pool_name)."""
    plain = password or crypto.random_password()
    with connect() as conn:
        row = conn.execute(
            "SELECT username, pool_name FROM pgbouncer_users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise ValueError("пользователь не найден")
        plain = plain.strip().strip("\r")
        auth_secret = crypto.pgbouncer_auth_secret(row["username"], plain)
        pwd_enc = crypto.encrypt_secret(plain, storage_key())
        conn.execute(
            "UPDATE pgbouncer_users SET auth_md5 = ?, password_enc = ? WHERE id = ?",
            (auth_secret, pwd_enc, user_id),
        )
        return row["username"], plain, row["pool_name"]


def reset_pgbouncer_password_by_name(username: str, password: str | None = None) -> tuple[str, str, str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM pgbouncer_users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            raise ValueError(f"пользователь «{username}» не найден")
        return reset_pgbouncer_password(row["id"], password)


def get_pgbouncer_user_auth(username: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT username, auth_md5, password_enc FROM pgbouncer_users WHERE username = ?",
            (username,),
        ).fetchone()


def pgbouncer_client_password(row: sqlite3.Row) -> str | None:
    enc = row["password_enc"]
    if not enc:
        return None
    return crypto.decrypt_secret(enc, storage_key())


def delete_pgbouncer_user(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM pgbouncer_users WHERE id = ?", (user_id,))
