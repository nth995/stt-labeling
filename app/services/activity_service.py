"""Tín hiệu còn hoạt động và tự xuất CSV khi phiên gán nhãn bị ngắt."""

import logging
import time

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.database.models import DatasetActivity
from app.services.csv_sync_service import sync_dataset_csv

LOGGER = logging.getLogger(__name__)
HEARTBEAT_TIMEOUT_SECONDS = 120


def mark_activity(db: Session, session_id: int, dataset_id: int) -> None:
    """Ghi dấu phiên đang làm việc với dataset, không thay đổi nhãn."""
    timestamp = int(time.time())
    command = sqlite_insert(DatasetActivity).values(
        session_id=session_id, dataset_id=dataset_id,
        last_seen_at=timestamp, ended_at=None,
    )
    db.execute(command.on_conflict_do_update(
        index_elements=["session_id", "dataset_id"],
        set_={"last_seen_at": timestamp, "ended_at": None},
    ))
    db.commit()


def finish_activity(db: Session, session_id: int, dataset_id: int) -> None:
    """Xuất CSV khi rời Web, chỉ kết thúc lượt đang hoạt động khi xuất thành công."""
    record = db.get(DatasetActivity, (session_id, dataset_id))
    if record is None or record.ended_at is not None:
        return
    seen_at = record.last_seen_at
    db.rollback()  # Không giữ read transaction trong lúc xuất CSV.
    sync_dataset_csv(dataset_id)
    # Nếu một tab khác vẫn đang gửi heartbeat, không kết thúc lượt mới.
    db.execute(
        update(DatasetActivity)
        .where(
            DatasetActivity.session_id == session_id,
            DatasetActivity.dataset_id == dataset_id,
            DatasetActivity.last_seen_at == seen_at,
            DatasetActivity.ended_at.is_(None),
        )
        .values(ended_at=int(time.time()))
    )
    db.commit()


def finish_all_activities(db: Session, session_id: int) -> None:
    """Đăng xuất: cố gắng xuất mọi dataset phiên đã mở; lỗi sẽ được thử lại."""
    ids = db.scalars(select(DatasetActivity.dataset_id).where(
        DatasetActivity.session_id == session_id,
        DatasetActivity.ended_at.is_(None),
    )).all()
    for dataset_id in ids:
        try:
            finish_activity(db, session_id, dataset_id)
        except (OSError, ValueError) as error:
            LOGGER.warning("Xuất CSV khi đăng xuất thất bại (dataset=%s): %s", dataset_id, error)


def sync_disconnected_sessions() -> None:
    """Phát hiện trình duyệt mất kết nối bằng heartbeat và đồng bộ bù."""
    cutoff = int(time.time()) - HEARTBEAT_TIMEOUT_SECONDS
    with SessionLocal() as db:
        expired = db.execute(
            select(DatasetActivity.session_id, DatasetActivity.dataset_id)
            .where(DatasetActivity.ended_at.is_(None), DatasetActivity.last_seen_at < cutoff)
        ).all()
    for session_id, dataset_id in expired:
        try:
            with SessionLocal() as db:
                record = db.get(DatasetActivity, (session_id, dataset_id))
                if record is None or record.ended_at is not None or record.last_seen_at >= cutoff:
                    continue
                finish_activity(db, session_id, dataset_id)
        except (OSError, ValueError) as error:
            LOGGER.warning("Không thể xuất CSV khi phiên ngắt (dataset=%s): %s", dataset_id, error)
