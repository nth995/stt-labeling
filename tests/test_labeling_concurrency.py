"""Kiểm thử tranh chấp mẫu bằng SQLite tạm, không dùng dữ liệu thật."""

import csv
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.models import Base, Dataset, Sample, SampleLock, User, UserSession


class LabelingConcurrencyTests(unittest.TestCase):
    def setUp(self):
        from app.services import labeling_service
        from app.services.labeling_service import LabelingError
        self.labeling_service = labeling_service
        self.LabelingError = LabelingError
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(f"sqlite+pysqlite:///{self.root / 'test.sqlite3'}")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            db.add(Dataset(id=1, folder_name="test"))
            db.add_all([
                User(id=1, username="u1", password_hash="x", role="annotator"),
                User(id=2, username="u2", password_hash="x", role="annotator"),
            ])
            db.add_all([
                UserSession(id=1, user_id=1, session_token_hash="session-1", expires_at=int(time.time()) + 3600),
                UserSession(id=2, user_id=2, session_token_hash="session-2", expires_at=int(time.time()) + 3600),
            ])
            db.add_all([
                Sample(id=1, dataset_id=1, position=0, audio_path="one.wav", original_label="gốc", current_label="gốc", status="pending"),
                Sample(id=2, dataset_id=1, position=1, audio_path="two.wav", original_label="", current_label="", status="pending"),
            ])
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_two_sessions_claim_distinct_samples(self):
        """Hai phiên tự nhận pending không nhận trùng mẫu."""
        with Session(self.engine) as a, Session(self.engine) as b:
            one = self.labeling_service.claim_next(a, 1, 1, 1)
            two = self.labeling_service.claim_next(b, 1, 2, 2)
            self.assertEqual(one.id, 1)
            self.assertEqual(two.id, 2)
            self.assertNotEqual(one.id, two.id)
            self.assertFalse(self.labeling_service.try_claim(b, b.get(Sample, 1), 2, 2))

    def test_stale_editor_does_not_override_confirmed_label(self):
        """Khóa hết hạn không cho phép ghi đè lên nhãn mới của người khác."""
        with Session(self.engine) as a, Session(self.engine) as b:
            self.assertTrue(self.labeling_service.try_claim(a, a.get(Sample, 1), 1, 1))
            previous_version = a.get(Sample, 1).version
            with Session(self.engine) as admin:
                admin.get(SampleLock, 1).expires_at = int(time.time()) - 1
                admin.commit()
            self.assertTrue(self.labeling_service.try_claim(b, b.get(Sample, 1), 2, 2))
            self.labeling_service.confirm_sample(b, b.get(Sample, 1), 2, 2, 0, "người B sửa", False)
            with self.assertRaises(self.LabelingError):
                self.labeling_service.confirm_sample(a, a.get(Sample, 1), 1, 1, previous_version, "người A ghi đè", False)
        with Session(self.engine) as check:
            sample = check.get(Sample, 1)
            self.assertEqual(sample.current_label, "người B sửa")
            self.assertEqual(sample.original_label, "gốc")
            self.assertEqual(sample.status, "confirmed")
            self.assertEqual(check.get(Dataset, 1).version, 1)

    def test_deliberately_empty_label_is_confirmed(self):
        """Không có lời nói vẫn có trạng thái confirmed với nhãn rỗng."""
        with Session(self.engine) as db:
            self.assertTrue(self.labeling_service.try_claim(db, db.get(Sample, 2), 1, 1))
            self.labeling_service.confirm_sample(db, db.get(Sample, 2), 1, 1, 0, "", True)
            self.assertEqual(db.get(Sample, 2).status, "confirmed")
            self.assertEqual(db.get(Sample, 2).current_label, "")

    def test_csv_import_is_transactional_and_handles_many_rows(self):
        """Nhập 1200 nhãn từ CSV theo batch và giữ đúng nhãn gốc."""
        directory = self.root / "data" / "test"
        directory.mkdir(parents=True)
        with (directory / "label.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["audio_path", "label"])
            for index in range(1200):
                writer.writerow([f"audio/{index:04d}.wav", f"nhãn gốc {index}"])
        with (directory / "label_reviewed.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["audio_path", "label", "status"])
            for index in range(1200):
                writer.writerow([f"audio/{index:04d}.wav", f"nhãn mới {index}", "pending"])
        # Dùng bảng dataset khác để không có sample ban đầu.
        with Session(self.engine) as db:
            db.add(Dataset(id=2, folder_name="import"))
            db.commit()
        mock_inspection = type("Inspection", (), {"ready": True})()
        with patch.object(self.labeling_service, "inspect_dataset", return_value=mock_inspection), \
             patch.object(self.labeling_service, "dataset_directory", return_value=directory):
            with Session(self.engine) as db:
                self.labeling_service.ensure_samples(db, db.get(Dataset, 2))
                rows = db.scalars(select(Sample).where(Sample.dataset_id == 2).order_by(Sample.position)).all()
                self.assertEqual(len(rows), 1200)
                self.assertEqual(rows[-1].current_label, "nhãn mới 1199")
                self.assertEqual(rows[-1].original_label, "nhãn gốc 1199")


if __name__ == "__main__":
    unittest.main()
