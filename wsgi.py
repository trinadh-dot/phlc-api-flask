"""
WSGI entrypoint for production (Render/Gunicorn).

Render start command example:
  gunicorn --bind 0.0.0.0:$PORT wsgi:app
"""

# The Flask app is created in `application.py` as `app = create_app()`.
# Import it here so Gunicorn can load `wsgi:app`.
from application import app  # noqa: F401


