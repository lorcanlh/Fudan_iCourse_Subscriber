"""Incrementally archive existing lecture text to a private Google Drive folder."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import date
from pathlib import Path

import requests

API = "https://www.googleapis.com/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"


class DriveError(RuntimeError):
    pass


class Drive:
    def __init__(self, credentials):
        self.credentials = credentials
        self.session = requests.Session()
        self.refresh()

    def refresh(self):
        # Never print responses here: they can contain access tokens.
        r = self.session.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "refresh_token",
            **{k: self.credentials[k] for k in
               ("client_id", "client_secret", "refresh_token")},
        }, timeout=60)
        if not r.ok:
            raise DriveError(f"Google token refresh failed (HTTP {r.status_code}); re-authorize locally")
        self.session.headers["Authorization"] = "Bearer " + r.json()["access_token"]

    def request(self, method, url=API, **kwargs):
        for attempt in range(5):
            r = self.session.request(method, url, timeout=120, **kwargs)
            if r.status_code == 401 and attempt == 0:
                self.refresh()
                continue
            # Retrying an ambiguous create can produce duplicates. A subsequent
            # workflow lists Drive again and recovers successfully created files.
            if method != "POST" and (r.status_code == 429 or r.status_code >= 500):
                time.sleep(2 ** attempt)
                continue
            if not r.ok:
                raise DriveError(f"Drive {method} failed (HTTP {r.status_code})")
            return r.json()
        raise DriveError("Drive retries exhausted")

    def children(self, parent):
        token = None
        files = []
        while True:
            params = {"q": f"'{parent}' in parents and trashed = false",
                      "fields": "nextPageToken,files(id,name,mimeType,appProperties)",
                      "pageSize": 1000}
            if token:
                params["pageToken"] = token
            result = self.request("GET", params=params)
            files.extend(result.get("files", []))
            token = result.get("nextPageToken")
            if not token:
                return files

    def folder(self, parent, name, key):
        matches = [f for f in self.children(parent)
                   if f.get("appProperties", {}).get("icourse_key") == key
                   and f["mimeType"] == FOLDER]
        if len(matches) > 1:
            raise DriveError("Duplicate managed folders; resolve in Drive before retrying")
        if matches:
            return matches[0]["id"]
        return self.request("POST", json={
            "name": name, "mimeType": FOLDER, "parents": [parent],
            "appProperties": {"icourse_key": key},
        }, params={"fields": "id"})["id"]

    def upload(self, parent, name, key, text, existing):
        data = text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        matches = [f for f in existing
                   if f.get("appProperties", {}).get("icourse_key") == key]
        if len(matches) > 1:
            raise DriveError("Duplicate managed files; resolve in Drive before retrying")
        old = matches[0] if matches else None
        if old and old.get("appProperties", {}).get("sha256") == digest and old["name"] == name:
            return False
        metadata = {"name": name, "appProperties": {"icourse_key": key, "sha256": digest}}
        if not old:
            metadata["parents"] = [parent]
        boundary = uuid.uuid4().hex
        payload = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                   + json.dumps(metadata, ensure_ascii=False)
                   + f"\r\n--{boundary}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        url = "https://www.googleapis.com/upload/drive/v3/files"
        if old:
            url += "/" + old["id"]
        result = self.request("PATCH" if old else "POST", url,
                             params={"uploadType": "multipart", "fields": "id,name,appProperties"},
                             headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                             data=payload)
        if old:
            existing.remove(old)
        existing.append(result)
        return True


def safe_name(text):
    return re.sub(r"[\\/\x00-\x1f]", "_", str(text)).strip()[:120] or "未命名"


def records(db_path, start, end, course_ids):
    # Read-only: a missing/decryption-failed DB must never become an empty DB.
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT l.*, c.title AS course_title, c.teacher
            FROM lectures l LEFT JOIN courses c USING(course_id)
            ORDER BY l.date, l.course_id, l.sub_id""").fetchall()
    skipped = 0
    for row in rows:
        if course_ids and str(row["course_id"]) not in course_ids:
            continue
        value = str(row["date"] or "")[:10]
        try:
            day = date.fromisoformat(value)
        except ValueError:
            skipped += 1
            continue
        if day < start or (end and day > end):
            continue
        if (row["transcript"] or "").strip() or (row["summary"] or "").strip():
            yield dict(row)
    if skipped:
        print(f"::warning::{skipped} lecture(s) with missing/invalid dates skipped; inspect database dates")


def sync(drive, rows, parent, label, namespace):
    ns = hashlib.sha256(namespace.encode()).hexdigest()[:16]
    root = drive.folder(parent, "iCourse 课程归档", f"{ns}:root")
    term = drive.folder(root, label, f"{ns}:term:{hashlib.sha256(label.encode()).hexdigest()[:16]}")
    courses = {}
    uploaded = unchanged = lectures = 0
    for row in rows:
        cid, sid = str(row["course_id"]), str(row["sub_id"])
        if cid not in courses:
            folder = drive.folder(term, safe_name(row["course_title"] or cid) + f" [{cid}]", f"{ns}:course:{cid}")
            courses[cid] = (folder, drive.children(folder))
        folder, existing = courses[cid]
        heading = (f"# {row['course_title'] or cid} — {row['sub_title'] or sid}\n\n"
                   f"日期：{row['date']}\n教师：{row['teacher'] or ''}\n"
                   f"课程 ID：{cid}\n课次 ID：{sid}\n\n")
        base = safe_name(f"{str(row['date'])[:10]}_{row['sub_title'] or sid}") + f"_{sid}"
        for field, suffix in (("transcript", "原始转录.txt"), ("summary", "AI分析.md")):
            content = row[field] or ""
            if not content.strip():
                continue  # Never replace an existing complete file with empty text.
            body = heading
            if field == "summary":
                body += f"分析模型：{row['summary_model'] or '未记录'}\n\n"
            body += content
            changed = drive.upload(folder, f"{base}_{suffix}",
                                   f"{ns}:{cid}:{sid}:{field}", body, existing)
            uploaded += int(changed)
            unchanged += int(not changed)
        lectures += 1
    print(f"Drive sync complete: {lectures} lectures, {uploaded} files uploaded/updated, {unchanged} unchanged")


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--db", default="data/icourse.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    start = date.fromisoformat(os.environ.get("GOOGLE_DRIVE_START_DATE", "2026-09-01"))
    end = date.fromisoformat(os.environ["GOOGLE_DRIVE_END_DATE"]) if os.environ.get("GOOGLE_DRIVE_END_DATE") else None
    if end and end < start:
        raise ValueError("End date precedes start date")
    rows = list(records(args.db, start, end, set(filter(None, (
        s.strip() for s in os.environ.get("GOOGLE_DRIVE_COURSE_IDS", "").split(","))))))
    if args.dry_run:
        print(f"Dry run: {len(rows)} lectures; {sum(bool((r[f] or '').strip()) for r in rows for f in ('transcript', 'summary'))} nonempty files")
        return
    credentials = json.loads(os.environ["GOOGLE_DRIVE_OAUTH_JSON"])
    drive = Drive(credentials)
    sync(drive, rows, os.environ.get("GOOGLE_DRIVE_PARENT_ID") or "root",
         os.environ.get("GOOGLE_DRIVE_TERM") or "2026-2027 秋季学期",
         os.environ.get("GITHUB_REPOSITORY", "icourse"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Credentials, API bodies and classroom text must not enter public logs.
        detail = str(exc) if isinstance(exc, DriveError) else "check authorization, configuration and database"
        print(f"::error::Drive sync failed ({type(exc).__name__}): {detail}")
        sys.exit(1)
