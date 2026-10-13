# Bước 2 — Đăng nhập và quản lý tài khoản trên Web

## Có gì trong bản cập nhật?

- `app/main.py`: thay giao diện thử nghiệm ghi CSV trực tiếp bằng các tuyến đăng nhập, Admin và Annotator.
- `app/services/auth_service.py`: xác thực cookie phiên, lưu bản băm token trong SQLite, chống giả mạo biểu mẫu.
- `app/routes/auth.py`: `/login`, `/logout`, `/workspace`.
- `app/routes/admin.py`: `/admin`, tạo Annotator, khóa/mở khóa, đặt lại mật khẩu.
- `app/templates/*`, `app/static/styles.css`: giao diện HTML/CSS.
- Không thay đổi schema SQLite và không chỉnh sửa các file `label.csv`.
- Không có giới hạn số lần đăng nhập sai, theo yêu cầu.

## 1. Sao lưu và ghi đè patch

Nếu thư mục mã nguồn hiện tại chưa commit, hãy commit trước để có thể quay lại.
Giải nén `stt-labeling-login-admin-patch.zip` vào thư mục gốc dự án.

## 2. Đảm bảo Docker và .env

`requirements.txt` hiện tại đã có các thư viện cần thiết.
`.env` phải có dòng `SESSION_SECRET=...` dài ít nhất 32 ký tự.
Không đưa `.env`, `data/` hoặc `state/` lên Git.

## 3. Chạy

```bash
docker compose down
docker compose up -d --build
docker compose logs --tail=80 web
docker compose ps
```

Truy cập cổng 8000 của Codespaces; nhập tài khoản `admin` đã tạo trước đó bằng CLI.

## 4. Thử các chức năng

1. Đăng nhập Admin -> `/admin`.
2. Tạo tài khoản `user01` với mật khẩu tối thiểu 4 ký tự.
3. Đăng xuất Admin rồi đăng nhập `user01` -> `/workspace`.
4. Đăng nhập Admin, khóa `user01`. Phiên đang mở của `user01` phải bị thu hồi.
5. Mở khóa và đặt lại mật khẩu `user01`.
6. Khởi động lại container; tài khoản phải tồn tại vì SQLite nằm ở `state/`.

## 5. Chú ý

- Đăng nhập sử dụng cookie HttpOnly và phiên lưu trong SQLite. Phiên hết hạn sau 12 giờ.
- Mặc định cookie `Secure=false` để truy cập HTTP qua mạng LAN. Khi dùng HTTPS, đặt `COOKIE_SECURE=true` trong `.env`.
- Tài khoản Admin được tạo hoặc đổi mật khẩu bằng lệnh CLI từ giai đoạn 1.
- Không mở cổng này ra Internet công khai.
- Chưa có giao diện cấp dataset và chưa có chức năng gán nhãn: đó là giai đoạn tiếp theo.
