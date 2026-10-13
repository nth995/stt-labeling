# Giai đoạn 5 — Đồng bộ SQLite → label_reviewed.csv

## Những gì được bổ sung

- Trang `/labeling/{dataset_id}` có **Lưu CSV** và **Tải CSV về máy** cho cả Admin/Annotator đã được cấp quyền.
- **Xác nhận nhãn** vẫn chỉ ghi `annotations.sqlite3`, tuyệt đối không ghi CSV ở thao tác này.
- CSV dùng chính xác ba cột `audio_path,label,status` và giữ thứ tự mẫu gốc.
- Mỗi dataset có một khóa ghi riêng (`state/csv_sync_<id>.lock`), nên hai người xuất cùng lúc không làm hỏng CSV. Xuất ra file tạm, sau đó thay thế nguyên tử.
- SQLite giữ `Dataset.version` và `Dataset.csv_synced_version`; những nhãn phát sinh khi đang xuất vẫn được nhận diện là chưa đồng bộ.
- Khi rời tab: trình duyệt **cố gắng** gửi `sendBeacon` để đồng bộ. Khi mất mạng hoặc đóng tab đột ngột: server nhận biết sau khi thiếu heartbeat khoảng 120–150 giây và tự đồng bộ.
- Khi đăng xuất: xuất các dataset đã mở trong phiên.
- Khi server khởi động: đồng bộ bù dataset có SQLite mới hơn CSV.

**Quan trọng:** Trình duyệt không bảo đảm gửi được sự kiện đóng tab. Kết quả **đã nhấn Xác nhận** vẫn an toàn trong SQLite; kết quả đang nhập nhưng *chưa xác nhận* có thể mất khi mất mạng. Nếu server đang tắt, phải chờ server chạy lại mới có thể đồng bộ CSV.

## Cập nhật trên Codespaces

1. Sao lưu/commit thay đổi trước khi giải nén.
2. Đưa file `stt-labeling-csv-sync-stage5-patch.zip` vào thư mục gốc repository.
3. Giải nén:

```bash
unzip -o stt-labeling-csv-sync-stage5-patch.zip -d .
```

4. Build và chạy:

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 web
```

Không xóa thư mục `data/` hoặc `state/`. SQLite hiện có được giữ nguyên; bảng mới `dataset_activities` được tạo tự động.

## Kiểm thử trên trình duyệt

1. Đăng nhập Admin hoặc Annotator đã được cấp quyền, mở dataset `test1`.
2. Sửa một nhãn, nhấn **Xác nhận & lưu**, kiểm tra hiển thị *Có thay đổi chưa xuất CSV*.
3. Kiểm tra `data/test1/label_reviewed.csv` **chưa thay đổi**.
4. Nhấn **Lưu CSV**. Kiểm tra file CSV có nhãn mới và trạng thái `confirmed`.
5. Nhấn **Tải CSV về máy** để tải bản đồng bộ mới nhất.
6. Đăng nhập người thứ hai, sửa một mẫu khác, sau đó người thứ nhất nhấn **Lưu CSV**. File phải có kết quả của cả hai.
7. Xác nhận thêm một nhãn và đóng tab gán nhãn. Kiểm tra CSV; nếu Beacon bị bỏ qua, server tự phát hiện sau tối đa khoảng 150 giây khi còn chạy.
8. Khi thử khởi động lại ứng dụng: xác nhận một nhãn, **chưa bấm Lưu CSV**, sau đó `docker compose restart web`. Khi khởi động, CSV phải được xuất bù.

## Kiểm thử tự động (bên trong Docker)

Dockerfile không sao chép thư mục `tests` vào image, nên có thể gắn thư mục test từ Codespaces:

```bash
docker compose run --rm -v "$(pwd)/tests:/app/tests:ro" web \
    python -m unittest discover -s tests -v
```

Test sử dụng thư mục tạm, không chạm đến dữ liệu nhãn và tài khoản hiện tại.

## Commit

```bash
git add app tests NEXT_STEP_CSV_SYNC.md
git commit -m "Add SQLite to CSV synchronization and session recovery"
git push -u origin feature/multi-user-admin
```

Đảm bảo `.gitignore` vẫn loại trừ `/data/`, `/state/` và `.env`.

## Ghi chú triển khai

- Cơ chế này sử dụng `fcntl.flock`, được thiết kế cho Docker Linux (Codespaces và server Linux trong LAN).
- Giữ một service Web với một Uvicorn worker theo Dockerfile hiện tại. Nếu về sau mở rộng sang nhiều máy chủ, nên chuyển điều phối công việc sang database/server riêng.
- Phát hiện phiên mất kết nối qua heartbeat không đồng nghĩa phát hiện ngay việc tắt toàn bộ server. Khi máy chủ bị dừng, không tiến trình nào có thể ghi CSV; SQLite đã được commit mới là dữ liệu bền vững.
