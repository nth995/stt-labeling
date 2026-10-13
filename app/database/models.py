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


class Sample(Base):
    """Một audio và nhãn đang làm việc trong SQLite; giữ nguyên CSV gốc."""

    __tablename__ = "samples"
    __table_args__ = (
        UniqueConstraint("dataset_id", "audio_path", name="uq_sample_dataset_path"),
        UniqueConstraint("dataset_id", "position", name="uq_sample_dataset_position"),
        CheckConstraint("status IN ('pending','confirmed')", name="ck_sample_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    audio_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    original_label: Mapped[str] = mapped_column(String, nullable=False, default="")
    current_label: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[int | None] = mapped_column(Integer)


class SampleLock(Base):
    """Quyền giữ mẫu trong thời gian giới hạn theo phiên đăng nhập."""

    __tablename__ = "sample_locks"

    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)


class AnnotationHistory(Base):
    """Nhật ký thay đổi nhãn và trạng thái đã xác nhận."""

    __tablename__ = "annotation_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    old_label: Mapped[str] = mapped_column(String, nullable=False)
    new_label: Mapped[str] = mapped_column(String, nullable=False)
    old_status: Mapped[str] = mapped_column(String(16), nullable=False)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    new_version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[int] = mapped_column(Integer, nullable=False, default=utc_timestamp)


class DatasetActivity(Base):
    """Mỗi phiên và dataset có tín hiệu hoạt động để phát hiện ngắt mạng."""

    __tablename__ = "dataset_activities"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    last_seen_at: Mapped[int] = mapped_column(Integer, nullable=False)
    ended_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DatasetSourceState(Base):
    """Mốc thay đổi của CSV nguồn/CSV kết quả để đối soát dữ liệu."""

    __tablename__ = "dataset_source_states"
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)
    source_size: Mapped[int] = mapped_column(Integer, nullable=False)
    source_mtime_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_size: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_mtime_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
