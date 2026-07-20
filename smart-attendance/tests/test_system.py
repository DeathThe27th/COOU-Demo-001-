"""System tests: DB flows, verification logic (synthetic embeddings), reports.

Face-model tests use synthetic embeddings so they run without a webcam or the
ONNX files; the real detect→embed pipeline is exercised by test_real_pipeline
only when the model files are present.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "test.db")
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username, password):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=True)


# ---------- auth & roles ----------

def test_login_required_redirects(client):
    assert client.get("/admin/").status_code == 302


def test_admin_login_and_dashboard(client):
    resp = login(client, "admin", "admin123")
    assert b"Flagged for review" in resp.data


def test_wrong_password_rejected(client):
    resp = login(client, "admin", "wrong")
    assert b"Invalid username or password" in resp.data


def test_lecturer_cannot_access_admin(client):
    login(client, "lecturer1", "lecturer123")
    resp = client.get("/admin/students", follow_redirects=True)
    assert b"permission" in resp.data


def test_lecturer_signup_own_courses_and_isolation(client, app):
    """A lecturer can self-register, add their own course, and never see or
    touch another lecturer's sessions/reports."""
    resp = client.post("/signup", data={
        "full_name": "Dr. Ada Nwafor", "username": "anwafor",
        "password": "secret6", "confirm_password": "secret6"},
        follow_redirects=True)
    assert b"Welcome, Dr. Ada Nwafor" in resp.data

    resp = client.post("/lecturer/courses/add", data={
        "course_code": "CSC 499", "course_name": "Final Year Project",
        "department": "Computer Science"}, follow_redirects=True)
    assert b"added to your dashboard" in resp.data
    assert b"CSC 499" in resp.data

    # seeded session/course belong to lecturer1, not the new signup
    with app.app_context():
        from modules import db
        other = db.query_db(
            "SELECT s.SessionID, s.CourseID FROM Session s "
            "JOIN Course c ON c.CourseID = s.CourseID "
            "JOIN User u ON u.UserID = c.LecturerID "
            "WHERE u.Username = 'lecturer1' LIMIT 1", one=True)

    resp = client.get(f"/lecturer/sessions/{other['SessionID']}",
                      follow_redirects=True)
    assert b"Session not found" in resp.data
    assert client.get(
        f"/lecturer/sessions/{other['SessionID']}/live.json").status_code == 404
    resp = client.post(f"/lecturer/sessions/{other['SessionID']}/close",
                       follow_redirects=True)
    assert b"Session closed" not in resp.data
    resp = client.get(f"/lecturer/reports/{other['CourseID']}/export.xlsx",
                      follow_redirects=True)
    assert b"your own courses" in resp.data
    # opening a session for someone else's course is also blocked
    resp = client.post("/lecturer/sessions/create", data={
        "course_id": other["CourseID"], "duration_minutes": 60},
        follow_redirects=True)
    assert b"only open sessions for your own courses" in resp.data


def test_signup_rejects_duplicate_username_and_bad_confirm(client):
    resp = client.post("/signup", data={
        "full_name": "X", "username": "lecturer1",
        "password": "secret6", "confirm_password": "secret6"},
        follow_redirects=True)
    assert b"already taken" in resp.data
    resp = client.post("/signup", data={
        "full_name": "X", "username": "newperson",
        "password": "secret6", "confirm_password": "different"},
        follow_redirects=True)
    assert b"do not match" in resp.data


# ---------- kiosk flow with synthetic embeddings ----------

@pytest.fixture()
def enrolled_student(app):
    """Give seeded student 2021/CS/001 a fake enrolled embedding set."""
    with app.app_context():
        from modules import db
        emb = [[1.0] + [0.0] * 127] * 5  # unit vector along axis 0
        db.execute_db(
            "UPDATE Student SET FaceEmbeddings = ? WHERE MatricNo = '2021/CS/001'",
            (json.dumps(emb),))
        student = db.query_db(
            "SELECT * FROM Student WHERE MatricNo = '2021/CS/001'", one=True)
        session = db.query_db("SELECT * FROM Session LIMIT 1", one=True)
        return dict(student), dict(session)


def test_kiosk_requires_login(client, enrolled_student):
    _, sess = enrolled_student
    assert client.get("/attend").status_code == 302
    assert client.post("/attend/check", json={
        "matric_no": "2021/CS/001",
        "session_id": sess["SessionID"]}).status_code == 302


