"""
Entry point for the FastAPI app.

Run:
    pip install -r requirements.txt
    cp .env.example .env  # then edit .env with your DB credentials
    uvicorn application:app --reload --port 8000

API Endpoints:
    POST /api/ingest/postgres - Ingest Excel file into PostgreSQL
    GET /api/status/{job_id} - Check ingestion job status
"""
import os
import sys

# --- Ensure project root is first on sys.path ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
# ------------------------------------------------

from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api import router as api_router
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events"""
    # Startup: Initialize database tables
    print("🚀 Starting application...")
    try:
        init_db()
        print("✅ Application startup complete")
    except Exception as e:
        print(f"❌ Failed to initialize database during startup: {e}")
        raise  # Fail fast - don't start if DB is not ready
    
    yield  # Application runs here
    
    # Shutdown: Cleanup (if needed)
    print("🛑 Shutting down application...")


app = FastAPI(
    title="PHLC Ingestion API",
    description="API for ingesting files into PostgreSQL, S3 and other data sources",
    lifespan=lifespan
)

# All your routes live under /api/...
app.include_router(api_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("application:app", host="0.0.0.0", port=8000, reload=True)
