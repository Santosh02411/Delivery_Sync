"""
Database backup (Phase 18).

Honest scope: this project's default and test configuration is
SQLite (see db/session.py's IS_SQLITE), so a real, working backup
mechanism is implemented for that case — a file-level copy, which is
the standard, correct way to back up a SQLite database (SQLite itself
recommends this over trying to re-implement it via SQL dump for a
simple point-in-time snapshot). For PostgreSQL (this project's other
supported mode, e.g. in production), backup is correctly a job for
`pg_dump`/managed-database automated backups running OUTSIDE the
application process, not something this API can honestly do — see
`create_backup()` below, which says so plainly and refuses to pretend
otherwise, rather than a fake "success" response that backs up
nothing.
"""

import hashlib
import os
import shutil
from datetime import datetime

from app.db.session import IS_SQLITE, SQLALCHEMY_DATABASE_URL

BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)


def _sqlite_db_path() -> str:
    # "sqlite:///./database.db" -> "./database.db"
    return SQLALCHEMY_DATABASE_URL.replace("sqlite:///", "", 1)


def _file_checksum(path: str) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def create_backup() -> dict:
    """
    Returns a dict describing the outcome. Never raises — a failed
    backup attempt should be a reported status, not a crashed request.
    """
    if not IS_SQLITE:
        return {
            "status": "not_applicable",
            "message": (
                "This organization's database is PostgreSQL, not SQLite. Use `pg_dump` or your managed "
                "database provider's automated backups — this endpoint only performs SQLite file backups "
                "and won't pretend to back up a Postgres database it has no access to."
            ),
        }

    db_path = _sqlite_db_path()
    if not os.path.exists(db_path):
        return {"status": "error", "message": f"Database file not found at {db_path}."}

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_filename = f"backup-{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    try:
        shutil.copyfile(db_path, backup_path)
        checksum = _file_checksum(backup_path)
        size_bytes = os.path.getsize(backup_path)
    except OSError as error:
        return {"status": "error", "message": str(error)}

    return {
        "status": "success",
        "filename": backup_filename,
        "size_bytes": size_bytes,
        "checksum_sha256": checksum,
        "created_at": timestamp,
    }


def verify_backup(filename: str) -> dict:
    """
    Confirms a backup file exists, computes its checksum, AND actually
    opens it as a real SQLite database and runs a trivial query — a
    file that copies successfully but isn't openable/valid (truncated
    mid-write, corrupted) is exactly the failure mode a checksum alone
    can't catch; only actually opening it as a database can.
    """
    backup_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(backup_path):
        return {"status": "error", "message": "Backup file not found."}

    import sqlite3
    try:
        conn = sqlite3.connect(backup_path)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1")
        conn.close()
    except sqlite3.DatabaseError as error:
        return {"status": "error", "message": f"Backup file is not a valid SQLite database: {error}"}

    return {
        "status": "success",
        "filename": filename,
        "size_bytes": os.path.getsize(backup_path),
        "checksum_sha256": _file_checksum(backup_path),
    }


def list_backups() -> list:
    backups = []
    for filename in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not filename.endswith(".db"):
            continue
        path = os.path.join(BACKUP_DIR, filename)
        backups.append({
            "filename": filename,
            "size_bytes": os.path.getsize(path),
            "created_at": datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat(),
        })
    return backups
