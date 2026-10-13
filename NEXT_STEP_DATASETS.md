# Giai đoạn tiếp theo — Quản lý dataset và cấp quyền Annotator

## Phạm vi

- Trang `/admin/datasets` cho phép **Admin** quét thư mục `data/`, đăng ký dataset vào SQLite, chuẩn bị hai file CSV nếu cần, và cấp/thu hồi quyền từng Annotator.
- Trang `/workspace` chỉ hiển thị dataset được phân quyền cho tài khoản đang đăng nhập.
- **Chưa** có trình phát audio, giao diện chỉnh sửa nhãn hay bảng `samples` — thực hiện ở giai đoạn tiếp theo.
- **Không** sửa bất kỳ file `label.csv` hay `label_reviewed.csv` nào đã tồn tại.
- Không ghi đè `state/annotations.sqlite3`, `data/`, `.env`, `compose.yaml` hoặc `Dockerfile`.

## 1. Cập nhật mã nguồn trên GitHub Codespaces

Ở thư mục `/workspaces/stt-labeling` trên nhánh `feature/multi-user-admin`:

```bash
git status
```

Commit các thay đổi đang có nếu cần. Sau đó tải `stt-labeling-datasets-permissions-patch.zip` lên Codespaces và giải nén:

```bash
unzip -o stt-labeling-datasets-permissions-patch.zip -d .
```

Bản cập nhật thay đổi `app/main.py`, `app/routes/auth.py`, `app/templates/base.html`, `app/templates/workspace.html`, `app/static/styles.css` và bổ sung `app/routes/admin_datasets.py`, `app/services/dataset_service.py`, `app/templates/admin_datasets.html`. Không cần cập nhật database schema; sử dụng bảng `Dataset` và `UserDataset` đã có.

## 2. Chạy Docker

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=60 web
```

Không cần xóa SQLite hay chạy lại lệnh tạo Admin. Nếu port 8000 bị chiếm bởi container test cũ, dùng `docker ps` kiểm tra rồi dừng **đúng container test đó** trước khi chạy.

## 3. Kiểm thử với dữ liệu hiện có

1. Đăng nhập Admin → mở **Datasets** hoặc truy cập `/admin/datasets`.
2. Bấm **Quét thư mục data/**. Sẽ nhận diện `test1` và `test2`, mỗi bộ 5 audio với dữ liệu mẫu trong dự án.
3. Bấm **Khởi tạo CSV còn thiếu** cho từng bộ. Hệ thống **không** ghi đè `label.csv` gốc; tạo thêm `label_reviewed.csv` ba cột `audio_path,label,status`, trong đó tất cả là `pending`.
4. Tích chọn `user01` trong phần phân quyền `test1` → **Lưu phân quyền**.
5. Đăng xuất Admin, đăng nhập `user01` → `/workspace` chỉ thấy `test1`, không thấy `test2`.
6. Đăng nhập lại Admin, bỏ chọn `user01` tại `test1` → Lưu phân quyền → kiểm tra `user01` không còn thấy bộ đó sau khi tải lại trang.

Với dataset **không có `label.csv`**, tạo thư mục có audio bất kỳ dưới `data/ten_dataset/`, rồi nhấn **Quét thư mục data/** và **Khởi tạo CSV còn thiếu**. Hệ thống tự tìm audio trong các thư mục con, tạo `label.csv` hai cột và `label_reviewed.csv` ba cột. Không cần thư mục audio tên `audio`.

Chấp nhận đường dẫn tương đối có dấu `/` hoặc `\\` từ Windows. Các đường dẫn tuyệt đối, vượt ra ngoài dataset, liên kết trỏ ra ngoài, file âm thanh không tồn tại sẽ bị từ chối khi kiểm tra.

## 4. Lưu Git

```bash
git add app/main.py app/routes/auth.py app/routes/admin_datasets.py \
    app/services/dataset_service.py app/templates/base.html \
    app/templates/workspace.html app/templates/admin_datasets.html \
    app/static/styles.css NEXT_STEP_DATASETS.md

git commit -m "Add dataset discovery and annotator permissions"
git push -u origin feature/multi-user-admin
```

Kiểm tra `.gitignore` trước khi commit, **không đưa `data/`, `state/`, `.env` lên Git**.

## 5. Chưa triển khai ở giai đoạn này

- Chưa có chức năng sửa nhãn, chưa cập nhật mẫu trong SQLite.
- Chưa có khóa audio / điều phối nhiều người, xuất SQLite → CSV, heartbeat.
- `label_reviewed.csv` chỉ được tạo **lần đầu** nếu chưa có. Sau này SQLite sẽ là nguồn dữ liệu chính; chỉ đồng bộ SQLite → CSV khi người dùng nhấn Lưu CSV hoặc có sự kiện đã chốt.

### Kiểm thử đã thực hiện

- Kiểm tra cú pháp Python.
- Smoke test FastAPI (với module băm mật khẩu mô phỏng do môi trường kiểm thử không có `pwdlib`): đăng nhập và phân quyền, quét dataset, khởi tạo CSV, không ghi đè file gốc, chỉ hiển thị dataset đã cấp, chặn Annotator vào trang Admin.
- Kiểm tra riêng đường dẫn Windows, ngăn thoát khỏi thư mục dataset, kiểm tra trạng thái reviewed CSV.
- **Chưa chạy Docker trên Codespaces của bạn**; vui lòng kiểm tra các bước Docker nêu trên.
