from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

from admin import crypto, db
from admin.backend_test import verify_postgres
from admin.settings import (
    DOCKER_COMPOSE,
    PGBOUNCER_INI,
    PGBOUNCER_LISTEN_PORT,
    PGPASS_CONTAINER_PATH,
    PGPASS_FILE,
    RUNTIME_DIR,
    USERLIST_TXT,
)


_CONN_SAFE = re.compile(r"^[\w.+-]+$")


def _userlist_quoted(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _conn_value(value: str) -> str:
    s = str(value).strip().strip("\r")
    if _CONN_SAFE.match(s):
        return s
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _pgpass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _pgpass_line(host: str, port: int, database: str, user: str, password: str) -> str:
    return ":".join(
        [
            _pgpass_escape(host),
            str(port),
            _pgpass_escape(database),
            _pgpass_escape(user),
            _pgpass_escape(password.strip().strip("\r")),
        ]
    )


def _backend_conn_str(host: str, port: int, database: str, user: str) -> str:
    """
    Без password= в ini: PgBouncer 1.24 ломает SCRAM при password= в [databases],
    хотя тот же пароль через psql/PGPASSFILE работает (см. test-backend --via-docker).
    Пароль берётся из файла PGPASSFILE в контейнере.
    """
    return (
        f"host={_conn_value(host)} "
        f"port={port} "
        f"dbname={_conn_value(database)} "
        f"user={_conn_value(user)}"
    )


def _write_pgpass(lines: list[str]) -> None:
    if PGPASS_FILE.exists() and PGPASS_FILE.is_dir():
        raise RuntimeError(
            f"{PGPASS_FILE} — это каталог (ошибка Docker). Выполните: rm -rf runtime/pgpass"
        )
    body = "\n".join(lines) + ("\n" if lines else "")
    PGPASS_FILE.write_text(body, encoding="utf-8", newline="\n")
    if lines:
        PGPASS_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


def generate_configs(*, verify_backends: bool = True) -> None:
    db.init_db()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    database_lines: list[str] = []
    userlist_lines: list[str] = []
    pgpass_lines: list[str] = []
    pgpass_seen: set[str] = set()

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT u.username, u.pool_name, u.auth_md5, u.password_enc,
                   s.id AS server_id, s.name AS server_name,
                   s.host, s.port, s.database, s.user, s.password_enc, s.sslmode
            FROM pgbouncer_users u
            JOIN postgres_servers s ON s.id = u.postgres_server_id
            ORDER BY u.pool_name
            """
        ).fetchall()

    server_tls_modes: list[str] = []

    for row in rows:
        pg_password = crypto.decrypt_secret(row["password_enc"], db.storage_key())
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
                    f"  Обновите пароль: python -m admin set-pg-password {row['server_name']} -p '...'"
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

        pline = _pgpass_line(
            row["host"], row["port"], row["database"], row["user"], pg_password
        )
        if pline not in pgpass_seen:
            pgpass_seen.add(pline)
            pgpass_lines.append(pline)

        conn_str = _backend_conn_str(
            row["host"], row["port"], row["database"], row["user"]
        )
        database_lines.append(f"{row['pool_name']} = {conn_str}")
        client_pwd = None
        if row["password_enc"]:
            client_pwd = crypto.decrypt_secret(row["password_enc"], db.storage_key())
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
                f"[PGB_DEBUG] {row['pool_name']}: user={row['user']} "
                f"password via {PGPASS_CONTAINER_PATH} len={len(pg_password.strip())}",
                flush=True,
            )

    server_tls = "disable"
    if any(m in ("require", "prefer", "allow") for m in server_tls_modes):
        server_tls = "require" if "require" in server_tls_modes else server_tls_modes[0]

    _write_pgpass(pgpass_lines)

    ini_body = f""";; Generated by pgbouncer-admin — do not edit manually while using the admin UI
;; Backend passwords: {PGPASS_CONTAINER_PATH} (PGPASSFILE)
;; Client passwords: plaintext in userlist (auth_type scram-sha-256)

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
    if not PGPASS_FILE.exists() or PGPASS_FILE.is_dir():
        if PGPASS_FILE.is_dir():
            raise RuntimeError("Удалите каталог runtime/pgpass: rm -rf runtime/pgpass")
    if not PGBOUNCER_INI.exists():
        generate_configs()
