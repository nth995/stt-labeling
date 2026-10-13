"""Nhập nhãn từ CSV một lần và phân phối audio an toàn giữa nhiều phiên."""

import csv
import fcntl
import hashlib
import time
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import STATE_DIR
from app.database.models import (
    AnnotationHistory, Dataset, DatasetSourceState, Sample, SampleLock, User, UserDataset
)
from app.services.dataset_service import (
    checked_audio_file, dataset_directory, inspect_dataset, iter_label_rows
)

LEASE_SECONDS = 2 * 60


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


def _file_signature(path: Path) -> tuple[int, int]:
    """Lấy kích thước và mtime_ns để phát hiện thay đổi từ bên ngoài."""
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _file_digest(path: Path, max_bytes: int | None = None) -> str:
    """Băm file theo luồng, có thể chỉ đọc phần prefix đã biết."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = max_bytes
        while remaining is None or remaining > 0:
            block = stream.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
            if not block:
                break
            digest.update(block)
            if remaining is not None:
                remaining -= len(block)
        if remaining is not None and remaining != 0:
            raise LabelingError("CSV kết quả đã bị cắt ngắn bên ngoài ứng dụng.")
    return digest.hexdigest()


def ensure_samples(db: Session, dataset: Dataset) -> None:
    """Đối soát trong khóa liên tiến trình, không để hai worker nhập cùng mẫu."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_DIR / f"dataset_import_{dataset.id}.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            _ensure_samples_locked(db, dataset)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _ensure_samples_locked(db: Session, dataset: Dataset) -> None:
    """Nhập lần đầu hoặc bổ sung audio vào cuối CSV, bảo toàn nhãn đã xác nhận."""
    try:
        directory = dataset_directory(dataset.folder_name)
        source_path = directory / "label.csv"
        reviewed_path = directory / "label_reviewed.csv"
        source_signature = _file_signature(source_path)
        reviewed_signature = _file_signature(reviewed_path)
        state = db.get(DatasetSourceState, dataset.id)
        source_unchanged = bool(state and
            (state.source_size, state.source_mtime_ns) == source_signature)
        reviewed_changed = bool(state and
            (state.reviewed_size, state.reviewed_mtime_ns) != reviewed_signature)
        if reviewed_changed:
            # Chỉ chấp nhận phần CSV mới NỐI THÊM vào cuối, không cho sửa các dòng cũ.
            if (source_unchanged or reviewed_signature[0] < state.reviewed_size or
                _file_digest(reviewed_path, state.reviewed_size) != state.reviewed_digest):
                raise LabelingError(
                    "label_reviewed.csv đã bị sửa các dòng cũ ngoài ứng dụng. "
                    "Hãy sao lưu và kiểm tra trước khi tiếp tục."
                )
        if source_unchanged:
            return

        count = db.scalar(select(func.count(Sample.id)).where(Sample.dataset_id == dataset.id)) or 0
        if count == 0 and not inspect_dataset(dataset.folder_name).ready:
            raise LabelingError("Dataset chưa sẵn sàng. Hãy nhờ Admin kiểm tra/khởi tạo CSV.")

        from itertools import zip_longest
        batch = []
        with reviewed_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            if next(reader, None) != ["audio_path", "label", "status"]:
                raise LabelingError("label_reviewed.csv phải có ba cột audio_path,label,status.")
            originals = iter_label_rows(directory)
            previous = []
            total = 0
            for position, (audio_path, original_label) in enumerate(originals):
                total = position + 1
                reviewed = next(reader, None)
                if position < count:
                    # Lấy 500 mẫu/lần, không tải cả dataset vào RAM.
                    if position % 500 == 0:
                        previous = db.scalars(
                            select(Sample).where(
                                Sample.dataset_id == dataset.id,
                                Sample.position >= position,
                                Sample.position < position + 500,
                            ).order_by(Sample.position)
                        ).all()
                    old = previous[position % 500]
                    if (old.position != position or old.audio_path != audio_path
                            or old.original_label != original_label):
                        raise LabelingError(
                            f"Dữ liệu gốc đã đổi ở mẫu {position + 1}. "
                            "Chỉ được bổ sung audio vào cuối label.csv; chưa ghi đè CSV."
                        )
                    if (reviewed is None or len(reviewed) != 3
                            or normalize_reviewed_path(reviewed[0]) != audio_path
                            or reviewed[2] not in ("pending", "confirmed")):
                        raise LabelingError(f"label_reviewed.csv không khớp mẫu cũ #{position + 1}.")
                else:
                    if count and not checked_audio_file(directory, audio_path):
                        raise LabelingError(f"Audio mới không tồn tại hoặc không hợp lệ: {audio_path}")
                    if reviewed is None:
                        current_label, status = original_label, "pending"
                    elif (len(reviewed) == 3 and
                          normalize_reviewed_path(reviewed[0]) == audio_path and
                          reviewed[2] in ("pending", "confirmed")):
                        current_label, status = reviewed[1], reviewed[2]
                    else:
                        raise LabelingError(f"label_reviewed.csv sai ở audio mới #{position + 1}.")
                    batch.append({
                        "dataset_id": dataset.id, "position": position,
                        "audio_path": audio_path, "original_label": original_label,
                        "current_label": current_label, "status": status, "version": 0,
                    })
                    if len(batch) == 500:
                        db.execute(sqlite_insert(Sample).values(batch))
                        batch.clear()
            if total < count:
                raise LabelingError("label.csv đã bị xóa bớt mẫu, không thể tự đồng bộ.")
            if next(reader, None) is not None:
                raise LabelingError("label_reviewed.csv có mẫu thừa so với label.csv.")
            if batch:
                db.execute(sqlite_insert(Sample).values(batch))

        new_count = total - count
        if new_count and count:
            db.execute(update(Dataset).where(Dataset.id == dataset.id).values(version=Dataset.version + 1))
        if state is None:
            state = DatasetSourceState(dataset_id=dataset.id,
                source_size=source_signature[0], source_mtime_ns=source_signature[1],
                reviewed_size=reviewed_signature[0], reviewed_mtime_ns=reviewed_signature[1],
                reviewed_digest=_file_digest(reviewed_path))
            db.add(state)
        else:
            state.source_size, state.source_mtime_ns = source_signature
            state.reviewed_size, state.reviewed_mtime_ns = reviewed_signature
            state.reviewed_digest = _file_digest(reviewed_path)
        db.commit()
    except (OSError, ValueError, UnicodeError, csv.Error, IndexError) as error:
        db.rollback()
        raise LabelingError(f"Không thể đối soát dataset: {error}") from error
    except Exception:
        db.rollback()
        raise


