"""Xác thực tài khoản bằng phiên đăng nhập lưu trong SQLite."""
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Request
from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database.models import User, UserSession

COOKIE_NAME = "stt_session"
SESSION_LIFETIME_SECONDS = 12 * 60 * 60
PASSWORD_HASHER = PasswordHash.recommended()


@dataclass
class AuthContext:
    """Thông tin tài khoản đang đăng nhập và mã phiên của trình duyệt."""
    user: User
    session: UserSession
    raw_token: str


def validate_session_secret() -> None:
    """Từ chối khởi động nếu thiếu khóa dùng bảo vệ biểu mẫu."""
    secret = os.getenv("SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("SESSION_SECRET trong .env phải dài ít nhất 32 ký tự")


def cookie_secure() -> bool:
    """Dùng Secure cookie nếu ứng dụng chạy HTTPS."""
    return os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")


def hash_session_token(raw_token: str) -> str:
    """Không lưu token đăng nhập dưới dạng nguyên bản."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> str:
    """Tạo phiên và lưu bản băm của token trong SQLite."""
    now = int(time.time())
    raw_token = secrets.token_urlsafe(32)
    db.add(UserSession(
        user_id=user.id,
        session_token_hash=hash_session_token(raw_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_LIFETIME_SECONDS,
    ))
    db.commit()
    return raw_token


def get_auth_context(request: Request, db: Session) -> AuthContext | None:
    """Đọc và xác minh phiên từ cookie của trình duyệt."""
    raw_token = request.cookies.get(COOKIE_NAME)
    if not raw_token:
        return None

    record = db.scalar(
        select(UserSession)
        .where(UserSession.session_token_hash == hash_session_token(raw_token))
    )
    now = int(time.time())
    if record is None or record.revoked_at is not None or record.expires_at <= now:
        return None

    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        return None

    if record.last_seen_at < now - 60:
        record.last_seen_at = now
        db.commit()
    return AuthContext(user=user, session=record, raw_token=raw_token)


def revoke_session(db: Session, context: AuthContext) -> None:
    """Thu hồi phiên hiện tại khi đăng xuất."""
    context.session.revoked_at = int(time.time())
    db.commit()


def revoke_user_sessions(db: Session, user_id: int) -> None:
    """Thu hồi mọi phiên khi tài khoản bị khóa hoặc đặt lại mật khẩu."""
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=int(time.time()))
    )


def get_csrf_token(context: AuthContext) -> str:
    """Tạo mã xác thực biểu mẫu phụ thuộc phiên đăng nhập."""
    key = os.environ["SESSION_SECRET"].encode("utf-8")
    return hmac.new(key, context.raw_token.encode("utf-8"), hashlib.sha256).hexdigest()


def is_valid_csrf(context: AuthContext, received: str) -> bool:
    """Chống yêu cầu sửa dữ liệu từ trang khác."""
    return hmac.compare_digest(get_csrf_token(context), received)
