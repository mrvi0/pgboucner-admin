from __future__ import annotations

import argparse
import subprocess
import sys

import uvicorn

from admin import backend_test, config_generator, db, ephemeral_auth
from admin.settings import DOCKER_COMPOSE, HOST, PORT, ROOT, SESSION_SECRET


def _print_ephemeral_credentials(username: str, password: str) -> None:
    url = f"http://{HOST}:{PORT}/"
    line = "═" * 52
    print(line)
    print("  Админка (только этот запуск)")
    print(f"  URL:     {url}")
    print(f"  Логин:   {username}")
    print(f"  Пароль:  {password}")
    print("  Скопируйте сейчас — при следующем serve будут другие.")
    print(line)
    print("  Ctrl+C — админка остановится, PgBouncer продолжит работать.")
    print()


def cmd_up(args: argparse.Namespace) -> int:
    db.init_db()
    config_generator.ensure_bootstrap_configs()
    cmd = [*DOCKER_COMPOSE.split(), "up", "-d", "pgbouncer"]
    print("Запуск PgBouncer:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode


def cmd_down(args: argparse.Namespace) -> int:
    cmd = [*DOCKER_COMPOSE.split(), "down"]
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode


def cmd_serve(args: argparse.Namespace) -> int:
    db.init_db()
    config_generator.ensure_bootstrap_configs()

    if SESSION_SECRET == "change-me-in-production":
        print(
            "Внимание: задайте PGB_ADMIN_SECRET в .env (сейчас значение по умолчанию).",
            file=sys.stderr,
        )

    username, password = ephemeral_auth.generate_credentials()
    ephemeral_auth.activate(username, password)
    _print_ephemeral_credentials(username, password)

    uvicorn.run(
        "admin.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    db.init_db()
    if not args.username:
        print("Укажите логин: python -m admin reset-password vi", file=sys.stderr)
        return 1
    try:
        username, plain, pool_name = db.reset_pgbouncer_password_by_name(
            args.username, args.password
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    config_generator.apply_and_reload()
    print(f"Пользователь: {username}")
    print(f"Пароль:       {plain}")
    print(f"Database:     {pool_name}")
    print("Подключение:  psql -h <host> -p 6432 -U", username, "-d", pool_name)
    return 0


def cmd_set_pg_password(args: argparse.Namespace) -> int:
    db.init_db()
    if not args.password:
        import getpass

        args.password = getpass.getpass("Пароль PostgreSQL: ")
    try:
        db.update_postgres_password_by_name(args.server_name, args.password)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    config_generator.apply_and_reload()
    print(f"Пароль для сервера «{args.server_name}» обновлён, PgBouncer перезагружен.")
    print(f"Длина сохранённого пароля: {len(args.password.strip())} символов")
    return 0


def cmd_test_backend(args: argparse.Namespace) -> int:
    db.init_db()
    ok, msg = backend_test.test_backend(args.pool)
    print(msg)
    if ok:
        print("\nЕсли test-backend OK, а PgBouncer — server conn crashed:")
        print("  docker compose exec pgbouncer sh -c 'apk add --no-cache postgresql-client 2>/dev/null; psql \"$DATABASE_URL\"'")
        print("  или: python -m admin reload && docker compose restart pgbouncer")
    return 0 if ok else 1


def cmd_reload(args: argparse.Namespace) -> int:
    from admin.config_generator import _backend_conn_str

    sample = _backend_conn_str("127.0.0.1", 5432, "db", "u", "p")
    if "sslmode" in sample or sample.startswith("postgres://"):
        print(
            "Ошибка: устаревший admin/config_generator.py (postgres:// или sslmode в [databases]).\n"
            "Скопируйте актуальный config_generator.py и повторите reload.",
            file=sys.stderr,
        )
        return 1

    db.init_db()
    config_generator.generate_configs()

    ini = config_generator.PGBOUNCER_INI.read_text(encoding="utf-8")
    if "postgres://" in ini or " sslmode=" in ini:
        print("Ошибка: в pgbouncer.ini недопустимый формат (postgres:// или sslmode в пуле).", file=sys.stderr)
        return 1

    for line in ini.splitlines():
        if line.strip() and not line.strip().startswith(";") and "=" in line:
            if line.strip().startswith("[") or "listen_" in line or "auth_" in line:
                continue
            if "pool_" in line or " =" in line:
                print("Сгенерировано:", line.split("=", 1)[0].strip(), "= host=...")
                break

    ok, msg = config_generator.reload_pgbouncer()
    print(msg)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PgBouncer: Docker-пул + эфемерная админка"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("up", help="Запустить PgBouncer в Docker (фон)")
    p_up.set_defaults(func=cmd_up)

    p_down = sub.add_parser("down", help="Остановить контейнер PgBouncer")
    p_down.set_defaults(func=cmd_down)

    p_serve = sub.add_parser(
        "serve",
        help="Веб-админка (одноразовый логин/пароль в консоли на каждый запуск)",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_pgpass = sub.add_parser(
        "set-pg-password",
        help="Обновить пароль PostgreSQL в БД админки (если прямой psql ок, а пул нет)",
    )
    p_pgpass.add_argument("server_name", help="Имя сервера из админки, как в списке")
    p_pgpass.add_argument("-p", "--password", help="Пароль (иначе запросит интерактивно)")
    p_pgpass.set_defaults(func=cmd_set_pg_password, password=None)

    p_test = sub.add_parser(
        "test-backend",
        help="Проверить PostgreSQL напрямую (как в админке, без PgBouncer)",
    )
    p_test.add_argument("--pool", default="pool_vi", help="Имя пула, по умолчанию pool_vi")
    p_test.set_defaults(func=cmd_test_backend)

    p_reload = sub.add_parser("reload", help="Перегенерировать конфиг и RELOAD")
    p_reload.set_defaults(func=cmd_reload)

    p_reset = sub.add_parser(
        "reset-password",
        help="Новый пароль пользователя PgBouncer (показать в консоли)",
    )
    p_reset.add_argument("username", help="Логин, например vi")
    p_reset.add_argument("-p", "--password", help="Задать пароль вручную")
    p_reset.set_defaults(func=cmd_reset_password, password=None)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
