from __future__ import annotations

from admin import crypto, db


def fetch_backend(pool_name: str) -> dict[str, str | int]:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT s.host, s.port, s.database, s.user, s.password_enc, s.sslmode
            FROM pgbouncer_users u
            JOIN postgres_servers s ON s.id = u.postgres_server_id
            WHERE u.pool_name = ?
            """,
            (pool_name,),
        ).fetchone()
    if not row:
        raise ValueError(f"пул «{pool_name}» не найден")
    return {
        "host": row["host"],
        "port": row["port"],
        "database": row["database"],
        "user": row["user"],
        "password": crypto.decrypt_secret(row["password_enc"], db.storage_key()),
        "sslmode": row["sslmode"] or "disable",
    }


def test_backend(pool_name: str = "pool_vi") -> tuple[bool, str]:
    try:
        import psycopg2
    except ImportError:
        return False, "установите: pip install psycopg2-binary"

    try:
        cfg = fetch_backend(pool_name)
    except ValueError as exc:
        return False, str(exc)

    dsn = (
        f"host={cfg['host']} port={cfg['port']} dbname={cfg['database']} "
        f"user={cfg['user']} password={cfg['password']} sslmode={cfg['sslmode']} "
        "connect_timeout=10"
    )
    summary = (
        f"{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']} "
        f"(sslmode={cfg['sslmode']})"
    )
    try:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, f"OK: {summary}\n    {version[:80]}"
    except Exception as exc:
        return False, f"Ошибка: {summary}\n    {type(exc).__name__}: {exc}"
