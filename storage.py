"""SQLite storage shared by the web application and the Excel importer."""

from pathlib import Path
import sqlite3


def connect_db(path):
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def init_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_db(path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                a_number TEXT NOT NULL DEFAULT '',
                reference TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'activo' CHECK(status IN ('activo', 'cerrado')),
                owner TEXT NOT NULL DEFAULT 'Mario',
                country TEXT NOT NULL DEFAULT '',
                court TEXT NOT NULL DEFAULT '',
                hearing_date TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                case_id INTEGER NOT NULL REFERENCES cases(id),
                worksheet TEXT NOT NULL DEFAULT '',
                row_number INTEGER NOT NULL DEFAULT 0,
                data TEXT NOT NULL DEFAULT '{}',
                fingerprint TEXT NOT NULL UNIQUE,
                imported_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY,
                case_id INTEGER NOT NULL REFERENCES cases(id),
                type TEXT NOT NULL DEFAULT 'nota'
                    CHECK(type IN ('actuacion', 'llamada', 'audiencia', 'documento', 'nota')),
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                occurred_on TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                source_key TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY,
                case_id INTEGER REFERENCES cases(id),
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                assignee TEXT NOT NULL DEFAULT '',
                due_date TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'media' CHECK(priority IN ('alta', 'media', 'baja')),
                status TEXT NOT NULL DEFAULT 'pendiente'
                    CHECK(status IN ('pendiente', 'en_curso', 'completada')),
                author TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                source_key TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS import_runs (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL DEFAULT '',
                file_hash TEXT NOT NULL UNIQUE,
                imported_at TEXT NOT NULL DEFAULT '',
                report TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_sources_case ON sources(case_id);
            CREATE INDEX IF NOT EXISTS idx_activities_case_date
                ON activities(case_id, occurred_on DESC, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_case ON tasks(case_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_date);
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'editor')),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                auth_version INTEGER NOT NULL DEFAULT 1 CHECK(auth_version >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_throttle (
                bucket TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL CHECK(attempts >= 0),
                window_started REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
        """)
        connection.commit()
    finally:
        connection.close()
