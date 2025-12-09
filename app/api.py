from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Depends, HTTPException, Form, Request, Query
from . import services, crud, models
from .db import SessionLocal
from .schemas import IngestResponse, StatusResponse, S3IngestRequest
import io
from sqlalchemy.orm import Session
from uuid import UUID
from typing import List, Optional, Dict, Any
from sqlalchemy import text
import re

router = APIRouter()

def get_db():
    """Database dependency - creates database session"""
    # Database is already initialized at startup, so we can directly create session
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post('/ingest/postgres', response_model=IngestResponse)
async def ingest_postgres(file: UploadFile = File(...), background_tasks: BackgroundTasks = None, db: Session = Depends(get_db)):
    """
    Ingest Excel file into PostgreSQL database.
    This endpoint is specific for PostgreSQL ingestion.
    """
    contents = await file.read()
    file_hash = services.compute_sha256_bytes(contents)
    ingestion_type = 'Postgres'
    
    # Check for duplicate (only if there's a successful job)
    if crud.has_successful_job(db, file_hash, ingestion_type=ingestion_type):
        completed_job = db.query(models.Job).filter(
            models.Job.file_hash == file_hash,
            models.Job.ingestion_type == ingestion_type,
            models.Job.status == 'completed'
        ).first()
        if completed_job:
            return IngestResponse(
                job_id=completed_job.id,
                message=f'File already successfully ingested to PostgreSQL. Status: {completed_job.status}',
                file_hash=completed_job.file_hash,
                status=completed_job.status,
                ingestion_type=completed_job.ingestion_type,
                is_duplicate=True
            )
    
    job = crud.create_job(db, file_hash=file_hash, ingestion_type=ingestion_type, status='running')
    
    # Step 5: Schedule background processing
    bio = io.BytesIO(contents)
    background_tasks.add_task(services.process_uploaded_file, job.id, bio, file.filename)
    
    return IngestResponse(
        job_id=job.id,
        message='PostgreSQL ingestion started',
        file_hash=job.file_hash,
        status=job.status,
        ingestion_type=job.ingestion_type,
        is_duplicate=False
    )

@router.post('/ingest/postgres/from-s3', response_model=IngestResponse)
async def ingest_postgres_from_s3(
    request: S3IngestRequest,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    """
    Ingest file from S3 into PostgreSQL database.
    The file must already be uploaded to S3 using /api/upload/s3 endpoint.
    
    Parameters:
    - s3_key: The S3 key (filename/path) of the file in the S3 bucket
    """
    ingestion_type = 'Postgres'
    s3_key = request.s3_key
    try:
        contents = services.download_file_from_s3(s3_key)
        file_hash = services.compute_sha256_bytes(contents)
        
        # Step 3: Check for duplicate (only if there's a successful job)
        if crud.has_successful_job(db, file_hash, ingestion_type=ingestion_type):
            completed_job = db.query(models.Job).filter(
                models.Job.file_hash == file_hash,
                models.Job.ingestion_type == ingestion_type,
                models.Job.status == 'completed'
            ).first()
            if completed_job:
                return IngestResponse(
                    job_id=completed_job.id,
                    message=f'File already successfully ingested to PostgreSQL. Status: {completed_job.status}',
                    file_hash=completed_job.file_hash,
                    status=completed_job.status,
                    ingestion_type=completed_job.ingestion_type,
                    is_duplicate=True
                )
        
        job = crud.create_job(db, file_hash=file_hash, ingestion_type=ingestion_type, status='running')
        filename = s3_key.split('/')[-1]
        bio = io.BytesIO(contents)
        background_tasks.add_task(services.process_uploaded_file, job.id, bio, filename)
        
        return IngestResponse(
            job_id=job.id,
            message=f'PostgreSQL ingestion started from S3: {s3_key}',
            file_hash=job.file_hash,
            status=job.status,
            ingestion_type=job.ingestion_type,
            is_duplicate=False
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/upload/s3', response_model=IngestResponse)
async def upload_to_s3(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(..., description="Upload single file, multiple files, or a folder (as multiple files)."),
    preserve_filename: bool = Form(True, description="If True, keep original filenames/paths in S3 (may overwrite). If False, use UUID prefixes for uniqueness."),
    db: Session = Depends(get_db)
):
    """
    Upload to S3 with optional folder structure:
    
    - 1 file -> stored as single object, status will show file_name
    - N files -> treated as a directory, status will show file_names + file_count
    - Prevents duplicate uploads using a content hash
    """
    ingestion_type = 'S3'
    payload_kind = None
    file_hash = None
    contents = None
    normalized_type = None
    raw_directory_entries = None

    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail='At least one file must be provided')

    # Case 1: Single file uploaded
    if len(files) == 1:
        file = files[0]
        contents = await file.read()
        
        # Check if it's a zip file
        if file.filename.lower().endswith('.zip'):
            # Treat as directory archive
            try:
                normalized_type = services.validate_upload_payload(contents, 'directory')
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            file_hash = services.compute_sha256_bytes(contents)
            payload_kind = 'directory_archive'
        else:
            # Treat as single file
            try:
                normalized_type = services.validate_upload_payload(contents, 'file')
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            file_hash = services.compute_sha256_bytes(contents)
            payload_kind = 'single'
    
    # Case 2: Multiple files uploaded (raw directory - supports nested folders)
    else:
        raw_directory_entries = []
        for entry in files:
            data = await entry.read()
            try:
                rel_path = services.normalize_relative_path(entry.filename)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            raw_directory_entries.append({
                'path': rel_path,
                'content': data,
                'content_type': entry.content_type
            })
        try:
            file_hash = services.compute_directory_hash(raw_directory_entries)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        payload_kind = 'raw_directory'

    existing = crud.get_job_by_hash(db, file_hash, ingestion_type=ingestion_type)
    if existing:
        return IngestResponse(
            job_id=existing.id,
            message=f'File already uploaded to S3. Current status: {existing.status}',
            file_hash=existing.file_hash,
            status=existing.status,
            ingestion_type=existing.ingestion_type,
            is_duplicate=True
        )
    job = crud.create_job(db, file_hash=file_hash, ingestion_type=ingestion_type, status='running')

    # Process based on payload kind
    if payload_kind == 'raw_directory':
        background_tasks.add_task(
            services.process_s3_directory_upload,
            job.id,
            raw_directory_entries,
            preserve_filename
        )
    else:
        background_tasks.add_task(
            services.process_s3_upload,
            job.id,
            contents,
            files[0].filename,
            'directory' if payload_kind == 'directory_archive' else 'file',
            files[0].content_type,
            preserve_filename
        )

    return IngestResponse(
        job_id=job.id,
        message='S3 upload started',
        file_hash=job.file_hash,
        status=job.status,
        ingestion_type=job.ingestion_type,
        is_duplicate=False
    )

@router.get('/status/{job_id}', response_model=StatusResponse)
def status(job_id: UUID, db: Session = Depends(get_db)):
    import json
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    
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
    
    return StatusResponse(
        job_id=job.id, 
        status=job.status, 
        ingestion_type=job.ingestion_type,
        inserted_count=job.inserted_count,
        file_count=job.file_count,
        file_name=file_name,
        file_names=file_names,
        file_size=file_size,
        message=job.message
    )

# -------------------------------------------------------
# New endpoints: list_tables and table_data (FastAPI style)
# -------------------------------------------------------

_NAME_RE = re.compile(r'^[A-Za-z0-9_\-\s]+$')

def _validate_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))

