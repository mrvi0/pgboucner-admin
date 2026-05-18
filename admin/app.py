from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from admin import config_generator, crypto, db, ephemeral_auth
from admin.settings import HOST, PORT, ROOT, SESSION_MAX_AGE, SESSION_SECRET

templates = Jinja2Templates(directory=str(ROOT / "admin" / "templates"))

app = FastAPI(title="PgBouncer Admin", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=False,
)


class _LoginRedirect(Exception):
    pass


@app.exception_handler(_LoginRedirect)
async def _login_redirect_handler(_request: Request, _exc: _LoginRedirect):
    return RedirectResponse("/login", status_code=303)


def require_login(request: Request) -> str:
    user = request.session.get("admin_user")
    if not user:
        raise _LoginRedirect()
    return user


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("admin_user"):
        return RedirectResponse("/login", status_code=303)
    servers = db.list_postgres_servers()
    users = db.list_pgbouncer_users()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"servers": servers, "users": users, "flash": request.session.pop("flash", None)},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("admin_user"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    if not ephemeral_auth.verify(username.strip(), password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Неверный логин или пароль"},
            status_code=401,
        )
    request.session["admin_user"] = username.strip()
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/servers/new", response_class=HTMLResponse)
async def server_new(request: Request, _: str = Depends(require_login)):
    return templates.TemplateResponse(request, "server_form.html", {"error": None})


@app.post("/servers/new")
async def server_create(
    request: Request,
    _: str = Depends(require_login),
    name: Annotated[str, Form()] = "",
    host: Annotated[str, Form()] = "",
    port: Annotated[int, Form()] = 5432,
    database: Annotated[str, Form()] = "",
    user: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    try:
        db.create_postgres_server(
            name.strip(),
            host.strip(),
            int(port),
            database.strip(),
            user.strip(),
            password,
        )
        ok, msg = config_generator.apply_and_reload()
        request.session["flash"] = (
            f"Сервер PostgreSQL «{name}» добавлен. {msg}"
            if ok
            else f"Сервер добавлен, но перезагрузка PgBouncer: {msg}"
        )
        return RedirectResponse("/", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "server_form.html",
            {"error": str(exc)},
            status_code=400,
        )


@app.post("/servers/{server_id}/delete")
async def server_delete(
    request: Request,
    server_id: int,
    _: str = Depends(require_login),
):
    if db.delete_postgres_server(server_id):
        config_generator.apply_and_reload()
        request.session["flash"] = "Сервер удалён."
    else:
        request.session["flash"] = "Нельзя удалить: есть пользователи PgBouncer, привязанные к серверу."
    return RedirectResponse("/", status_code=303)


@app.get("/users/new", response_class=HTMLResponse)
async def user_new(request: Request, _: str = Depends(require_login)):
    servers = db.list_postgres_servers()
    return templates.TemplateResponse(
        request, "user_form.html", {"servers": servers, "error": None, "created": None}
    )


@app.post("/users/new")
async def user_create(
    request: Request,
    _: str = Depends(require_login),
    username: Annotated[str, Form()] = "",
    postgres_server_id: Annotated[int, Form()] = 0,
    password: Annotated[str, Form()] = "",
):
    servers = db.list_postgres_servers()
    plain = password.strip() or crypto.random_password()
    try:
        _user_id, pool_name = db.create_pgbouncer_user(
            username.strip(),
            plain,
            int(postgres_server_id),
        )
        ok, msg = config_generator.apply_and_reload()
        return templates.TemplateResponse(
            request,
            "user_form.html",
            {
                "servers": servers,
                "error": None,
                "created": {
                    "username": username.strip(),
                    "password": plain,
                    "pool_name": pool_name,
                    "reload": msg if ok else f"ошибка reload: {msg}",
                },
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "user_form.html",
            {"servers": servers, "error": str(exc), "created": None},
            status_code=400,
        )


@app.post("/users/{user_id}/delete")
async def user_delete(
    request: Request,
    user_id: int,
    _: str = Depends(require_login),
):
    db.delete_pgbouncer_user(user_id)
    config_generator.apply_and_reload()
    request.session["flash"] = "Пользователь PgBouncer удалён."
    return RedirectResponse("/", status_code=303)


def create_app() -> FastAPI:
    return app
