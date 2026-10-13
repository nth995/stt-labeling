"""Sao lưu SQLite an toàn khi nhiều người đang gán nhãn (kể cả WAL)."""

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import DATABASE_PATH, STATE_DIR


def backup_database(source: Path, destination: Path) -> Path:
    """Dùng SQLite Online Backup API, không sao chép riêng tệp .sqlite3."""
    source = Path(source)
    destination = Path(destination)
    if not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy cơ sở dữ liệu: {source}")
    if source.resolve() == destination.resolve():
        raise ValueError("Không được ghi đè cơ sở dữ liệu đang sử dụng.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Tạo độc quyền với quyền 600; không ghi đè bản sao lưu có sẵn.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(source) as source_connection:
            with sqlite3.connect(destination) as backup_connection:
                source_connection.backup(backup_connection)
                result = backup_connection.execute("PRAGMA integrity_check").fetchone()
                if result is None or result[0] != "ok":
                    raise RuntimeError("Kiểm tra toàn vẹn SQLite sao lưu thất bại.")
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    """Sao lưu dữ liệu vào state/backups, hỗ trợ --output khi cần."""
    parser = argparse.ArgumentParser(description="Sao lưu SQLite khi Web đang chạy")
    parser.add_argument("--output", type=Path, default=None, help="Đường dẫn file sao lưu mới")
    args = parser.parse_args()
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    default_path = STATE_DIR / "backups" / f"annotations-{suffix}-{uuid4().hex[:8]}.sqlite3"
    output = backup_database(DATABASE_PATH, args.output or default_path)
    print(f"Đã sao lưu SQLite: {output}")


if __name__ == "__main__":
    main()
