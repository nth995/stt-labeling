"""Kết nối SQLite và cấu hình phục vụ nhiều người dùng."""

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATABASE_PATH, STATE_DIR
from app.database.models import Base

STATE_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite+pysqlite:///{DATABASE_PATH.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 5},
)


@event.listens_for(engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record) -> None:
    """Bật khóa ngoại và thời gian chờ ghi trên mọi kết nối."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_database() -> None:
    """Bật WAL và khởi tạo các bảng còn thiếu; không xóa dữ liệu cũ."""
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """Cung cấp và đóng một phiên làm việc SQLite cho mỗi yêu cầu API."""
    with SessionLocal() as session:
        yield session
