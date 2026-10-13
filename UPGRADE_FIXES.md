# STT Labeling — Bản sửa tổng hợp

## Triển khai cập nhật

1. **Sao lưu trước:** `docker compose exec web python -m app.scripts.backup_sqlite`; sao lưu các file `label.csv`/`label_reviewed.csv` (và tải backup SQLite ra máy). `git status` và commit các thay đổi mã nguồn hiện tại.
2. Giải nén gói cập nhật vào gốc repo; không chép dữ liệu thử nghiệm `data/` hoặc `state/` từ nơi khác.
3. Chạy `bash scripts/prepare_lan.sh` trên HOST. Nếu không cùng UID/GID, kiểm tra owner và chỉnh bằng `sudo chown` chỉ sau khi chắc chắn thư mục dữ liệu tương ứng. Script cũng kiểm tra quyền đọc/ghi các file CSV/SQLite hiện có.
4. `docker compose up -d --build` rồi `docker compose logs --tail=80 web` và `docker compose ps`.
5. `docker compose run --rm -v "$(pwd)/tests:/app/tests:ro" web python -m unittest discover -s tests -v`.
6. Đăng nhập Admin, kiểm tra danh sách khóa dưới mỗi tài khoản, thử gỡ một khóa và tạo/xóa tài khoản thử nghiệm. Kiểm tra từ hai trình duyệt.

## Điểm thay đổi

- Khi `label.csv` có thêm mẫu **ở cuối**, ứng dụng nhập phần mới vào SQLite và xuất CSV đầy đủ khi có yêu cầu. Nếu sửa/xóa/đảo thứ tự nhãn gốc, hệ thống **chặn** để bảo vệ kết quả cũ. Không tự xóa nhãn đã xác nhận.
- Nếu `label_reviewed.csv` bị sửa **các dòng cũ** bên ngoài Web, hệ thống chặn đồng bộ. Có thể nối thêm đúng các dòng audio mới vào cuối cả hai CSV; các dòng cũ phải giữ nguyên byte-for-byte. Chỉ sửa nhãn qua Web khi SQLite đang là nguồn chính.
- Cấp khóa theo audio thay vì xóa mọi khóa của cùng phiên đăng nhập. Khi điều hướng trong cùng trang, khóa mẫu cũ được trả lại; các tab khác của cùng tài khoản không mất khóa.
- Admin có thể gỡ một hoặc tất cả audio đang giữ, xóa tài khoản Annotator sau khi nhập đúng tên. Việc xóa tài khoản giữ nguyên mẫu và nhật ký nhãn, nhưng thông tin ID người sửa trước đó trở thành NULL vì ràng buộc SET NULL; nếu cần truy nguyên người sửa, xuất/sao lưu thông tin tác giả trước khi xóa.
- Giao diện thu gọn, vùng nhập nhãn được cuộn vào tầm nhìn sau chuyển audio; phần Lưu/Tải CSV đưa xuống dưới.
- CSV kiểm tra đường dẫn sau chuẩn hóa dấu `\\` thành `/`; với bộ rất lớn, chống trùng chuyển sang SQLite tạm trên đĩa để giảm RAM.
- SQLite có quản lý phiên bản schema qua PRAGMA `user_version`, tạo chỉ mục theo phiên bản, và thêm bảng `dataset_source_states` để phát hiện CSV thay đổi.

## Hạn chế có chủ đích

- Chỉ hỗ trợ **bổ sung** mẫu vào cuối `label.csv` sau khi bắt đầu gán nhãn. Muốn thay toàn bộ danh sách/thứ tự phải tạo dataset mới hoặc có quy trình chuyển dữ liệu riêng; không tự động ghi đè.
- SQLite là dữ liệu chính khi sử dụng, CSV chỉ được xuất theo yêu cầu hoặc phát hiện kết thúc phiên. Đóng/mất mạng là best-effort, nhưng nhãn đã xác nhận vẫn được lưu SQLite.
- Server LAN cần mount `data/` và `state/` cục bộ trên một máy có quyền ghi, không chia sẻ file SQLite qua ổ mạng. Không triển khai Internet công khai.
- Mật khẩu tối thiểu 4 ký tự như yêu cầu, không thêm chức năng giới hạn số lần đăng nhập sai.
