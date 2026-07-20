"""Production/demo server entrypoint.

Use this for the live demo instead of `flask run`. The Flask dev server drops
idle keep-alive connections, which makes the first kiosk POST after a pause
fail with a gateway 408 before it ever reaches the app. Waitress holds
connections properly, so verification works on the first try.

    python3 serve.py            # http://0.0.0.0:5000
    PORT=8080 python3 serve.py

Fully offline — waitress is a pure-Python WSGI server with no network calls.
"""
import os

from waitress import serve

from app import create_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f" * Omnipresent by COOU — serving on http://{host}:{port}")
    print(" * Press Ctrl+C to stop.")
    serve(
        create_app(),
        host=host,
        port=port,
        threads=8,
        # Generous: kiosk POSTs carry a base64 JPEG frame and students may
        # pause between steps. Prevents premature connection teardown.
        channel_timeout=120,
        ident="Omnipresent",
    )
