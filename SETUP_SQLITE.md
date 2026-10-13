# Thiết lập SQLite trên GitHub Codespaces

Các file mới trong gói này chỉ bổ sung phần cơ sở dữ liệu và lệnh tạo Admin; không ghi đè `app/main.py`, `Dockerfile`, `compose.yaml`, `.env` hoặc file trong `data/`.

## 1. Chép các thư mục `app/database`, `app/scripts` và file `app/config.py` vào dự án.

## 2. Bổ sung hai thư viện vào `requirements.txt`

```text
sqlalchemy>=2,<3
pwdlib[argon2]
```

Có thể giữ các thư viện khác trong `requirements.txt` mà bạn đã thêm ở bước trước.

## 3. Cài thư viện bằng một trong hai cách

### Trong Codespaces (Python cục bộ)
```bash
python3 -m pip install -r requirements.txt
python3 -m app.database.init_db
python3 -m app.scripts.create_admin --username admin
```

### Trong Docker Compose
```bash
docker compose build
docker compose run --rm web python -m app.database.init_db
docker compose run --rm -it web python -m app.scripts.create_admin --username admin
```

Các lệnh dưới `docker compose run` tạo container chạy một lần và thoát, không cần giữ Web hoạt động.

## 4. Kiểm tra SQLite

```bash
python3 - <<'PY'
import sqlite3
from pathlib import Path
path = Path('state/annotations.sqlite3')
assert path.is_file(), f'Không tìm thấy: {path}'
with sqlite3.connect(path) as conn:
    print('Journal:', conn.execute('PRAGMA journal_mode').fetchone()[0])
    print('Tables:', [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])
    print('Users:', conn.execute('SELECT username,role,is_active FROM users').fetchall())
PY
```

SQLite phải hiển thị `wal`; có 4 bảng `datasets`, `user_datasets`, `user_sessions`, `users`; tài khoản `admin` chỉ tồn tại nếu bạn đã chạy lệnh tạo tài khoản.

## 5. Lưu ý

- Chưa có trang đăng nhập hoặc dashboard: đó là bước kế tiếp.
- Chưa đụng đến `label.csv` và `label_reviewed.csv`.
- SQLite được lưu ở `state/annotations.sqlite3` và phải được mount ra ngoài Docker.
- Không commit `state/`, `data/` hoặc `.env` lên Git.
- Không chạy lệnh tạo Admin nhiều lần với cùng một tên.
- Chưa dùng `create_all` để cập nhật cấu trúc bảng khi schema thay đổi: giai đoạn sau sử dụng Alembic migration.
