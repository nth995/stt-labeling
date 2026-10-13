# Giai đoạn 4 — Nghe và gán/sửa nhãn audio (đa người dùng)

## 1. Cập nhật mã nguồn

Tại `/workspaces/stt-labeling` trên Codespaces, lưu các chỉnh sửa trước khi ghi đè (nếu có). Tải ZIP cập nhật vào thư mục gốc và chạy:

```bash
unzip -o stt-labeling-labeling-stage4-patch.zip -d .
docker compose up -d --build
docker compose logs --tail=60 web
```

Mở cổng 8000 trong tab **PORTS**. Không cần chạy lại `create_admin.py` hay xoá `annotations.sqlite3`.

## 2. Chức năng mới

- Annotator vào `/workspace` → **Mở công cụ gán nhãn**.
- Admin vào `/admin/datasets` → **Mở gán nhãn (Admin)**.
- Lần mở đầu tiên, hệ thống nhập nhãn từ `label.csv` và `label_reviewed.csv` vào các bảng SQLite `samples`; **không thay đổi hai file CSV**. Nếu chưa có CSV, dùng mục Admin để khởi tạo trước.
- Nút **Nhận audio pending tiếp theo** phân phối mẫu chưa được người khác giữ.
- Danh sách `pending`, `confirmed`, `Tất cả` cho phép mở mẫu thủ công để sửa lại.
- Nhấn **Xác nhận & lưu** hoặc **Xác nhận & tiếp theo** để cập nhật SQLite (kể cả trạng thái `confirmed`).
- Nếu audio không có lời nói, tích **Xác nhận audio không có lời nói**; nhãn rỗng vẫn được xác nhận hợp lệ.
- Khóa mẫu có thời hạn 10 phút, được gia hạn 30 giây/lần trong khi trang đang hoạt động; có kiểm tra phiên bản để chống ghi đè.
- **Giai đoạn 4 chưa có chức năng đồng bộ SQLite → `label_reviewed.csv`**. Bản CSV có thể cũ hơn SQLite cho đến khi hoàn thành giai đoạn tiếp theo.

## 3. Các bước kiểm thử

1. Đăng nhập `admin`, xác nhận `test1`, `test2` có `label.csv` và `label_reviewed.csv`; cấp cho hai tài khoản Annotator quyền `test1`.
2. Mở một tab bằng `user01`, một tab ẩn danh bằng `user02` (hai tài khoản khác nhau).
3. Cả hai cùng chọn `test1` và nhấn **Nhận audio pending tiếp theo**. Hai audio phải khác nhau.
4. Mở thử mẫu người kia đang giữ: phải báo đang bị khóa.
5. `user01` sửa nhãn, nhấn **Xác nhận & tiếp theo**. Kiểm tra số `confirmed` tăng và được cấp audio khác.
6. `user02` xác nhận một audio không có lời nói với nhãn trống.
7. Làm mới trang: SQLite vẫn giữ các thay đổi; mở lại mẫu đã `confirmed` vẫn có thể sửa.
8. Dùng Admin mở `test1/label_reviewed.csv`: **chưa bị cập nhật** ở giai đoạn này.
9. Thử tài khoản không được cấp quyền `test2`, phải nhận 403.

## 4. Kiểm tra SQLite

```bash
python3 - <<'PY'
import sqlite3
with sqlite3.connect('state/annotations.sqlite3') as db:
    for table in ('samples', 'sample_locks', 'annotation_history'):
        count = db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        print(f'{table}: {count}')
    for dataset_id, version, synced in db.execute('SELECT id, version, csv_synced_version FROM datasets'):
        print(f'dataset={dataset_id}, sqlite_version={version}, csv_synced_version={synced}')
PY
```

Khi nhãn đã chỉnh sửa, `version` của dataset tăng. `csv_synced_version` chưa tăng trong giai đoạn 4.

## 5. Lưu ý vận hành

- Mọi dữ liệu SQLite nằm trong volume `./state:/app/state`. Không xóa `state/` hoặc chạy `docker compose down -v` nếu chưa sao lưu.
- `label.csv` giữ nguyên. Audio được đọc theo đường dẫn tương đối từ thư mục dataset, không yêu cầu thư mục audio có tên cố định.
- Không cần mở port ra Internet. Khi chạy trong LAN, giới hạn cổng 8000 bằng tường lửa theo mạng nội bộ.
- Trình duyệt có thể chặn tự phát audio: nhấn nút Play nếu chưa phát.
- Khi nhiều người cùng thao tác, SQLite WAL cho phép nhiều người đọc nhưng vẫn chỉ có một giao dịch ghi tại một thời điểm.
- Chưa cần tạo tài khoản hoặc cơ sở dữ liệu mới. Schema được bổ sung bằng `create_all` khi ứng dụng khởi động.

## 6. Sau khi kiểm thử thành công

```bash
git add app/main.py app/database/models.py app/routes/labeling.py app/services/labeling_service.py \
    app/templates/labeling.html app/templates/workspace.html app/templates/admin_datasets.html \
    app/static/styles.css NEXT_STEP_LABELING.md
git commit -m "Add multi-user audio labeling with SQLite locks"
git push -u origin feature/multi-user-admin
```

**Giai đoạn 5**: Nút `Lưu CSV` cho cả Admin/Annotator; một bộ ghi CSV duy nhất, đồng bộ khi đóng trang hoặc mất kết nối (best-effort), đối chiếu và phục hồi khi khởi động.
