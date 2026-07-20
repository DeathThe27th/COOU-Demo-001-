"""Smart Attendance System — Flask application entry point.

Run:  python app.py   (after `pip install -r requirements.txt` and
                       `python setup_models.py` — one-time, needs internet;
                       everything after that is fully offline)
"""
from flask import Flask

import config
from modules import db
from routes import admin, auth, lecturer, student


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # base64 frames stay small

    db.init_db(app)

    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(lecturer.bp)
    app.register_blueprint(student.bp)

    @app.context_processor
    def inject_user():
        return {"current_user": auth.current_user()}

    return app


app = create_app()

if __name__ == "__main__":
    missing = [p.name for p in (config.YUNET_MODEL_PATH, config.SFACE_MODEL_PATH)
               if not p.exists()]
    if missing:
        print(f"[warn] missing model files: {', '.join(missing)} — "
              "run `python setup_models.py` once (needs internet).")
    # threaded dev server is fine for classroom scale; use waitress/gunicorn
    # if deploying beyond a single lab machine
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
