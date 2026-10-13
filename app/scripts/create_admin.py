"""Tạo tài khoản Admin đầu tiên mà không lưu mật khẩu rõ trong mã nguồn.

Chạy: python -m app.scripts.create_admin --username admin
"""

import argparse
import getpass
import re

from pwdlib import PasswordHash
from sqlalchemy import select

from app.config import MIN_PASSWORD_LENGTH
from app.database.connection import SessionLocal, initialize_database
from app.database.models import User

PASSWORD_HASHER = PasswordHash.recommended()


def create_admin(username: str) -> None:
    """Nhập mật khẩu, kiểm tra độ dài và từ chối tạo trùng tài khoản."""
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,64}", username):
        raise ValueError("Username phải dài 3–64 ký tự, dùng chữ thường, số, _, -, .")

    initialize_database()
    with SessionLocal() as session:
        if session.scalar(select(User).where(User.username == username)) is not None:
            raise ValueError(f"Tài khoản '{username}' đã tồn tại; không tạo lại.")

    password = getpass.getpass("Mật khẩu Admin: ")
    confirmation = getpass.getpass("Nhập lại mật khẩu: ")
    if password != confirmation:
        raise ValueError("Hai mật khẩu không trùng nhau.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Mật khẩu cần ít nhất {MIN_PASSWORD_LENGTH} ký tự.")

    password_hash = PASSWORD_HASHER.hash(password)
    with SessionLocal.begin() as session:
        if session.scalar(select(User).where(User.username == username)) is not None:
            raise ValueError("Tài khoản đã được tạo bởi một phiên khác.")
        session.add(User(username=username, password_hash=password_hash, role="admin"))
    print(f"Đã tạo tài khoản Admin: {username}")


def main() -> None:
    """Đọc tên tài khoản từ tham số dòng lệnh."""
    parser = argparse.ArgumentParser(description="Tạo tài khoản Admin đầu tiên")
    parser.add_argument("--username", required=True)
    arguments = parser.parse_args()
    create_admin(arguments.username)


if __name__ == "__main__":
    main()