@router.get('/list_tables')
def list_tables(db: Session = Depends(get_db)):
    """
    List all tables in the public schema.
    Returns: [{ "table_name": <name>, "schema_name": "public" }, ...]
    """
    try:
        sql = text("""
            SELECT table_name, table_schema AS schema_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        result = db.execute(sql)
        rows = [dict(r._mapping) for r in result.fetchall()]
        return rows
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch tables: {str(exc)}")

@router.get('/table_data/{table_name}')
def get_table_data(
    table_name: str,
    request: Request,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Get data from a specific table.
    Query parameters:
      - limit: number of rows to return (default 100)
      - offset: number of rows to skip (default 0)
      - filter_<column_name>=<value> (optional, multiple supported)
    """
    # Validate table name
    if not _validate_name(table_name):
        raise HTTPException(status_code=400, detail="Invalid table name")

    try:
        # 1) Fetch available columns for the table (so we can validate filter columns)
        cols_sql = text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name ILIKE :table_name
            ORDER BY ordinal_position
        """)
        cols_result = db.execute(cols_sql, {"table_name": table_name})
        columns = [r._mapping['column_name'] for r in cols_result.fetchall()]

        if not columns:
            raise HTTPException(status_code=404, detail="Table not found or has no columns")

        # Build WHERE clauses from query parameters
        params: Dict[str, Any] = {}
        where_clauses: List[str] = []

        for k, v in request.query_params.multi_items():
            if not k.startswith("filter_"):
                continue

            col = k[len("filter_"):]

            if not _validate_name(col):
                raise HTTPException(status_code=400, detail=f"Invalid filter column: {col}")

            if col not in columns:
                raise HTTPException(status_code=400, detail=f"Filter column does not exist on table: {col}")

            param_key = f"f_{col}"
            where_clauses.append(f'"{col}"::text ILIKE :{param_key}')
            params[param_key] = f"%{v}%"

        where_clause_sql = ""
        if where_clauses:
            where_clause_sql = "WHERE " + " AND ".join(where_clauses)

        # --- SAFETY: Quote table identifier so mixed-case/quoted tables are found ---
        quoted_table = f'"{table_name}"'

        # 2) Get total count (use quoted table name)
        count_sql = text(f"SELECT COUNT(*) as total FROM {quoted_table} {where_clause_sql}")
        count_res = db.execute(count_sql, params)
        total_rows = int(count_res.scalar() or 0)

        # 3) Fetch data rows (ordered by first column if present)
        data_sql = text(f"SELECT * FROM {quoted_table} {where_clause_sql} ORDER BY 1 LIMIT :limit OFFSET :offset")
        params['limit'] = limit
        params['offset'] = offset
        data_res = db.execute(data_sql, params)
        data_rows = [dict(r._mapping) for r in data_res.fetchall()]

        return {
            "data": data_rows,
            "total_rows": total_rows,
            "columns": columns
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch table data: {str(exc)}")