def normalize_reviewed_path(raw: str) -> str:
    """Dùng cùng quy tắc chuẩn hóa cho nhãn gốc và nhãn đã đánh giá."""
    from app.services.dataset_service import normalize_audio_path
    return normalize_audio_path(raw)


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
    db.commit()
    return result.rowcount == 1


def resume_own_sample(
    db: Session, dataset_id: int, user_id: int, session_id: int,
    sample_id: int | None = None,
) -> Sample | None:
    """Nhận lại khóa của chính tài khoản ở phiên cũ; không lấy khóa người khác."""
    current_time = now_seconds()
    statement = (
        select(Sample)
        .join(SampleLock, SampleLock.sample_id == Sample.id)
        .where(
            Sample.dataset_id == dataset_id,
            SampleLock.user_id == user_id,
            SampleLock.expires_at > current_time,
        )
        .order_by(Sample.position)
    )
    if sample_id is not None:
        statement = statement.where(Sample.id == sample_id)

    for sample in db.scalars(statement).all():
        # Chỉ được chuyển quyền giữ cho cùng user, kể cả khi phiên khác vừa làm mới khóa.
        result = db.execute(
            update(SampleLock)
            .where(
                SampleLock.sample_id == sample.id,
                SampleLock.user_id == user_id,
                SampleLock.expires_at > now_seconds(),
            )
            .values(session_id=session_id, expires_at=now_seconds() + LEASE_SECONDS)
        )
        db.commit()
        if result.rowcount == 1:
            return sample
    return None


def release_session_locks(db: Session, session_id: int, dataset_id: int | None = None) -> int:
    """Giải phóng khóa của phiên khi đăng xuất hoặc mất kết nối đủ lâu."""
    statement = SampleLock.__table__.delete().where(SampleLock.session_id == session_id)
    if dataset_id is not None:
        statement = statement.where(
            SampleLock.sample_id.in_(
                select(Sample.id).where(Sample.dataset_id == dataset_id)
            )
        )
    result = db.execute(statement)
    db.commit()
    return result.rowcount


def claim_next(
    db: Session, dataset_id: int, user_id: int, session_id: int,
    exclude_sample_id: int | None = None,
) -> Sample | None:
    """Cấp mẫu pending đầu tiên chưa bị giữ; thử tiếp nếu có tranh chấp."""
    # Ưu tiên mẫu phiên này đang giữ để khi F5/đóng tab rồi quay lại không bị bỏ qua.
    current_time = now_seconds()
    existing = db.scalar(
        select(Sample)
        .join(SampleLock, SampleLock.sample_id == Sample.id)
        .where(
            Sample.dataset_id == dataset_id,
            Sample.status == "pending",
            SampleLock.session_id == session_id,
            SampleLock.user_id == user_id,
            SampleLock.expires_at > current_time,
        )
        .order_by(Sample.position)
        .limit(1)
    )
    if existing is not None and existing.id != exclude_sample_id and try_claim(db, existing, user_id, session_id):
        return existing

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
    if exclude_sample_id is not None:
        statement = statement.where(Sample.id != exclude_sample_id)
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
