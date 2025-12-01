from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID

class IngestResponse(BaseModel):
    job_id: UUID
    message: str
    file_hash: Optional[str] = None
    status: Optional[str] = None
    ingestion_type: Optional[str] = None
    is_duplicate: Optional[bool] = False

class StatusResponse(BaseModel):
    job_id: UUID
    status: str
    ingestion_type: Optional[str] = None
    file_size: Optional[str] = None   # formatted as "11.8 KB"
    file_count: Optional[int] = None
    message: Optional[str] = None
    file_name: Optional[str] = None
    file_names: Optional[List[str]] = None
    
    def model_dump(self, exclude_none=True, **kwargs):
        # Exclude None values from the response
        return super().model_dump(exclude_none=exclude_none, **kwargs)

class S3IngestRequest(BaseModel):
    s3_key: str

