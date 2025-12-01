from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
import uuid
from sqlalchemy.dialects.postgresql import UUID

Base = declarative_base()

class Job(Base):
    __tablename__ = 'jobs'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_hash = Column(String(128), nullable=False, index=True)
    ingestion_type = Column(String(32), nullable=False, default='Postgres')  # Postgres, S3, etc.
    status = Column(String(32), nullable=False, default='queued')  # queued, running, completed, failed
    table_name = Column(String(128), nullable=True)
    inserted_count = Column(Integer, nullable=True)
    file_names = Column(Text, nullable=True)
    file_count = Column(Integer, nullable=True)
    message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
