"""Kiểm thử tích hợp Admin: gỡ audio và xóa người dùng qua HTTP."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class AdminWebTests(unittest.TestCase):
    def test_release_and_delete_account_safely(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            env = os.environ.copy()
            env["STATE_DIR"] = str(root / "state")
            env["DATA_DIR"] = str(root / "data")
            env["SESSION_SECRET"] = "test-password-cookie-secret-longer-than-32-characters"
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
            script = r'''
from pathlib import Path
import os,time
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.database.models import Dataset, Sample, SampleLock, User, UserSession, AnnotationHistory
from app.database.connection import SessionLocal
from app.services.auth_service import PASSWORD_HASHER, get_csrf_token, AuthContext, hash_session_token
from app.services.dataset_service import initialize_dataset_files
root=Path(os.environ['DATA_DIR'])/'test';root.mkdir(parents=True)
(root/'one.wav').write_bytes(b'RIFF' + b'0'*200)
(root/'label.csv').write_text('audio_path,label\none.wav,nhãn gốc\n',encoding='utf-8')
with TestClient(app) as container_client:
    with SessionLocal.begin() as db:
        db.add_all([
          User(username='admin',role='admin',password_hash=PASSWORD_HASHER.hash('1234')),
          User(username='user01',role='annotator',password_hash=PASSWORD_HASHER.hash('1234')),
        ])
    def login(username):
        client=TestClient(app)
        resp=client.post('/login',data={'username':username,'password':'1234'},follow_redirects=False)
        assert resp.status_code==303,(resp.status_code,resp.text)
        raw=client.cookies['stt_session']
        with SessionLocal() as db:
            sess=db.scalar(select(UserSession).where(UserSession.session_token_hash==hash_session_token(raw)))
            user=db.get(User,sess.user_id)
            token=get_csrf_token(AuthContext(user=user,session=sess,raw_token=raw))
        return client,token
    admin,tok=login('admin')
    user,user_tok=login('user01')
    r=admin.post('/admin/datasets/scan',data={'csrf_token':tok},follow_redirects=False)
    assert r.status_code==303
    with SessionLocal() as db:
        ds=db.scalar(select(Dataset).where(Dataset.folder_name=='test'))
        uid=db.scalar(select(User.id).where(User.username=='user01'))
        did=ds.id
    assert admin.post(f'/admin/datasets/{did}/initialize',data={'csrf_token':tok},follow_redirects=False).status_code==303
    assert admin.post(f'/admin/datasets/{did}/permissions',data={'csrf_token':tok,'user_ids':str(uid)},follow_redirects=False).status_code==303
    assert user.get(f'/labeling/{did}').status_code==200
    assert user.post(f'/labeling/{did}/next',data={'csrf_token':user_tok},follow_redirects=False).status_code==303
    with SessionLocal() as db:
        sample=db.scalar(select(Sample).where(Sample.dataset_id==did));sample_id=sample.id
        assert db.get(SampleLock,sample_id) is not None
    html=admin.get('/admin').text
    assert 'Gỡ audio' in html and 'Xóa tài khoản' in html
    # user cannot use admin endpoints
    assert user.post(f'/admin/users/{uid}/release-locks',data={'csrf_token':user_tok},follow_redirects=False).status_code==403
    # CSRF protected
    assert admin.post(f'/admin/users/{uid}/release-locks',data={'csrf_token':'bad'},follow_redirects=False).status_code==403
    assert admin.post(f'/admin/users/{uid}/release-locks',data={'csrf_token':tok,'sample_id':str(sample_id)},follow_redirects=False).status_code==303
    with SessionLocal() as db:
        assert db.get(SampleLock,sample_id) is None
    # Saving after admin freed the lock must fail
    failed=user.post(f'/labeling/{did}/confirm',data={'csrf_token':user_tok,'sample_id':sample_id,'version':0,'label':'nhãn thay'},follow_redirects=False)
    assert failed.status_code==200 and 'Khóa' in failed.text
    # Re-lock then confirm so history and labels are retained when deleting account
    assert user.post(f'/labeling/{did}/open',data={'csrf_token':user_tok,'sample_id':str(sample_id)},follow_redirects=False).status_code==303
    assert user.post(f'/labeling/{did}/confirm',data={'csrf_token':user_tok,'sample_id':sample_id,'version':0,'label':'đã xác nhận'},follow_redirects=False).status_code==303
    wrong=admin.post(f'/admin/users/{uid}/delete',data={'csrf_token':tok,'confirm_username':'someone-else'},follow_redirects=False)
    assert wrong.status_code==303
    with SessionLocal() as db: assert db.get(User,uid) is not None
    assert admin.post(f'/admin/users/{uid}/delete',data={'csrf_token':tok,'confirm_username':'user01'},follow_redirects=False).status_code==303
    with SessionLocal() as db:
        assert db.get(User,uid) is None
        assert db.get(Sample,sample_id).current_label=='đã xác nhận'
        assert db.scalar(select(AnnotationHistory).where(AnnotationHistory.sample_id==sample_id)) is not None
    assert user.get('/workspace',follow_redirects=False).status_code==303
'''
            result = subprocess.run([sys.executable, "-c", textwrap.dedent(script)], env=env,
                                    capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
