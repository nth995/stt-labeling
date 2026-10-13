"""Kiểm thử nền tảng trước khi triển khai LAN; không động vào dữ liệu thật."""

import csv
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape



class ProductionReadinessTests(unittest.TestCase):
    def test_labeling_template_keeps_javascript_out_of_title(self):
        """JavaScript gửi Beacon phải nằm trong body, không lọt vào <title>."""
        template_dir = Path(__file__).resolve().parents[1] / "app" / "templates"
        templates = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html"]),
        )
        html = templates.get_template("labeling.html").render(
            dataset=SimpleNamespace(id=1, folder_name="test", version=0, csv_synced_version=0),
            user=SimpleNamespace(role="annotator", username="user01"),
            csrf_token="test-token", counts=SimpleNamespace(total=0, pending=0, confirmed=0),
            current=None, samples=[], status="pending", page=1,
            filter_status="pending", message=None, error=None,
        )
        title = html.split("<title>", 1)[1].split("</title>", 1)[0]
        self.assertNotIn("<script", title)
        self.assertIn("Gán nhãn test", title)
        self.assertIn("navigator.sendBeacon", html.split("</head>", 1)[1])

    def test_online_backup_includes_committed_updates_from_wal(self):
        """Bản sao lưu có dữ liệu đã commit kể cả khi SQLite chưa checkpoint WAL."""
        from app.scripts.backup_sqlite import backup_database
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "annotations.sqlite3"
            target = Path(temp) / "backups" / "copy.sqlite3"
            with sqlite3.connect(source) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE labels (text TEXT NOT NULL)")
                connection.execute("INSERT INTO labels VALUES (?)", ("Xin chào Việt Nam",))
                connection.commit()
                result = backup_database(source, target)
            self.assertEqual(result, target)
            with sqlite3.connect(target) as connection:
                self.assertEqual(connection.execute("SELECT text FROM labels").fetchone()[0], "Xin chào Việt Nam")
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                backup_database(source, target)

    def test_compose_has_lan_port_and_nonroot_user(self):
        """Cấu hình triển khai không hardcode root và có volume dữ liệu."""
        root = Path(__file__).resolve().parents[1]
        config = (root / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("APP_UID", config)
        self.assertIn("APP_GID", config)
        self.assertIn("./data:/app/data", config)
        self.assertIn("./state:/app/state", config)
        self.assertIn("/health", config)


if __name__ == "__main__":
    unittest.main()
