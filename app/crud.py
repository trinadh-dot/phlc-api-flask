from sqlalchemy.orm import Session
from . import models
from uuid import UUID

def create_job(db: Session, file_hash: str, ingestion_type: str = 'Postgres', status: str = 'running'):
    job = models.Job(file_hash=file_hash, ingestion_type=ingestion_type, status=status)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job

def get_job_by_hash(db: Session, file_hash: str, ingestion_type: str = 'Postgres'):
    """Get job by hash and ingestion type to avoid conflicts between different ingestion types"""
    return db.query(models.Job).filter(
        models.Job.file_hash == file_hash,
        models.Job.ingestion_type == ingestion_type
    ).first()

def has_successful_job(db: Session, file_hash: str, ingestion_type: str = 'Postgres'):
    """
    Check if there's at least one successful (completed) job for the given hash and ingestion type.
    Returns True if there's a completed job, False otherwise (including if all jobs are failed).
    """
    successful_job = db.query(models.Job).filter(
        models.Job.file_hash == file_hash,
        models.Job.ingestion_type == ingestion_type,
        models.Job.status == 'completed'
    ).first()
    return successful_job is not None

def get_job(db: Session, job_id: UUID):
    return db.query(models.Job).filter(models.Job.id==job_id).first()

def update_job_status(db: Session, job, **kwargs):
    for k,v in kwargs.items():
        setattr(job, k, v)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
