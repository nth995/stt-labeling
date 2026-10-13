"""Kiểm thử hồi quy các lỗi dữ liệu và điều phối mẫu (mỗi kịch bản tách SQLite)."""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HardeningTests(unittest.TestCase):
    def run_isolated(self, body: str) -> None:
        """Chạy riêng process để mỗi test có DATA_DIR, STATE_DIR độc lập."""
        with tempfile.TemporaryDirectory() as folder:
            script = '''
import csv
import time
from pathlib import Path
from sqlalchemy import select
from app.database.connection import initialize_database, SessionLocal, engine
from app.database.models import Dataset, Sample, SampleLock, User, UserSession, AnnotationHistory
from app.services.dataset_service import initialize_dataset_files, iter_label_rows, inspect_dataset
from app.services.labeling_service import ensure_samples, try_claim, require_lease, LabelingError
from app.services.csv_sync_service import sync_dataset_csv
from app.services.auth_service import create_session
initialize_database()
directory = Path(__import__('os').environ['DATA_DIR']) / 'test'
directory.mkdir(parents=True)
'''
            environment = os.environ.copy()
            environment["DATA_DIR"] = str(Path(folder) / "data")
            environment["STATE_DIR"] = str(Path(folder) / "state")
            environment["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            result = subprocess.run(
                [sys.executable, "-c", textwrap.dedent(script) + "\n" + textwrap.dedent(body)],
                text=True, capture_output=True, env=environment, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_append_only_import_preserves_confirmed_and_exports_all(self):
        self.run_isolated('''
(directory / 'one.wav').write_bytes(b'RIFF')
(directory / 'label.csv').write_text('audio_path,label\\none.wav,gốc\\n',encoding='utf-8')
initialize_dataset_files('test')
with SessionLocal() as db:
    dataset = Dataset(folder_name='test'); db.add(dataset); db.commit()
    dataset_id=dataset.id
    ensure_samples(db,dataset)
    old=db.scalar(select(Sample)); old.current_label='đã sửa'; old.status='confirmed'
    dataset.version+=1; db.commit()
(directory/'two.wav').write_bytes(b'RIFF')
with (directory/'label.csv').open('a') as file: file.write('two.wav,nhãn hai\\n')
with SessionLocal() as db:
    ensure_samples(db,db.get(Dataset,dataset_id))
    rows=db.scalars(select(Sample).order_by(Sample.position)).all()
    assert len(rows)==2
    assert rows[0].current_label=='đã sửa' and rows[0].status=='confirmed'
    assert rows[1].current_label=='nhãn hai' and rows[1].status=='pending'
sync_dataset_csv(dataset_id)
with (directory/'label_reviewed.csv').open(encoding='utf-8') as file:
    rows=list(csv.DictReader(file))
assert len(rows)==2 and rows[0]['label']=='đã sửa' and rows[1]['label']=='nhãn hai'
''')

    def test_append_both_csvs_preserves_old_labels_and_accepts_new_review(self):
        self.run_isolated('''
(directory/'a.wav').write_bytes(b'RIFF')
(directory/'label.csv').write_text('audio_path,label\\na.wav,A\\n')
initialize_dataset_files('test')
with SessionLocal() as db:
    d=Dataset(folder_name='test');db.add(d);db.commit();did=d.id
    ensure_samples(db,d)
    row=db.scalar(select(Sample));row.current_label='đã sửa';row.status='confirmed';d.version=1;db.commit()
(directory/'b.wav').write_bytes(b'RIFF')
with (directory/'label.csv').open('a') as f:f.write('b.wav,B\\n')
with (directory/'label_reviewed.csv').open('a') as f:f.write('b.wav,nhãn bổ sung,confirmed\\n')
with SessionLocal() as db:
    ensure_samples(db,db.get(Dataset,did))
    rows=db.scalars(select(Sample).order_by(Sample.position)).all()
    assert len(rows)==2
    assert rows[0].current_label=='đã sửa'
    assert rows[1].current_label=='nhãn bổ sung' and rows[1].status=='confirmed'
sync_dataset_csv(did)
with (directory/'label_reviewed.csv').open() as f:
    rows=list(csv.DictReader(f))
assert len(rows)==2
''')

    def test_reordered_source_blocks_csv_overwrite(self):
        self.run_isolated('''
for x in ['a.wav','b.wav']:(directory/x).write_bytes(b'RIFF')
(directory/'label.csv').write_text('audio_path,label\\na.wav,A\\nb.wav,B\\n')
initialize_dataset_files('test')
with SessionLocal() as db:
    d=Dataset(folder_name='test');db.add(d);db.commit();did=d.id
    ensure_samples(db,d)
    d.version=1;db.commit()
original=(directory/'label_reviewed.csv').read_bytes()
(directory/'label.csv').write_text('audio_path,label\\nb.wav,B\\na.wav,A\\n')
try:sync_dataset_csv(did)
except LabelingError:pass
else:raise AssertionError('Lẽ ra phải chặn thay đổi thứ tự nguồn')
assert (directory/'label_reviewed.csv').read_bytes()==original
''')

    def test_external_reviewed_edit_is_detected(self):
        self.run_isolated('''
(directory/'a.wav').write_bytes(b'RIFF')
(directory/'label.csv').write_text('audio_path,label\\na.wav,A\\n')
initialize_dataset_files('test')
with SessionLocal() as db:
    d=Dataset(folder_name='test');db.add(d);db.commit();did=d.id
    ensure_samples(db,d);d.version+=1;db.commit()
(directory/'label_reviewed.csv').write_text('audio_path,label,status\\na.wav,ngoài web,confirmed\\n')
try:sync_dataset_csv(did)
except LabelingError:pass
else:raise AssertionError('Không được ghi đè sửa đổi ngoài Web')
assert 'ngoài web' in (directory/'label_reviewed.csv').read_text()
''')

    def test_tab_locks_and_admin_can_release(self):
        self.run_isolated('''
from sqlalchemy import delete
for name in ['a.wav','b.wav']:(directory/name).write_bytes(b'RIFF')
with SessionLocal.begin() as db:
    db.add(Dataset(id=1,folder_name='test'))
    db.add(User(id=1,username='person',password_hash='test',role='annotator'))
    db.add(UserSession(id=1,user_id=1,session_token_hash='x',expires_at=int(time.time())+500))
    for idx,name in enumerate(['a.wav','b.wav'],1):
        db.add(Sample(id=idx,dataset_id=1,position=idx-1,audio_path=name,original_label='',current_label=''))
with SessionLocal() as db:
    assert try_claim(db,db.get(Sample,1),1,1)
    assert try_claim(db,db.get(Sample,2),1,1)
    require_lease(db,1,1)
    require_lease(db,2,1)
    db.execute(delete(SampleLock).where(SampleLock.user_id==1,SampleLock.sample_id==1));db.commit()
    try:require_lease(db,1,1)
    except LabelingError:pass
    else:raise AssertionError('Khóa phải bị giải phóng')
    require_lease(db,2,1)
''')

    def test_path_normalization_and_disk_based_duplicates(self):
        self.run_isolated('''
from app.services.dataset_service import DuplicatePathChecker
(directory/'label.csv').write_text('audio_path,label\\naudio/x.wav,a\\naudio\\\\x.wav,b\\n')
try:list(iter_label_rows(directory))
except ValueError as error:assert 'trùng' in str(error)
else:raise AssertionError('Hai cách ghi đường dẫn phải được nhận là trùng')
checker=DuplicatePathChecker(threshold=2)
assert checker.add('1') and checker.add('2') and checker.add('3')
assert not checker.add('2')
checker.close()
''')

    def test_versioned_database_migration(self):
        self.run_isolated('''
from sqlalchemy import text
with engine.connect() as connection:
    assert connection.exec_driver_sql('PRAGMA user_version').scalar_one()==1
    names={row[1] for row in connection.exec_driver_sql('PRAGMA index_list(samples)')}
    assert 'ix_samples_dataset_status_position' in names
    assert 'ix_sample_locks_user_expires' in {
        row[1] for row in connection.exec_driver_sql('PRAGMA index_list(sample_locks)')
    }
''')

    def test_existing_users_survive_database_upgrade(self):
        """Nâng cấp SQLite cũ phải giữ tài khoản, nhãn và lịch sử."""
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            env=os.environ.copy()
            env["DATA_DIR"]=str(folder/"data")
            env["STATE_DIR"]=str(folder/"state")
            env["PYTHONPATH"]=str(PROJECT_ROOT)+os.pathsep+env.get("PYTHONPATH","")
            script=r'''
import os,sqlite3
from pathlib import Path
from app.database.models import Base,User,Dataset,Sample,AnnotationHistory
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
path=Path(os.environ['STATE_DIR']);path.mkdir(parents=True,exist_ok=True)
e=create_engine(f"sqlite+pysqlite:///{path/'annotations.sqlite3'}")
Base.metadata.create_all(e)
with Session(e) as db:
    db.add(User(id=7,username='olduser',password_hash='oldhash'))
    db.add(Dataset(id=10,folder_name='mydata'))
    db.add(Sample(id=31,dataset_id=10,position=0,audio_path='a.wav',original_label='A',current_label='B',status='confirmed'))
    db.add(AnnotationHistory(sample_id=31,user_id=7,old_label='A',new_label='B',old_status='pending',new_status='confirmed',new_version=1))
    db.commit()
e.dispose()
from app.database.connection import initialize_database,SessionLocal
initialize_database()
with SessionLocal() as db:
    assert db.get(User,7).username=='olduser'
    assert db.get(Sample,31).current_label=='B'
    assert db.scalar(select(AnnotationHistory).where(AnnotationHistory.sample_id==31)) is not None
with sqlite3.connect(path/'annotations.sqlite3') as raw:
    assert raw.execute('PRAGMA user_version').fetchone()[0]==1
    assert raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dataset_source_states'").fetchone()
'''
            process=subprocess.run([sys.executable,"-c",textwrap.dedent(script)],env=env,
                capture_output=True,text=True,timeout=30)
            self.assertEqual(process.returncode,0,process.stderr)

    def test_compact_labeling_and_gitignore(self):
        markup=(PROJECT_ROOT/'app/templates/labeling.html').read_text()
        self.assertLess(markup.index('id="label-editor"'), markup.index('class="card csv-toolbar"'))
        self.assertIn('scrollIntoView', markup)
        self.assertIn('previous_sample_id', markup)
        gitignore_lines = (PROJECT_ROOT / '.gitignore').read_text().splitlines()
        active_rules = {line.strip() for line in gitignore_lines
                        if line.strip() and not line.lstrip().startswith('#')}
        self.assertIn('/data/', active_rules)
        self.assertIn('/state/', active_rules)
        self.assertIn('.env', active_rules)

    def test_admin_routes_have_csrf_and_confirmation(self):
        admin=(PROJECT_ROOT/'app/routes/admin.py').read_text()
        self.assertIn('def delete_annotator(', admin)
        self.assertIn('confirm_username.strip() != account.username', admin)
        self.assertIn('def release_user_locks(', admin)
        self.assertIn('checked_admin(request, db, csrf_token)', admin)

if __name__ == '__main__':
    unittest.main()
