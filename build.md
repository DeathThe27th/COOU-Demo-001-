# BUILD SPEC — Smart Attendance System Using Facial Recognition

## Context

Final-year Computer Science project, Chukwuemeka Odumegwu Ojukwu University (COOU),
Uli, Nigeria. Supervisor: Prof. I. J. Mgbeafulike. Chapters 1–3 of the seminar
document are already written (system analysis, methodology, requirements, DB
design). This build intentionally UPGRADES the recognition approach beyond what
chapter 3 originally specified (LBPH + passive 1:N) to a more efficient,
scalable, and accurate design (embedding-based 1:1 verification). The seminar
report will be revised afterward to match — the build should not compromise
on efficiency to match outdated document text.

Build the best, most efficient version of this system given the constraints below.

## Core requirement

A **Flask web application** (Python) that:
- Enrolls students by capturing their face via webcam (browser-side capture)
- At attendance time: student enters their registration/matric number first,
  then the system verifies their live face against ONLY that student's stored
  face data (1:1 verification, not a scan-everyone-and-guess approach)
- Automatically logs attendance with a timestamp on successful match
- Works **fully offline at runtime** — no internet needed once dependencies
  and model files are downloaded during setup
- Runs on a standard laptop webcam (RGB only, no IR/depth hardware)
- Scales cleanly — adding student #501 should not require retraining anything

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask |
| Face detection | `cv2.FaceDetectorYN` (YuNet, ONNX, bundled-downloadable model) |
| Face recognition | `cv2.FaceRecognizerSF` (SFace, ONNX) — outputs 128-d embedding + built-in cosine similarity scoring |
| Database | SQLite |
| Frontend | HTML, CSS, vanilla JS (Jinja2 templates via Flask) |
| Camera capture | Browser `getUserMedia` → frame POSTed to Flask as base64 → OpenCV decodes server-side |
| Reporting export | `pandas` + `openpyxl` for .xlsx generation |
| Version control | Git / GitHub (new repo) |

