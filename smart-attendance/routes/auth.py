"""Login/logout and role-based access decorator."""
from functools import wraps

from flask import (Blueprint, flash, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from modules import db

bp = Blueprint("auth", __name__)


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.query_db("SELECT * FROM User WHERE UserID = ?", (uid,), one=True)


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("auth.login", next=request.path))
            if user["Role"] not in roles:
                flash("You do not have permission to view that page.", "error")
                return redirect(url_for("auth.home"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


@bp.route("/")
def home():
    user = current_user()
    if user is None:
        return redirect(url_for("auth.login"))
    if user["Role"] == "admin":
        return redirect(url_for("admin.dashboard"))
    if user["Role"] == "lecturer":
        return redirect(url_for("lecturer.dashboard"))
    return redirect(url_for("student.my_attendance"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and current_user():
        return redirect(url_for("auth.home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.query_db("SELECT * FROM User WHERE Username = ?", (username,), one=True)
        if user and check_password_hash(user["PasswordHash"], password):
            session.clear()
            session["user_id"] = user["UserID"]
            session["role"] = user["Role"]
            session["name"] = user["FullName"] or user["Username"]
            return redirect(request.args.get("next") or url_for("auth.home"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Lecturer self-registration. Accounts are always created with the
    lecturer role — admin and student accounts are provisioned elsewhere."""
    if current_user():
        return redirect(url_for("auth.home"))
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        error = None
        if not full_name or not username or not password:
            error = "Full name, username and password are all required."
        elif len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif db.query_db("SELECT 1 FROM User WHERE Username = ?", (username,), one=True):
            error = "That username is already taken — pick another."

        if error:
            flash(error, "error")
        else:
            uid = db.execute_db(
                "INSERT INTO User (Username, PasswordHash, Role, FullName) "
                "VALUES (?, ?, 'lecturer', ?)",
                (username, generate_password_hash(password), full_name))
            session.clear()
            session["user_id"] = uid
            session["role"] = "lecturer"
            session["name"] = full_name
            flash(f"Welcome, {full_name}. Add your first course below to start "
                  "taking attendance.", "success")
            return redirect(url_for("lecturer.dashboard"))
    return render_template("signup.html")


@bp.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("auth.login"))
