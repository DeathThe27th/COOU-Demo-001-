"""Lecturer: courses + rosters, sessions, live attendance, reports, .xlsx export."""
import json
from datetime import datetime, timedelta

from flask import (Blueprint, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.security import generate_password_hash

import config
from modules import db, reports
from routes.auth import current_user, role_required

bp = Blueprint("lecturer", __name__, url_prefix="/lecturer")


def _my_courses():
    user = current_user()
    if user["Role"] == "admin":
        return db.query_db("SELECT * FROM Course ORDER BY CourseCode")
    return db.query_db("SELECT * FROM Course WHERE LecturerID = ? ORDER BY CourseCode",
                       (user["UserID"],))


def _owns_course(course_id):
    return any(c["CourseID"] == course_id for c in _my_courses())


def _owned_session(session_id):
    """The session (joined with its course), but only if the current user
    may manage it — admins see everything, lecturers only their courses."""
    user = current_user()
    sess = db.query_db(
        "SELECT s.*, c.CourseCode, c.CourseName, c.LecturerID FROM Session s "
        "JOIN Course c ON c.CourseID = s.CourseID WHERE s.SessionID = ?",
        (session_id,), one=True)
    if not sess:
        return None
    if user["Role"] != "admin" and sess["LecturerID"] != user["UserID"]:
        return None
    return sess


@bp.route("/")
@role_required("lecturer", "admin")
def dashboard():
    courses = _my_courses()
    course_ids = [c["CourseID"] for c in courses]
    sessions = []
    if course_ids:
        marks = ",".join("?" * len(course_ids))
        sessions = db.query_db(
            f"SELECT s.*, c.CourseCode, c.CourseName, "
            f"  (SELECT COUNT(*) FROM Attendance a WHERE a.SessionID = s.SessionID "
            f"   AND a.Status = 'present') AS PresentCount "
            f"FROM Session s JOIN Course c ON c.CourseID = s.CourseID "
            f"WHERE s.CourseID IN ({marks}) ORDER BY s.StartTime DESC LIMIT 20",
            course_ids)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    roster_counts = {r["CourseID"]: r["c"] for r in db.query_db(
        "SELECT CourseID, COUNT(*) c FROM CourseStudent GROUP BY CourseID")}
    return render_template("dashboard.html", role="lecturer", courses=courses,
                           sessions=sessions, now=now,
                           roster_counts=roster_counts)


@bp.route("/courses/add", methods=["POST"])
@role_required("lecturer", "admin")
def add_course():
    """Lecturers register the courses they teach; each course they add is
    theirs alone — it never appears on another lecturer's dashboard."""
    user = current_user()
    code = request.form.get("course_code", "").strip().upper()
    name = request.form.get("course_name", "").strip()
    if not code or not name:
        flash("Course code and name are required.", "error")
    elif db.query_db("SELECT 1 FROM Course WHERE CourseCode = ?", (code,), one=True):
        flash(f"{code} is already registered. If it's your course, ask the "
              "admin to assign it to you.", "error")
    else:
        db.execute_db(
            "INSERT INTO Course (CourseCode, CourseName, LecturerID, Department) "
            "VALUES (?, ?, ?, ?)",
            (code, name, user["UserID"],
             request.form.get("department", "").strip() or None))
        flash(f"Course {code} added to your dashboard.", "success")
    return redirect(url_for("lecturer.dashboard"))


# ---------- course roster ----------

def _owned_course(course_id):
    user = current_user()
    course = db.query_db("SELECT * FROM Course WHERE CourseID = ?",
                         (course_id,), one=True)
    if not course:
        return None
    if user["Role"] != "admin" and course["LecturerID"] != user["UserID"]:
        return None
    return course


@bp.route("/courses/<int:course_id>/students")
@role_required("lecturer", "admin")
def course_students(course_id):
    course = _owned_course(course_id)
    if not course:
        flash("Course not found.", "error")
        return redirect(url_for("lecturer.dashboard"))
    roster = db.query_db(
        "SELECT st.* FROM CourseStudent cs "
        "JOIN Student st ON st.StudentID = cs.StudentID "
        "WHERE cs.CourseID = ? ORDER BY st.MatricNo", (course_id,))
    enriched = [db.Row(r, Shots=len(json.loads(r["FaceEmbeddings"] or "[]")))
                for r in roster]
    return render_template("course_students.html", course=course, roster=enriched,
                           min_shots=config.MIN_ENROLL_SHOTS)


@bp.route("/courses/<int:course_id>/students/add", methods=["POST"])
@role_required("lecturer", "admin")
def course_students_add(course_id):
    """Add a student to the course list by matric number. If the matric isn't
    registered yet and a full name was supplied, register the student too."""
    course = _owned_course(course_id)
    if not course:
        flash("Course not found.", "error")
        return redirect(url_for("lecturer.dashboard"))
    matric = request.form.get("matric_no", "").strip()
    if not matric:
        flash("Enter the student's matric number.", "error")
        return redirect(url_for("lecturer.course_students", course_id=course_id))

    student = db.query_db("SELECT * FROM Student WHERE MatricNo = ?",
                          (matric,), one=True)
    if student:
        already = db.query_db(
            "SELECT 1 FROM CourseStudent WHERE CourseID = ? AND StudentID = ?",
            (course_id, student["StudentID"]), one=True)
        if already:
            flash(f"{student['FullName']} is already in {course['CourseCode']}.",
                  "info")
        else:
            db.execute_db(
                "INSERT INTO CourseStudent (CourseID, StudentID) VALUES (?, ?)",
                (course_id, student["StudentID"]))
            flash(f"{student['FullName']} added to {course['CourseCode']}.",
                  "success")
        return redirect(url_for("lecturer.course_students", course_id=course_id))

    name = request.form.get("full_name", "").strip()
    if not name:
        flash(f"No student with matric number {matric} is registered yet — "
              "enter their full name as well to register them.", "error")
        return redirect(url_for("lecturer.course_students", course_id=course_id))
    sid = db.execute_db(
        "INSERT INTO Student (FullName, MatricNo, Department, Level) "
        "VALUES (?, ?, ?, ?)",
        (name, matric, course["Department"] or "",
         request.form.get("level", "").strip()))
    # student portal account (username = matric no), same as the admin flow
    db.execute_db(
        "INSERT OR IGNORE INTO User (Username, PasswordHash, Role, FullName, "
        "LinkedStudentID) VALUES (?, ?, 'student', ?, ?)",
        (matric, generate_password_hash("student123"), name, sid))
    db.execute_db("INSERT INTO CourseStudent (CourseID, StudentID) VALUES (?, ?)",
                  (course_id, sid))
    flash(f"{name} registered and added to {course['CourseCode']}. "
          "Now capture their face shots.", "success")
    return redirect(url_for("lecturer.enroll", student_id=sid,
                            course_id=course_id))


@bp.route("/courses/<int:course_id>/students/remove", methods=["POST"])
@role_required("lecturer", "admin")
def course_students_remove(course_id):
    course = _owned_course(course_id)
    if not course:
        flash("Course not found.", "error")
        return redirect(url_for("lecturer.dashboard"))
    student_id = request.form.get("student_id", type=int)
    db.execute_db("DELETE FROM CourseStudent WHERE CourseID = ? AND StudentID = ?",
                  (course_id, student_id))
    flash("Student removed from the course list (their attendance history "
          "is kept).", "info")
    return redirect(url_for("lecturer.course_students", course_id=course_id))


# ---------- face enrollment (lecturer-scoped) ----------

def _enrollable_student(student_id):
    """The student, if the lecturer may capture face shots for them: they must
    be on one of the lecturer's course lists and not yet fully enrolled.
    Fully-enrolled students can only be re-captured or cleared by the admin,
    so one lecturer can never overwrite face data other courses rely on."""
    user = current_user()
    student = db.query_db("SELECT * FROM Student WHERE StudentID = ?",
                          (student_id,), one=True)
    if not student:
        return None, "Student not found."
    if user["Role"] != "admin":
        on_my_roster = db.query_db(
            "SELECT 1 FROM CourseStudent cs "
            "JOIN Course c ON c.CourseID = cs.CourseID "
            "WHERE cs.StudentID = ? AND c.LecturerID = ?",
            (student_id, user["UserID"]), one=True)
        if not on_my_roster:
            return None, "Student not found."
        shots = len(json.loads(student["FaceEmbeddings"] or "[]"))
        if shots >= config.MIN_ENROLL_SHOTS:
            return None, ("This student is already fully face-enrolled — only "
                          "the admin can re-capture or clear face data.")
    return student, None


@bp.route("/enroll/<int:student_id>")
@role_required("lecturer", "admin")
def enroll(student_id):
    student, err = _enrollable_student(student_id)
    course_id = request.args.get("course_id", type=int)
    back_url = (url_for("lecturer.course_students", course_id=course_id)
                if course_id and _owned_course(course_id)
                else url_for("lecturer.dashboard"))
    if not student:
        flash(err, "error")
        return redirect(back_url)
    return render_template(
        "enroll.html", student=student,
        min_shots=config.MIN_ENROLL_SHOTS, max_shots=config.MAX_ENROLL_SHOTS,
        capture_url=url_for("lecturer.enroll_capture", student_id=student_id),
        back_url=back_url, back_label="Done — back to course list")


@bp.route("/enroll/<int:student_id>/capture", methods=["POST"])
@role_required("lecturer", "admin")
def enroll_capture(student_id):
    from routes.admin import capture_shot
    student, err = _enrollable_student(student_id)
    if not student:
        return jsonify({"status": "error", "message": err})
    return capture_shot(student)


@bp.route("/sessions/create", methods=["POST"])
@role_required("lecturer", "admin")
def create_session():
    course_id = request.form.get("course_id", type=int)
    duration = request.form.get("duration_minutes", default=60, type=int)
    if not any(c["CourseID"] == course_id for c in _my_courses()):
        flash("You can only open sessions for your own courses.", "error")
        return redirect(url_for("lecturer.dashboard"))
    now = datetime.now()
    sid = db.execute_db(
        "INSERT INTO Session (CourseID, Date, StartTime, EndTime) VALUES (?, ?, ?, ?)",
        (course_id, now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S"),
         (now + timedelta(minutes=duration)).strftime("%Y-%m-%d %H:%M:%S")))
    flash("Session opened. Share the attendance kiosk with the class.", "success")
    return redirect(url_for("lecturer.session_detail", session_id=sid))


@bp.route("/sessions/<int:session_id>")
@role_required("lecturer", "admin")
def session_detail(session_id):
    sess = _owned_session(session_id)
    if not sess:
        flash("Session not found.", "error")
        return redirect(url_for("lecturer.dashboard"))
    rows = reports.session_attendance(session_id)
    active = (sess["StartTime"] <= datetime.now().strftime("%Y-%m-%d %H:%M:%S")
              <= sess["EndTime"])
    return render_template("session_detail.html", sess=sess, rows=rows, active=active)


@bp.route("/sessions/<int:session_id>/live.json")
@role_required("lecturer", "admin")
def session_live(session_id):
    """Polled by dashboard.js for the live attendance table."""
    if not _owned_session(session_id):
        return jsonify([]), 404
    rows = reports.session_attendance(session_id)
    return jsonify([
        {"matric": r["MatricNo"], "name": r["FullName"],
         "status": r["Status"] or "absent", "time": r["Timestamp"] or ""}
        for r in rows])


@bp.route("/sessions/<int:session_id>/close", methods=["POST"])
@role_required("lecturer", "admin")
def close_session(session_id):
    if not _owned_session(session_id):
        flash("Session not found.", "error")
        return redirect(url_for("lecturer.dashboard"))
    db.execute_db("UPDATE Session SET EndTime = ? WHERE SessionID = ?",
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session_id))
    flash("Session closed.", "info")
    return redirect(url_for("lecturer.session_detail", session_id=session_id))


@bp.route("/reports")
@role_required("lecturer", "admin")
def report_view():
    courses = _my_courses()
    course_id = request.args.get("course_id", type=int)
    if course_id and not _owns_course(course_id):
        flash("You can only view reports for your own courses.", "error")
        course_id = None
    course_id = course_id or (courses[0]["CourseID"] if courses else None)
    summary, low, threshold, sessions = [], [], None, []
    if course_id:
        summary = reports.course_summary(course_id)
        low, threshold = reports.low_attendance_students(course_id)
        sessions = reports.course_sessions(course_id)
    return render_template("reports.html", courses=courses, course_id=course_id,
                           summary=summary, low=low, threshold=threshold,
                           sessions=sessions)


@bp.route("/reports/<int:course_id>/export.xlsx")
@role_required("lecturer", "admin")
def export_xlsx(course_id):
    if not _owns_course(course_id):
        flash("You can only export reports for your own courses.", "error")
        return redirect(url_for("lecturer.report_view"))
    buf, filename = reports.export_course_xlsx(course_id)
    return send_file(
        buf, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
