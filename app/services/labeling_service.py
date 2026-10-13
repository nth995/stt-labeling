"""Nhập nhãn từ CSV một lần và phân phối audio an toàn giữa nhiều phiên."""

import csv
import time
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database.models import (
    AnnotationHistory, Dataset, Sample, SampleLock, User, UserDataset
)
from app.services.dataset_service import (
    checked_audio_file, dataset_directory, inspect_dataset, iter_label_rows
)

LEASE_SECONDS = 10 * 60


class LabelingError(Exception):
    """Lỗi nghiệp vụ trả về cho giao diện, không ghi thay đổi dữ liệu."""


def now_seconds() -> int:
    """Thời gian máy chủ theo Unix timestamp."""
    return int(time.time())


def allowed_dataset(db: Session, dataset_id: int, user: User) -> Dataset:
    """Kiểm tra quyền truy cập mỗi khi đọc hay ghi, kể cả API audio."""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise LabelingError("Không tìm thấy dataset.")
    if user.role != "admin" and db.get(UserDataset, (user.id, dataset_id)) is None:
        raise LabelingError("Bạn chưa được cấp quyền đối với dataset này.")
    return dataset


def ensure_samples(db: Session, dataset: Dataset) -> None:
    """Lần đầu mở: nhập nhãn từ CSV; về sau SQLite luôn là nguồn chính."""
    exists = db.scalar(select(Sample.id).where(Sample.dataset_id == dataset.id).limit(1))
    if exists is not None:
        return
    inspection = inspect_dataset(dataset.folder_name)
    if not inspection.ready:
        raise LabelingError("Dataset chưa sẵn sàng. Hãy nhờ Admin kiểm tra/khởi tạo CSV.")
    directory = dataset_directory(dataset.folder_name)
    reviewed_path = directory / "label_reviewed.csv"

    # Đọc ngoài transaction ghi để tránh giữ khóa SQLite trong lúc đọc CSV.
    originals = list(iter_label_rows(directory))
    with reviewed_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        reviewed = list(reader)
    if len(originals) != len(reviewed):
        raise LabelingError("Số dòng của hai file CSV không khớp.")

    records = []
    for index, ((audio_path, original_label), row) in enumerate(zip(originals, reviewed)):
        if row["audio_path"] != audio_path or row["status"] not in ("pending", "confirmed"):
            raise LabelingError("File label_reviewed.csv không nhất quán với nhãn gốc.")
        records.append({
            "dataset_id": dataset.id,
            "position": index,
            "audio_path": audio_path,
            "original_label": original_label,
            "current_label": row["label"],
            "status": row["status"],
            "version": 0,
        })

    # Giao dịch duy nhất, import trùng từ hai phiên không tạo hàng trùng.
    # Chia theo batch để không vượt giới hạn tham số SQLite.
    if records:
        for offset in range(0, len(records), 100):
            statement = sqlite_insert(Sample).values(records[offset:offset + 100])
            db.execute(statement.on_conflict_do_nothing(index_elements=["dataset_id", "audio_path"]))
        db.commit()


def dataset_counts(db: Session, dataset_id: int) -> dict[str, int]:
    """Tổng hợp tiến độ lấy từ SQLite, không đọc CSV."""
    rows = db.execute(
        select(Sample.status, func.count(Sample.id))
        .where(Sample.dataset_id == dataset_id)
        .group_by(Sample.status)
    ).all()
    counts = {"pending": 0, "confirmed": 0}
    counts.update({status: amount for status, amount in rows})
    counts["total"] = counts["pending"] + counts["confirmed"]
    return counts


def sample_page(db: Session, dataset_id: int, status: str, page: int, limit: int = 30):
    """Lấy từng trang mẫu theo thứ tự CSV gốc."""
    statement = select(Sample).where(Sample.dataset_id == dataset_id)
    if status in ("pending", "confirmed"):
        statement = statement.where(Sample.status == status)
    return db.scalars(statement.order_by(Sample.position).offset((page - 1) * limit).limit(limit)).all()


def try_claim(db: Session, sample: Sample, user_id: int, session_id: int) -> bool:
    """Cấp mẫu nguyên tử, từ chối phiên khác còn giữ khóa hợp lệ."""
    current_time = now_seconds()
    statement = sqlite_insert(SampleLock).values(
        sample_id=sample.id, user_id=user_id,
        session_id=session_id, expires_at=current_time + LEASE_SECONDS
    )
    statement = statement.on_conflict_do_update(
        index_elements=["sample_id"],
        set_={
            "user_id": user_id,
            "session_id": session_id,
            "expires_at": current_time + LEASE_SECONDS,
        },
        where=(SampleLock.expires_at <= current_time) | (SampleLock.session_id == session_id),
    )
    result = db.execute(statement)
    if result.rowcount == 1:
        # Mỗi phiên chỉ giữ một mẫu; giải phóng audio trước khi chuyển sang mẫu mới.
        db.execute(
            SampleLock.__table__.delete().where(
                SampleLock.session_id == session_id,
                SampleLock.sample_id != sample.id,
            )
        )
    db.commit()
    return result.rowcount == 1


