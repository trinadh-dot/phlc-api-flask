"""
WSGI entrypoint for production (Render/Gunicorn).

Render start command example:
  gunicorn --bind 0.0.0.0:$PORT wsgi:app
"""

from app_auto import create_app

# Gunicorn looks for a module-level variable named `app`
app = create_app()


