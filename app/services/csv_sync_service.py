"""Xuất nhãn SQLite sang CSV an toàn khi được yêu cầu.

SQLite luôn là nguồn chính. Mỗi dataset chỉ có một tiến trình ghi CSV tại
một thời điểm, kể cả khi nhiều request đến đồng thời.
"""

import csv
import fcntl
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update

from app.config import STATE_DIR
from app.database.connection import SessionLocal
from app.database.models import Dataset, Sample
from app.services.dataset_service import dataset_directory

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    """Thông tin phiên bản và số dòng của một lần xuất CSV."""

    dataset_id: int
    exported_version: int
    latest_version: int
    sample_count: int
    skipped: bool
    path: Path


def _lock_path(dataset_id: int) -> Path:
    """Đặt tệp khóa ngoài thư mục audio, chung cho mọi worker/container."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"csv_sync_{dataset_id}.lock"


def sync_dataset_csv(dataset_id: int, force: bool = False) -> SyncResult:
    """Ghi một bản chụp nhất quán SQLite sang CSV rồi cập nhật mốc đồng bộ.

    Khóa file ngăn hai bộ xuất ghi chồng lên nhau. Nếu nhãn mới xuất hiện sau
    bản chụp, dataset.version vẫn lớn hơn csv_synced_version và lần xuất sau
    sẽ tiếp tục đồng bộ. CSV được thay thế nguyên tử trên cùng ổ đĩa.
    """
    with _lock_path(dataset_id).open("a+b") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            with SessionLocal() as db:
                # Khởi đầu giao dịch đọc thật sự trước SELECT đầu tiên để toàn
                # bộ nhãn và số version đến từ cùng một SQLite snapshot.
                db.connection().exec_driver_sql("BEGIN")
                dataset = db.get(Dataset, dataset_id)
                if dataset is None:
                    raise ValueError("Không tìm thấy dataset để lưu CSV.")
                directory = dataset_directory(dataset.folder_name)
                target = directory / "label_reviewed.csv"
                version = dataset.version
                synchronized = dataset.csv_synced_version
                if not force and version <= synchronized and target.is_file():
                    return SyncResult(dataset_id, version, version, 0, True, target)

                temporary: Path | None = None
                count = 0
                try:
                    descriptor, name = tempfile.mkstemp(
                        prefix=".label_reviewed_", suffix=".tmp", dir=directory
                    )
                    temporary = Path(name)
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
                        writer = csv.writer(output)
                        writer.writerow(["audio_path", "label", "status"])
                        samples = db.scalars(
                            select(Sample)
                            .where(Sample.dataset_id == dataset_id)
                            .order_by(Sample.position)
                            .execution_options(yield_per=500)
                        )
                        for sample in samples:
                            writer.writerow([sample.audio_path, sample.current_label, sample.status])
                            count += 1
                        output.flush()
                        os.fsync(output.fileno())
                    # Nếu chưa có sample, không được ghi đè CSV gốc bằng file rỗng.
                    if count == 0:
                        raise ValueError("Dataset chưa nhập audio vào SQLite; hãy mở dataset trước.")
                    os.replace(temporary, target)
                    temporary = None
                    # Đẩy metadata thay đổi xuống đĩa sau khi rename thành công.
                    directory_fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                db.rollback()  # Đóng read snapshot trước khi ghi mốc đồng bộ.

            with SessionLocal.begin() as db:
                db.execute(
                    update(Dataset)
                    .where(Dataset.id == dataset_id, Dataset.csv_synced_version < version)
                    .values(csv_synced_version=version)
                )
                latest_version = db.scalar(select(Dataset.version).where(Dataset.id == dataset_id))
            return SyncResult(dataset_id, version, latest_version, count, False, target)
        finally:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


def sync_dirty_on_startup() -> None:
    """Sau khởi động, xuất bù các dataset có phiên bản CSV cũ hơn SQLite."""
    with SessionLocal() as db:
        ids = db.scalars(
            select(Dataset.id).where(Dataset.version > Dataset.csv_synced_version)
        ).all()
    for dataset_id in ids:
        try:
            sync_dataset_csv(dataset_id)
        except (OSError, ValueError) as error:
            LOGGER.warning("Chưa thể đồng bộ dataset %s: %s", dataset_id, error)
