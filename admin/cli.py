from __future__ import annotations

import argparse
import subprocess
import sys

import uvicorn

from admin import backend_test, client_auth, config_generator, db, debug_pool, ephemeral_auth
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


def cmd_verify_client(args: argparse.Namespace) -> int:
    db.init_db()
    if not args.username:
        print("Укажите логин: python -m admin verify-client vi -p '...'", file=sys.stderr)
        return 1
    if args.password is None:
        import getpass

        args.password = getpass.getpass("Пароль PgBouncer: ")
    print(client_auth.verify_client_password(args.username, args.password))
    return 0


def cmd_debug_pool(args: argparse.Namespace) -> int:
    db.init_db()
    if args.compare is not None:
        print(debug_pool.compare_password(args.pool, args.compare))
    else:
        print(debug_pool.debug_pool(args.pool))
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
    if args.via_docker:
        ok, msg = backend_test.test_backend_via_docker(args.pool)
    else:
        ok, msg = backend_test.test_backend(args.pool)
    print(msg)
    if ok:
        print("\nЕсли test-backend OK, а PgBouncer — server conn crashed:")
        print("  docker compose exec pgbouncer sh -c 'apk add --no-cache postgresql-client 2>/dev/null; psql \"$DATABASE_URL\"'")
        print("  или: python -m admin reload && docker compose restart pgbouncer")
    return 0 if ok else 1


def cmd_reload(args: argparse.Namespace) -> int:
    from admin.config_generator import _backend_conn_str

    sample = _backend_conn_str("127.0.0.1", 5432, "db", "u")
    if "password=" in sample or "passfile=" in sample or sample.startswith("postgres://"):
        print(
            "Ошибка: устаревший config_generator.py (password= в ini не используем).",
            file=sys.stderr,
        )
        return 1

    db.init_db()
    try:
        config_generator.generate_configs(verify_backends=not args.skip_verify)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    ini = config_generator.PGBOUNCER_INI.read_text(encoding="utf-8")
    if "postgres://" in ini or "passfile=" in ini or " password=" in ini:
        print(
            "Ошибка: в ini не должно быть password=/passfile= — пароль в runtime/pgpass + PGPASSFILE.",
            file=sys.stderr,
        )
        return 1
    from admin.settings import PGPASS_FILE

    if "pool_" in ini and not PGPASS_FILE.is_file():
        print(f"Ошибка: нет файла {PGPASS_FILE}", file=sys.stderr)
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

    p_debug = sub.add_parser(
        "debug-pool",
        help="Показать пароль backend из БД и pgbouncer.ini (только для отладки)",
    )
    p_debug.add_argument("--pool", default="pool_vi")
    p_debug.add_argument(
        "--compare",
        metavar="PASSWORD",
        help="Сравнить с паролем, который вы считаете правильным",
    )
    p_debug.set_defaults(func=cmd_debug_pool, compare=None)

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
    p_test.add_argument(
        "--via-docker",
        action="store_true",
        help="Проверка psql из контейнера pgbouncer (как у пула)",
    )
    p_test.set_defaults(func=cmd_test_backend, via_docker=False)

    p_reload = sub.add_parser("reload", help="Перегенерировать конфиг и RELOAD")
    p_reload.add_argument(
        "--skip-verify",
        action="store_true",
        help="Не проверять PostgreSQL через psycopg2 (не рекомендуется)",
    )
    p_reload.set_defaults(func=cmd_reload, skip_verify=False)

    p_reset = sub.add_parser(
        "reset-password",
        help="Новый пароль пользователя PgBouncer (показать в консоли)",
    )
    p_reset.add_argument("username", help="Логин, например vi")
    p_reset.add_argument("-p", "--password", help="Задать пароль вручную")
    p_reset.set_defaults(func=cmd_reset_password, password=None)

    p_verify = sub.add_parser(
        "verify-client",
        help="Проверить пароль клиента PgBouncer (до подключения DataGrip)",
    )
    p_verify.add_argument("username", help="Логин, например vi")
    p_verify.add_argument("-p", "--password", help="Пароль для проверки")
    p_verify.set_defaults(func=cmd_verify_client, password=None)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
