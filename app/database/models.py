"""Mô hình dữ liệu nền tảng: người dùng, dataset và phiên đăng nhập."""

import time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_timestamp() -> int:
    """Trả về Unix timestamp theo giây để thống nhất thời gian lưu."""
    return int(time.time())


class Base(DeclarativeBase):
    """Lớp cơ sở dùng chung cho các bảng SQLAlchemy."""


class User(Base):
    """Tài khoản quản trị viên hoặc người gán nhãn."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'annotator')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="annotator")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=utc_timestamp)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=utc_timestamp)


class Dataset(Base):
    """Dataset được nhận diện bởi tên thư mục bên trong data/."""

    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    csv_synced_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=utc_timestamp)


class UserDataset(Base):
    """Quyền được truy cập một dataset của người gán nhãn."""

    __tablename__ = "user_datasets"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)
    granted_at: Mapped[int] = mapped_column(Integer, nullable=False, default=utc_timestamp)


class UserSession(Base):
    """Phiên đăng nhập; lưu mã phiên nội bộ, không lưu mật khẩu hay token rõ."""

    __tablename__ = "user_sessions"
    __table_args__ = (UniqueConstraint("session_token_hash", name="uq_session_token_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=utc_timestamp)
    last_seen_at: Mapped[int] = mapped_column(Integer, nullable=False, default=utc_timestamp)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
