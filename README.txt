Cập nhật chính sách mật khẩu tối thiểu 4 ký tự cho STT Labeling.

1) Giải nén vào thư mục gốc dự án trên GitHub Codespaces.
2) docker compose build web
3) Nếu muốn đổi mật khẩu admin hiện tại:
   docker compose run --rm -it web python -m app.scripts.reset_password --username admin

Các file cập nhật:
- app/config.py: MIN_PASSWORD_LENGTH = 4
- app/scripts/create_admin.py: dùng cấu hình chung
- app/scripts/reset_password.py: công cụ đặt lại mật khẩu đã tồn tại

Không sửa dữ liệu trong SQLite hoặc file CSV.
