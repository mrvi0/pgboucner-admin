# PgBouncer + эфемерная админка

PgBouncer работает в Docker постоянно. Веб-админка запускается вручную и **недоступна**, когда процесс остановлен — снижает риск брутфорса.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# отредактируйте PGB_ADMIN_SECRET

python -m admin up            # поднять PgBouncer в Docker
python -m admin serve         # в консоли появятся одноразовые логин и пароль
```

При каждом `serve` в терминале печатаются **новые** логин и пароль (например `admin_a1b2c3d4`). Их нет в базе — только в памяти процесса.

Остановите `serve` (Ctrl+C) — админка выключится, контейнер PgBouncer продолжит работать.

## Что делает админка

1. **Серверы PostgreSQL** — реальные host, port, database, user, password (пароль шифруется в SQLite).
2. **Пользователи PgBouncer** — логин/пароль для клиентов; каждый привязан к одному серверу PostgreSQL через отдельный пул.

Клиент подключается так:

```bash
psql -h localhost -p 6432 -U <pgb_user> -d pool_<pgb_user>
```

- `-U` — логин PgBouncer из админки  
- `-d` — имя пула (`pool_…`), показано после создания пользователя  

## Команды

| Команда | Описание |
|---------|----------|
| `python -m admin up` | Запустить PgBouncer (Docker) |
| `python -m admin down` | Остановить контейнер |
| `python -m admin serve` | Веб-UI + одноразовые креды в консоли |
| `python -m admin reload` | Пересобрать конфиг и SIGHUP reload |

## Архитектура

- `docker-compose.yml` — контейнер [edoburu/pgbouncer](https://hub.docker.com/r/edoburu/pgbouncer)
- `runtime/pgbouncer.ini`, `runtime/userlist.txt` — генерируются из SQLite
- `data/admin.db` — настройки (не коммитить)

При изменениях в UI выполняется `RELOAD` через `kill -HUP` в контейнере.

## Безопасность

- Админка по умолчанию на `127.0.0.1`
- Задайте сильный `PGB_ADMIN_SECRET` до продакшена
- Не открывайте порт админки наружу без reverse proxy и TLS
