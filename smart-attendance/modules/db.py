"""Database layer: per-request connection, schema init, seed data.

Supports two backends behind one API. SQLite is the default and powers the
fully-offline demo; PostgreSQL (Supabase) is used when DATABASE_URL is set, for
the hosted deployment where the container filesystem is ephemeral.

Everything above this module — routes/, modules/, templates/ — is written once,
against the SQLite dialect, and works unchanged on both. Three things make that
possible, and all three live in this file:

  * `Row` is a case-insensitive mapping. SQLite echoes column names back with
    the casing used in the query (`MatricNo`); Postgres folds unquoted
    identifiers to lowercase (`matricno`). Templates keep writing
    {{ s.MatricNo }} and neither backend cares.
  * `_to_pg` rewrites the handful of SQLite-only constructs actually used here
    (`?` placeholders, `INSERT OR IGNORE`, the reserved word `User`).
  * `execute_db` emulates sqlite3's `lastrowid` with a `RETURNING` clause.
"""
import re
import sqlite3
from datetime import datetime, timedelta

from flask import g
from werkzeug.security import generate_password_hash

import config

if config.USE_POSTGRES:  # imported only when actually needed
    import psycopg


# --------------------------------------------------------------------------
# Row mapping
# --------------------------------------------------------------------------

class Row(dict):
    """A dict whose string keys are matched case-insensitively.

    Exact hits take the normal dict fast path; `__missing__` only runs when a
    lookup fails, which is the cross-backend casing case. Attribute access is
    supported so Jinja's `{{ row.MatricNo }}` resolves the same way.
    """

    __slots__ = ()

    def __missing__(self, key):
        if isinstance(key, str):
            lowered = key.lower()
            for existing, value in self.items():
                if isinstance(existing, str) and existing.lower() == lowered:
                    return value
        raise KeyError(key)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _sqlite_row_factory(cursor, values):
    return Row(zip((c[0] for c in cursor.description), values))


def _pg_row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []

    def build(values):
        return Row(zip(names, values))

    return build


# --------------------------------------------------------------------------
# SQLite -> PostgreSQL statement rewriting
# --------------------------------------------------------------------------

# `User` is reserved in Postgres and must be quoted. The word boundaries keep
# this away from `Username`, `UserID` and an already-quoted `"User"`.
_USER_TABLE = re.compile(r'(?<!")\bUser\b(?!")')
_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)


def _to_pg(sql):
    """Rewrite a SQLite statement into its PostgreSQL equivalent."""
    if _INSERT_OR_IGNORE.search(sql):
        sql = _INSERT_OR_IGNORE.sub("INSERT INTO", sql, count=1)
        # Every INSERT OR IGNORE in this codebase is a single-statement insert,
        # so appending the conflict clause at the end is safe.
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    sql = _USER_TABLE.sub('"User"', sql)

    # Positional placeholders: ? -> %s, skipping anything inside a string
    # literal. Also escape any literal % so psycopg's own interpolation does
    # not misread it.
    out, in_string = [], False
    for ch in sql:
        if ch == "'":
            in_string = not in_string
            out.append(ch)
        elif in_string:
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


def _adapt(sql):
    return _to_pg(sql) if config.USE_POSTGRES else sql


# --------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------

def _connect():
    """Open a new backend connection with Row mapping configured."""
    if config.USE_POSTGRES:
        # autocommit mirrors sqlite3's behaviour here (this app never spans a
        # transaction across requests) and keeps no idle-in-transaction
        # connections parked on the Supabase pooler.
        return psycopg.connect(
            config.DATABASE_URL,
            autocommit=True,
            row_factory=_pg_row_factory,
            connect_timeout=15,
        )
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = _sqlite_row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_db(sql, args=(), one=False):
    cur = get_db().cursor()
    cur.execute(_adapt(sql), tuple(args))
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute_db(sql, args=()):
    """Run a write statement. Returns the new primary key for INSERTs.

    Postgres has no `lastrowid`, so an INSERT gets a `RETURNING` clause and the
    first returned column is read back. The primary key is the first column of
    every table in this schema, which makes that equivalent to sqlite3's
    behaviour. An `INSERT OR IGNORE` that hits a conflict returns no row at all;
    that yields None, and the callers of those statements ignore the result.
    """
    db = get_db()
    adapted = _adapt(sql)

    if not config.USE_POSTGRES:
        cur = db.execute(adapted, tuple(args))
        db.commit()
        return cur.lastrowid

    is_insert = adapted.lstrip().lower().startswith("insert")
    if is_insert and "returning" not in adapted.lower():
        adapted = adapted.rstrip().rstrip(";") + " RETURNING *"

    cur = db.cursor()
    cur.execute(adapted, tuple(args))
    new_id = None
    if is_insert and cur.description:
        row = cur.fetchone()
        if row:
            new_id = next(iter(row.values()), None)
    cur.close()
    return new_id


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def get_setting(key, default=None):
    row = query_db("SELECT Value FROM Settings WHERE Key = ?", (key,), one=True)
    return row["Value"] if row else default


def set_setting(key, value):
    execute_db(
        "INSERT INTO Settings (Key, Value) VALUES (?, ?) "
        "ON CONFLICT(Key) DO UPDATE SET Value = excluded.Value",
        (key, str(value)),
    )


def get_similarity_threshold():
    return float(get_setting("similarity_threshold", config.DEFAULT_SIMILARITY_THRESHOLD))


def get_attendance_percent_threshold():
    return float(get_setting("attendance_percent_threshold",
                             config.DEFAULT_ATTENDANCE_PERCENT_THRESHOLD))