def test_login_page_redirects_when_already_signed_in(client):
    login(client, "lecturer1", "lecturer123")
    assert client.get("/login").status_code == 302


def test_kiosk_scopes_sessions_to_lecturer(client, enrolled_student):
    """A lecturer's kiosk lists only their own open sessions and refuses
    to run against another lecturer's session."""
    _, sess = enrolled_student
    client.post("/signup", data={
        "full_name": "Dr. C", "username": "drc",
        "password": "secret6", "confirm_password": "secret6"})
    resp = client.get("/attend")
    assert b"No active session found" in resp.data
    r = client.post("/attend/check", json={
        "matric_no": "2021/CS/001", "session_id": sess["SessionID"]}).get_json()
    assert r["status"] == "error"


def test_kiosk_check_unknown_matric(client, enrolled_student):
    _, sess = enrolled_student
    login(client, "lecturer1", "lecturer123")
    resp = client.post("/attend/check", json={
        "matric_no": "9999/XX/999", "session_id": sess["SessionID"]}).get_json()
    assert resp["status"] == "error"
    assert "No student found" in resp["message"]


def test_kiosk_check_ok_then_duplicate_short_circuit(client, app, enrolled_student):
    student, sess = enrolled_student
    login(client, "lecturer1", "lecturer123")
    body = {"matric_no": student["MatricNo"], "session_id": sess["SessionID"]}

    resp = client.post("/attend/check", json=body).get_json()
    assert resp["status"] == "ok"

    with app.app_context():
        from modules import attendance
        attendance.record_attendance(student["StudentID"], sess["CourseID"],
                                     sess["SessionID"])
    resp = client.post("/attend/check", json=body).get_json()
    assert resp["status"] == "already_marked"  # camera never opens


def test_verification_match_and_mismatch(app, enrolled_student):
    """Cosine similarity decision logic, independent of camera/model."""
    from modules import embedding
    stored = [[1.0] + [0.0] * 127]
    same = [1.0] + [0.01] * 127           # nearly identical direction (cos ~0.994)
    different = [0.0, 1.0] + [0.0] * 126  # orthogonal
    assert embedding.best_similarity(same, stored) > 0.9
    assert embedding.best_similarity(different, stored) < 0.1


def test_three_failed_attempts_flags_record(client, app, enrolled_student, monkeypatch):
    student, sess = enrolled_student
    login(client, "lecturer1", "lecturer123")
    from modules import attendance as att
    # Simulate "face detected but similarity below threshold" on every frame
    monkeypatch.setattr(att, "verify_frame", lambda img, st: (False, 0.2, None))

    body = {"matric_no": student["MatricNo"], "session_id": sess["SessionID"],
            "image": "data:image/jpeg;base64,AAAA"}
    r1 = client.post("/attend/verify", json=body).get_json()
    r2 = client.post("/attend/verify", json=body).get_json()
    assert r1["status"] == "retry" and r1["attempts_left"] == 2
    assert r2["status"] == "retry" and r2["attempts_left"] == 1
    r3 = client.post("/attend/verify", json=body).get_json()
    assert r3["status"] == "flagged"

    with app.app_context():
        from modules import db
        rec = db.query_db(
            "SELECT * FROM Attendance WHERE StudentID = ? AND SessionID = ?",
            (student["StudentID"], sess["SessionID"]), one=True)
        assert rec["Status"] == "flagged_manual_review"


def test_successful_verify_logs_attendance(client, app, enrolled_student, monkeypatch):
    student, sess = enrolled_student
    login(client, "lecturer1", "lecturer123")
    from modules import attendance as att
    monkeypatch.setattr(att, "verify_frame", lambda img, st: (True, 0.82, None))

    body = {"matric_no": student["MatricNo"], "session_id": sess["SessionID"],
            "image": "data:image/jpeg;base64,AAAA"}
    resp = client.post("/attend/verify", json=body).get_json()
    assert resp["status"] == "success"

    # duplicate protection at DB level: second verify short-circuits
    resp2 = client.post("/attend/verify", json=body).get_json()
    assert resp2["status"] == "already_marked"


def test_unenrolled_student_gets_clear_error(client, app, enrolled_student):
    _, sess = enrolled_student
    login(client, "lecturer1", "lecturer123")
    body = {"matric_no": "2021/CS/002", "session_id": sess["SessionID"],
            "image": "data:image/jpeg;base64,AAAA"}
    resp = client.post("/attend/verify", json=body).get_json()
    assert resp["status"] == "retry"
    assert "no enrolled face data" in resp["message"]


