# STT Labeling — Công cụ gán nhãn audio nhiều người dùng

Ứng dụng Web nội bộ để **nghe audio, gán nhãn mới, sửa nhãn có sẵn và xác nhận nhãn tiếng nói (STT/ASR)**. Chạy bằng **FastAPI + SQLite + Docker**, truy cập từ nhiều máy qua **IP và cổng trong mạng LAN**. Có trang quản trị tài khoản và dataset, phân phối audio tránh trùng, lưu lịch sử, sao lưu SQLite và xuất CSV dùng chung.

> **Phạm vi:** triển khai trong mạng nội bộ được phép truy cập. Chưa thiết kế để công khai trực tiếp trên Internet. **Không tính WER.** `label.csv` gốc được giữ nguyên trong quá trình gán nhãn; nhãn xác nhận lưu **ngay vào SQLite**, không xuất lại CSV sau mỗi thao tác.

## 1. Chức năng

| Nhóm | Chức năng |
|---|---|
| Admin | Tạo/khóa/mở khóa/xóa tài khoản Annotator; đặt lại mật khẩu; xem và thu hồi từng khóa audio hoặc toàn bộ khóa của tài khoản |
| Dataset | Tự phát hiện thư mục trong `data/`; kiểm tra đường dẫn; khởi tạo CSV còn thiếu; cấp/thu hồi quyền theo dataset |
| Gán nhãn | Phát audio trên trình duyệt; nhãn gốc chỉ đọc; nhập/sửa nhãn; xác nhận; bỏ qua; xác nhận audio không có lời nói với nhãn trống |
| Làm việc nhóm | Cấp audio `pending` không trùng, mở lại `confirmed`, khóa tạm thời và kiểm tra phiên bản để chống ghi đè |
| Tiếp tục công việc | Đăng xuất giải phóng khóa; quay lại cùng phiên có thể nhận tiếp mẫu đang giữ; cùng tài khoản có thể chủ động nhận lại khóa từ phiên khác |
| Kết quả | SQLite là nguồn chính; Admin/Annotator được cấp quyền đều có thể **Lưu CSV** hoặc **Tải CSV** của dataset |
| Vận hành | Phát hiện phiên mất heartbeat, đồng bộ CSV khi rời phiên (cố gắng thực hiện), đồng bộ bù khi khởi động, sao lưu SQLite đang chạy, API `/health` |

### Hai vai trò

- **Admin:** quản lý tài khoản, dataset và phân quyền; cũng có thể trực tiếp gán nhãn và xuất CSV.
- **Annotator:** chỉ nhìn thấy và thao tác trên các dataset đã được cấp; được phép xác nhận nhãn, lưu và tải CSV của các dataset đó. Không được quản lý tài khoản hoặc quyền người khác.

Không có đăng ký tài khoản tự do. Admin cấp tài khoản. Mật khẩu tối thiểu **4 ký tự** theo cấu hình hiện tại (`app/config.py`). Ứng dụng hiện **không có giới hạn số lần đăng nhập sai**; vì vậy phải hạn chế truy cập ở tầng mạng LAN.

## 2. Kiến trúc và nguyên tắc dữ liệu

```text
Máy A/B/C trong LAN (trình duyệt)
                 |
       http://IP_SERVER:8000
                 |
      Docker: FastAPI / Uvicorn
        |                 |
  SQLite (WAL)       File audio trong data/
        |
  Bộ xuất CSV theo dataset
        |
  data/<dataset>/label_reviewed.csv
```

- **Mọi lần nhấn “Xác nhận & lưu / Xác nhận & tiếp theo”** ghi nhãn, trạng thái và lịch sử vào `state/annotations.sqlite3` trong một giao dịch. **Không** ghi CSV lúc này.
- **Lưu CSV / Tải CSV:** lấy bản dữ liệu mới nhất đã commit của **tất cả người đang làm việc trong cùng dataset**, xuất sang `label_reviewed.csv` theo thứ tự mẫu gốc.
- **Đăng xuất/đóng tab/mất mạng:** hệ thống cố gắng đồng bộ khi nhận thông báo rời trang; nếu mất kết nối đột ngột, bộ giám sát kiểm tra heartbeat để xử lý muộn hơn. Không thể bảo đảm trình duyệt gửi được sự kiện đóng trang.
- **Khởi động lại:** ứng dụng đối chiếu phiên bản SQLite và CSV để xuất bù nếu còn thay đổi chưa đồng bộ.
- Mỗi dataset có khóa xuất CSV riêng; ghi qua file tạm và `os.replace`, giúp tránh hai thao tác xuất đồng thời ghi chồng lên nhau. CSV là **bản kết quả**, SQLite là **nguồn dữ liệu chính**.

