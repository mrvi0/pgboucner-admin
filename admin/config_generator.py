from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from admin import crypto, db
from admin.backend_test import verify_postgres
from admin.settings import (
    DOCKER_COMPOSE,
    PGBOUNCER_INI,
    PGBOUNCER_LISTEN_PORT,
    RUNTIME_DIR,
    USERLIST_TXT,
)

# Безопасно без кавычек в строке [databases] PgBouncer (пробел = конец значения)
_INI_SAFE = re.compile(r"^[\w.@+-]+$")


def _userlist_quoted(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _ini_param_value(value: str) -> str:
    """Значение в pool = host=... password=... — только одинарные кавычки PgBouncer."""
    s = str(value).strip().strip("\r")
    if _INI_SAFE.match(s):
        return s
    return "'" + s.replace("'", "''") + "'"


def _backend_conn_str(
    host: str, port: int, database: str, user: str, password: str
) -> str:
    """
    password= обязателен: PgBouncer НЕ читает PGPASSFILE/libpq .pgpass для backend.
    Без password= в ini уходит пустой пароль → v_redka падает на SCRAM.
    """
    pwd = password.strip().strip("\r")
    return (
        f"host={_ini_param_value(host)} "
        f"port={port} "
        f"dbname={_ini_param_value(database)} "
        f"user={_ini_param_value(user)} "
        f"password={_ini_param_value(pwd)}"
    )


def generate_configs(*, verify_backends: bool = True) -> None:
    db.init_db()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    database_lines: list[str] = []
    userlist_lines: list[str] = []

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT u.username, u.pool_name, u.auth_md5,
                   u.password_enc AS client_password_enc,
                   s.id AS server_id, s.name AS server_name,
                   s.host, s.port, s.database, s.user,
                   s.password_enc AS server_password_enc, s.sslmode
            FROM pgbouncer_users u
            JOIN postgres_servers s ON s.id = u.postgres_server_id
            ORDER BY u.pool_name
            """
        ).fetchall()

    server_tls_modes: list[str] = []

    for row in rows:
        server_enc = row["server_password_enc"]
        if not server_enc:
            raise RuntimeError(
                f"Пул «{row['pool_name']}»: нет пароля PostgreSQL для сервера "
                f"«{row['server_name']}». Задайте: make set-pg-password SERVER={row['server_name']} PASS='...'"
            )
        pg_password = crypto.decrypt_secret(server_enc, db.storage_key())
        sslmode = row["sslmode"] or "disable"
        working_ssl = sslmode
        if verify_backends:
            ok, err, working_ssl = verify_postgres(
                row["host"],
                row["port"],
                row["database"],
                row["user"],
                pg_password,
                sslmode,
            )
            if not ok:
                raise RuntimeError(
                    f"Пул «{row['pool_name']}»: PostgreSQL отклоняет логин "
                    f"{row['user']}@{row['host']}:{row['port']}/{row['database']}.\n"
                    f"  {err}\n"
                    f"  Обновите пароль: make set-pg-password SERVER={row['server_name']} PASS='...'"
                )
        if verify_backends and working_ssl != sslmode:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE postgres_servers SET sslmode = ? WHERE id = ?",
                    (working_ssl, row["server_id"]),
                )
            print(
                f"Пул «{row['pool_name']}»: sslmode={working_ssl} (было {sslmode})",
                flush=True,
            )
            sslmode = working_ssl
        server_tls_modes.append(sslmode)

        conn_str = _backend_conn_str(
            row["host"], row["port"], row["database"], row["user"], pg_password
        )
        database_lines.append(f"{row['pool_name']} = {conn_str}")

        client_pwd = None
        if row["client_password_enc"]:
            client_pwd = crypto.decrypt_secret(
                row["client_password_enc"], db.storage_key()
            )
        if client_pwd is not None:
            userlist_lines.append(
                f"{_userlist_quoted(row['username'])} {_userlist_quoted(client_pwd)}"
            )
        else:
            print(
                f"Внимание: у «{row['username']}» нет сохранённого пароля — "
                f"сбросьте: make reset-password USER={row['username']}",
                flush=True,
            )
            userlist_lines.append(
                f"{_userlist_quoted(row['username'])} {_userlist_quoted(row['auth_md5'])}"
            )
        if os.environ.get("PGB_DEBUG_LOG_PASSWORDS") == "1":
            print(
                f"[PGB_DEBUG] {row['pool_name']}: backend {row['user']} "
                f"password len={len(pg_password.strip())} (в ini password=)",
                flush=True,
            )

    server_tls = "disable"
    if any(m in ("require", "prefer", "allow") for m in server_tls_modes):
        server_tls = "require" if "require" in server_tls_modes else server_tls_modes[0]

    ini_body = f""";; Generated by pgbouncer-admin — do not edit manually while using the admin UI
;; Backend: password= в строке пула (PgBouncer не использует PGPASSFILE)
;; Client: plaintext в userlist.txt (auth_type scram-sha-256)

[databases]
{chr(10).join(database_lines) if database_lines else "; add pools via admin UI"}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = {PGBOUNCER_LISTEN_PORT}
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt
server_tls_sslmode = {server_tls}
pool_mode = transaction
verbose = 1
log_pooler_errors = 1
max_client_conn = 1000
default_pool_size = 20
min_pool_size = 0
reserve_pool_size = 5
server_reset_query = DISCARD ALL
ignore_startup_parameters = extra_float_digits

[users]
"""

    USERLIST_TXT.write_text(
        ("\n".join(userlist_lines) + "\n") if userlist_lines else "\n",
        encoding="utf-8",
        newline="\n",
    )
    PGBOUNCER_INI.write_text(ini_body, encoding="utf-8", newline="\n")


def reload_pgbouncer() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [*DOCKER_COMPOSE.split(), "exec", "-T", "pgbouncer", "kill", "-HUP", "1"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=Path(PGBOUNCER_INI).parents[1],
        )
        if result.returncode == 0:
            return True, "RELOAD OK (SIGHUP)"
        return False, (result.stderr or result.stdout or "reload failed").strip()
    except FileNotFoundError:
        return False, "docker compose not found"
    except subprocess.TimeoutExpired:
        return False, "reload timed out"


def apply_and_reload(*, verify_backends: bool = True) -> tuple[bool, str]:
    generate_configs(verify_backends=verify_backends)
    ok, msg = reload_pgbouncer()
    if not ok:
        return False, f"config written; reload: {msg}"
    return True, msg


def ensure_bootstrap_configs() -> None:
    db.init_db()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if not PGBOUNCER_INI.exists():
        generate_configs(verify_backends=False)
