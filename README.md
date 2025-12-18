# phlc-api (Flask)

Source repo: [trinadh-dot/phlc-api-flask](https://github.com/trinadh-dot/phlc-api-flask.git)

## Deploy on Render

### Option A (recommended): Blueprint (`render.yaml`)

- **Step 1**: Push your latest changes (this repo includes `render.yaml`).
- **Step 2**: In Render, click **New** → **Blueprint** and connect your GitHub repo.
- **Step 3**: Render will provision:
  - a **Web Service** (`phlc-api-flask`)
  - a **PostgreSQL** database (`phlc-db`)
- **Step 4**: After deploy, open your service URL:
  - `/admin/` for Admin UI
  - `/apidocs/` for Swagger UI

### Option B: Manual Web Service setup (no blueprint)

Create a **Web Service** from your GitHub repo and set:

- **Build command**:
  - `pip install -r requirements.txt`
- **Start command** (important):
  - `gunicorn --bind 0.0.0.0:$PORT wsgi:app`

### Required environment variables

Your app requires `DATABASE_URL` (see `app/db.py`). On Render you can either:

- **Use Render Postgres** and set `DATABASE_URL` from the database “connectionString”, or
- **Provide your own** Postgres URL as `DATABASE_URL`

Recommended env vars:

- `DATABASE_URL` (required)
- `SECRET_KEY` (recommended)
- `UPLOAD_FOLDER` (optional; default `/tmp/phlc_uploads` in `app/services.py`)
- `AWS_REGION` (optional; default `us-east-1`)
- `S3_BUCKET` (optional; default `phlc`)

If you use S3 APIs, also set standard AWS credentials:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
