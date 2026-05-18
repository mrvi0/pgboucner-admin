from __future__ import annotations

import re
from pathlib import Path

from admin import crypto, db
from admin.settings import PGBOUNCER_INI


def _parse_pool_line(line: str) -> dict[str, str]:
    """Разбор строки pool = host=... port=... password=..."""
    _, _, rhs = line.partition("=")
    rhs = rhs.strip()
    out: dict[str, str] = {}
    for m in re.finditer(
        r'(host|port|dbname|user|password)=("(?:\\.|[^"])*"|\S+)',
        rhs,
    ):
        key, val = m.group(1), m.group(2)
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out[key] = val
    return out


def _read_ini_pool(pool_name: str) -> dict[str, str] | None:
    if not PGBOUNCER_INI.exists():
        return None
    prefix = f"{pool_name} ="
    for line in PGBOUNCER_INI.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(prefix):
            return _parse_pool_line(line)
    return None


def debug_pool(pool_name: str) -> str:
    cfg = None
    from admin.backend_test import fetch_backend

    try:
        cfg = fetch_backend(pool_name)
    except ValueError as exc:
        return str(exc)

    ini = _read_ini_pool(pool_name)
    pwd = cfg["password"]
    quote_warn = ""
    if pwd and (pwd[0] in "'\"" or pwd[-1] in "'\""):
        quote_warn = "  ⚠ В пароле есть кавычки как СИМВОЛЫ — удалите их в set-pg-password"

    lines = [
        f"=== Пул «{pool_name}» ===",
        f"Сервер в админке: «{cfg['server_name']}»",
        "",
        "--- Из SQLite (что подставляется при reload) ---",
        f"  host:     {cfg['host']}",
        f"  port:     {cfg['port']}",
        f"  dbname:   {cfg['database']}",
        f"  user:     {cfg['user']}",
        f"  password: [{pwd}]",
        f"  (квадратные скобки — рамка; одинарные кавычки '...' в repr НЕ часть пароля)",
        f"  длина:    {len(pwd)} символов",
        quote_warn,
    ]
    if pwd:
        lines.append(f"  hex:      {pwd.encode('utf-8').hex()}")

    if ini:
        same = ini.get("password") == cfg["password"]
        lines.extend(
            [
                "",
                "--- Из runtime/pgbouncer.ini (что читает контейнер) ---",
                f"  host:     {ini.get('host', '?')}",
                f"  port:     {ini.get('port', '?')}",
                f"  dbname:   {ini.get('dbname', '?')}",
                f"  user:     {ini.get('user', '?')}",
                f"  password: [{ini.get('password', '?')}]",
                f"  длина:    {len(ini.get('password', ''))} символов",
                "",
                f"  Совпадает с SQLite: {'ДА' if same else 'НЕТ — сделайте python -m admin reload'}",
            ]
        )
    else:
        lines.append("")
        lines.append(f"--- В {PGBOUNCER_INI} строка для «{pool_name}» не найдена ---")

    lines.extend(
        [
            "",
            "Сравнить с вашим паролем:",
            "  python -m admin debug-pool pool_vi --compare 'ваш_пароль'",
            "",
            "ВНИМАНИЕ: не оставляйте вывод в screen/tmux и смените пароль после отладки.",
        ]
    )
    return "\n".join(lines)


def compare_password(pool_name: str, candidate: str) -> str:
    from admin.backend_test import fetch_backend

    cfg = fetch_backend(pool_name)
    stored = cfg["password"]
    cand = candidate
    lines = [
        f"Пул «{pool_name}», пользователь PostgreSQL «{cfg['user']}»",
        f"  в БД:      [{stored}] ({len(stored)} симв.)",
        f"  вы ввели:  [{cand}] ({len(cand)} симв.)",
        f"  равны:     {'ДА' if stored == cand else 'НЕТ'}",
        "  (символы [ ] — только оформление вывода, не кавычки пароля)",
    ]
    if stored != cand:
        lines.append("")
        lines.append("Побайтово (первое отличие):")
        for i, (a, b) in enumerate(zip(stored.encode(), cand.encode())):
            if a != b:
                lines.append(f"  позиция {i}: БД=0x{a:02x} ({chr(a) if 32<=a<127 else '?'})  "
                               f"вы=0x{b:02x} ({chr(b) if 32<=b<127 else '?'})")
                break
        else:
            if len(stored) != len(cand):
                lines.append(f"  длины разные: {len(stored)} vs {len(cand)}")
    return "\n".join(lines)
