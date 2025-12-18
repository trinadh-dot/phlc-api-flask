from flask import Blueprint, request, jsonify, abort
from uuid import UUID
from typing import List, Dict, Any
import io
import json
import re

from sqlalchemy import text

from . import services, crud, models
from .db import SessionLocal


api_bp = Blueprint("api", __name__)


def _get_db():
    """Simple helper to create and clean up a SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@api_bp.route("/ingest/postgres", methods=["POST"])
def ingest_postgres():
    """
    Ingest Excel file into PostgreSQL database.
    ---
    consumes:
      - multipart/form-data
    parameters:
      - in: formData
        name: file
        type: file
        required: true
        description: Excel file to ingest
    responses:
      200:
        description: Job created
    """
    file = request.files.get("file")
    if not file:
        abort(400, description="Missing 'file' in form-data")

    contents = file.read()
    file_hash = services.compute_sha256_bytes(contents)
    ingestion_type = "Postgres"

    db_gen = _get_db()
    db = next(db_gen)

    # Check for duplicate (only if there's a successful job)
    if crud.has_successful_job(db, file_hash, ingestion_type=ingestion_type):
        completed_job = (
            db.query(models.Job)
            .filter(
                models.Job.file_hash == file_hash,
                models.Job.ingestion_type == ingestion_type,
                models.Job.status == "completed",
            )
            .first()
        )
        if completed_job:
            return jsonify(
                {
                    "job_id": str(completed_job.id),
                    "message": f"File already successfully ingested to PostgreSQL. Status: {completed_job.status}",
                    "file_hash": completed_job.file_hash,
                    "status": completed_job.status,
                    "ingestion_type": completed_job.ingestion_type,
                    "is_duplicate": True,
                }
            )

    job = crud.create_job(
        db, file_hash=file_hash, ingestion_type=ingestion_type, status="running"
    )

    # Process file synchronously (no FastAPI background tasks)
    bio = io.BytesIO(contents)
    services.process_uploaded_file(job.id, bio, file.filename)

    return jsonify(
        {
            "job_id": str(job.id),
            "message": "PostgreSQL ingestion started",
            "file_hash": job.file_hash,
            "status": job.status,
            "ingestion_type": job.ingestion_type,
            "is_duplicate": False,
        }
    )


@api_bp.route("/ingest/postgres/from-s3", methods=["POST"])
def ingest_postgres_from_s3():
    """
    Ingest file from S3 into PostgreSQL database.
    The file must already be uploaded to S3 using /api/upload/s3 endpoint.
    ---
    consumes:
      - application/json
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            s3_key:
              type: string
              example: "path/to/file.xlsx"
          required:
            - s3_key
    responses:
      200:
        description: Job created
    """
    data = request.get_json(silent=True) or {}
    s3_key = data.get("s3_key")
    if not s3_key:
        abort(400, description="Missing 's3_key' in JSON body")

    ingestion_type = "Postgres"

    try:
        contents = services.download_file_from_s3(s3_key)
        file_hash = services.compute_sha256_bytes(contents)

        db_gen = _get_db()
        db = next(db_gen)

        # Check for duplicate (only if there's a successful job)
        if crud.has_successful_job(db, file_hash, ingestion_type=ingestion_type):
            completed_job = (
                db.query(models.Job)
                .filter(
                    models.Job.file_hash == file_hash,
                    models.Job.ingestion_type == ingestion_type,
                    models.Job.status == "completed",
                )
                .first()
            )
            if completed_job:
                return jsonify(
                    {
                        "job_id": str(completed_job.id),
                        "message": f"File already successfully ingested to PostgreSQL. Status: {completed_job.status}",
                        "file_hash": completed_job.file_hash,
                        "status": completed_job.status,
                        "ingestion_type": completed_job.ingestion_type,
                        "is_duplicate": True,
                    }
                )

        job = crud.create_job(
            db, file_hash=file_hash, ingestion_type=ingestion_type, status="running"
        )
        filename = s3_key.split("/")[-1]
        bio = io.BytesIO(contents)
        services.process_uploaded_file(job.id, bio, filename)

        return jsonify(
            {
                "job_id": str(job.id),
                "message": f"PostgreSQL ingestion started from S3: {s3_key}",
                "file_hash": job.file_hash,
                "status": job.status,
                "ingestion_type": job.ingestion_type,
                "is_duplicate": False,
            }
        )
    except ValueError as e:
        abort(404, description=str(e))
    except RuntimeError as e:
        abort(500, description=str(e))


@api_bp.route("/upload/s3", methods=["POST"])
def upload_to_s3():
    """
    Upload to S3 with optional folder structure.

    - 1 file -> stored as single object, status will show file_name
    - N files -> treated as a directory, status will show file_names + file_count
    - Prevents duplicate uploads using a content hash
    ---
    consumes:
      - multipart/form-data
    parameters:
      - in: formData
        name: files
        type: file
        required: true
        description: One or more files (can include folder paths)
      - in: formData
        name: preserve_filename
        type: boolean
        required: false
        default: true
    responses:
      200:
        description: Upload job created
    """
    ingestion_type = "S3"
    payload_kind = None
    file_hash = None
    contents = None
    normalized_type = None
    raw_directory_entries = None

    files = request.files.getlist("files")
    preserve_filename_raw = request.form.get("preserve_filename", "true")
    preserve_filename = (
        str(preserve_filename_raw).lower() in {"1", "true", "yes", "on"}
    )

    if not files:
        abort(400, description="At least one file must be provided")

    # Case 1: Single file uploaded
    if len(files) == 1:
        file = files[0]
        contents = file.read()

        # Check if it's a zip file
        if file.filename.lower().endswith(".zip"):
            # Treat as directory archive
            try:
                normalized_type = services.validate_upload_payload(contents, "directory")
            except ValueError as exc:
                abort(400, description=str(exc))
            file_hash = services.compute_sha256_bytes(contents)
            payload_kind = "directory_archive"
        else:
            # Treat as single file
            try:
                normalized_type = services.validate_upload_payload(contents, "file")
            except ValueError as exc:
                abort(400, description=str(exc))
            file_hash = services.compute_sha256_bytes(contents)
            payload_kind = "single"

    # Case 2: Multiple files uploaded (raw directory - supports nested folders)
    else:
        raw_directory_entries = []
        for entry in files:
            data = entry.read()
            try:
                rel_path = services.normalize_relative_path(entry.filename)
            except ValueError as exc:
                abort(400, description=str(exc))
            raw_directory_entries.append(
                {
                    "path": rel_path,
                    "content": data,
                    "content_type": entry.content_type,
                }
            )
        try:
            file_hash = services.compute_directory_hash(raw_directory_entries)
        except ValueError as exc:
            abort(400, description=str(exc))
        payload_kind = "raw_directory"

    db_gen = _get_db()
    db = next(db_gen)

    existing = crud.get_job_by_hash(db, file_hash, ingestion_type=ingestion_type)
    if existing:
        return jsonify(
            {
                "job_id": str(existing.id),
                "message": f"File already uploaded to S3. Current status: {existing.status}",
                "file_hash": existing.file_hash,
                "status": existing.status,
                "ingestion_type": existing.ingestion_type,
                "is_duplicate": True,
            }
        )
    job = crud.create_job(
        db, file_hash=file_hash, ingestion_type=ingestion_type, status="running"
    )

    # Process based on payload kind
    if payload_kind == "raw_directory":
        services.process_s3_directory_upload(job.id, raw_directory_entries, preserve_filename)
    else:
        services.process_s3_upload(
            job.id,
            contents,
            files[0].filename,
            "directory" if payload_kind == "directory_archive" else "file",
            files[0].content_type,
            preserve_filename,
        )

    return jsonify(
        {
            "job_id": str(job.id),
            "message": "S3 upload started",
            "file_hash": job.file_hash,
            "status": job.status,
            "ingestion_type": job.ingestion_type,
            "is_duplicate": False,
        }
    )


@api_bp.route("/status/<uuid:job_id>", methods=["GET"])
def status(job_id: UUID):
    """
    Get ingestion job status.
    ---
    parameters:
      - in: path
        name: job_id
        required: true
        type: string
        format: uuid
    responses:
      200:
        description: Current status of the ingestion job
    """
    db_gen = _get_db()
    db = next(db_gen)

    job = crud.get_job(db, job_id)
    if not job:
        abort(404, description="Job not found")

    # Parse file_names if it's a JSON string (for multiple files)
    file_names = None
    file_name = None
    if job.file_names:
        try:
            # Try to parse as JSON (for multiple files)
            file_names = json.loads(job.file_names)
        except (json.JSONDecodeError, TypeError):
            # If not JSON, treat as single filename
            file_name = job.file_names

    # Format file size if available
    file_size = None
    if job.inserted_count:
        size_bytes = job.inserted_count
        if size_bytes < 1024:
            file_size = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            file_size = f"{size_bytes / 1024:.1f} KB"
        else:
            file_size = f"{size_bytes / (1024 * 1024):.1f} MB"

    return jsonify(
        {
            "job_id": str(job.id),
            "status": job.status,
            "ingestion_type": job.ingestion_type,
            "inserted_count": job.inserted_count,
            "file_count": job.file_count,
            "file_name": file_name,
            "file_names": file_names,
            "file_size": file_size,
            "message": job.message,
        }
    )


# -------------------------------------------------------
# New endpoints: list_tables and table_data (Flask style)
# -------------------------------------------------------

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\s]+$")


def _validate_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


@api_bp.route("/list_tables", methods=["GET"])
def list_tables():
    """
    List all tables in the public schema.
    ---
    responses:
      200:
        description: List of tables
    """
    db_gen = _get_db()
    db = next(db_gen)

    try:
        sql = text(
            """
            SELECT table_name, table_schema AS schema_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """
        )
        result = db.execute(sql)
        rows = [dict(r._mapping) for r in result.fetchall()]
        return jsonify(rows)
    except Exception as exc:
        abort(500, description=f"Failed to fetch tables: {str(exc)}")


@api_bp.route("/table_data/<table_name>", methods=["GET"])
def get_table_data(table_name: str):
    """
    Get data from a specific table.
    ---
    parameters:
      - in: path
        name: table_name
        type: string
        required: true
      - in: query
        name: limit
        type: integer
        required: false
        default: 100
      - in: query
        name: offset
        type: integer
        required: false
        default: 0
    responses:
      200:
        description: Paginated table data
    Query parameters:
      - limit: number of rows to return (default 100)
      - offset: number of rows to skip (default 0)
      - filter_<column_name>=<value> (optional, multiple supported)
    """
    # Validate table name
    if not _validate_name(table_name):
        abort(400, description="Invalid table name")

    # Pagination
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        abort(400, description="limit and offset must be integers")

    if limit < 1 or limit > 5000 or offset < 0:
        abort(400, description="Invalid pagination values")

    db_gen = _get_db()
    db = next(db_gen)

    try:
        # 1) Fetch available columns for the table (so we can validate filter columns)
        cols_sql = text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name ILIKE :table_name
            ORDER BY ordinal_position
        """
        )
        cols_result = db.execute(cols_sql, {"table_name": table_name})
        columns = [r._mapping["column_name"] for r in cols_result.fetchall()]

        if not columns:
            abort(404, description="Table not found or has no columns")

        # Build WHERE clauses from query parameters
        params: Dict[str, Any] = {}
        where_clauses: List[str] = []

        for k, v in request.args.lists():
            if not k.startswith("filter_"):
                continue

            col = k[len("filter_") :]

            if not _validate_name(col):
                abort(400, description=f"Invalid filter column: {col}")

            if col not in columns:
                abort(
                    400,
                    description=f"Filter column does not exist on table: {col}",
                )

            for value in v:
                param_key = f"f_{col}_{len(params)}"
                where_clauses.append(f'"{col}"::text ILIKE :{param_key}')
                params[param_key] = f"%{value}%"

        where_clause_sql = ""
        if where_clauses:
            where_clause_sql = "WHERE " + " AND ".join(where_clauses)

        # --- SAFETY: Quote table identifier so mixed-case/quoted tables are found ---
        quoted_table = f'"{table_name}"'

        # 2) Get total count (use quoted table name)
        count_sql = text(
            f"SELECT COUNT(*) as total FROM {quoted_table} {where_clause_sql}"
        )
        count_res = db.execute(count_sql, params)
        total_rows = int(count_res.scalar() or 0)

        # 3) Fetch data rows (ordered by first column if present)
        data_sql = text(
            f"SELECT * FROM {quoted_table} {where_clause_sql} ORDER BY 1 LIMIT :limit OFFSET :offset"
        )
        params["limit"] = limit
        params["offset"] = offset
        data_res = db.execute(data_sql, params)
        data_rows = [dict(r._mapping) for r in data_res.fetchall()]

        return jsonify(
            {
                "data": data_rows,
                "total_rows": total_rows,
                "columns": columns,
            }
        )

    except Exception as exc:
        abort(500, description=f"Failed to fetch table data: {str(exc)}")