### Cấu trúc `data/`

```text
data/
├── dataset_A/
│   ├── label.csv
│   ├── label_reviewed.csv
│   ├── audio/001.wav
│   └── train/speaker_01/001.wav
└── dataset_B/
    ├── label.csv
    ├── label_reviewed.csv
    ├── train/speaker_01/001.wav
    └── recordings/2026/002.flac
```

**Không quy định** thư mục audio phải tên gì, nằm sâu bao nhiêu cấp. Mỗi dòng CSV chỉ định đường dẫn tương đối **tính từ chính thư mục chứa `label.csv`**.

`label.csv` — **2 cột bắt buộc**, dữ liệu đầu vào:

```csv
audio_path,label
audio/001.wav,XIN CHÀO
train/speaker_01/001.wav,
```

`label_reviewed.csv` — **3 cột bắt buộc**, dữ liệu đầu ra:

```csv
audio_path,label,status
audio/001.wav,XIN CHÀO,pending
train/speaker_01/001.wav,,confirmed
```

Chỉ có **2 trạng thái**:

- `pending`: chưa xác nhận; nhãn có thể có hoặc trống.
- `confirmed`: đã xác nhận; **nhãn trống cũng hợp lệ** nếu người dùng chọn “Audio không có lời nói”.

Khi dataset chưa có `label.csv`, Admin chọn **Quét thư mục data/** rồi **Khởi tạo CSV còn thiếu** để ứng dụng quét đệ quy audio và tạo nhãn rỗng. Nếu đã có `label.csv` thì dùng đường dẫn trong CSV, không tự đoán cấu trúc thư mục.

**Các nguyên tắc bảo vệ dữ liệu:**

1. Không tự sửa/ghi đè `label.csv` gốc khi gán nhãn.
2. `label_reviewed.csv` được khởi tạo với nhãn gốc và trạng thái `pending` nếu chưa tồn tại.
3. Sau khi SQLite đã nhập dataset, chỉ cho phép **bổ sung audio vào cuối `label.csv`**; không tự động chấp nhận xóa, đảo thứ tự hoặc sửa nhãn gốc của mẫu cũ. Có thể nối thêm dòng tương ứng vào cuối `label_reviewed.csv` khi giữ nguyên toàn bộ phần cũ.
4. Nếu `label_reviewed.csv` bị chỉnh sửa ngoài ứng dụng ở phần dữ liệu cũ, quá trình đối soát sẽ từ chối đồng bộ thay vì âm thầm ghi đè. Không sửa CSV trực tiếp khi nhóm đang dùng Web.
5. Đường dẫn được chuẩn hóa `\` thành `/`; từ chối đường dẫn tuyệt đối, `..`, file ngoài dataset, symlink trỏ ra ngoài, đường dẫn trùng sau chuẩn hóa và audio không tồn tại.
6. Một số đuôi audio được nhận diện: `.wav`, `.mp3`, `.flac`, `.ogg`, `.opus`, `.m4a`, `.aac`, `.webm`, `.wma`. **Trình duyệt có thể không phát được tất cả định dạng**; nên ưu tiên WAV/MP3 hoặc chuyển đổi audio phía ngoài nếu cần.

Trong ZIP này có các dataset `test1`–`test4` để minh họa; **không sử dụng chúng thay cho dữ liệu nghiệp vụ thật**.

## 3. Quy tắc nhiều người dùng và thời gian giữ audio

- Khi bấm **Nhận audio pending tiếp theo**, hệ thống chọn mẫu `pending` chưa bị người khác giữ. Người dùng cũng có thể mở trực tiếp mẫu `pending` hoặc `confirmed` từ danh sách.
- Mỗi audio có khóa trong `sample_locks`; phiên đang giữ được sửa và xác nhận mẫu đó. Lưu nhãn kiểm tra **quyền giữ khóa + phiên bản nhãn**, nên thao tác của phiên cũ không thể âm thầm ghi đè.
- Thời gian sống một khóa: **2 phút (120 giây)** — `LEASE_SECONDS = 2 * 60` trong `app/services/labeling_service.py`.
- Khi người dùng còn mở trang và kết nối, trình duyệt gia hạn khóa **mỗi 30 giây**. Vì vậy **2 phút là thời gian không được gia hạn**, không phải thời gian tối đa để nghe/gán nhãn.
- Bộ giám sát phiên xem phiên mất kết nối sau **120 giây** không còn heartbeat (`HEARTBEAT_TIMEOUT_SECONDS` trong `app/services/activity_service.py`); tác vụ kiểm tra chạy mỗi **30 giây**. Việc phát hiện có thể trễ thêm một chu kỳ.
- Đăng xuất giải phóng khóa ngay. Cùng phiên quay lại có thể tiếp tục audio đang giữ. Nếu đăng nhập bằng phiên khác, người dùng có thể chủ động bấm **Nhận lại audio của tôi** hoặc **Nhận lại audio này**; phiên cũ sẽ không được phép xác nhận nữa.
- Admin có thể gỡ một hoặc toàn bộ khóa của Annotator, không xóa nhãn đã lưu.
- Nội dung **đang gõ nhưng chưa nhấn Xác nhận** có thể mất khi đóng trang/mất điện/mất mạng; chỉ nội dung đã được lưu thành công vào SQLite mới được bảo vệ.

**Phím tắt trên màn hình gán nhãn:** `Alt+P` phát/dừng audio, `Ctrl+Enter` xác nhận nhãn. Giao diện sẽ đưa vùng gán nhãn vào tầm nhìn khi chuyển audio.

## 4. Cấu trúc mã nguồn

```text
stt-labeling/
├── app/
│   ├── main.py                         # Khởi động Web, lifespan, bộ giám sát, /health
│   ├── config.py                       # Đường dẫn SQLite, mật khẩu tối thiểu
│   ├── database/
│   │   ├── connection.py               # Engine, session, WAL và khóa ngoại
│   │   ├── models.py                   # Bảng và quan hệ SQLite
│   │   ├── migrations.py               # PRAGMA user_version và các index
│   │   └── init_db.py                  # Lệnh khởi tạo database
│   ├── routes/
│   │   ├── auth.py                     # Đăng nhập, đăng xuất, workspace
│   │   ├── admin.py                    # Quản lý tài khoản, thu hồi khóa
│   │   ├── admin_datasets.py           # Quét dataset và cấp quyền
│   │   ├── labeling.py                 # Phát audio, nhận mẫu, xác nhận, heartbeat
│   │   └── csv_sync.py                 # Lưu/Tải CSV, kết thúc phiên
│   ├── services/
│   │   ├── auth_service.py             # Token phiên, băm mật khẩu, CSRF
│   │   ├── dataset_service.py          # Quét/kiểm tra CSV, chuẩn hóa đường dẫn
│   │   ├── labeling_service.py         # Nhập nhãn, khóa audio, lịch sử, xác nhận
│   │   ├── activity_service.py         # Theo dõi heartbeat/mất kết nối
│   │   └── csv_sync_service.py         # Đồng bộ SQLite → CSV an toàn
│   ├── scripts/
│   │   ├── create_admin.py             # Tạo Admin đầu tiên
│   │   ├── reset_password.py           # Đặt lại mật khẩu qua Terminal
│   │   └── backup_sqlite.py            # Sao lưu SQLite Online Backup API
│   ├── templates/                      # HTML + Jinja2
│   └── static/styles.css               # Giao diện Web
├── data/                                # Audio + CSV; gắn từ ngoài container
├── state/                               # SQLite, file khóa và backups (tạo khi chạy)
├── scripts/prepare_lan.sh              # Kiểm tra UID/GID, quyền thư mục
├── tests/                               # Kiểm thử tự động
├── Dockerfile                           # Python 3.11, Uvicorn port 8000
├── compose.yaml                         # Container + cổng LAN + bind mount
├── requirements.txt                     # Thư viện Python chạy ứng dụng
├── .dockerignore
├── .gitignore
└── README.md                             # Tài liệu duy nhất
```

### Các bảng SQLite chính

| Bảng | Công dụng |
|---|---|
| `users` | Tài khoản, vai trò, mật khẩu đã băm, trạng thái hoạt động |
| `user_sessions` | Phiên đăng nhập, thời hạn phiên, thu hồi phiên |
| `datasets` | Dataset, phiên bản SQLite và phiên bản CSV đã đồng bộ |
| `user_datasets` | Phân quyền Annotator theo dataset |
| `samples` | Đường dẫn audio, nhãn gốc, nhãn hiện tại, trạng thái, phiên bản |
| `sample_locks` | Người/phiên giữ audio và thời hạn khóa |
| `annotation_history` | Nhật ký trước/sau khi xác nhận nhãn |
| `dataset_activities` | Tín hiệu hoạt động của phiên trên từng dataset |
| `dataset_source_states` | Kích thước/thời gian/băm CSV phục vụ đối soát |

SQLite chạy chế độ **WAL** và bật kiểm tra khóa ngoại. Phiên bản schema hiện được quản lý qua `PRAGMA user_version`; nâng cấp hiện tại có phiên bản **1**. Cơ chế này chưa phải hệ thống migration đầy đủ cho mọi thay đổi trong tương lai: khi sửa cấu trúc bảng, cần bổ sung migration tương ứng, **không xóa database để tạo lại**.

### Một số đường dẫn Web

| Đường dẫn | Mục đích |
|---|---|
| `/login` | Đăng nhập |
| `/admin` | Quản lý tài khoản, gỡ khóa audio |
| `/admin/datasets` | Quét dataset, khởi tạo CSV và cấp quyền |
| `/workspace` | Danh sách dataset của Annotator |
| `/labeling/{dataset_id}` | Nghe và gán nhãn |
| `/health` | Kiểm tra ứng dụng và kết nối SQLite; trả `{"status":"ok"}` khi khỏe |

Các tuyến sửa dữ liệu sử dụng `POST`, xác thực phiên và token CSRF. API phát audio cũng kiểm tra quyền dataset; không phục vụ file tùy ý từ máy chủ.

## 5. Triển khai trên server Linux trong mạng LAN

### 5.1. Yêu cầu

- Server **Linux x86-64** (khuyến nghị Debian 12 / Ubuntu 24.04; Docker hỗ trợ kiến trúc khác tùy image).
- Docker Engine và **Docker Compose plugin** (`docker compose`, không phải Compose v1).
- Có địa chỉ **IP LAN** cố định hoặc DHCP reservation; máy người dùng truy cập được IP/port đã chọn.
- Ổ đĩa đủ cho dữ liệu audio, CSV, SQLite và backup. Khuyến nghị đặt `data/` và `state/` trên **đĩa cục bộ** của server; **không đặt một file SQLite dùng chung trên NFS/SMB**.
- Thư mục dữ liệu có quyền đọc/ghi tương thích UID/GID chạy container.

Kiểm tra Docker:

```bash
docker --version
docker compose version
docker info
```

Nếu chưa có Docker, cài Docker Engine + Compose plugin theo tài liệu chính thức:

- Debian: <https://docs.docker.com/engine/install/debian/>
- Ubuntu: <https://docs.docker.com/engine/install/ubuntu/>

**Lưu ý về LXC/Proxmox:** Docker bên trong LXC có thể cần quyền và cấu hình từ máy chủ quản trị. Nếu bị hạn chế, ưu tiên triển khai trong VM Linux có Docker. Không nên thay đổi cấu hình LXC đang chạy công việc quan trọng mà chưa sao lưu.

### 5.2. Đưa mã nguồn lên server

Dùng một trong hai cách:

```bash
# Cách A: clone repository mà bạn có quyền truy cập
git clone <URL_REPOSITORY> stt-labeling
cd stt-labeling
# Nếu mã nguồn đã nằm ở nhánh phát triển:
git switch feature/multi-user-admin
```

Hoặc giải nén gói mã nguồn này thành thư mục `stt-labeling/`, rồi `cd` vào thư mục chứa `compose.yaml`.

**Không chép dữ liệu thật vào GitHub.** Copy dataset vào `data/<dataset_name>/` bằng kênh nội bộ phù hợp (SCP, rsync, ổ đĩa nội bộ...). File ZIP đính kèm này có dataset thử nghiệm; nên thay chúng bằng dữ liệu của bạn khi nghiệm thu xong.

### 5.3. Tạo cấu hình `.env`

Tại thư mục gốc dự án, tạo `.env` (file này đã được bỏ qua khi commit Git). Tạo khóa bí mật bằng Python:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
id -u
id -g
hostname -I
```

Ví dụ **chỉ để minh họa** (`192.168.1.100` phải thay bằng IP LAN thật, `SESSION_SECRET` dùng chuỗi vừa sinh):

```dotenv
SESSION_SECRET=THAY_BANG_CHUOI_NGAU_NHIEN_IT_NHAT_32_KY_TU
APP_UID=1000
APP_GID=1000
APP_BIND_IP=192.168.1.100
APP_PORT=8000
COOKIE_SECURE=false
```

- `SESSION_SECRET`: bắt buộc dài **ít nhất 32 ký tự**, giữ bí mật, không commit.
- `APP_UID`/`APP_GID`: UID và GID **của tài khoản sở hữu `data/`, `state/` trên server**, lấy qua `id -u` / `id -g`; không mặc định máy nào cũng là 1000.
- `APP_BIND_IP`: IP của server sẽ lắng nghe; đặt đúng IP LAN hạn chế mở nhầm trên mọi giao diện. Có thể dùng `0.0.0.0` khi thực sự cần và đã kiểm soát firewall.
- `APP_PORT`: cổng bên ngoài, ví dụ 8000. Trong container Web lắng nghe cổng 8000.
- `COOKIE_SECURE=false`: ứng dụng truy cập qua **HTTP nội bộ**. Nếu sau này dùng HTTPS thật qua proxy thì đổi thành `true` sau khi kiểm tra cấu hình proxy.

Đặt quyền cho `.env`:

```bash
chmod 600 .env
```

### 5.4. Chuẩn bị `data/`, `state/` và quyền file

```bash
mkdir -p data state
bash scripts/prepare_lan.sh
```

Script chỉ **kiểm tra**, không tự động đổi chủ sở hữu. Nếu xuất hiện cảnh báo `root:root` hoặc `Permission denied`, kiểm tra trước:

```bash
id
ls -ld data state
ls -l data/*/label.csv data/*/label_reviewed.csv 2>/dev/null
```

Nếu bạn chắc chắn các thư mục này phải do tài khoản triển khai hiện tại sở hữu, có thể điều chỉnh **có kiểm soát** (không chạy tùy tiện trên dữ liệu nhiều người cùng dùng):

```bash
sudo chown -R "$(id -u):$(id -g)" data state
bash scripts/prepare_lan.sh
```

Không dùng `chmod -R 777`. Nếu `APP_UID`/`APP_GID` trong `.env` **khác** user chạy `prepare_lan.sh`, hãy chạy script với `APP_UID=<uid> APP_GID=<gid>` tương ứng.

### 5.5. Build, khởi tạo SQLite và tạo Admin

```bash
# Kiểm tra cấu hình Compose mà không in SESSION_SECRET
docker compose config --quiet

# Tạo image
docker compose build

# Tạo bảng SQLite nếu chưa có
docker compose run --rm web python -m app.database.init_db

# Chỉ tạo Admin đầu tiên một lần
docker compose run --rm -it web \
  python -m app.scripts.create_admin --username admin
```

Lệnh tạo Admin yêu cầu mật khẩu qua Terminal, **không ghi mật khẩu rõ trong shell history**. Nếu đã có tài khoản `admin` thì không tạo lại.

### 5.6. Chạy dịch vụ và mở cho các máy trong LAN

```bash
docker compose up -d
docker compose ps
docker compose logs --tail=100 web
```

Kiểm tra tại server:

```bash
curl -f http://127.0.0.1:8000/health
```

**Chú ý:** Lệnh `curl 127.0.0.1:8000` chỉ đúng nếu `APP_BIND_IP=0.0.0.0` hoặc `127.0.0.1`. Khi dùng `APP_BIND_IP=192.168.1.100`, kiểm tra qua `http://192.168.1.100:8000/health` hoặc chạy kiểm tra bên trong container:

```bash
docker compose exec web python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

Từ một máy khác cùng LAN, mở:

```text
http://192.168.1.100:8000
```

Thay IP/port bằng cấu hình thật. Nếu không truy cập được, kiểm tra tuyến mạng, firewall của server và chính sách nội bộ. **Chỉ cho dải LAN được phép kết nối cổng này.** Docker published ports có cách xử lý iptables riêng; không nên cho rằng chỉ bật UFW là đã chặn được mọi kết nối ngoài ý muốn. Không cấu hình port-forward công khai từ Internet vào server.

### 5.7. Bắt đầu sử dụng

1. Admin vào `/login`, đăng nhập.
2. Mở `/admin/datasets` → **Quét thư mục data/** → **Khởi tạo CSV còn thiếu** cho dataset đủ audio → cấp quyền Annotator.
3. Admin vào `/admin` để tạo/khóa/mở/xóa tài khoản và quản lý khóa audio.
4. Annotator đăng nhập `/workspace` → chọn dataset → **Nhận audio pending tiếp theo** → nghe và chỉnh nhãn → **Xác nhận & tiếp theo**.
5. Khi cần xuất file: bấm **Lưu CSV** hoặc **Tải CSV về máy**. Một người lưu CSV là xuất kết quả của **cả nhóm** trong dataset đó.

## 6. Vận hành, sao lưu, cập nhật và khôi phục

### Các lệnh thường dùng

```bash
docker compose ps                        # Trạng thái container
docker compose logs -f --tail=100 web    # Theo dõi log
docker compose restart web              # Khởi động lại Web
docker compose down                     # Dừng/xóa container, giữ bind mount
docker compose up -d                     # Khởi động lại
docker compose up -d --build             # Build lại sau khi sửa code
```

`data/` và `state/` được gắn trực tiếp từ host bằng bind mount nên **không bị xóa** khi `docker compose down` hoặc tạo lại container. **Không xóa trực tiếp** các thư mục này; không dùng lệnh dọn dẹp Docker có phạm vi chưa kiểm tra. Với dữ liệu thật, nên có bản backup bên ngoài máy chủ.

### Sao lưu SQLite đúng cách khi bật WAL

```bash
docker compose exec web python -m app.scripts.backup_sqlite
```

Backup được lưu ở `state/backups/` và dùng **SQLite Online Backup API**, an toàn hơn việc chỉ copy `annotations.sqlite3` khi ứng dụng đang ghi. Đồng thời **sao lưu riêng** `data/` gồm audio, `label.csv`, `label_reviewed.csv` bằng cơ chế backup nội bộ. Nếu server chết hoàn toàn mà backup cùng ổ đĩa, backup cũng có thể mất; định kỳ chuyển backup sang vị trí khác.

Kiểm tra toàn vẹn SQLite (khi truy cập file được cấp quyền):

```bash
python3 - <<'PY'
import sqlite3
with sqlite3.connect('state/annotations.sqlite3') as db:
    print(db.execute('PRAGMA integrity_check').fetchone()[0])
PY
```

Kết quả bình thường: `ok`.

### Đặt lại mật khẩu

```bash
docker compose run --rm -it web \
  python -m app.scripts.reset_password --username admin
```

Nếu đã có nhiều tài khoản, thay `admin` bằng username tương ứng. Thao tác đặt lại mật khẩu thu hồi các phiên đăng nhập cũ.

### Cập nhật mã nguồn mà giữ dữ liệu

1. Báo người dùng lưu các nhãn đang nhập. **Sao lưu SQLite và các CSV quan trọng trước**.
2. Lưu mã nguồn/nhánh Git hiện tại để có thể quay lại khi cần.
3. Cập nhật mã nguồn (pull đúng nhánh hoặc thay thế file trong bản phát hành đã kiểm tra).
4. `docker compose up -d --build`; xem logs, `/health`, thử đăng nhập, phát audio và gán nhãn thử.
5. **Không xóa `state/annotations.sqlite3`**, không chạy `docker compose down -v` một cách tùy tiện. Khi schema DB đổi, cần migration tương ứng, không được thay database bằng file rỗng.

### Khôi phục từ backup khi có sự cố

1. Dừng dịch vụ: `docker compose down`.
2. Giữ lại một bản sao của thư mục `state/` hiện tại để điều tra.
3. Khôi phục **bản backup SQLite hợp lệ** vào `state/annotations.sqlite3`, khi không còn tiến trình nào truy cập database. Không để tệp WAL/SHM cũ lẫn với database vừa khôi phục; sao lưu riêng trạng thái cũ trước và xử lý các tệp đi kèm khi dịch vụ đã dừng hoàn toàn.
4. Kiểm tra quyền file, chạy `bash scripts/prepare_lan.sh`, rồi `docker compose up -d`.
5. Kiểm tra `PRAGMA integrity_check`, tài khoản, quyền dataset, nhãn và trạng thái; bấm **Lưu CSV** nếu muốn xuất lại bản kết quả từ SQLite đã khôi phục.

Khôi phục bản backup cũ sẽ làm mất các thay đổi được commit sau thời điểm backup đó. Cần đối chiếu mốc dữ liệu trước khi khôi phục lên server thật.

## 7. Kiểm thử trước khi vận hành

Bộ kiểm thử nằm tại `tests/`, chạy trên dữ liệu tạm, gồm các nhóm đăng nhập/phân quyền, CSV, khóa audio, nhiều phiên đồng thời, Admin gỡ khóa/xóa tài khoản, nâng cấp SQLite và sao lưu.

**Lưu ý về thư viện kiểm thử:** `Dockerfile` không copy `tests/`, `.gitignore`, `compose.yaml` vào image. Các bài kiểm thử Web bằng FastAPI/Starlette TestClient có thể cần **`httpx2` hoặc `httpx`**, tùy phiên bản Starlette được cài. Không cần thêm chúng vào image chạy dịch vụ nếu chỉ dùng cho test. Ví dụ dưới đây sử dụng gói `httpx2` mà Codespaces đã cần trong lần kiểm thử trước (máy/container phải truy cập được PyPI):

```bash
docker compose run --rm \
  -v "$(pwd)/tests:/app/tests:ro" \
  -v "$(pwd)/.gitignore:/app/.gitignore:ro" \
  -v "$(pwd)/compose.yaml:/app/compose.yaml:ro" \
  web sh -c '
    python -m pip install --quiet --no-cache-dir --target /tmp/stt-test-deps httpx2 &&
    PYTHONPATH=/tmp/stt-test-deps:/app python -m unittest discover -s tests -v
  '
```

Nếu đã có gói TestClient tương thích thì bỏ bước `pip install` và chạy thẳng `python -m unittest discover -s tests -v` với ba bind mount trên. Nếu container không ra Internet nhưng host có mạng, tải wheel phù hợp từ host và gắn vào container để cài ngoại tuyến.

**Danh sách nghiệm thu bắt buộc trên Docker thật (không thay thế bằng unit test):**

1. Admin đăng nhập, tạo tài khoản thử, cấp/thu hồi quyền một dataset, khóa/mở/xóa tài khoản thử.
2. Hai Annotator trên hai trình duyệt nhận audio khác nhau; Admin gỡ khóa, phiên cũ không thể lưu đè.
3. Chuyển 5–10 audio liên tiếp: trình phát và ô nhãn nằm trong tầm nhìn, không cần cuộn tìm.
4. Xác nhận nhãn có nội dung và nhãn trống có chủ đích; nhãn xuất ra CSV có trạng thái `confirmed`.
5. Xác nhận nhãn nhưng **chưa** bấm Lưu CSV: SQLite thay đổi, CSV chưa bị viết lại. Bấm Lưu CSV thì kết quả chung xuất đầy đủ.
6. Thử đăng xuất, đóng tab, mất kết nối; kiểm tra khóa hết hạn/được thu hồi, nhãn **đã xác nhận** vẫn còn.
7. Thử `docker compose restart web` và `docker compose down && docker compose up -d`: SQLite, CSV, tài khoản và phân quyền vẫn còn.
8. Thử bổ sung audio **vào cuối** dataset trên dữ liệu giả lập; kiểm tra không mất nhãn cũ. Thử đảo thứ tự để xác nhận ứng dụng từ chối ghi đè.
9. Truy cập URL bằng một máy thứ hai thực sự trong LAN, kiểm tra audio và quyền truy cập.

> Kiểm thử cục bộ của bản tài liệu hóa này được mô tả ở mục 9. **Không khẳng định đã kiểm thử Docker thực tế trên server của bạn.**

## 8. Xử lý sự cố thường gặp

| Hiện tượng | Cách kiểm tra / xử lý |
|---|---|
| `Permission denied` với CSV/SQLite | Kiểm tra `id`, `ls -l`, `APP_UID`/`APP_GID`, bind mount và `scripts/prepare_lan.sh`. Không dùng `chmod 777`. |
| CSV không đổi sau khi nhấn **Xác nhận** | **Đúng thiết kế**: thao tác này chỉ lưu SQLite. Dùng **Lưu CSV** hoặc **Tải CSV**. |
| Audio bị giữ sau khi thoát | Kiểm tra còn tab/phiên khác, heartbeat và TTL 120 giây. Thử **Nhận lại audio của tôi** hoặc nhờ Admin gỡ khóa. |
| Thông báo khóa đã hết hạn | Khóa không được gia hạn hoặc người khác đã nhận lại/gỡ khóa; mở lại mẫu, không gửi dữ liệu bằng phiên bản cũ. |
| Không phát được audio | Kiểm tra đường dẫn trong CSV, file có tồn tại, quyền đọc, và codec được trình duyệt hỗ trợ. |
| Không nhìn thấy dataset | Admin chưa quét/khởi tạo CSV hoặc chưa cấp quyền; kiểm tra trang `/admin/datasets`. |
| CSV bị sửa ngoài ứng dụng | Sao lưu file và đối chiếu thay đổi; không cố ép ghi đè từ SQLite trước khi giải quyết xung đột. |
| `docker compose config` in khóa bí mật | Dùng `docker compose config --quiet` khi chỉ cần kiểm tra cú pháp. |
| Test lỗi thiếu `.gitignore`/`compose.yaml` | Gắn hai file vào `/app` như lệnh test ở trên. |
| TestClient thiếu thư viện | Cài gói kiểm thử tương thích vào container test tạm; nếu lỗi DNS, xử lý mạng hoặc dùng wheel ngoại tuyến. |
| Lỗi kết nối port từ máy khác | Kiểm tra IP `APP_BIND_IP`, port `APP_PORT`, firewall, VLAN và chính sách LAN. |
| SQLite báo `database is locked` | Xem tác vụ nào đang giữ giao dịch dài, tải hệ thống và thời gian `busy_timeout`; không mở SQLite qua file share mạng. |

## 9. Rà soát mã nguồn, kết quả kiểm thử và giới hạn

Đối chiếu với bản ZIP `stt-labeling-feature-multi-user-admin(3).zip` (13/10/2026):

- Đã rà soát cấu trúc toàn bộ thư mục `app/`, routes, services, các bảng SQLite, scripts, mẫu CSV, Compose và tests.
- Cấu hình khóa hiện là **120 giây** (`LEASE_SECONDS = 2 * 60`), heartbeat gia hạn khóa 30 giây/lần; phiên mất kết nối được phát hiện sau khoảng 120–150 giây khi server còn chạy.
- Kiểm tra lại `.gitignore`: bản ZIP đầu vào **vô tình chú thích** quy tắc `/data/` thành `#/data/`; bản này đã sửa và tăng độ chặt bài kiểm thử để không bỏ lọt lỗi tương tự. Đồng thời sửa chú thích cũ “10 phút” tại API heartbeat thành “2 phút”.
- **31/31 bài kiểm thử** đã chạy thành công trên bản sao của mã nguồn bằng Python cục bộ, với **mô-đun `pwdlib` thay thế bằng Argon2 chỉ trong môi trường kiểm thử** do môi trường hiện tại chưa có `pwdlib`; mô-đun thay thế **không** được đưa vào ZIP dự án. Các bài kiểm thử gồm cả Web TestClient và các ca bảo vệ dữ liệu, nhưng đây **không** phải kết quả Docker thật.
- Chưa xác minh trực tiếp việc mở cổng LAN trên server công ty, UI trình duyệt thực, hiệu năng dataset rất lớn hay tính tương thích của toàn bộ codec audio. Cần thực hiện danh sách nghiệm thu ở mục 7 trước khi dùng dữ liệu thật.

### Những giới hạn/điểm cân nhắc nếu mở rộng

- Một server, một file SQLite trên **đĩa cục bộ** và một dịch vụ Web là cấu hình mục tiêu. Nếu chuyển sang nhiều server/worker ghi tập trung quy mô lớn, nên đánh giá chuyển sang PostgreSQL và dịch vụ điều phối xuất CSV chuyên biệt.
- Đọc danh sách mẫu đã phân trang và chống trùng đường dẫn dựa trên SQLite tạm khi CSV rất lớn; chưa có benchmark thực tế với hàng triệu mẫu.
- **Xóa** tài khoản Annotator giữ nhãn và lịch sử, nhưng khóa ngoại `updated_by`/`annotation_history.user_id` chuyển thành `NULL`; nếu cần truy nguyên tên người sửa sau khi xóa thì nên giữ tài khoản ở trạng thái **khóa** thay vì xóa hẳn.
- Mật khẩu tối thiểu 4 ký tự, không giới hạn thử sai là lựa chọn vận hành nội bộ; không nên công khai Web lên Internet hoặc để mạng LAN không tin cậy truy cập. Dùng hạn chế mạng và quản lý tài khoản phù hợp.
- Phát hiện đóng tab/mất mạng là **best-effort**; không coi `label_reviewed.csv` là bản tức thời sau mỗi thao tác. **Nhãn được xác nhận trong SQLite** mới là dữ liệu chính thức.

---

**Quy ước tài liệu:** Dự án chỉ duy trì **một file hướng dẫn `README.md`**. Các tài liệu giai đoạn, báo cáo QA và hướng dẫn nâng cấp rời rạc đã được gộp vào đây; không xóa mã nguồn, script triển khai, bộ kiểm thử hoặc dữ liệu mẫu trong ZIP.
