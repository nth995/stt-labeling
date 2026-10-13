#!/usr/bin/env bash
# Chạy trên HOST (Codespaces/server LAN) trước khi docker compose up.
set -euo pipefail
cd "$(dirname "$0")/.."
APP_UID="${APP_UID:-$(id -u)}"
APP_GID="${APP_GID:-$(id -g)}"
mkdir -p data state
printf 'UID/GID chạy container: %s:%s\n' "$APP_UID" "$APP_GID"
for folder in data state; do
  owner="$(stat -c '%u:%g' "$folder")"
  if [[ "$owner" != "$APP_UID:$APP_GID" ]]; then
    echo "CẢNH BÁO: $folder thuộc $owner, không phải $APP_UID:$APP_GID." >&2
    echo "Kiểm tra quyền rồi chạy: sudo chown -R $APP_UID:$APP_GID $folder" >&2
    exit 1
  fi
  if [[ ! -w "$folder" ]]; then
    echo "CẢNH BÁO: Không có quyền ghi $folder" >&2
    exit 1
  fi
done
# Kiểm tra các file cần được đọc/ghi, tránh root:root 600 từ lần chạy cũ.
shopt -s nullglob
for file in data/*/label.csv data/*/label_reviewed.csv state/annotations.sqlite3; do
  if [[ ! -r "$file" ]]; then
    echo "Không thể đọc $file; kiểm tra lại UID/GID và quyền file." >&2
    exit 1
  fi
  if [[ "$file" != */label.csv && ! -w "$file" ]]; then
    echo "Không thể ghi $file; kiểm tra lại UID/GID và quyền file." >&2
    exit 1
  fi
done
echo 'OK: data/ và state/ sẵn sàng cho Docker.'