def claim_next(db: Session, dataset_id: int, user_id: int, session_id: int) -> Sample | None:
    """Cấp mẫu pending đầu tiên chưa bị giữ; thử tiếp nếu có tranh chấp."""
    current_time = now_seconds()
    statement = (
        select(Sample)
        .outerjoin(SampleLock, SampleLock.sample_id == Sample.id)
        .where(
            Sample.dataset_id == dataset_id,
            Sample.status == "pending",
            (SampleLock.sample_id.is_(None) | (SampleLock.expires_at <= current_time)),
        )
        .order_by(Sample.position)
    )
    # Duyệt từng trang để không commit khi con trỏ SQLite đang streaming.
    last_position = -1
    while True:
        candidates = db.scalars(
            statement.where(Sample.position > last_position).limit(100)
        ).all()
        if not candidates:
            return None
        for sample in candidates:
            last_position = sample.position
            if try_claim(db, sample, user_id, session_id):
                return sample


def require_lease(db: Session, sample_id: int, session_id: int) -> SampleLock:
    """Chỉ phiên đang giữ khóa hợp lệ được xác nhận nhãn."""
    lease = db.get(SampleLock, sample_id)
    if lease is None or lease.session_id != session_id or lease.expires_at <= now_seconds():
        raise LabelingError("Khóa audio đã hết hạn hoặc được phiên khác sử dụng. Hãy mở lại mẫu.")
    return lease


def renew_lease(db: Session, sample_id: int, session_id: int) -> bool:
    """Gia hạn khóa khi trang phát tín hiệu hoạt động."""
    current_time = now_seconds()
    updated = db.execute(
        update(SampleLock)
        .where(SampleLock.sample_id == sample_id,
               SampleLock.session_id == session_id,
               SampleLock.expires_at > current_time)
        .values(expires_at=current_time + LEASE_SECONDS)
    )
    db.commit()
    return updated.rowcount == 1


def release_lease(db: Session, sample_id: int, session_id: int) -> None:
    """Giải phóng khóa khi bỏ qua hoặc hoàn tất."""
    current_time = now_seconds()
    db.execute(
        SampleLock.__table__.delete()
        .where(SampleLock.sample_id == sample_id, SampleLock.session_id == session_id)
    )
    db.commit()


def confirm_sample(
    db: Session, sample: Sample, user_id: int, session_id: int,
    version: int, label: str, no_speech: bool
) -> None:
    """Kiểm tra khóa + phiên bản rồi lưu nhãn và lịch sử trong một giao dịch."""
    if len(label) > 10000:
        raise LabelingError("Nhãn quá dài (tối đa 10.000 ký tự).")
    if not label.strip() and not no_speech:
        raise LabelingError("Nhập nhãn hoặc xác nhận audio không có lời nói.")
    require_lease(db, sample.id, session_id)
    normalized_label = "" if no_speech else label.strip()
    original_label = sample.current_label
    original_status = sample.status
    updated_at = now_seconds()
    # Bảo vệ optimistic-lock: nếu phiên khác đã ghi trước thì từ chối.
    result = db.execute(
        update(Sample)
        .where(
            Sample.id == sample.id,
            Sample.version == version,
            select(SampleLock.sample_id).where(
                SampleLock.sample_id == sample.id,
                SampleLock.session_id == session_id,
                SampleLock.expires_at > updated_at,
            ).exists(),
        )
        .values(
            current_label=normalized_label, status="confirmed",
            version=Sample.version + 1, updated_by=user_id, updated_at=updated_at,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise LabelingError("Khóa đã hết hạn hoặc nhãn đã đổi phiên bản. Hãy tải lại mẫu trước khi lưu.")
    db.add(AnnotationHistory(
        sample_id=sample.id, user_id=user_id,
        old_label=original_label, new_label=normalized_label,
        old_status=original_status, new_status="confirmed",
        new_version=version + 1, changed_at=updated_at,
    ))
    db.execute(update(Dataset).where(Dataset.id == sample.dataset_id).values(version=Dataset.version + 1))
    db.execute(
        SampleLock.__table__.delete().where(
            SampleLock.sample_id == sample.id, SampleLock.session_id == session_id
        )
    )
    db.commit()