# --------------------------------------------------------------------------
# Schema init + seed
# --------------------------------------------------------------------------

class _InitCursor:
    """Thin wrapper so `_seed` can be written once against the SQLite dialect."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, args=()):
        adapted = _adapt(sql)
        if config.USE_POSTGRES:
            is_insert = adapted.lstrip().lower().startswith("insert")
            if is_insert and "returning" not in adapted.lower():
                adapted = adapted.rstrip().rstrip(";") + " RETURNING *"
            cur = self._conn.cursor()
            cur.execute(adapted, tuple(args))
            row = cur.fetchone() if (is_insert and cur.description) else None
            cur.close()
            return _Result(next(iter(row.values()), None) if row else None, row)
        cur = self._conn.execute(adapted, tuple(args))
        return _Result(cur.lastrowid, cur.fetchone() if _is_select(adapted) else None)


class _Result:
    def __init__(self, lastrowid, row):
        self.lastrowid = lastrowid
        self._row = row

    def fetchone(self):
        return self._row


def _is_select(sql):
    return sql.lstrip().lower().startswith("select")


def _scalar(conn, sql, args=()):
    """Run a SELECT during init, before the request-scoped helpers exist."""
    cur = conn.cursor()
    cur.execute(_adapt(sql), tuple(args))
    row = cur.fetchone()
    cur.close()
    return row


def init_db(app):
    """Create tables (idempotent) and seed defaults on first run."""
    if config.USE_POSTGRES:
        schema_path = config.SCHEMA_POSTGRES_PATH
        conn = _connect()
    else:
        config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        schema_path = config.SCHEMA_PATH
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.row_factory = _sqlite_row_factory

    with open(schema_path) as f:
        schema_sql = f.read()

    if config.USE_POSTGRES:
        conn.execute(schema_sql)
    else:
        conn.executescript(schema_sql)

    cur = _InitCursor(conn)

    # Migration for databases created before the CourseStudent roster existed:
    # every student who ever had attendance in a course belongs on its roster.
    cur.execute(
        "INSERT OR IGNORE INTO CourseStudent (CourseID, StudentID) "
        "SELECT DISTINCT CourseID, StudentID FROM Attendance")

    _seed(conn, cur)

    if not config.USE_POSTGRES:
        conn.commit()
    conn.close()
    app.teardown_appcontext(close_db)


def _seed(conn, cur):
    """Seed default settings, accounts, courses and demo students (first run only)."""
    cur.execute(
        "INSERT OR IGNORE INTO Settings (Key, Value) VALUES (?, ?)",
        ("similarity_threshold", str(config.DEFAULT_SIMILARITY_THRESHOLD)),
    )
    cur.execute(
        "INSERT OR IGNORE INTO Settings (Key, Value) VALUES (?, ?)",
        ("attendance_percent_threshold", str(config.DEFAULT_ATTENDANCE_PERCENT_THRESHOLD)),
    )

    if _scalar(conn, "SELECT COUNT(*) c FROM User")["c"] > 0:
        return  # already seeded

    cur.execute(
        "INSERT INTO User (Username, PasswordHash, Role, FullName) VALUES (?, ?, ?, ?)",
        ("admin", generate_password_hash("admin123"), "admin", "System Administrator"),
    )
    lecturer_id = cur.execute(
        "INSERT INTO User (Username, PasswordHash, Role, FullName) VALUES (?, ?, ?, ?)",
        ("lecturer1", generate_password_hash("lecturer123"), "lecturer",
         "Prof. I. J. Mgbeafulike"),
    ).lastrowid

    courses = [
        ("CSC 401", "Artificial Intelligence", "Computer Science"),
        ("CSC 405", "Software Engineering II", "Computer Science"),
    ]
    course_ids = []
    for code, name, dept in courses:
        course_ids.append(cur.execute(
            "INSERT INTO Course (CourseCode, CourseName, LecturerID, Department) "
            "VALUES (?, ?, ?, ?)",
            (code, name, lecturer_id, dept),
        ).lastrowid)

    # Demo students — profiles only; face embeddings are added live via the
    # admin enrollment page (webcam capture).
    students = [
        ("Somto Okafor", "2021/CS/001"),
        ("Adaeze Nwosu", "2021/CS/002"),
        ("Chinedu Eze", "2021/CS/003"),
        ("Ifeoma Obi", "2021/CS/004"),
        ("Emeka Uche", "2021/CS/005"),
    ]
    for name, matric in students:
        student_id = cur.execute(
            "INSERT INTO Student (FullName, MatricNo, Department, Level) "
            "VALUES (?, ?, 'Computer Science', '400')",
            (name, matric),
        ).lastrowid
        # student portal login: username = matric, password = student123
        cur.execute(
            "INSERT INTO User (Username, PasswordHash, Role, FullName, LinkedStudentID) "
            "VALUES (?, ?, 'student', ?, ?)",
            (matric, generate_password_hash("student123"), name, student_id),
        )
        # demo students take both seeded courses
        for cid in course_ids:
            cur.execute(
                "INSERT INTO CourseStudent (CourseID, StudentID) VALUES (?, ?)",
                (cid, student_id),
            )

    # One demo session for today, open for the next 2 hours
    now = datetime.now()
    cur.execute(
        "INSERT INTO Session (CourseID, Date, StartTime, EndTime) VALUES (?, ?, ?, ?)",
        (course_ids[0], now.strftime("%Y-%m-%d"),
         now.strftime("%Y-%m-%d %H:%M:%S"),
         (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")),
    )
