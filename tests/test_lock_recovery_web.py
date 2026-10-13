"""Kiểm thử Web việc tự nhả khóa khi logout và chủ động nhận lại phiên cũ."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class LockRecoveryWebTests(unittest.TestCase):
    def test_logout_then_resume_via_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env['DATA_DIR'] = str(Path(temporary) / 'data')
            env['STATE_DIR'] = str(Path(temporary) / 'state')
            env['SESSION_SECRET'] = 'test-session-secret-over-32-characters-long'
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get('PYTHONPATH', '')
            snippet = r'''
from pathlib import Path
import os
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.database.connection import SessionLocal
from app.database.models import Dataset, Sample, SampleLock, User, UserDataset, UserSession
from app.services.auth_service import PASSWORD_HASHER, get_csrf_token, AuthContext, hash_session_token
from app.services.dataset_service import initialize_dataset_files

folder=Path(os.environ['DATA_DIR'])/'test'
folder.mkdir(parents=True)
(folder/'one.wav').write_bytes(b'RIFF' + b'0'*200)
(folder/'label.csv').write_text('audio_path,label\none.wav,nhãn gốc\n',encoding='utf-8')
initialize_dataset_files('test')
with TestClient(app) as bootstrap:
    with SessionLocal.begin() as db:
        db.add(User(id=1, username='u1',role='annotator',password_hash=PASSWORD_HASHER.hash('1234')))
        db.add(Dataset(id=1,folder_name='test'))
        db.add(UserDataset(user_id=1,dataset_id=1))

def login():
    client=TestClient(app)
    response=client.post('/login',data={'username':'u1','password':'1234'},follow_redirects=False)
    assert response.status_code==303,(response.status_code,response.text)
    raw=client.cookies['stt_session']
    with SessionLocal() as db:
        session=db.scalar(select(UserSession).where(UserSession.session_token_hash==hash_session_token(raw)))
        user=db.get(User,session.user_id)
        token=get_csrf_token(AuthContext(user=user,session=session,raw_token=raw))
        return client,session.id,token

first,sid1,token1=login()
assert first.get('/labeling/1').status_code==200
assert first.post('/labeling/1/next',data={'csrf_token':token1},follow_redirects=False).status_code==303
with SessionLocal() as db:
    sample_id=db.scalar(select(Sample.id).where(Sample.dataset_id==1))
    assert db.get(SampleLock,sample_id).session_id==sid1
assert first.post('/logout',data={'csrf_token':token1},follow_redirects=False).status_code==303
with SessionLocal() as db:
    assert db.get(SampleLock,sample_id) is None

second,sid2,token2=login()
assert second.post('/labeling/1/next',data={'csrf_token':token2},follow_redirects=False).status_code==303
with SessionLocal() as db:
    assert db.get(SampleLock,sample_id).session_id==sid2

# Một trình duyệt thứ hai đăng nhập bằng cùng tài khoản, chủ động nhận lại mẫu.
third,sid3,token3=login()
page=third.get('/labeling/1',params={'sample_id':sample_id})
assert page.status_code==200
assert 'Nhận lại audio này' in page.text
response=third.post('/labeling/1/resume',data={'csrf_token':token3,'sample_id':str(sample_id)},follow_redirects=False)
assert response.status_code==303,(response.status_code,response.text)
with SessionLocal() as db:
    assert db.get(SampleLock,sample_id).session_id==sid3

# Phiên cũ không được ghi đè sau khi khóa đổi chủ sở hữu.
old=second.post('/labeling/1/confirm',data={'csrf_token':token2,'sample_id':str(sample_id),'version':'0','label':'GHI ĐÈ'},follow_redirects=False)
assert old.status_code==200 and 'Khóa' in old.text
new=third.post('/labeling/1/confirm',data={'csrf_token':token3,'sample_id':str(sample_id),'version':'0','label':'NHÃN MỚI'},follow_redirects=False)
assert new.status_code==303,(new.status_code,new.text)
with SessionLocal() as db:
    sample=db.get(Sample,sample_id)
    assert sample.current_label=='NHÃN MỚI' and sample.status=='confirmed'
'''
            result=subprocess.run([sys.executable,'-c',textwrap.dedent(snippet)],
                                  env=env,capture_output=True,text=True,timeout=50)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':
    unittest.main()
