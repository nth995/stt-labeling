"""Các bản nâng cấp SQLite tăng dần, có kiểm tra version và chỉ mục."""

from sqlalchemy.engine import Engine


def migrate_database(engine: Engine) -> None:
    """Áp dụng nâng cấp schema có version, không xóa dữ liệu đã lưu."""
    with engine.begin() as connection:
        version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
        if version > 1:
            raise RuntimeError("Cơ sở dữ liệu tạo bởi phiên bản ứng dụng mới hơn.")
        if version < 1:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_samples_dataset_status_position "
                "ON samples (dataset_id, status, position)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_sample_locks_user_expires "
                "ON sample_locks (user_id, expires_at)"
            )
            connection.exec_driver_sql("PRAGMA user_version=1")
