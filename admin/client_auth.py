from __future__ import annotations

from admin import crypto, db


def verify_client_password(username: str, candidate: str) -> str:
    row = db.get_pgbouncer_user_auth(username)
    if not row:
        return f"Пользователь PgBouncer «{username}» не найден."

    cand = candidate.strip().strip("\r")
    lines = [f"Пользователь PgBouncer: «{username}»", ""]

    stored = db.pgbouncer_client_password(row)
    if stored is not None:
        lines.append("--- пароль в SQLite (для userlist) ---")
        lines.append(f"  совпадает с введённым: {'ДА' if stored == cand else 'НЕТ'}")
        if stored != cand:
            lines.append(f"  длина в БД: {len(stored)}, вы ввели: {len(cand)}")
    else:
        lines.append(
            "--- password_enc нет (пользователь создан до обновления) ---"
        )
        lines.append("  Сбросьте пароль: make reset-password USER=" + username)

    scram_ok = crypto.scram_secret_matches_password(cand, row["auth_md5"])
    lines.extend(
        [
            "",
            "--- SCRAM-секрет (auth_md5) ---",
            f"  совпадает с введённым: {'ДА' if scram_ok else 'НЕТ'}",
            "",
            "Если оба «НЕТ» — в DataGrip/JDBC указан неверный пароль.",
            "Если «ДА», но PgBouncer отклоняет — выполните: make reload",
        ]
    )
    return "\n".join(lines)
