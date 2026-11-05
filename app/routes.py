from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import desc, select

from app.auth import create_access_token, decode_access_token, hash_password, verify_password
from app.config import settings
from app.db import session_maker
from app.models import Client, User

router = APIRouter()

_PHONE_RE = re.compile(r"^[+]?[\d\s()\-]{5,20}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_templates_dir = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_templates_dir)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _render(name: str, **context) -> str:
    return _env.get_template(name).render(**context)


def _get_current_user(request: Request) -> int | None:
    token = request.cookies.get("token")
    if not token:
        return None
    sub = decode_access_token(token)
    if sub is None:
        return None
    try:
        return int(sub)
    except ValueError:
        return None


def _login_required(request: Request) -> int:
    user_id = _get_current_user(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user_id


# ---- Auth ----

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if _get_current_user(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return HTMLResponse(_render("login.html"))


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_render("login.html"))


@router.post("/login")
async def login_post(username: str = Form(...), password: str = Form(...)):
    async with session_maker() as session:
        user = await session.scalar(select(User).where(User.username == username))
        if not user or not verify_password(password, user.password_hash):
            return HTMLResponse(_render("login.html", error="Неверное имя или пароль"))
        if not user.is_active:
            return HTMLResponse(_render("login.html", error="Пользователь заблокирован"))
        token = create_access_token(str(user.id))
        resp = RedirectResponse(url="/dashboard", status_code=302)
        resp.set_cookie(key="token", value=token, max_age=settings.jwt_expire_minutes * 60, httponly=True)
        return resp


@router.get("/register", response_class=HTMLResponse)
async def register_page():
    return HTMLResponse(_render("register.html"))


@router.post("/register")
async def register_post(username: str = Form(...), password: str = Form(...)):
    if len(username.strip()) < 3 or len(password) < 4:
        return HTMLResponse(_render("register.html", error="Имя от 3 символов, пароль от 4"))
    async with session_maker() as session:
        existing = await session.scalar(select(User).where(User.username == username.strip()))
        if existing:
            return HTMLResponse(_render("register.html", error="Пользователь уже существует"))
        user = User(
            username=username.strip(),
            password_hash=hash_password(password),
            is_admin=(username.strip() == settings.admin_username),
        )
        session.add(user)
        await session.commit()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("token")
    return resp


# ---- Dashboard ----

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, search: str = ""):
    user_id = _login_required(request)
    async with session_maker() as session:
        user = await session.get(User, user_id)
        q = select(Client).where(Client.user_id == user_id)
        if search.strip():
            q = q.where(Client.name.ilike(f"%{search.strip()}%"))
        q = q.order_by(desc(Client.id))
        clients = (await session.scalars(q)).all()
    return HTMLResponse(
        _render("dashboard.html", user=user, clients=clients, search=search)
    )


@router.get("/dashboard/client/new", response_class=HTMLResponse)
async def client_new_form(request: Request):
    _login_required(request)
    return HTMLResponse(_render("item_form.html"))


@router.post("/dashboard/client/create")
async def client_create(
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    note: str = Form(""),
):
    user_id = _login_required(request)
    if len(name.strip()) < 2:
        return HTMLResponse(
            _render("item_form.html", error="Имя должно быть минимум 2 символа")
        )
    phone_raw = phone.strip()
    if phone_raw and not _PHONE_RE.match(phone_raw):
        return HTMLResponse(
            _render("item_form.html", error="Некорректный телефон. Пример: +7 900 000-00-00")
        )
    email_raw = email.strip()
    if email_raw and not _EMAIL_RE.match(email_raw):
        return HTMLResponse(
            _render("item_form.html", error="Некорректный email")
        )
    async with session_maker() as session:
        c = Client(
            user_id=user_id,
            name=name.strip(),
            email=email.strip() or None,
            phone=phone.strip() or None,
            note=note.strip() or None,
        )
        session.add(c)
        await session.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/dashboard/client/{client_id}/edit", response_class=HTMLResponse)
async def client_edit_form(request: Request, client_id: int):
    user_id = _login_required(request)
    async with session_maker() as session:
        client = await session.get(Client, client_id)
        if not client or client.user_id != user_id:
            return HTMLResponse(_render("dashboard.html", error="Клиент не найден"))
    return HTMLResponse(_render("item_form.html", client=client, edit=True))


@router.post("/dashboard/client/{client_id}/edit")
async def client_edit(
    request: Request,
    client_id: int,
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    note: str = Form(""),
):
    user_id = _login_required(request)
    async with session_maker() as session:
        client = await session.get(Client, client_id)
        if not client or client.user_id != user_id:
            return HTMLResponse(
                _render("item_form.html", error="Клиент не найден")
            )
        if len(name.strip()) < 2:
            return HTMLResponse(
                _render("item_form.html", error="Имя должно быть минимум 2 символа")
            )
        phone_raw = phone.strip()
        if phone_raw and not _PHONE_RE.match(phone_raw):
            return HTMLResponse(
                _render("item_form.html", error="Некорректный телефон")
            )
        email_raw = email.strip()
        if email_raw and not _EMAIL_RE.match(email_raw):
            return HTMLResponse(
                _render("item_form.html", error="Некорректный email")
            )
        client.name = name.strip()
        client.email = email.strip() or None
        client.phone = phone.strip() or None
        client.note = note.strip() or None
        await session.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/dashboard/client/{client_id}/delete")
async def client_delete(request: Request, client_id: int):
    user_id = _login_required(request)
    async with session_maker() as session:
        client = await session.get(Client, client_id)
        if client and client.user_id == user_id:
            await session.delete(client)
            await session.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


# ---- Admin ----

@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user_id = _login_required(request)
    async with session_maker() as session:
        user = await session.get(User, user_id)
        if not user or not user.is_admin:
            return HTMLResponse(_render("login.html", error="Доступ запрещён"))
        users = (await session.scalars(select(User).order_by(User.id))).all()
    return HTMLResponse(_render("admin.html", users=users))


@router.post("/admin/user/{target_id}/toggle")
async def admin_toggle_user(request: Request, target_id: int):
    user_id = _login_required(request)
    async with session_maker() as session:
        admin = await session.get(User, user_id)
        if not admin or not admin.is_admin:
            raise HTTPException(status_code=403)
        target = await session.get(User, target_id)
        if target and target.id != user_id:
            target.is_active = not target.is_active
            await session.commit()
    return RedirectResponse(url="/admin", status_code=303)


# ---- Export ----

@router.get("/dashboard/clients.csv")
async def export_clients(request: Request):
    user_id = _login_required(request)
    async with session_maker() as session:
        clients = (
            await session.scalars(
                select(Client).where(Client.user_id == user_id).order_by(Client.id)
            )
        ).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "name", "email", "phone", "note", "created_at"])
    for c in clients:
        w.writerow(
            [
                c.id,
                c.name,
                c.email or "",
                c.phone or "",
                c.note or "",
                c.created_at.isoformat() if c.created_at else "",
            ]
        )
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=clients.csv"},
    )
