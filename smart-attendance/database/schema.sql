-- Smart Attendance System schema (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Student (
    StudentID      INTEGER PRIMARY KEY AUTOINCREMENT,
    FullName       TEXT NOT NULL,
    MatricNo       TEXT NOT NULL UNIQUE,   -- typed by the student at attendance time
    Department     TEXT,
    Level          TEXT,
    FaceEmbeddings TEXT                    -- JSON array of 128-d embedding vectors
);

CREATE TABLE IF NOT EXISTS User (
    UserID           INTEGER PRIMARY KEY AUTOINCREMENT,
    Username         TEXT NOT NULL UNIQUE,
    PasswordHash     TEXT NOT NULL,
    Role             TEXT NOT NULL CHECK (Role IN ('admin', 'lecturer', 'student')),
    FullName         TEXT,
    LinkedStudentID  INTEGER REFERENCES Student(StudentID)
);

CREATE TABLE IF NOT EXISTS Course (
    CourseID   INTEGER PRIMARY KEY AUTOINCREMENT,
    CourseCode TEXT NOT NULL,
    CourseName TEXT NOT NULL,
    LecturerID INTEGER REFERENCES User(UserID),  -- role = lecturer
    Department TEXT
);

-- Course roster: which students are registered in which course. Kiosk
-- verification and all reports operate on this list, never on all students.
CREATE TABLE IF NOT EXISTS CourseStudent (
    CourseID  INTEGER NOT NULL REFERENCES Course(CourseID),
    StudentID INTEGER NOT NULL REFERENCES Student(StudentID),
    PRIMARY KEY (CourseID, StudentID)
);

CREATE TABLE IF NOT EXISTS Session (
    SessionID INTEGER PRIMARY KEY AUTOINCREMENT,
    CourseID  INTEGER NOT NULL REFERENCES Course(CourseID),
    Date      DATE NOT NULL,
    StartTime TIMESTAMP NOT NULL,
    EndTime   TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS Attendance (
    AttendanceID INTEGER PRIMARY KEY AUTOINCREMENT,
    StudentID    INTEGER NOT NULL REFERENCES Student(StudentID),
    CourseID     INTEGER NOT NULL REFERENCES Course(CourseID),
    SessionID    INTEGER NOT NULL REFERENCES Session(SessionID),
    Status       TEXT NOT NULL CHECK (Status IN ('present', 'absent', 'flagged_manual_review')),
    Timestamp    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (StudentID, SessionID)           -- no duplicate attendance, enforced at DB level
);

-- Runtime-tunable settings (similarity threshold, attendance % alert level)
CREATE TABLE IF NOT EXISTS Settings (
    Key   TEXT PRIMARY KEY,
    Value TEXT NOT NULL
);
