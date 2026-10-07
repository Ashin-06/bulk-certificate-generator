import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    issued_on TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recipients (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    position INTEGER NOT NULL,
                    name TEXT,
                    email TEXT,
                    status TEXT NOT NULL CHECK(status IN ('pending','processing','succeeded','failed')),
                    error_code TEXT,
                    error TEXT,
                    svg TEXT,
                    UNIQUE(job_id, position)
                );
                CREATE INDEX IF NOT EXISTS recipients_queue ON recipients(status, job_id);
            """)
