from __future__ import annotations

import argparse
import subprocess
import sys

import uvicorn

from admin import config_generator, db, ephemeral_auth
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


def cmd_reload(args: argparse.Namespace) -> int:
    db.init_db()
    config_generator.generate_configs()
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

    p_reload = sub.add_parser("reload", help="Перегенерировать конфиг и RELOAD")
    p_reload.set_defaults(func=cmd_reload)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
