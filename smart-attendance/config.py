"""Central configuration for the Smart Attendance System."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Load .env for local development if python-dotenv is installed. Optional by
# design: the offline demo needs no configuration at all, and hosted
# deployments get their environment from the platform, so a missing dotenv
# must never be fatal.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

# --- Flask ---
SECRET_KEY = os.environ.get("SECRET_KEY", "coou-smart-attendance-dev-key")

# --- Database ---
# Two backends, one codebase:
#
#   DATABASE_URL unset  -> SQLite at database/attendance.db. This is the offline
#                          demo path; no network, nothing to configure.
#   DATABASE_URL set    -> PostgreSQL (Supabase). Used by the hosted deployment,
#                          where the container filesystem is ephemeral and a
#                          local SQLite file would be wiped on every restart.
#
# Note for Supabase: use the *Session pooler* connection string (IPv4). The
# direct-connection host is IPv6-only and will not resolve from Render. If the
# password contains reserved URI characters (@ : / ?) they must be
# percent-encoded, or the URI parser will split the string at the wrong place.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

DATABASE_PATH = BASE_DIR / "database" / "attendance.db"
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"
SCHEMA_POSTGRES_PATH = BASE_DIR / "database" / "schema_postgres.sql"

# --- Face model files (downloaded once by setup_models.py) ---
MODELS_DIR = BASE_DIR / "models"
YUNET_MODEL_PATH = MODELS_DIR / "face_detection_yunet.onnx"
SFACE_MODEL_PATH = MODELS_DIR / "face_recognition_sface.onnx"

# --- Face detection ---
DETECTION_SCORE_THRESHOLD = 0.7  # YuNet confidence for a valid face

# --- Face verification ---
# Cosine similarity threshold for SFace 1:1 verification.
# Spec starting point: 0.5 (tune empirically; OpenCV's published SFace
# cosine benchmark threshold is 0.363 — lower it if false rejects are high).
# This is only the seed default; the live value is stored in the Settings
# table and editable from the admin settings page.
DEFAULT_SIMILARITY_THRESHOLD = 0.5

# Enrollment shots
MIN_ENROLL_SHOTS = 5
MAX_ENROLL_SHOTS = 8

# Verification attempts before flagging for manual review
MAX_VERIFY_ATTEMPTS = 3

# --- Attendance ---
# Default cumulative attendance %, below which a student is flagged on the
# dashboard. Live value stored in Settings table (admin-editable).
DEFAULT_ATTENDANCE_PERCENT_THRESHOLD = 75
