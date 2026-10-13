# STT Labeling — Giai đoạn 6: Hoàn thiện và kiểm thử triển khai LAN

## 1. Phạm vi cập nhật

- Sửa lỗi JavaScript `pagehide`/`sendBeacon` nằm nhầm trong `<title>` tại `app/templates/labeling.html`. Giờ mã chạy ở cuối `<body>` khi tab gán nhãn đóng hoặc rời Web.
- Chạy Docker bằng UID/GID của người sở hữu thư mục `data/`, `state/` qua `APP_UID`, `APP_GID`. Mặc định 1000:1000 cho GitHub Codespaces.
- Thêm kiểm tra `/health` và Docker healthcheck.
- Tạo chỉ mục SQLite `ix_samples_dataset_status_position` để lọc và phân trang `pending`/`confirmed`.
- Nhập CSV vào SQLite theo batch 100 mẫu, không giữ cả hai file nhãn trong RAM.
- Thêm lệnh sao lưu SQLite bằng **SQLite Online Backup API** (hỗ trợ WAL khi đang có người ghi).
- Thêm kiểm thử tự động tranh chấp hai phiên, nhập 1.200 mẫu, sao lưu và kết cấu HTML.

**Giữ nguyên quy tắc:** `label.csv` không ghi đè; chỉ `pending`/`confirmed`; xác nhận chỉ lưu SQLite; `label_reviewed.csv` được xuất khi nhấn Lưu/Tải CSV hoặc khi phát hiện kết thúc phiên; không WER.

## 2. Cập nhật GitHub Codespaces

Đứng ở thư mục `/workspaces/stt-labeling`, **kiểm tra và commit các thay đổi code hiện tại trước khi giải nén**.

```bash
cd /workspaces/stt-labeling
git status
```

Đưa ZIP vào thư mục gốc, giải nén:

```bash
unzip -o stt-labeling-stage6-hardening-patch.zip -d .
```

### Cấu hình quyền file

Trong Codespaces, tài khoản thường có UID/GID 1000:1000. Nếu **chỉ đang dùng dữ liệu thử nghiệm** và các file trong `data/`, `state/` đã bị Docker cũ tạo bằng root:

```bash
docker compose down
sudo chown -R "$(id -u):$(id -g)" data state
```

**Chỉ dùng chown -R cho dữ liệu thử nghiệm mà bạn kiểm soát.** Với server LAN và dữ liệu lớn, hãy kiểm tra owner hiện tại (`id`, `ls -ld data state`) rồi đặt `APP_UID`, `APP_GID` theo tài khoản sở hữu; không thay owner toàn bộ dữ liệu thật nếu không cần.

Ví dụ ở server có UID/GID khác 1000, thêm các dòng dưới vào `.env` (không commit file này):

```dotenv
APP_UID=1001
APP_GID=1001
# Nếu có nhiều IP, chỉ lắng nghe trên IP LAN của server
# APP_BIND_IP=192.168.1.100
APP_PORT=8000
```

Lưu ý: `${APP_UID}` trong `compose.yaml` được Docker Compose đọc từ file `.env` hoặc biến môi trường; giá trị `user` phải trùng quyền thư mục được bind-mount.

### Build và chạy

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 web
```

Kiểm tra sức khỏe ứng dụng và tài khoản tiến trình:

```bash
docker compose exec web id
docker compose exec web python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

Kết quả health mong đợi `{"status":"ok"}`. Ở GitHub Codespaces, mở port 8000 trong tab PORTS. Server LAN dùng `http://<IP-LAN-SERVER>:8000` nếu mạng và firewall cho phép.

## 3. Chạy kiểm thử tự động

Do Docker Image không chứa thư mục `tests/` và file `compose.yaml`, mount cả hai khi chạy:

```bash
docker compose run --rm \
  -v "$(pwd)/tests:/app/tests:ro" \
  -v "$(pwd)/compose.yaml:/app/compose.yaml:ro" \
  web python -m unittest discover -s tests -v
```

Kết quả kiểm thử cục bộ của gói: **13/13 đạt**. Cần kiểm thử lại Docker/Browser trên Codespaces vì môi trường thực tế khác máy chạy kiểm thử cục bộ.

## 4. Sao lưu SQLite trong khi ứng dụng chạy

```bash
docker compose exec web python -m app.scripts.backup_sqlite
```

Bản sao lưu nằm trong `state/backups/` phía ngoài container. Chúng được sao lưu nhất quán kể cả khi database đang ở chế độ WAL. Không dùng `cp state/annotations.sqlite3` đơn lẻ khi database đang hoạt động vì có thể bỏ sót dữ liệu trong WAL.

Khôi phục dữ liệu là thao tác có rủi ro: dừng ứng dụng, lưu bản dự phòng hiện tại và xác minh file sao lưu trước khi thay cơ sở dữ liệu; không ghi đè SQLite khi server đang chạy.

## 5. Kiểm tra thực tế trên Web

1. Đăng nhập bằng Admin và Annotator, truy cập `test1`/`test2` theo phân quyền.
2. Mở **hai trình duyệt/tài khoản khác nhau**, mỗi người nhận một audio `pending` khác nhau.
3. A xác nhận một nhãn; đảm bảo `label_reviewed.csv` **chưa** thay đổi ngay.
4. B nhấn **Lưu CSV**; file sẽ nhận cả các thay đổi đã lưu của A.
5. Đóng tab gán nhãn; kiểm tra CSV đồng bộ. Khi mất mạng đột ngột, chờ heartbeat timeout khoảng 120–150 giây. Đây là cơ chế dự phòng, không bảo đảm đồng bộ tức thì trong mọi tình huống.
6. Kiểm tra sau khi nhấn lưu CSV, VS Code có thể mở `label_reviewed.csv` và quyền sở hữu là UID của bạn (`ls -l`).
7. Khởi động lại `docker compose restart web`, kiểm tra tài khoản, nhãn và khóa mẫu vẫn nhất quán.
8. Chạy lệnh sao lưu, kiểm tra file mới trong `state/backups/`.

## 6. Lưu Git

```bash
git add .dockerignore Dockerfile compose.yaml app/main.py \
  app/database/connection.py app/services/labeling_service.py \
  app/templates/labeling.html app/scripts/backup_sqlite.py \
  tests/test_labeling_concurrency.py tests/test_production_readiness.py \
  README_STAGE6.md

git commit -m "Harden STT labeling for LAN deployment"
git push -u origin feature/multi-user-admin
```

Không thêm `data/`, `state/` và `.env` vào Git.
