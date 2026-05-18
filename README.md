# PgBouncer + эфемерная админка

PgBouncer работает в Docker постоянно. Веб-админка запускается вручную и **недоступна**, когда процесс остановлен — снижает риск брутфорса.

## Быстрый старт

```bash
cp .env.example .env
# отредактируйте PGB_ADMIN_SECRET

make install                  # один раз: .venv + pip
make up                       # поднять PgBouncer в Docker
make serve                    # в консоли — одноразовые логин и пароль
```

Без `make`: `source .venv/bin/activate` и `python -m admin …`. Список целей: `make` или `make help`.

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

| Make | `python -m admin` | Описание |
|------|-------------------|----------|
| `make up` | `up` | Запустить PgBouncer (Docker) |
| `make down` | `down` | Остановить контейнер |
| `make serve` | `serve` | Веб-UI + одноразовые креды в консоли |
| `make reload` | `reload` | Пересобрать конфиг и SIGHUP reload |
| `make reset-password USER=vi` | `reset-password vi` | Сменить пароль клиента PgBouncer |
| `make test-backend POOL=pool_vi` | `test-backend --pool …` | Проверка логина в PostgreSQL |

## Архитектура

- `docker-compose.yml` — контейнер [edoburu/pgbouncer](https://hub.docker.com/r/edoburu/pgbouncer)
- `runtime/pgbouncer.ini`, `runtime/userlist.txt` — генерируются из SQLite
- `data/admin.db` — настройки (не коммитить)

При изменениях в UI выполняется `RELOAD` через `kill -HUP` в контейнере.

## Ошибка «password authentication failed»

В логах смотрите **кто** отклонён:

| Лог | Где ошибка |
|-----|------------|
| `pool_vi/vi@…` + `SASL authentication failed` | **Клиент → PgBouncer** — неверный пароль `vi` |
| `pool_vi/vi@…` + `SCRAM server-final-message` затем `v_redka` failed | **Клиент OK**, падает **backend** — `make set-pg-password` + `make reload` |
| `pool_vi/v_redka@…` + `server login failed` | **PgBouncer → PostgreSQL** — неверный `password=` в ini (не .pgpass) |

### Клиент (vi / DataGrip)

1. Пароль PgBouncer показывается **один раз** при создании или сбросе.
2. Проверка на сервере:

```bash
make verify-client USER=vi PASS='ваш_пароль'
```

3. Сброс и reload (не требует рабочего PostgreSQL — только пароль клиента):

```bash
make reset-password USER=vi
```

Если при `reload` падает проверка `v_redka` — это **backend**, не клиент `vi`:

```bash
make set-pg-password SERVER=prod PASS='пароль_postgres'
```

Или в админке: **Новый пароль** у пользователя (reload выполняется автоматически).

После обновления кода один раз сбросьте пароль — в `userlist` пишется plaintext (PgBouncer сам делает SCRAM с клиентом).

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

PgBouncer **не читает** `PGPASSFILE` / `.pgpass` для соединений к PostgreSQL — только `password=` в строке `[databases]`.

```bash
make set-pg-password SERVER=prod PASS='пароль_v_redka'
make reload
docker compose up -d --force-recreate
grep password= runtime/pgbouncer.ini   # должна быть строка pool_vi = ... password=...
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
