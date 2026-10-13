"""Phát hiện bộ dữ liệu, xác minh đường dẫn audio và khởi tạo CSV an toàn."""

import csv
import os
import tempfile
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database.models import Dataset

DATA_ROOT = Path(os.environ.get("DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
AUDIO_EXTENSIONS = frozenset({
    ".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".webm", ".wma"
})


@dataclass
class DatasetInspection:
    """Thông tin tổng hợp cho trang Admin, không giữ nội dung nhãn trong RAM."""

    name: str
    has_labels: bool
    has_reviewed: bool
    audio_count: int
    missing_count: int
    invalid_count: int
    unlabeled_count: int
    error: str | None = None

    @property
    def ready(self) -> bool:
        """Chỉ được cấp quyền nếu nhãn hợp lệ và đã khởi tạo kết quả."""
        return self.has_labels and self.has_reviewed and not self.error and self.invalid_count == 0


def dataset_directory(folder_name: str) -> Path:
    """Đảm bảo tên bộ dữ liệu chỉ trỏ tới thư mục con trực tiếp của data/."""
    if not folder_name or folder_name in {".", ".."} or Path(folder_name).name != folder_name:
        raise ValueError("Tên dataset không hợp lệ.")
    directory = DATA_ROOT / folder_name
    if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
        raise ValueError("Thư mục dataset không tồn tại hoặc là liên kết tượng trưng.")
    return directory


def discover_datasets() -> list[str]:
    """Liệt kê thư mục dataset có CSV hoặc audio, không giả định vị trí audio."""
    if not DATA_ROOT.is_dir():
        return []
    result = []
    for entry in sorted(DATA_ROOT.iterdir(), key=lambda item: item.name.casefold()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        if (entry / "label.csv").is_file() or any(iter_audio_files(entry)):
            result.append(entry.name)
    return result


def iter_audio_files(directory: Path):
    """Duyệt đệ quy audio; từ chối symlink trỏ ra ngoài dataset."""
    for root, folders, files in os.walk(directory, followlinks=False):
        folders[:] = sorted(
            folder for folder in folders
            if not (Path(root) / folder).is_symlink()
        )
        for name in sorted(files):
            audio = Path(root) / name
            if audio.suffix.lower() not in AUDIO_EXTENSIONS or audio.is_symlink():
                continue
            if audio.is_file() and audio.resolve().is_relative_to(directory.resolve()):
                yield audio


def checked_audio_file(directory: Path, raw_path: str) -> bool:
    """Chặn đường dẫn tuyệt đối, thoát thư mục và audio không tồn tại."""
    if not raw_path or "\x00" in raw_path:
        return False
    # Chấp nhận cả dấu / và \\ trong đường dẫn tương đối xuất từ Windows.
    normalized = raw_path.replace("\\", "/")
    if ":" in normalized or normalized.startswith("//"):
        return False
    supplied = Path(normalized)
    if supplied.is_absolute() or any(part in {"..", "."} for part in supplied.parts):
        return False
    candidate = directory / supplied
    return (
        candidate.suffix.lower() in AUDIO_EXTENSIONS
        and candidate.is_file()
        and candidate.resolve().is_relative_to(directory.resolve())
    )


def normalize_audio_path(raw: str) -> str:
    """Chuẩn hóa dấu phân cách để cùng một audio chỉ có một khóa định danh."""
    from pathlib import PurePosixPath
    path = raw.replace("\\", "/")
    if not path or path.startswith("/") or ":" in path or "\x00" in path:
        raise ValueError("Đường dẫn audio không hợp lệ.")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Đường dẫn audio có thành phần không hợp lệ.")
    return PurePosixPath(path).as_posix()


class DuplicatePathChecker:
    """Chống trùng bằng bộ nhớ nhỏ, chuyển sang SQLite tạm khi dữ liệu lớn."""
    def __init__(self, threshold: int = 10000):
        self.threshold = threshold
        self.seen = set()
        self.connection = None
        self.file = None

    def add(self, path: str) -> bool:
        """Trả False nếu đường dẫn đã xuất hiện."""
        if self.connection is None and len(self.seen) < self.threshold:
            if path in self.seen:
                return False
            self.seen.add(path)
            return True
        if self.connection is None:
            descriptor, name = tempfile.mkstemp(prefix=".stt_paths_", suffix=".db")
            os.close(descriptor)
            self.file = Path(name)
            self.connection = sqlite3.connect(name)
            self.connection.execute("CREATE TABLE paths (path TEXT PRIMARY KEY) WITHOUT ROWID")
            self.connection.executemany("INSERT INTO paths VALUES (?)", ((value,) for value in self.seen))
            self.seen.clear()
        try:
            self.connection.execute("INSERT INTO paths VALUES (?)", (path,))
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self) -> None:
        """Đóng SQLite tạm và xóa file nếu từng sử dụng."""
        if self.connection is not None:
            self.connection.close()
        if self.file is not None:
            self.file.unlink(missing_ok=True)


def iter_label_rows(directory: Path):
    """Đọc lần lượt nhãn gốc, chuẩn hóa đường dẫn và chống trùng giới hạn RAM."""
    checker = DuplicatePathChecker()
    try:
        with (directory / "label.csv").open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            if next(reader, None) != ["audio_path", "label"]:
                raise ValueError("label.csv phải có đúng header: audio_path,label")
            for number, row in enumerate(reader, start=2):
                if len(row) != 2:
                    raise ValueError(f"label.csv dòng {number}: cần đúng 2 cột.")
                try:
                    audio_path = normalize_audio_path(row[0])
                except ValueError as error:
                    raise ValueError(f"label.csv dòng {number}: {error}") from error
                if not checker.add(audio_path):
                    raise ValueError(f"label.csv dòng {number}: audio_path trùng sau chuẩn hóa.")
                yield audio_path, row[1]
    finally:
        checker.close()


def verify_reviewed_csv(directory: Path):
    """Kiểm tra reviewed CSV tương ứng nhãn gốc, chỉ pending/confirmed."""
    source = iter_label_rows(directory)
    with (directory / "label_reviewed.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        if next(reader, None) != ["audio_path", "label", "status"]:
            raise ValueError("label_reviewed.csv phải có header: audio_path,label,status")
        for line_no, original in enumerate(source, start=2):
            reviewed = next(reader, None)
            if reviewed is None or len(reviewed) != 3:
                raise ValueError(f"label_reviewed.csv thiếu mẫu hoặc sai cột ở dòng {line_no}.")
            if normalize_audio_path(reviewed[0]) != original[0]:
                raise ValueError(f"label_reviewed.csv khác thứ tự audio_path ở dòng {line_no}.")
            if reviewed[2] not in ("pending", "confirmed"):
                raise ValueError(f"label_reviewed.csv có trạng thái không hợp lệ ở dòng {line_no}.")
        if next(reader, None) is not None:
            raise ValueError("label_reviewed.csv có nhiều dòng hơn label.csv.")


def inspect_dataset(folder_name: str) -> DatasetInspection:
    """Tổng hợp dữ liệu hiện có, không thay đổi file hoặc cơ sở dữ liệu."""
    directory = dataset_directory(folder_name)
    labels = directory / "label.csv"
    reviewed = directory / "label_reviewed.csv"
    if not labels.exists():
        count = sum(1 for _ in iter_audio_files(directory))
        return DatasetInspection(folder_name, False, reviewed.exists(), count, 0, 0, count)
    try:
        total = invalid = unlabeled = 0
        for audio_path, label in iter_label_rows(directory):
            total += 1
            if not checked_audio_file(directory, audio_path):
                invalid += 1
            if not label.strip():
                unlabeled += 1
        if reviewed.exists():
            verify_reviewed_csv(directory)
        return DatasetInspection(folder_name, True, reviewed.exists(), total, invalid, invalid, unlabeled)
    except (ValueError, UnicodeError, OSError, csv.Error) as error:
        return DatasetInspection(folder_name, True, reviewed.exists(), 0, 0, 0, 0, str(error))


def register_datasets(db: Session) -> int:
    """Đăng ký thư mục mới vào SQLite, không xóa những dataset đã đăng ký."""
    existing = set(db.scalars(select(Dataset.folder_name)).all())
    new_names = [name for name in discover_datasets() if name not in existing]
    for name in new_names:
        db.add(Dataset(folder_name=name))
    if new_names:
        db.commit()
    return len(new_names)


def write_csv_new(path: Path, header: list[str], rows):
    """Ghi vào file tạm trước; công bố file mới mà không ghi đè file cũ."""
    descriptor, temp_name = tempfile.mkstemp(prefix=".stt_tmp_", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        # Tạo liên kết cứng độc quyền: nếu đích đã tồn tại thì tuyệt đối không ghi đè.
        os.link(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def initialize_dataset_files(folder_name: str) -> str:
    """Tạo label.csv nếu thiếu và reviewed CSV (pending) nếu chưa có."""
    directory = dataset_directory(folder_name)
    source_file = directory / "label.csv"
    reviewed_file = directory / "label_reviewed.csv"
    messages = []

    if not source_file.exists():
        count = sum(1 for _ in iter_audio_files(directory))
        if not count:
            raise ValueError("Dataset chưa có audio để khởi tạo nhãn.")
        try:
            write_csv_new(
                source_file, ["audio_path", "label"],
                ((audio.relative_to(directory).as_posix(), "") for audio in iter_audio_files(directory)),
            )
            messages.append(f"Đã tạo label.csv cho {count} audio.")
        except FileExistsError:
            messages.append("label.csv đã được tạo từ một phiên khác.")

    total = invalid_count = 0
    for audio_path, _ in iter_label_rows(directory):
        total += 1
        if not checked_audio_file(directory, audio_path):
            invalid_count += 1
    if invalid_count:
        raise ValueError(f"Có {invalid_count} đường dẫn audio không hợp lệ/không tồn tại; chưa khởi tạo CSV kết quả.")
    if reviewed_file.exists():
        verify_reviewed_csv(directory)
        messages.append("label_reviewed.csv đã tồn tại, giữ nguyên không ghi đè.")
    else:
        try:
            write_csv_new(
                reviewed_file,
                ["audio_path", "label", "status"],
                ((audio_path, label, "pending") for audio_path, label in iter_label_rows(directory)),
            )
            messages.append(f"Đã tạo label_reviewed.csv ({total} mẫu, tất cả pending).")
        except FileExistsError:
            verify_reviewed_csv(directory)
            messages.append("label_reviewed.csv đã được tạo từ một phiên khác.")
    return " ".join(messages)
