# STT Labeling — Sửa lỗi khóa audio khi đăng xuất / quay lại Web

## Nguyên nhân

- `logout` chỉ thu hồi phiên đăng nhập, chưa giải phóng `sample_locks` của phiên.
- `claim_next` bỏ qua mọi mẫu có khóa, kể cả mẫu do chính phiên hiện tại giữ. Nếu đóng tab rồi mở lại, nút "Nhận audio pending tiếp theo" không lấy lại mẫu đang làm.
- Khi đóng Web mà không đăng xuất hoặc mất mạng, khóa cũ có thể tồn tại cho đến khi hết hạn (mặc định 10 phút).

## Thay đổi

1. Đăng xuất: đồng bộ CSV như cũ, sau đó giải phóng mọi khóa của phiên, rồi thu hồi phiên.
2. Vào lại cùng phiên: nút "Nhận audio pending tiếp theo" ưu tiên mẫu `pending` mà chính phiên đang giữ. Nhưng khi bấm **Next** từ một mẫu đang mở, vẫn chuyển sang mẫu khác.
3. Đăng nhập bằng phiên mới: nút **Nhận lại audio của tôi** chuyển khóa *của chính tài khoản đó* sang phiên hiện tại sau khi người dùng xác nhận. Nếu mở trực tiếp audio mà tài khoản giữ ở phiên khác, sẽ có nút **Nhận lại audio này**.
4. Sau khi mất heartbeat vượt ngưỡng và đồng bộ CSV thành công, bộ giám sát giải phóng khóa của phiên đã kết thúc trong dataset tương ứng.
5. Khi một phiên nhận lại khóa, phiên cũ không được xác nhận nhãn nữa; SQLite vẫn kiểm tra phiên bản và sở hữu khóa.

**Không thay đổi database schema**, `label.csv`, `label_reviewed.csv`, `pending/confirmed`, hoặc logic gán nhãn/lưu CSV.

## Cài đặt Codespaces

```bash
cd /workspaces/stt-labeling

git status

# Sao lưu SQLite (nếu ứng dụng đang chạy)
docker compose exec web python -m app.scripts.backup_sqlite

# Tải gói patch vào thư mục gốc dự án, rồi:
unzip -o stt-labeling-lock-recovery-patch.zip -d .

docker compose up -d --build
docker compose logs --tail=80 web
```

## Kiểm thử

- Đăng nhập user01 → nhận mẫu 1 → **Đăng xuất** → đăng nhập lại → **Nhận audio pending tiếp theo**: phải có thể nhận lại mẫu 1, nếu người khác chưa nhận.
- Nhận mẫu 1 → đóng tab (không đăng xuất) → mở lại cùng tài khoản: nếu còn cookie phiên cũ, **Nhận audio pending tiếp theo** trả về mẫu 1.
- Nhận mẫu 1 trên trình duyệt A → đăng nhập cùng tài khoản ở B → bấm **Nhận lại audio của tôi** và xác nhận: B chỉnh sửa được, A bị từ chối nếu cố lưu nhãn cũ.
- user02 không được lấy lại mẫu do user01 giữ.
- Nhấn **Nhận audio pending tiếp theo** khi đang mở mẫu 1: nếu còn mẫu khác, chuyển sang mẫu khác như trước.
- Sau khi nhấn Xác nhận, nhãn vẫn chỉ được ghi vào SQLite; không tự xuất CSV.

Bộ mới thêm 7 kiểm thử (`test_lock_recovery.py` và `test_lock_recovery_web.py`); tổng cộng 31 khi chạy cùng 24 kiểm thử hiện có.

```bash
docker compose run --rm \
  -v "$(pwd)/tests:/app/tests:ro" \
  -v "$(pwd)/.gitignore:/app/.gitignore:ro" \
  -v "$(pwd)/compose.yaml:/app/compose.yaml:ro" \
  web python -m unittest discover -s tests -v
```

**Chú ý:** Trình chạy các bài HTTP cần thư viện kiểm thử TestClient (`httpx2` đối với phiên bản Starlette trên Codespaces của bạn). Nếu trước đó bạn đã chạy 24 bài bằng một lệnh bổ sung thư viện tạm, tiếp tục sử dụng cách bổ sung đó cho lượt kiểm thử này. Không cần đưa thư viện kiểm thử vào image chạy ứng dụng chính.