# ---------- course roster ----------

def test_roster_scopes_kiosk_and_reports(client, app, enrolled_student):
    """A student who isn't on a course's list can't mark attendance there,
    and course reports cover exactly the course list."""
    _, sess = enrolled_student
    login(client, "admin", "admin123")
    client.post("/admin/students/add", data={
        "full_name": "Outsider Obi", "matric_no": "2021/CS/099",
        "department": "Computer Science", "level": "400"})

    resp = client.post("/attend/check", json={
        "matric_no": "2021/CS/099", "session_id": sess["SessionID"]}).get_json()
    assert resp["status"] == "error"
    assert "not registered for" in resp["message"]

    with app.app_context():
        from modules import reports as rp
        matrics = [r["MatricNo"] for r in rp.course_summary(sess["CourseID"])]
    assert "2021/CS/099" not in matrics
    assert "2021/CS/001" in matrics


def test_lecturer_manages_roster_for_own_course_only(client, app, enrolled_student):
    _, sess = enrolled_student
    login(client, "lecturer1", "lecturer123")

    resp = client.get(f"/lecturer/courses/{sess['CourseID']}/students")
    assert resp.status_code == 200

    # brand-new matric + full name → registered and added to the course
    resp = client.post(
        f"/lecturer/courses/{sess['CourseID']}/students/add",
        data={"matric_no": "2021/CS/050", "full_name": "Ngozi Okeke",
              "level": "400"}, follow_redirects=True)
    assert b"registered and added" in resp.data

    # she can now mark attendance for this course (roster check passes)
    resp = client.post("/attend/check", json={
        "matric_no": "2021/CS/050", "session_id": sess["SessionID"]}).get_json()
    assert resp["status"] == "retry" or resp["status"] == "ok" or \
        "no enrolled face data" in resp.get("message", "")

    # remove puts her off the roster again
    with app.app_context():
        from modules import db as dbm
        sid = dbm.query_db("SELECT StudentID FROM Student WHERE MatricNo = "
                           "'2021/CS/050'", one=True)["StudentID"]
    client.post(f"/lecturer/courses/{sess['CourseID']}/students/remove",
                data={"student_id": sid})
    resp = client.post("/attend/check", json={
        "matric_no": "2021/CS/050", "session_id": sess["SessionID"]}).get_json()
    assert "not registered for" in resp["message"]

    # a different lecturer can't even view this course's roster
    client.get("/logout")
    client.post("/signup", data={
        "full_name": "Dr. B", "username": "drb",
        "password": "secret6", "confirm_password": "secret6"})
    resp = client.get(f"/lecturer/courses/{sess['CourseID']}/students",
                      follow_redirects=True)
    assert b"Course not found" in resp.data


# ---------- reports & export ----------

def test_xlsx_export(client, app, enrolled_student):
    student, sess = enrolled_student
    with app.app_context():
        from modules import attendance
        attendance.record_attendance(student["StudentID"], sess["CourseID"],
                                     sess["SessionID"])
    login(client, "lecturer1", "lecturer123")
    resp = client.get(f"/lecturer/reports/{sess['CourseID']}/export.xlsx")
    assert resp.status_code == 200
    assert resp.data[:2] == b"PK"  # valid zip/xlsx container

    import io
    import pandas as pd
    df = pd.read_excel(io.BytesIO(resp.data))
    assert "Attendance %" in df.columns
    row = df[df["Matric No"] == student["MatricNo"]].iloc[0]
    assert row["Attendance %"] == 100.0


def test_admin_manual_correction_override(client, app, enrolled_student):
    student, sess = enrolled_student
    login(client, "admin", "admin123")
    client.post("/admin/corrections/set", data={
        "student_id": student["StudentID"], "session_id": sess["SessionID"],
        "status": "present"})
    with app.app_context():
        from modules import db
        rec = db.query_db(
            "SELECT * FROM Attendance WHERE StudentID = ? AND SessionID = ?",
            (student["StudentID"], sess["SessionID"]), one=True)
        assert rec["Status"] == "present"


# ---------- real ONNX pipeline (only when models are downloaded) ----------

@pytest.mark.skipif(not (config.YUNET_MODEL_PATH.exists()
                         and config.SFACE_MODEL_PATH.exists()),
                    reason="model files not downloaded (run setup_models.py)")
def test_real_pipeline_no_face_in_blank_image():
    import numpy as np
    from modules import detection
    blank = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert detection.detect_largest_face(blank) is None
