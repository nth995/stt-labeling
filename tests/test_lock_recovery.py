"""Kiểm thử hồi quy cho khóa audio khi đăng xuất, đóng tab và đăng nhập lại."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import (
    Base, Dataset, DatasetActivity, Sample, SampleLock, User, UserSession
)


class LockRecoveryTests(unittest.TestCase):
    def setUp(self):
        from app.services import activity_service, labeling_service
        self.activity_service = activity_service
        self.labeling_service = labeling_service
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite+pysqlite:///{Path(self.directory.name) / 'test.db'}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine)
        with self.Session.begin() as db:
            db.add(Dataset(id=1, folder_name='test'))
            db.add_all([
                User(id=1, username='user01', password_hash='irrelevant', role='annotator'),
                User(id=2, username='user02', password_hash='irrelevant', role='annotator'),
            ])
            for session_id, uid in [(11, 1), (12, 1), (21, 2)]:
                db.add(UserSession(id=session_id, user_id=uid,
                                   session_token_hash=f'session_{session_id}',
                                   expires_at=int(time.time()) + 3600))
            db.add_all([
                Sample(id=i, dataset_id=1, position=i-1, audio_path=f'{i}.wav',
                       original_label='nhãn gốc', current_label='nhãn gốc', status='pending')
                for i in range(1, 4)
            ])

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def test_same_session_can_resume_its_existing_pending_audio(self):
        """Đóng tab rồi trở lại bằng cookie cũ không được bỏ qua mẫu đang giữ."""
        with self.Session() as db:
            first = self.labeling_service.claim_next(db, 1, 1, 11)
            again = self.labeling_service.claim_next(db, 1, 1, 11)
            self.assertEqual(first.id, 1)
            self.assertEqual(again.id, 1)
            self.assertEqual(db.get(SampleLock, 1).session_id, 11)

    def test_explicit_next_skips_current_audio_but_keeps_other_users_safe(self):
        """Bấm Next thực sự đổi mẫu và có thể bỏ qua mẫu trước."""
        with self.Session() as db:
            first = self.labeling_service.claim_next(db, 1, 1, 11)
            second = self.labeling_service.claim_next(db, 1, 1, 11, exclude_sample_id=first.id)
            self.assertEqual(second.id, 2)
            self.labeling_service.release_lease(db, first.id, 11)
            self.assertIsNone(db.get(SampleLock, 1))
            self.assertEqual(db.get(SampleLock, 2).session_id, 11)

    def test_logout_releases_only_its_sessions_locks(self):
        """Khi đăng xuất, mẫu được trả lại, nhưng phiên khác không bị ảnh hưởng."""
        with self.Session() as db:
            self.labeling_service.try_claim(db, db.get(Sample, 1), 1, 11)
            self.labeling_service.try_claim(db, db.get(Sample, 2), 2, 21)
            self.assertEqual(self.labeling_service.release_session_locks(db, 11), 1)
            self.assertIsNone(db.get(SampleLock, 1))
            self.assertIsNotNone(db.get(SampleLock, 2))
            reclaimed = self.labeling_service.claim_next(db, 1, 1, 12)
            self.assertEqual(reclaimed.id, 1)

    def test_explicit_reclaim_own_lock_from_old_session(self):
        """Đăng nhập từ trình duyệt mới có thể chủ động nhận lại khóa của mình."""
        with self.Session() as db:
            self.labeling_service.try_claim(db, db.get(Sample, 1), 1, 11)
            resumed = self.labeling_service.resume_own_sample(db, 1, 1, 12, sample_id=1)
            self.assertEqual(resumed.id, 1)
            self.assertEqual(db.get(SampleLock, 1).session_id, 12)
            with self.assertRaises(self.labeling_service.LabelingError):
                self.labeling_service.require_lease(db, 1, 11)
            self.assertEqual(self.labeling_service.require_lease(db, 1, 12).user_id, 1)

    def test_cannot_reclaim_another_accounts_audio(self):
        """Chỉ tài khoản sở hữu khóa có quyền nhận lại, không vượt phân quyền."""
        with self.Session() as db:
            self.labeling_service.try_claim(db, db.get(Sample, 1), 2, 21)
            self.assertIsNone(self.labeling_service.resume_own_sample(db, 1, 1, 12, sample_id=1))
            self.assertEqual(db.get(SampleLock, 1).session_id, 21)

    def test_heartbeat_timeout_releases_abandoned_lock(self):
        """Khi ngắt mạng vượt ngưỡng và đã xuất CSV thì giải phóng khóa."""
        cutoff = int(time.time()) - self.activity_service.HEARTBEAT_TIMEOUT_SECONDS - 15
        with self.Session.begin() as db:
            db.add(DatasetActivity(session_id=11, dataset_id=1,
                                   last_seen_at=cutoff, ended_at=None))
        with self.Session() as db:
            self.labeling_service.try_claim(db, db.get(Sample, 1), 1, 11)
        with patch.object(self.activity_service, 'SessionLocal', self.Session), \
             patch.object(self.activity_service, 'sync_dataset_csv') as sync:
            self.activity_service.sync_disconnected_sessions()
            sync.assert_called_once_with(1)
        with self.Session() as db:
            self.assertIsNone(db.get(SampleLock, 1))
            self.assertIsNotNone(db.get(DatasetActivity, (11, 1)).ended_at)


if __name__ == '__main__':
    unittest.main()
