import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.sync_drive import Drive, DriveError, records, sync
from src.data.schema import SCHEMA_SQL


class MemoryDrive(Drive):
    def __init__(self):
        self.files = {}
        self.bodies = {}
        self.writes = 0

    def request(self, method, url=None, **kw):
        if "json" in kw:
            metadata = copy.deepcopy(kw["json"])
        else:
            payload = kw["data"]
            metadata = json.loads(payload.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
        if method == "POST":
            fid = str(len(self.files) + 1)
            metadata["id"] = fid
            self.files[fid] = metadata
        else:
            fid = url.rsplit("/", 1)[1]
            self.files[fid].update(metadata)
        if "data" in kw:
            self.writes += 1
            self.bodies[fid] = kw["data"]
        return copy.deepcopy(self.files[fid])

    def children(self, parent):
        return [copy.deepcopy(f) for f in self.files.values() if parent in f.get("parents", [])]


def lecture():
    return dict(course_id="12", sub_id="34", course_title="属性数据分析",
                teacher="教师", sub_title="2026-10-01 课堂", date="2026-10-01",
                transcript="完整原始转录\n保留换行", summary="# 分析\n公式 $x$",
                summary_model="model")


class SyncTests(unittest.TestCase):
    def test_backfill_no_duplicates_changed_content_and_missing_field(self):
        drive = MemoryDrive()
        row = lecture()
        with redirect_stdout(io.StringIO()):
            sync(drive, [row], "root", "秋季", "repo")
            first_ids = set(drive.files)
            self.assertEqual(drive.writes, 2)
            sync(drive, [row], "root", "秋季", "repo")
            self.assertEqual(drive.writes, 2)
            row["summary"] = "更新分析"
            row["transcript"] = ""  # Partial retry never erases original.
            sync(drive, [row], "root", "秋季", "repo")
            self.assertEqual(drive.writes, 3)
            self.assertEqual(set(drive.files), first_ids)
        self.assertTrue(any("完整原始转录\n保留换行".encode() in b for b in drive.bodies.values()))

    def test_interrupted_run_resumes_without_duplicate(self):
        drive = MemoryDrive()
        row = lecture()
        row["summary"] = None
        with redirect_stdout(io.StringIO()):
            sync(drive, [row], "root", "秋季", "repo")
            sync(drive, [lecture()], "root", "秋季", "repo")
        self.assertEqual(drive.writes, 2)

    def test_date_course_filters_and_partial_lectures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            with sqlite3.connect(path) as db:
                db.executescript(SCHEMA_SQL)
                db.execute("INSERT INTO courses VALUES ('12', '课程', '教师')")
                for sid, day, transcript, summary in [
                    ("1", "2026-08-31", "旧课", "旧分析"),
                    ("2", "2026-09-01", "仅转录", None),
                    ("3", "2026-10-01", None, "仅分析"),
                    ("4", "invalid", "正文", "分析"),
                    ("5", "2026-10-02", None, None),
                    ("6", "2027-02-01", "下学期", "分析"),
                ]:
                    db.execute("INSERT INTO lectures (sub_id,course_id,date,transcript,summary) VALUES (?, '12', ?, ?, ?)",
                               (sid, day, transcript, summary))
            with redirect_stdout(io.StringIO()) as logs:
                found = list(records(path, date(2026, 9, 1), date(2027, 1, 31), {"12"}))
            self.assertEqual([r["sub_id"] for r in found], ["2", "3"])
            self.assertIn("1 lecture(s)", logs.getvalue())
            self.assertEqual(list(records(path, date(2026, 9, 1), None, {"99"})), [])
            with self.assertRaises(sqlite3.OperationalError):
                list(records(Path(directory) / "missing.db", date.today(), None, set()))

    def test_drive_pagination(self):
        drive = Drive.__new__(Drive)
        drive.request = Mock(side_effect=[{"files": [{"id": "1"}], "nextPageToken": "next"}, {"files": [{"id": "2"}]}])
        self.assertEqual([f["id"] for f in drive.children("root")], ["1", "2"])
        self.assertEqual(drive.request.call_args.kwargs["params"]["pageToken"], "next")

    def test_auth_refresh_and_non_retrying_create(self):
        drive = Drive.__new__(Drive)
        drive.session = Mock()
        drive.refresh = Mock()
        drive.session.request.side_effect = [Mock(status_code=401), Mock(status_code=200, ok=True, json=lambda: {"id": "ok"})]
        self.assertEqual(drive.request("GET"), {"id": "ok"})
        drive.refresh.assert_called_once()
        drive.session.request.side_effect = None
        drive.session.request.return_value = Mock(status_code=503, ok=False)
        with self.assertRaises(DriveError):
            drive.request("POST")
        self.assertEqual(drive.session.request.call_count, 3)

    def test_duplicate_managed_file_fails(self):
        drive = MemoryDrive()
        existing = [{"id": str(i), "appProperties": {"icourse_key": "key"}} for i in (1, 2)]
        with self.assertRaises(DriveError):
            drive.upload("root", "name.txt", "key", "text", existing)


if __name__ == "__main__":
    unittest.main()
