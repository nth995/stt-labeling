"""Đăng nhập, đăng xuất và trang dành cho người gán nhãn."""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import User
from app.services.auth_service import (
    COOKIE_NAME,
    PASSWORD_HASHER,
    SESSION_LIFETIME_SECONDS,
    cookie_secure,
    create_session,
    get_auth_context,
    get_csrf_token,
    is_valid_csrf,
    revoke_session,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@router.get("/", include_in_schema=False)
def home(request: Request, db: Session = Depends(get_db)):
    """Điều hướng theo vai trò của tài khoản."""
    context = get_auth_context(request, db)
    if context is None:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/admin" if context.user.role == "admin" else "/workspace", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    """Hiển thị trang đăng nhập."""
    context = get_auth_context(request, db)
    if context is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": None, "username": ""}
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Kiểm tra mật khẩu và cấp cookie phiên đăng nhập."""
    normalized = username.strip().lower()
    user = db.scalar(select(User).where(User.username == normalized))
    if user is None or not user.is_active or not PASSWORD_HASHER.verify(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Tài khoản hoặc mật khẩu không chính xác.", "username": normalized},
            status_code=401,
        )

    raw_token = create_session(db, user)
    response = RedirectResponse(
        "/admin" if user.role == "admin" else "/workspace", status_code=303
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_LIFETIME_SECONDS,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
        path="/",
    )
    return response


@router.post("/logout")
def logout(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Thu hồi phiên và xóa cookie đăng nhập."""
    context = get_auth_context(request, db)
    if context is None:
        return RedirectResponse("/login", status_code=303)
    if not is_valid_csrf(context, csrf_token):
        return HTMLResponse("Yêu cầu không hợp lệ.", status_code=403)

    revoke_session(db, context)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/workspace", response_class=HTMLResponse)
def workspace(request: Request, db: Session = Depends(get_db)):
    """Trang tạm của Annotator, sẽ thay bằng giao diện gán nhãn."""
    context = get_auth_context(request, db)
    if context is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
        context={"user": context.user, "csrf_token": get_csrf_token(context)},
    )
