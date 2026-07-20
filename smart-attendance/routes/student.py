"""Attendance kiosk (matric entry → 1:1 face verification) + student history view.

The kiosk (/attend) runs on the lecturer's signed-in machine during class.
It only operates on sessions that are currently open — and for lecturers,
only on their own sessions. It never exposes face data: frames go in, only
a match/no-match decision comes out.
"""
from flask import Blueprint, jsonify, render_template, request

from modules import attendance, db, embedding, reports
from routes.auth import current_user, role_required

bp = Blueprint("student", __name__)


def _visible_sessions(user):
    """Active sessions this account may run the kiosk for: lecturers get
    their own sessions, students the courses they're registered in, admin all."""
    sessions = attendance.get_active_sessions()
    if user["Role"] == "lecturer":
        return [s for s in sessions if s["LecturerID"] == user["UserID"]]
    if user["Role"] == "student":
        rostered = {r["CourseID"] for r in db.query_db(
            "SELECT CourseID FROM CourseStudent WHERE StudentID = ?",
            (user["LinkedStudentID"],))}
        return [s for s in sessions if s["CourseID"] in rostered]
    return sessions


# ---------- kiosk ----------

@bp.route("/attend")
@role_required("admin", "lecturer", "student")
def kiosk():
    sessions = _visible_sessions(current_user())
    preselected = request.args.get("session_id", type=int)
    return render_template("attendance_verify.html", sessions=sessions,
                           preselected=preselected)


def _validate_kiosk_request(data):
    """Common validation for kiosk POSTs. Returns (student, session, error_json)."""
    matric = (data.get("matric_no") or "").strip()
    session_id = data.get("session_id")
    if not matric or not session_id:
        return None, None, {"status": "error",
                            "message": "Enter your matric number first."}
    sess = attendance.get_session(session_id)
    if not sess:
        return None, None, {"status": "error", "message": "Unknown session."}
    if not any(s["SessionID"] == sess["SessionID"]
               for s in _visible_sessions(current_user())):
        return None, None, {"status": "error",
                            "message": "This session is no longer open."}
    student = attendance.get_student_by_matric(matric)
    if not student:
        return None, None, {"status": "error",
                            "message": f"No student found with matric number "
                                       f"“{matric}”. Check and re-enter."}
    on_roster = db.query_db(
        "SELECT 1 FROM CourseStudent WHERE CourseID = ? AND StudentID = ?",
        (sess["CourseID"], student["StudentID"]), one=True)
    if not on_roster:
        return None, None, {
            "status": "error",
            "message": f"{student['FullName']}, you are not registered for "
                       f"{sess['CourseCode']} — ask your lecturer to add you "
                       "to the course list."}
    return student, sess, None


@bp.route("/attend/check", methods=["POST"])
@role_required("admin", "lecturer", "student")
def kiosk_check():
    """Duplicate short-circuit — runs BEFORE the camera is opened at all."""
    student, sess, error = _validate_kiosk_request(request.get_json(silent=True) or {})
    if error:
        return jsonify(error)
    rec = attendance.existing_record(student["StudentID"], sess["SessionID"])
    if rec:
        return jsonify(attendance._already_marked_response(rec))
    return jsonify({"status": "ok",
                    "student_name": student["FullName"],
                    "message": f"Hello {student['FullName']} — position your face "
                               "in front of the camera."})


@bp.route("/attend/verify", methods=["POST"])
@role_required("admin", "lecturer", "student")
def kiosk_verify():
    """Receive one live frame and run the full 1:1 verification flow."""
    data = request.get_json(silent=True) or {}
    student, sess, error = _validate_kiosk_request(data)
    if error:
        return jsonify(error)
    image = embedding.decode_base64_image(data.get("image", ""))
    return jsonify(attendance.process_verification(image, student, sess))


# ---------- logged-in student history ----------

@bp.route("/my-attendance")
@role_required("student")
def my_attendance():
    user = current_user()
    student = db.query_db("SELECT * FROM Student WHERE StudentID = ?",
                          (user["LinkedStudentID"],), one=True)
    history = reports.student_history(student["StudentID"]) if student else []
    return render_template("my_attendance.html", student=student, history=history)
