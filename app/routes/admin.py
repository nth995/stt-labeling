"""Quản lý tài khoản Annotator qua giao diện Admin."""
import re
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import MIN_PASSWORD_LENGTH
from app.database.connection import get_db
from app.database.models import User, utc_timestamp
from app.services.auth_service import (
    get_auth_context,
    get_csrf_token,
    is_valid_csrf,
    revoke_user_sessions,
)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
password_hasher = PasswordHash.recommended()


def admin_context(request: Request, db: Session):
    """Kiểm tra phiên đăng nhập và vai trò Admin."""
    context = get_auth_context(request, db)
    if context is None:
        return None, RedirectResponse("/login", status_code=303)
    if context.user.role != "admin":
        return None, HTMLResponse("Không có quyền quản trị.", status_code=403)
    return context, None


def notice(message: str, error: bool = False):
    """Điều hướng về trang Admin và hiển thị kết quả thao tác."""
    query = urlencode({"error" if error else "message": message})
    return RedirectResponse(f"/admin?{query}", status_code=303)


def checked_admin(request: Request, db: Session, csrf_token: str):
    """Kiểm tra quyền Admin và token bảo vệ biểu mẫu."""
    context, response = admin_context(request, db)
    if response is not None:
        return response
    if not is_valid_csrf(context, csrf_token):
        return HTMLResponse("Yêu cầu không hợp lệ.", status_code=403)
    return None


@router.get("", response_class=HTMLResponse)
def list_users(request: Request, db: Session = Depends(get_db)):
    """Hiển thị danh sách tài khoản và biểu mẫu cấp tài khoản."""
    context, response = admin_context(request, db)
    if response is not None:
        return response
    users = db.scalars(select(User).order_by(User.id)).all()
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "user": context.user,
            "users": users,
            "csrf_token": get_csrf_token(context),
            "min_password_length": MIN_PASSWORD_LENGTH,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/users/create")
def create_annotator(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Admin cấp tài khoản Annotator (không tự tạo tài khoản Admin mới)."""
    response = checked_admin(request, db, csrf_token)
    if response is not None:
        return response

    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,64}", username):
        return notice("Tên tài khoản phải dài 3–64 ký tự, chỉ gồm chữ thường, số, _, -, .", error=True)
    if len(password) < MIN_PASSWORD_LENGTH:
        return notice(f"Mật khẩu phải dài ít nhất {MIN_PASSWORD_LENGTH} ký tự.", error=True)

    if db.scalar(select(User.id).where(User.username == username)) is not None:
        return notice("Tên tài khoản đã tồn tại.", error=True)

    db.add(User(username=username, password_hash=password_hasher.hash(password), role="annotator"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return notice("Tên tài khoản đã được sử dụng.", error=True)
    return notice(f"Đã tạo tài khoản {username}.")


@router.post("/users/{user_id}/toggle")
def toggle_user(
    user_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Khóa hoặc mở tài khoản Annotator và thu hồi phiên khi khóa."""
    response = checked_admin(request, db, csrf_token)
    if response is not None:
        return response
    user = db.get(User, user_id)
    if user is None or user.role != "annotator":
        return notice("Không tìm thấy Annotator cần cập nhật.", error=True)
    user.is_active = not user.is_active
    user.updated_at = utc_timestamp()
    if not user.is_active:
        revoke_user_sessions(db, user.id)
    db.commit()
    return notice(f"Đã {'mở' if user.is_active else 'khóa'} tài khoản {user.username}.")


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    request: Request,
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Admin đặt lại mật khẩu và thu hồi các phiên cũ của Annotator."""
    response = checked_admin(request, db, csrf_token)
    if response is not None:
        return response
    user = db.get(User, user_id)
    if user is None or user.role != "annotator":
        return notice("Không tìm thấy Annotator cần cập nhật.", error=True)
    if len(password) < MIN_PASSWORD_LENGTH:
        return notice(f"Mật khẩu phải dài ít nhất {MIN_PASSWORD_LENGTH} ký tự.", error=True)
    user.password_hash = password_hasher.hash(password)
    user.updated_at = utc_timestamp()
    revoke_user_sessions(db, user.id)
    db.commit()
    return notice(f"Đã đặt lại mật khẩu cho {user.username}.")
