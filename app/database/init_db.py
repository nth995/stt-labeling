"""Chạy: python -m app.database.init_db"""

from sqlalchemy import inspect

from app.config import DATABASE_PATH
from app.database.connection import engine, initialize_database


def main() -> None:
    """Khởi tạo SQLite và in danh sách bảng để kiểm tra."""
    initialize_database()
    table_names = inspect(engine).get_table_names()
    print(f"SQLite: {DATABASE_PATH}")
    print(f"Đã sẵn sàng: {', '.join(table_names)}")


if __name__ == "__main__":
    main()
