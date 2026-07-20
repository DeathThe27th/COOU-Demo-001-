"""Reporting: attendance summaries, low-attendance alerts, pivoted .xlsx export."""
import io

import pandas as pd

from modules import db


def course_sessions(course_id):
    return db.query_db(
        "SELECT * FROM Session WHERE CourseID = ? ORDER BY StartTime", (course_id,)
    )


def session_attendance(session_id):
    """The session's course roster with each student's status (absent if no row)."""
    return db.query_db(
        "SELECT st.StudentID, st.FullName, st.MatricNo, a.Status, a.Timestamp, "
        "       a.AttendanceID "
        "FROM CourseStudent cs "
        "JOIN Student st ON st.StudentID = cs.StudentID "
        "LEFT JOIN Attendance a ON a.StudentID = st.StudentID AND a.SessionID = ? "
        "WHERE cs.CourseID = (SELECT CourseID FROM Session WHERE SessionID = ?) "
        "ORDER BY st.MatricNo",
        (session_id, session_id),
    )


def course_summary(course_id):
    """Per-student presence count and percentage for a course."""
    sessions = course_sessions(course_id)
    total = len(sessions)
    rows = db.query_db(
        "SELECT st.StudentID, st.FullName, st.MatricNo, "
        "       COUNT(CASE WHEN a.Status = 'present' THEN 1 END) AS PresentCount "
        "FROM CourseStudent cs "
        "JOIN Student st ON st.StudentID = cs.StudentID "
        "LEFT JOIN Attendance a ON a.StudentID = st.StudentID "
        "     AND a.CourseID = cs.CourseID "
        "WHERE cs.CourseID = ? "
        "GROUP BY st.StudentID ORDER BY st.MatricNo",
        (course_id,),
    )
    summary = []
    for r in rows:
        pct = round(100.0 * r["PresentCount"] / total, 1) if total else 0.0
        summary.append(db.Row(r, TotalSessions=total, Percent=pct))
    return summary


def low_attendance_students(course_id):
    threshold = db.get_attendance_percent_threshold()
    return [s for s in course_summary(course_id) if s["Percent"] < threshold], threshold


def flagged_records():
    return db.query_db(
        "SELECT a.AttendanceID, a.Timestamp, st.FullName, st.MatricNo, "
        "       c.CourseCode, c.CourseName, s.Date, s.SessionID "
        "FROM Attendance a "
        "JOIN Student st ON st.StudentID = a.StudentID "
        "JOIN Course c ON c.CourseID = a.CourseID "
        "JOIN Session s ON s.SessionID = a.SessionID "
        "WHERE a.Status = 'flagged_manual_review' ORDER BY a.Timestamp DESC"
    )


def student_history(student_id):
    return db.query_db(
        "SELECT a.Status, a.Timestamp, c.CourseCode, c.CourseName, s.Date "
        "FROM Attendance a "
        "JOIN Course c ON c.CourseID = a.CourseID "
        "JOIN Session s ON s.SessionID = a.SessionID "
        "WHERE a.StudentID = ? ORDER BY a.Timestamp DESC",
        (student_id,),
    )


def export_course_xlsx(course_id):
    """Pivot: rows = students, columns = sessions, cells = Present/Absent,
    trailing Attendance % column. Returns (BytesIO, filename)."""
    course = db.query_db("SELECT * FROM Course WHERE CourseID = ?", (course_id,), one=True)
    sessions = course_sessions(course_id)
    students = db.query_db(
        "SELECT st.* FROM CourseStudent cs "
        "JOIN Student st ON st.StudentID = cs.StudentID "
        "WHERE cs.CourseID = ? ORDER BY st.MatricNo", (course_id,))
    attendance = db.query_db(
        "SELECT StudentID, SessionID, Status FROM Attendance WHERE CourseID = ?",
        (course_id,),
    )
    present = {(a["StudentID"], a["SessionID"])
               for a in attendance if a["Status"] == "present"}

    session_cols = [f"{s['Date']} (S{s['SessionID']})" for s in sessions]
    records = []
    for st in students:
        row = {"Matric No": st["MatricNo"], "Full Name": st["FullName"]}
        hits = 0
        for s, col in zip(sessions, session_cols):
            is_present = (st["StudentID"], s["SessionID"]) in present
            row[col] = "Present" if is_present else "Absent"
            hits += is_present
        row["Attendance %"] = round(100.0 * hits / len(sessions), 1) if sessions else 0.0
        records.append(row)

    df = pd.DataFrame(records,
                      columns=["Matric No", "Full Name", *session_cols, "Attendance %"])
    buf = io.BytesIO()
    sheet = f"{course['CourseCode']}"[:31]
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet)
        ws = writer.sheets[sheet]
        for col_cells in ws.columns:
            width = max(len(str(c.value or "")) for c in col_cells) + 2
            ws.column_dimensions[col_cells[0].column_letter].width = min(width, 30)
    buf.seek(0)
    filename = f"attendance_{course['CourseCode'].replace(' ', '')}.xlsx"
    return buf, filename
