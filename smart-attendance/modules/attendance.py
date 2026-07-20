"""Verification flow: duplicate short-circuit, 1:1 face match, attempt limiting."""
import json
from datetime import datetime

from flask import session as flask_session

import config
from modules import db, detection, embedding


def get_active_sessions():
    """Sessions currently open (now between StartTime and EndTime)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return db.query_db(
        "SELECT s.*, c.CourseCode, c.CourseName, c.LecturerID FROM Session s "
        "JOIN Course c ON c.CourseID = s.CourseID "
        "WHERE ? BETWEEN s.StartTime AND s.EndTime ORDER BY s.StartTime",
        (now,),
    )


def get_session(session_id):
    return db.query_db(
        "SELECT s.*, c.CourseCode, c.CourseName, c.LecturerID FROM Session s "
        "JOIN Course c ON c.CourseID = s.CourseID WHERE s.SessionID = ?",
        (session_id,), one=True,
    )


def get_student_by_matric(matric_no):
    return db.query_db(
        "SELECT * FROM Student WHERE MatricNo = ?", (matric_no.strip(),), one=True
    )


def existing_record(student_id, session_id):
    return db.query_db(
        "SELECT * FROM Attendance WHERE StudentID = ? AND SessionID = ?",
        (student_id, session_id), one=True,
    )


def record_attendance(student_id, course_id, session_id, status="present"):
    return db.execute_db(
        "INSERT INTO Attendance (StudentID, CourseID, SessionID, Status, Timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (student_id, course_id, session_id, status,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


# ---- attempt limiting (kept in the signed Flask session cookie) ----

def _attempt_key(student_id, session_id):
    return f"verify_attempts:{session_id}:{student_id}"


def get_attempts(student_id, session_id):
    return int(flask_session.get(_attempt_key(student_id, session_id), 0))


def bump_attempts(student_id, session_id):
    key = _attempt_key(student_id, session_id)
    flask_session[key] = int(flask_session.get(key, 0)) + 1
    return flask_session[key]


def clear_attempts(student_id, session_id):
    flask_session.pop(_attempt_key(student_id, session_id), None)


# ---- core verification ----

def verify_frame(image_bgr, student_row):
    """1:1 verification of a live frame against ONE student's stored embeddings.

    Returns (matched: bool, similarity: float, error: str|None).
    error is set when the frame is unusable (no face found / no enrollment).
    """
    stored = json.loads(student_row["FaceEmbeddings"] or "[]")
    if not stored:
        return False, 0.0, "This student has no enrolled face data. Contact the admin."
    if image_bgr is None:
        return False, 0.0, "Could not decode the camera frame. Please try again."

    face = detection.detect_largest_face(image_bgr)
    if face is None:
        return False, 0.0, ("No face detected. Check lighting and face the camera "
                            "directly, then try again.")

    live = embedding.extract_embedding(image_bgr, face)
    similarity = embedding.best_similarity(live, stored)
    threshold = db.get_similarity_threshold()
    return similarity >= threshold, similarity, None


def process_verification(image_bgr, student_row, session_row):
    """Full attendance attempt: verify, log or count the failure, maybe flag.

    Returns a dict ready to be JSON-serialised to the browser.
    """
    student_id = student_row["StudentID"]
    session_id = session_row["SessionID"]

    # Safety net — the UI already short-circuits before opening the camera
    rec = existing_record(student_id, session_id)
    if rec:
        return _already_marked_response(rec)

    matched, similarity, error = verify_frame(image_bgr, student_row)

    if matched:
        record_attendance(student_id, session_row["CourseID"], session_id, "present")
        clear_attempts(student_id, session_id)
        return {
            "status": "success",
            "similarity": round(similarity, 3),
            "message": f"Welcome, {student_row['FullName']} — attendance recorded.",
        }

    if error:
        # Unusable frame (no face / no enrollment) — does not consume an attempt
        return {"status": "retry", "attempts_left": config.MAX_VERIFY_ATTEMPTS
                - get_attempts(student_id, session_id), "message": error}

    attempts = bump_attempts(student_id, session_id)
    if attempts >= config.MAX_VERIFY_ATTEMPTS:
        record_attendance(student_id, session_row["CourseID"], session_id,
                          "flagged_manual_review")
        clear_attempts(student_id, session_id)
        return {
            "status": "flagged",
            "similarity": round(similarity, 3),
            "message": ("Face could not be verified after "
                        f"{config.MAX_VERIFY_ATTEMPTS} attempts. Your record has been "
                        "flagged for manual review — please see your lecturer."),
        }

    return {
        "status": "retry",
        "similarity": round(similarity, 3),
        "attempts_left": config.MAX_VERIFY_ATTEMPTS - attempts,
        "message": (f"Face not recognized (attempt {attempts} of "
                    f"{config.MAX_VERIFY_ATTEMPTS}). Please try again."),
    }


def _already_marked_response(rec):
    if rec["Status"] == "flagged_manual_review":
        return {"status": "flagged",
                "message": ("Your record for this session is flagged for manual "
                            "review — please see your lecturer.")}
    return {"status": "already_marked",
            "message": f"Already marked present at {rec['Timestamp']}."}