Setup note: YuNet and SFace `.onnx` model files must be downloaded once
(from OpenCV's model zoo / GitHub releases) during initial setup — after that,
zero internet dependency at runtime. Document the exact download step in
README/setup script since this is the one non-offline part of setup.

## Actors

1. **Student** — enters reg number, presents face for verification during a session; can log in separately to view own attendance history.
2. **Lecturer** — creates sessions per course, views/exports attendance.
3. **Administrator** — enrolls students, manages courses, sets attendance thresholds, generates cumulative reports, manually corrects records.

Simple Flask session-based auth, `role` field on User table (admin/lecturer/student).

## Database schema

```
User
  UserID INTEGER PRIMARY KEY
  Username TEXT UNIQUE
  PasswordHash TEXT
  Role TEXT   -- 'admin' / 'lecturer' / 'student'
  LinkedStudentID INTEGER NULL FK -> Student
  LinkedLecturerID INTEGER NULL FK -> Lecturer

Student
  StudentID INTEGER PRIMARY KEY
  FullName TEXT
  MatricNo TEXT UNIQUE           -- what the student types in at attendance time
  Department TEXT
  Level TEXT
  FaceEmbeddings TEXT            -- JSON array of 128-d embedding vectors from enrollment shots

Course
  CourseID INTEGER PRIMARY KEY
  CourseCode TEXT
  CourseName TEXT
  LecturerID INTEGER FK -> User (role=lecturer)
  Department TEXT

Session
  SessionID INTEGER PRIMARY KEY
  CourseID INTEGER FK -> Course
  Date DATE
  StartTime TIMESTAMP
  EndTime TIMESTAMP

Attendance
  AttendanceID INTEGER PRIMARY KEY
  StudentID INTEGER FK -> Student
  CourseID INTEGER FK -> Course
  SessionID INTEGER FK -> Session
  Status TEXT       -- 'present' / 'flagged_manual_review'
  Timestamp TIMESTAMP
  UNIQUE(StudentID, SessionID)   -- enforce no duplicate attendance at DB level
```

## Functional requirements

1. Admin registers new students: profile info + 5-8 webcam shots → embeddings extracted and stored
2. Attendance flow is verification-first:
   a. Student enters MatricNo
   b. System fetches that student's stored embeddings only
   c. Webcam captures live frame → detect face → extract embedding → cosine similarity vs stored
   d. Above threshold → attendance logged, success message shown
   e. Below threshold → "face not recognized, try again" (allow ~3 attempts, then flag `flagged_manual_review` for lecturer/admin to resolve manually)
3. Before opening the camera at all: check if StudentID already has an Attendance row for this SessionID — if so, short-circuit with "already marked present," skip the camera step entirely
4. Web dashboard for lecturers: view session attendance, per-course/per-date-range reports
5. Threshold alerts: flag students whose cumulative attendance % falls below a configurable value
6. Admin can manually correct/override attendance records
7. Export: generate a pivoted spreadsheet (rows = students, columns = sessions, cells = present/absent, trailing column = attendance %) as downloadable .xlsx, per course/semester

## Non-functional targets

- Verification + logging within ~2-3 seconds of camera activation
- ≥90% recognition accuracy on enrolled dataset (should exceed this easily with embeddings vs LBPH, especially at small N like 5 students)
- Usable by non-technical lecturers/students with no training
- Facial data and attendance records accessible only to authenticated users matching their role
- O(1) verification cost regardless of total enrolled students (no scaling degradation, unlike 1:N matching)

## Operational flow

```
Student enters MatricNo → System loads that student's embeddings only →
Webcam captures live frame → Face detection (YuNet) → Embedding extraction (SFace) →
Cosine similarity vs stored embeddings → Match? →
  YES: Attendance logged (timestamp, status=present)
  NO: "Try again" (up to 3 attempts) → still no match: flagged for manual review
```

## Suggested folder structure

```
smart-attendance/
├── app.py
├── config.py
├── requirements.txt
├── setup_models.py             # one-time download of YuNet/SFace onnx files
├── database/
│   ├── schema.sql
│   └── attendance.db           # gitignored
├── models/
│   ├── face_detection_yunet.onnx
│   └── face_recognition_sface.onnx
├── modules/
│   ├── detection.py            # YuNet wrapper
│   ├── embedding.py            # SFace embedding extraction + cosine similarity
│   ├── attendance.py           # verification flow, duplicate-check, attempt limiting
│   ├── reports.py              # pandas pivot + xlsx export
│   └── db.py
├── routes/
│   ├── auth.py
│   ├── admin.py                # enroll student, manage courses, thresholds
│   ├── lecturer.py             # sessions, reports, export
│   └── student.py              # matric entry + verification flow, own attendance view
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── enroll.html
│   ├── attendance_verify.html  # matric entry -> webcam verification UI
│   ├── dashboard.html
│   └── reports.html
├── static/
│   ├── css/
│   └── js/
│       ├── capture.js          # getUserMedia + frame POST + retry logic
│       └── dashboard.js
└── tests/
```

## Build order

1. DB schema + db.py, seed test data
2. `setup_models.py` — download/verify YuNet + SFace onnx files exist locally
3. Enrollment: browser capture → YuNet detect/crop → SFace embed → store JSON array on Student row
4. Verification flow: matric entry → fetch single student's embeddings → live capture → detect → embed → cosine similarity → threshold decision → retry/flag logic
5. Duplicate-check short-circuit (before camera even opens)
6. Attendance logging tied to Session/Course
7. Auth + role-based routing
8. Dashboard + reports UI
9. Export to .xlsx (pandas pivot: students × sessions × %)
10. Manual correction UI for flagged/unrecognized cases
11. Polish: error states (no face detected, poor lighting, camera permission denied), styling

## Known constraints to flag, not silently patch around

- Cosine similarity threshold needs empirical tuning during testing (start ~0.5-0.6 for SFace, adjust based on false accept/reject rate observed with your actual test group)
- 5-8 clear, well-lit enrollment shots per student minimum
- No liveness/anti-spoof detection — stated limitation, not a gap to fix with added scope
- This is a 2D RGB-camera appearance-based system, distinct from depth-based systems (Windows Hello/Face ID)
- Initial validation target: 5 enrolled students, should comfortably exceed 90% accuracy at this scale

## What "done" looks like for the demo

- Admin enrolls 5 students live via webcam
- Lecturer opens a session for a course
- Each student types their matric number, verifies face in ~2-3 seconds, gets logged — no duplicates possible even if they try twice
- A wrong/unenrolled face at a given matric number correctly fails verification
- Dashboard shows live session attendance
- Lecturer exports a semester-collated .xlsx report (students × sessions × attendance %)
- Entire demo runs with wifi off
