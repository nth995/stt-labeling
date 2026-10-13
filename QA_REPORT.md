# Báo cáo sửa lỗi và kiểm thử STT Labeling

Ngày: 2026-10-13. Nguồn: `stt-labeling-feature-multi-user-admin(2).zip`.

## Những thay đổi

| Hạng mục | Sửa đổi | Bảo vệ dữ liệu |
|---|---|---|
| Import dữ liệu | Khi `label.csv` được **nối thêm mẫu ở cuối**, nhập thêm vào SQLite | Giữ `confirmed`, chặn thay đổi/xóa/reorder mẫu cũ |
| Xuất CSV | Đối chiếu CSV gốc và kết quả qua dấu thời gian/kích thước + SHA-256 phần cũ; khóa ghi, kiểm tra nguồn trước thay file | Không âm thầm làm mất dòng mới |
| Git | Bỏ qua `data/` | Không vô tình add audio/nhãn |
| Docker LAN | Script chuẩn bị `data/`,`state/` đúng chủ sở hữu | Không dùng chmod 777 |
| Nhiều tab | Không xóa mọi khóa theo cùng `session_id` khi một tab lấy mẫu mới | Mỗi tab có thể giữ mẫu độc lập, giao diện gửi mẫu cũ khi chuyển trong tab |
| CSV đường dẫn | Chuẩn hóa dấu `\\` thành `/` | Chặn đường dẫn trùng và vượt thư mục |
| SQLite schema | `PRAGMA user_version=1`, nâng cấp idempotent, tạo index mới | Giữ tài khoản, labels, history cũ |
| Dataset lớn | Chống trùng bằng SQLite tạm sau 10.000 đường dẫn; quét/generate CSV theo luồng | Giảm RAM tăng theo số audio |
| Admin | Xóa Annotator, gỡ 1/toàn bộ khóa đang giữ | Không xóa audio/nhãn, không xóa Admin |
| Giao diện | Editor hiện trước thanh xuất CSV, nhỏ gọn, tự cuộn editor vào khung nhìn | Không phải kéo xuống tìm vùng sửa nhãn |

## Kiểm thử đã chạy

- `python -m compileall -q app`: không lỗi cú pháp.
- `python -m unittest discover -s tests -q`: **24/24** thành công, tổng ~20 giây. Bao gồm 13 bài cũ, 11 bài mới: Web Admin, thêm audio, thứ tự nguồn bị thay đổi, CSV sửa ngoài ứng dụng, chuẩn hóa đường dẫn, khóa nhiều tab, migration và nâng cấp DB cũ.
- Thử nghiệm riêng 16 phiên cùng nhận mẫu: **16 khóa cho 16 mẫu khác nhau**, không có lỗi trong lần thử.
- Kiểm thử HTTP bằng FastAPI TestClient: Admin đăng nhập, xem khóa, gỡ khóa, người dùng cũ bị từ chối lưu, xác nhận nhãn, xóa user, xác minh vẫn giữ nội dung SQLite và lịch sử.

**Giới hạn kiểm thử:** Máy kiểm thử không có Docker daemon, không có bộ trình duyệt Playwright được cài đặt, không có thư viện `pwdlib` thật. Các bài Web dùng shim Argon2 chỉ trong môi trường kiểm thử; không đóng gói shim vào mã ứng dụng. Cần chạy Docker và kiểm thử thủ công trên Codespaces hoặc server LAN trước khi dùng dữ liệu thật. Chưa benchmark dataset triệu audio.

## Nghiệm thu trong Codespaces

1. Sao lưu trước khi thay đổi. Không giải nén bản đầy đủ đè lên data/state thật; chỉ dùng ZIP bản vá.
2. Giải nén, chạy `bash scripts/prepare_lan.sh`, `docker compose up -d --build`.
3. Chạy `docker compose run --rm -v "$(pwd)/tests:/app/tests:ro" web python -m unittest discover -s tests -v`.
4. Kiểm tra đăng nhập cả Admin và Annotator; mở một bộ dữ liệu, chuyển 3-5 audio xem trang tự chuyển tới ô nhãn.
5. Dùng hai trình duyệt/tài khoản, nhận hai mẫu khác nhau. Admin gỡ một audio đang giữ, kiểm tra thao tác lưu của người cũ bị từ chối.
6. Xóa một tài khoản **thử nghiệm**; kiểm tra phiên đăng nhập bị hủy, nhãn vẫn còn và không xóa audio.
7. Trước khi thay `label.csv`, sao lưu. Thử nối thêm một dòng hợp lệ ở CUỐI file: mở dataset → Lưu CSV → file kết quả có dòng mới, vẫn giữ nhãn cũ.
8. Dùng dataset thử nghiệm để kiểm tra sửa thứ tự dòng cũ trong `label.csv`: hệ thống báo lỗi và không ghi đè `label_reviewed.csv`.
9. Dùng nút Lưu CSV, đóng tab, mất mạng, restart Docker, xác minh nhãn đã xác nhận khôi phục được.

## Giới hạn thiết kế còn giữ

- Thao tác sửa danh sách nguồn theo kiểu reorder/xóa/trộn không tự di trú; bị chặn an toàn.
- SQLite là nguồn chính; chỉ xuất CSV khi bấm yêu cầu hoặc khi phiên kết thúc/mất kết nối được phát hiện.
- Khi xóa tài khoản, lịch sử nhãn được giữ nhưng tham chiếu đến ID người đã xóa sẽ trở thành NULL theo khóa ngoại. Nên lưu báo cáo người thực hiện trước khi xóa tài khoản thật nếu cần truy vết.
- Web dùng HTTP trong mạng LAN, không triển khai ra Internet.
