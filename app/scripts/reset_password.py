"""Đặt lại mật khẩu cho tài khoản hiện có bằng câu lệnh quản trị.

Chạy: python -m app.scripts.reset_password --username admin
"""

import argparse
import getpass

from pwdlib import PasswordHash
from sqlalchemy import select, update

from app.config import MIN_PASSWORD_LENGTH
from app.database.connection import SessionLocal, initialize_database
from app.database.models import User, UserSession, utc_timestamp

PASSWORD_HASHER = PasswordHash.recommended()


def reset_password(username: str) -> None:
    """Đặt lại mật khẩu và thu hồi các phiên đã đăng nhập của tài khoản."""
    username = username.strip().lower()
    initialize_database()

    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            raise ValueError(f"Không tìm thấy tài khoản '{username}'.")
        user_id = user.id

    password = getpass.getpass("Mật khẩu mới: ")
    confirmation = getpass.getpass("Nhập lại mật khẩu mới: ")
    if password != confirmation:
        raise ValueError("Hai mật khẩu không trùng nhau.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Mật khẩu cần ít nhất {MIN_PASSWORD_LENGTH} ký tự.")

    password_hash = PASSWORD_HASHER.hash(password)
    now = utc_timestamp()

    with SessionLocal.begin() as session:
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None:
            raise ValueError("Tài khoản không còn tồn tại.")
        user.password_hash = password_hash
        user.updated_at = now
        session.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    print(f"Đã đặt lại mật khẩu của tài khoản: {username}")


def main() -> None:
    """Đọc username rồi yêu cầu mật khẩu mới ở Terminal."""
    parser = argparse.ArgumentParser(description="Đặt lại mật khẩu tài khoản")
    parser.add_argument("--username", required=True)
    arguments = parser.parse_args()
    reset_password(arguments.username)


if __name__ == "__main__":
    main()
