"""Admin: student enrollment (webcam), courses, settings, manual corrections."""
import json
from datetime import datetime

from flask import (Blueprint, Response, flash, jsonify, redirect, render_template,
                   request, url_for)
from werkzeug.security import generate_password_hash

import config
from modules import db, detection, embedding, reports, roster_import
from routes.auth import role_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@role_required("admin")
def dashboard():
    flagged = reports.flagged_records()

    # Students who cannot verify at the kiosk yet, by the same rule the students
    # page uses (shots < MIN_ENROLL_SHOTS). Embeddings are JSON, so the count
    # happens here rather than in SQL.
    awaiting = []
    for r in db.query_db("SELECT StudentID, FullName, MatricNo, Department, "
                         "FaceEmbeddings FROM Student ORDER BY MatricNo"):
        shots = len(json.loads(r["FaceEmbeddings"] or "[]"))
        if shots < config.MIN_ENROLL_SHOTS:
            awaiting.append(db.Row(r, Shots=shots))

    recent_sessions = db.query_db(
        "SELECT s.SessionID, s.Date, s.StartTime, s.EndTime, "
        "       c.CourseCode, c.CourseName, "
        "       (SELECT COUNT(*) FROM Attendance a "
        "        WHERE a.SessionID = s.SessionID AND a.Status = 'present') PresentCount "
        "FROM Session s JOIN Course c ON c.CourseID = s.CourseID "
        "ORDER BY s.StartTime DESC LIMIT 6")

    stats = {
        "students": db.query_db("SELECT COUNT(*) c FROM Student", one=True)["c"],
        "enrolled": db.query_db(
            "SELECT COUNT(*) c FROM Student WHERE FaceEmbeddings IS NOT NULL "
            "AND FaceEmbeddings != '[]'", one=True)["c"],
        "courses": db.query_db("SELECT COUNT(*) c FROM Course", one=True)["c"],
        "flagged": len(flagged),
    }
    return render_template("dashboard.html", role="admin", stats=stats,
                           flagged=flagged, awaiting=awaiting,
                           recent_sessions=recent_sessions,
                           min_shots=config.MIN_ENROLL_SHOTS,
                           now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# ---------- students ----------

@bp.route("/students")
@role_required("admin")
def students():
    rows = db.query_db("SELECT * FROM Student ORDER BY MatricNo")
    enriched = []
    for r in rows:
        shots = len(json.loads(r["FaceEmbeddings"] or "[]"))
        # db.Row, not a plain dict: the template reads {{ s.MatricNo }} and only
        # Row matches that case-insensitively across both database backends.
        enriched.append(db.Row(r, Shots=shots))
    return render_template("students.html", students=enriched)


@bp.route("/students/add", methods=["POST"])
@role_required("admin")
def add_student():
    f = request.form
    matric = f.get("matric_no", "").strip()
    name = f.get("full_name", "").strip()
    if not matric or not name:
        flash("Full name and matric number are required.", "error")
        return redirect(url_for("admin.students"))
    if db.query_db("SELECT 1 FROM Student WHERE MatricNo = ?", (matric,), one=True):
        flash(f"A student with matric number {matric} already exists.", "error")
        return redirect(url_for("admin.students"))
    sid = db.execute_db(
        "INSERT INTO Student (FullName, MatricNo, Department, Level) VALUES (?, ?, ?, ?)",
        (name, matric, f.get("department", "").strip(), f.get("level", "").strip()),
    )
    # student portal account (username = matric no)
    db.execute_db(
        "INSERT OR IGNORE INTO User (Username, PasswordHash, Role, FullName, "
        "LinkedStudentID) VALUES (?, ?, 'student', ?, ?)",
        (matric, generate_password_hash("student123"), name, sid),
    )
    flash(f"Student {name} added. Now capture their face shots.", "success")
    return redirect(url_for("admin.enroll", student_id=sid))


@bp.route("/students/template.csv")
@role_required("admin")
def students_template():
    """A correctly-shaped example file. Format confusion is the main reason a
    bulk import fails, so hand out the answer rather than describing it."""
    return Response(
        roster_import.template_csv(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=student-roster-template.csv"},
    )


@bp.route("/students/import", methods=["POST"])
@role_required("admin")
def import_students():
    """Bulk-register students from a CSV of names and matric numbers."""
    upload = request.files.get("csv_file")
    if not upload or not upload.filename:
        flash("Choose a CSV file to import.", "error")
        return redirect(url_for("admin.students"))

    rows, problems = roster_import.parse(upload.read())
    if not rows:
        flash("Nothing imported. " + (problems[0] if problems else "No rows found."),
              "error")
        return redirect(url_for("admin.students"))

    department = request.form.get("department", "").strip()
    level = request.form.get("level", "").strip()

    # One query for every existing matric number, rather than one per row.
    existing = {r["MatricNo"] for r in db.query_db("SELECT MatricNo FROM Student")}

    added, duplicates, seen = 0, 0, set()
    for name, matric in rows:
        if matric in existing or matric in seen:
            duplicates += 1
            continue
        seen.add(matric)
        sid = db.execute_db(
            "INSERT INTO Student (FullName, MatricNo, Department, Level) "
            "VALUES (?, ?, ?, ?)", (name, matric, department, level))
        # student portal account, matching the single-student form
        db.execute_db(
            "INSERT OR IGNORE INTO User (Username, PasswordHash, Role, FullName, "
            "LinkedStudentID) VALUES (?, ?, 'student', ?, ?)",
            (matric, generate_password_hash("student123"), name, sid))
        added += 1

    if added:
        flash(f"Imported {added} student{'s' if added != 1 else ''}. "
              "Capture face shots next — they cannot verify at the kiosk until you do.",
              "success")
    if duplicates:
        flash(f"Skipped {duplicates} row{'s' if duplicates != 1 else ''} "
              "already registered with that matric number.", "info")
    if problems:
        shown = "; ".join(problems[:5])
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        flash(f"{len(problems)} row{'s' if len(problems) != 1 else ''} skipped — "
              f"{shown}{more}", "error")
    return redirect(url_for("admin.students"))


@bp.route("/enroll/<int:student_id>")
@role_required("admin")
def enroll(student_id):
    student = db.query_db("SELECT * FROM Student WHERE StudentID = ?",
                          (student_id,), one=True)
    if not student:
        flash("Student not found.", "error")
        return redirect(url_for("admin.students"))
    return render_template(
        "enroll.html", student=student,
        min_shots=config.MIN_ENROLL_SHOTS, max_shots=config.MAX_ENROLL_SHOTS,
        capture_url=url_for("admin.enroll_capture", student_id=student_id),
        back_url=url_for("admin.students"), back_label="Done — back to students")


def capture_shot(student):
    """Receive one webcam shot, extract its embedding, append to the student.
    Shared by the admin enrollment page and the lecturer roster flow."""
    student_id = student["StudentID"]
    data = request.get_json(silent=True) or {}
    image = embedding.decode_base64_image(data.get("image", ""))
    if image is None:
        return jsonify({"status": "error",
                        "message": "Could not decode the camera frame."})

    face = detection.detect_largest_face(image)
    if face is None:
        return jsonify({"status": "no_face",
                        "message": "No face detected — adjust lighting/position "
                                   "and capture again."})

    vec = embedding.extract_embedding(image, face)
    stored = json.loads(student["FaceEmbeddings"] or "[]")
    if len(stored) >= config.MAX_ENROLL_SHOTS:
        return jsonify({"status": "full",
                        "count": len(stored),
                        "message": f"Maximum of {config.MAX_ENROLL_SHOTS} shots "
                                   "already stored."})
    stored.append(vec)
    db.execute_db("UPDATE Student SET FaceEmbeddings = ? WHERE StudentID = ?",
                  (json.dumps(stored), student_id))
    done = len(stored) >= config.MIN_ENROLL_SHOTS
    return jsonify({"status": "ok", "count": len(stored),
                    "min_reached": done,
                    "message": f"Shot {len(stored)} stored."})


@bp.route("/enroll/<int:student_id>/capture", methods=["POST"])
@role_required("admin")
def enroll_capture(student_id):
    student = db.query_db("SELECT * FROM Student WHERE StudentID = ?",
                          (student_id,), one=True)
    if not student:
        return jsonify({"status": "error", "message": "Student not found."}), 404
    return capture_shot(student)


@bp.route("/enroll/<int:student_id>/reset", methods=["POST"])
@role_required("admin")
def enroll_reset(student_id):
    db.execute_db("UPDATE Student SET FaceEmbeddings = NULL WHERE StudentID = ?",
                  (student_id,))
    flash("Face data cleared — re-capture enrollment shots.", "info")
    return redirect(url_for("admin.enroll", student_id=student_id))


# ---------- courses ----------

@bp.route("/courses")
@role_required("admin")
def courses():
    rows = db.query_db(
        "SELECT c.*, u.FullName AS LecturerName FROM Course c "
        "LEFT JOIN User u ON u.UserID = c.LecturerID ORDER BY c.CourseCode")
    lecturers = db.query_db("SELECT * FROM User WHERE Role = 'lecturer'")
    return render_template("courses.html", courses=rows, lecturers=lecturers)


@bp.route("/courses/add", methods=["POST"])
@role_required("admin")
def add_course():
    f = request.form
    code, name = f.get("course_code", "").strip(), f.get("course_name", "").strip()
    if not code or not name:
        flash("Course code and name are required.", "error")
    else:
        db.execute_db(
            "INSERT INTO Course (CourseCode, CourseName, LecturerID, Department) "
            "VALUES (?, ?, ?, ?)",
            (code, name, f.get("lecturer_id") or None, f.get("department", "").strip()))
        flash(f"Course {code} added.", "success")
    return redirect(url_for("admin.courses"))


# ---------- settings ----------

@bp.route("/settings", methods=["GET", "POST"])
@role_required("admin")
def settings():
    if request.method == "POST":
        sim = request.form.get("similarity_threshold", "").strip()
        pct = request.form.get("attendance_percent_threshold", "").strip()
        try:
            sim_v, pct_v = float(sim), float(pct)
            if not (0 < sim_v < 1) or not (0 <= pct_v <= 100):
                raise ValueError
            db.set_setting("similarity_threshold", sim_v)
            db.set_setting("attendance_percent_threshold", pct_v)
            flash("Settings saved.", "success")
        except ValueError:
            flash("Similarity must be between 0 and 1; attendance % between "
                  "0 and 100.", "error")
        return redirect(url_for("admin.settings"))
    return render_template(
        "settings.html",
        similarity_threshold=db.get_similarity_threshold(),
        attendance_percent_threshold=db.get_attendance_percent_threshold())


# ---------- manual corrections ----------

@bp.route("/corrections")
@role_required("admin")
def corrections():
    session_id = request.args.get("session_id", type=int)
    sessions = db.query_db(
        "SELECT s.*, c.CourseCode FROM Session s "
        "JOIN Course c ON c.CourseID = s.CourseID ORDER BY s.StartTime DESC")
    rows = reports.session_attendance(session_id) if session_id else []
    return render_template("corrections.html", sessions=sessions,
                           selected_session=session_id, rows=rows,
                           flagged=reports.flagged_records())


@bp.route("/corrections/set", methods=["POST"])
@role_required("admin")
def set_record():
    """Create or override an attendance record (manual admin action)."""
    student_id = request.form.get("student_id", type=int)
    session_id = request.form.get("session_id", type=int)
    status = request.form.get("status")
    if status not in ("present", "absent", "flagged_manual_review"):
        flash("Invalid status.", "error")
        return redirect(request.referrer or url_for("admin.corrections"))
    sess = db.query_db("SELECT * FROM Session WHERE SessionID = ?",
                       (session_id,), one=True)
    existing = db.query_db(
        "SELECT * FROM Attendance WHERE StudentID = ? AND SessionID = ?",
        (student_id, session_id), one=True)
    if existing:
        db.execute_db("UPDATE Attendance SET Status = ? WHERE AttendanceID = ?",
                      (status, existing["AttendanceID"]))
    else:
        db.execute_db(
            "INSERT INTO Attendance (StudentID, CourseID, SessionID, Status) "
            "VALUES (?, ?, ?, ?)", (student_id, sess["CourseID"], session_id, status))
    flash("Record updated.", "success")
    return redirect(request.referrer
                    or url_for("admin.corrections", session_id=session_id))
