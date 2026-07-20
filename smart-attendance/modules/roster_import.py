"""Parse a student roster CSV into (full_name, matric_no) pairs.

Pure parsing — no database access — so the messy real-world cases (Excel BOMs,
semicolon delimiters, header spellings, columns in either order) can be tested
directly. routes/admin.py does the inserting.

Lecturers export these lists from Excel, Google Sheets, or the departmental
portal, and every one of those produces a slightly different file. The parser
accepts what they actually produce rather than demanding one exact shape.
"""
import csv
import io
import re

# Accepted spellings for each column, normalised (lowercased, punctuation and
# spacing stripped) before comparison.
NAME_KEYS = {
    "name", "fullname", "studentname", "student", "names", "surname",
    "fullnames", "nameofstudent",
}
MATRIC_KEYS = {
    "matricno", "matric", "matricnumber", "matriculationnumber", "matricnumbers",
    "regno", "regnumber", "registrationno", "registrationnumber", "registration",
    "jambno", "studentid", "id",
}

MAX_ROWS = 2000  # a sane ceiling; a class list is nowhere near this


def _norm(value):
    """Lowercase and strip everything that varies between exports."""
    return re.sub(r"[^a-z0-9]", "", (value or "").strip().lower())


def _looks_like_matric(value):
    """Matric numbers carry digits; names essentially never do."""
    return bool(re.search(r"\d", value or ""))


def _decode(raw):
    """Decode uploaded bytes, tolerating Excel's UTF-8 BOM and legacy encodings."""
    if isinstance(raw, str):
        return raw
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse(raw):
    """Parse CSV bytes/text.

    Returns (rows, problems) where rows is a list of (full_name, matric_no) in
    file order and problems is a list of human-readable strings naming the line
    number and what was wrong with it. Duplicates are not resolved here — the
    caller checks those against the database.
    """
    text = _decode(raw)
    if not text.strip():
        return [], ["The file is empty."]

    # Sniff the delimiter; several African university portals export semicolon
    # or tab separated files that are still named .csv.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    records = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    records = [r for r in records if any((c or "").strip() for c in r)]
    if not records:
        return [], ["The file has no readable rows."]

    name_idx, matric_idx, start = _resolve_columns(records[0])
    if name_idx is None:
        return [], [
            "Could not tell which column holds the name and which holds the "
            "matric number. Add a header row with 'Full Name' and 'Matric No'."
        ]

    rows, problems, line = [], [], start
    for record in records[start:]:
        line += 1
        if len(records) > MAX_ROWS:
            problems.append(f"Stopped at {MAX_ROWS} rows — split the file and retry.")
            break
        name = (record[name_idx] if name_idx < len(record) else "").strip()
        matric = (record[matric_idx] if matric_idx < len(record) else "").strip()
        if not name and not matric:
            continue
        if not name:
            problems.append(f"Row {line}: missing name (matric {matric}).")
            continue
        if not matric:
            problems.append(f"Row {line}: missing matric number (for {name}).")
            continue
        rows.append((" ".join(name.split()), matric))
    return rows, problems


def _resolve_columns(first_row):
    """Work out which column is which, and whether row one is a header.

    Returns (name_idx, matric_idx, first_data_row_index).
    """
    normalised = [_norm(c) for c in first_row]

    name_idx = next((i for i, c in enumerate(normalised) if c in NAME_KEYS), None)
    matric_idx = next((i for i, c in enumerate(normalised) if c in MATRIC_KEYS), None)
    if name_idx is not None and matric_idx is not None:
        return name_idx, matric_idx, 1

    # No usable header. Fall back to a two-column positional read, deciding the
    # order by which cell contains digits — matric numbers do, names do not.
    cells = [(c or "").strip() for c in first_row]
    if len(cells) >= 2:
        first_is_matric = _looks_like_matric(cells[0])
        second_is_matric = _looks_like_matric(cells[1])
        if first_is_matric and not second_is_matric:
            return 1, 0, 0
        if second_is_matric and not first_is_matric:
            return 0, 1, 0
        # Ambiguous but still two columns: assume the common "name, matric" order.
        return 0, 1, 0
    return None, None, 0


def template_csv():
    """A correctly-shaped example file, offered as a download on the import card."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Full Name", "Matric No"])
    w.writerow(["Somto Okafor", "2021/CS/001"])
    w.writerow(["Adaeze Nwosu", "2021/CS/002"])
    w.writerow(["Chinedu Eze", "2021/CS/003"])
    return buf.getvalue()
