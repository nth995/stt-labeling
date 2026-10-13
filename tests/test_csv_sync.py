"""Kiểm thử độc lập SQLite → CSV, không dùng dữ liệu thật của người dùng."""

import csv
import os
import tempfile
import time
import unittest
from pathlib import Path


class CsvSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Tạo database riêng trước khi import các module kết nối."""
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        os.environ["STATE_DIR"] = str(cls.root / "state")
        os.environ["DATA_DIR"] = str(cls.root / "data")

        from app.database.connection import SessionLocal, engine, initialize_database
        from app.database.models import Dataset, DatasetActivity, Sample, User, UserSession
        from app.services import activity_service, csv_sync_service
        cls.SessionLocal = SessionLocal
        cls.engine = engine
        cls.Dataset = Dataset
        cls.DatasetActivity = DatasetActivity
        cls.Sample = Sample
        cls.User = User
        cls.UserSession = UserSession
        cls.activity = activity_service
        cls.csv_sync = csv_sync_service
        initialize_database()

        (cls.root / "data" / "testdata" / "audio").mkdir(parents=True)
        (cls.root / "data" / "testdata" / "audio" / "1.wav").write_bytes(b"RIFF")
        (cls.root / "data" / "testdata" / "audio" / "2.wav").write_bytes(b"RIFF")
        (cls.root / "data" / "testdata" / "label.csv").write_text(
            "audio_path,label\naudio/1.wav,xin chào\naudio/2.wav,\n", encoding="utf-8"
        )
        (cls.root / "data" / "testdata" / "label_reviewed.csv").write_text(
            "audio_path,label,status\naudio/1.wav,xin chào,pending\naudio/2.wav,,pending\n", encoding="utf-8"
        )
        with SessionLocal.begin() as db:
            db.add(Dataset(id=1, folder_name="testdata", version=0, csv_synced_version=0))
            db.add(User(id=1, username="u1", password_hash="example", role="annotator"))
            db.add(UserSession(id=1, user_id=1, session_token_hash="token", expires_at=int(time.time()) + 3600))
            db.add(Sample(id=1, dataset_id=1, position=0, audio_path="audio/1.wav", original_label="xin chào", current_label="xin chào", status="pending"))
            db.add(Sample(id=2, dataset_id=1, position=1, audio_path="audio/2.wav", original_label="", current_label="", status="pending"))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls.temp.cleanup()

    def reviewed_rows(self):
        """Đọc CSV kết quả để đối chiếu nội dung thật trên ổ đĩa."""
        with (self.root / "data" / "testdata" / "label_reviewed.csv").open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_01_sql_save_does_not_modify_csv(self):
        """Đổi nhãn trong SQLite chưa đồng nghĩa xuất CSV."""
        with self.SessionLocal.begin() as db:
            db.get(self.Sample, 1).current_label = "xin chào bạn"
            db.get(self.Sample, 1).status = "confirmed"
            db.get(self.Dataset, 1).version = 1
        self.assertEqual(self.reviewed_rows()[0]["label"], "xin chào")

    def test_02_manual_export_and_skip(self):
        """Xuất CSV cập nhật mọi mẫu; không thay đổi CSV gốc."""
        first = self.csv_sync.sync_dataset_csv(1)
        self.assertFalse(first.skipped)
        self.assertEqual(first.sample_count, 2)
        self.assertEqual(self.reviewed_rows()[0]["label"], "xin chào bạn")
        self.assertEqual(self.reviewed_rows()[0]["status"], "confirmed")
        self.assertTrue(self.csv_sync.sync_dataset_csv(1).skipped)
        self.assertIn("xin chào", (self.root / "data" / "testdata" / "label.csv").read_text())

    def test_03_export_keeps_dirty_marker_for_concurrent_edit(self):
        """Nhãn thay đổi lúc đang ghi CSV phải còn đánh dấu chưa đồng bộ."""
        with self.SessionLocal.begin() as db:
            db.get(self.Sample, 1).current_label = "bản chụp đầu"
            db.get(self.Dataset, 1).version = 2
        original_replace = self.csv_sync.os.replace
        modified = []

        def replace_with_concurrent_write(src, dst):
            # Trong WAL, writer khác vẫn có thể sửa SQLite khi CSV đang xuất.
            with self.SessionLocal.begin() as db:
                db.get(self.Sample, 1).current_label = "bản cập nhật sau"
                db.get(self.Dataset, 1).version = 3
            modified.append(True)
            return original_replace(src, dst)

        from unittest.mock import patch
        with patch.object(self.csv_sync.os, "replace", side_effect=replace_with_concurrent_write):
            result = self.csv_sync.sync_dataset_csv(1)
        self.assertTrue(modified)
        self.assertEqual(result.exported_version, 2)
        self.assertEqual(result.latest_version, 3)
        self.assertEqual(self.reviewed_rows()[0]["label"], "bản chụp đầu")
        with self.SessionLocal() as db:
            row = db.get(self.Dataset, 1)
            self.assertEqual((row.version, row.csv_synced_version), (3, 2))
        self.csv_sync.sync_dataset_csv(1)
        self.assertEqual(self.reviewed_rows()[0]["label"], "bản cập nhật sau")

    def test_04_activity_timeout_exports(self):
        """Mất heartbeat quá 120 giây phải xuất CSV tự động."""
        with self.SessionLocal.begin() as db:
            db.get(self.Sample, 2).current_label = "số tài khoản"
            db.get(self.Sample, 2).status = "confirmed"
            db.get(self.Dataset, 1).version = 4
        with self.SessionLocal() as db:
            self.activity.mark_activity(db, 1, 1)
            record = db.get(self.DatasetActivity, (1, 1))
            record.last_seen_at -= 200
            db.commit()
        self.activity.sync_disconnected_sessions()
        self.assertEqual(self.reviewed_rows()[1]["label"], "số tài khoản")
        with self.SessionLocal() as db:
            self.assertIsNotNone(db.get(self.DatasetActivity, (1, 1)).ended_at)

    def test_05_two_exporters_do_not_corrupt_csv(self):
        """Hai request cùng lúc phải tạo một file CSV hoàn chỉnh."""
        from concurrent.futures import ThreadPoolExecutor
        with self.SessionLocal.begin() as db:
            db.get(self.Sample, 1).current_label = "đồng thời"
            db.get(self.Dataset, 1).version = 5
        with ThreadPoolExecutor(max_workers=2) as pool:
            exports = list(pool.map(lambda _: self.csv_sync.sync_dataset_csv(1), range(2)))
        self.assertEqual(len(exports), 2)
        self.assertEqual(self.reviewed_rows()[0]["label"], "đồng thời")
        self.assertEqual(len(self.reviewed_rows()), 2)
        with self.SessionLocal() as db:
            self.assertEqual(db.get(self.Dataset, 1).csv_synced_version, 5)

    def test_06_export_error_does_not_mark_synced(self):
        """Lỗi thay thế file không được đánh dấu phiên bản đã xuất."""
        with self.SessionLocal.begin() as db:
            db.get(self.Sample, 1).current_label = "nội dung cần xuất"
            db.get(self.Dataset, 1).version = 6
        from unittest.mock import patch
        with patch.object(self.csv_sync.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.csv_sync.sync_dataset_csv(1)
        with self.SessionLocal() as db:
            row = db.get(self.Dataset, 1)
            self.assertEqual(row.version, 6)
            self.assertLess(row.csv_synced_version, 6)
        self.csv_sync.sync_dirty_on_startup()
        self.assertEqual(self.reviewed_rows()[0]["label"], "nội dung cần xuất")


if __name__ == "__main__":
    unittest.main()
