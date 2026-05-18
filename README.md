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

## Ошибка «password authentication failed»

1. Пароль PgBouncer показывается **один раз** при создании или сбросе — сохраните его.
2. DataGrip/JDBC используют SCRAM — в конфиге `auth_type = scram-sha-256`.
3. Сбросить пароль на сервере:

```bash
python -m admin reset-password vi
python -m admin reload
```

Или в админке: кнопка **Новый пароль** у пользователя.

## JDBC (DBeaver, DataGrip, приложения)

```
jdbc:postgresql://89.125.17.107:6432/pool_vi?user=vi&password=ВАШ_ПАРОЛЬ&sslmode=disable
```

- **user** — логин PgBouncer (не пользователь PostgreSQL на backend)
- **database** в URL — имя пула (`pool_vi` для пользователя `vi`)
- **sslmode=disable** — PgBouncer в Docker без TLS; с `sslmode=require` клиент может не подключиться

## Если «Connection timed out»

Таймаут почти всегда значит, что **TCP до порта 6432 не доходит** (не пароль и не имя БД).

**На сервере:**

```bash
docker ps | grep pgbouncer
ss -tlnp | grep 6432          # должно быть 0.0.0.0:6432
sudo ufw allow 6432/tcp         # если включён ufw
sudo ufw status
```

В панели хостинга (Hetzner, AWS, …) откройте **входящий TCP 6432** в Security Group / firewall.

**С вашего ПК:**

```bash
nc -zv 89.125.17.107 6432
```

Если `timed out` — порт закрыт снаружи. Если `succeeded` — укажите в JDBC `user`, `password` и `sslmode=disable`.

После правок: `python -m admin reload` и при необходимости `docker compose up -d`.

## «server conn crashed» / «server DNS lookup failed»

| Симптом | Причина |
|---------|---------|
| `134.209.242.88\` в логах | Старый конфиг с экранированными пробелами → `python -m admin reload` |
| `server conn crashed?` | PgBouncer дошёл до PostgreSQL, но backend разорвал соединение |

**Проверка с сервера vpn-panel** (минуя PgBouncer, без SSL):

```bash
psql "postgresql://v_redka:ПАРОЛЬ@134.209.242.88:42002/rate_shopper?sslmode=disable"
```

Если не подключается — host, port `42002`, user/password PostgreSQL или firewall (**разрешите IP vpn-panel** `89.125.17.107`).

Диагностика одной командой (с vpn-panel, без PgBouncer):

```bash
pip install psycopg2-binary   # если ещё нет
python -m admin test-backend --pool pool_vi
```

Если **test-backend OK**, а в логах PgBouncer `password authentication failed` для backend — пароль PostgreSQL **не** кладётся в `password=` в `pgbouncer.ini` (у PgBouncer 1.24 бывает сбой SCRAM). Используется файл `runtime/pgpass` и переменная `PGPASSFILE` в Docker (это **не** параметр `passfile=` в строке пула).

```bash
rm -rf runtime/pgpass   # если случайно создался каталог
python -m admin reload
docker compose up -d --force-recreate
```

Если **test-backend OK**, а в логах `server conn crashed` — после обновления кода:

```bash
python -m admin reload
docker compose restart pgbouncer
```

Если **test-backend FAIL** — исправьте host/port/user/password PostgreSQL в админке (это не PgBouncer).

## Безопасность

- Админка по умолчанию слушает `0.0.0.0` (доступ с других хостов, пока запущен `serve`)
- Задайте сильный `PGB_ADMIN_SECRET` до продакшена
- Для доступа только с сервера: `PGB_ADMIN_HOST=127.0.0.1` в `.env`
- Открывайте порт в firewall только на время настройки; одноразовые креды в консоли
