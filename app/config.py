"""Cấu hình đường dẫn của ứng dụng."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("STATE_DIR", str(PROJECT_ROOT / "state"))).resolve()
DATABASE_PATH = STATE_DIR / "annotations.sqlite3"

# Độ dài tối thiểu của mật khẩu cho tất cả tài khoản.
MIN_PASSWORD_LENGTH = 4
