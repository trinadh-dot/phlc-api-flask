import os
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL is None:
    raise RuntimeError('Please set DATABASE_URL in .env')

# Use SQLAlchemy engine (synchronous)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database"""
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        exists = table_name in tables
        print(f"🔍 Checking if table '{table_name}' exists: {exists}")
        return exists
    except Exception as e:
        print(f"⚠️  Error checking if table exists: {e}")
        # If we can't check, assume it doesn't exist and try to create
        return False

def init_db():
    """Create tables if they don't exist"""
    from .models import Base
    from sqlalchemy.exc import ProgrammingError, OperationalError
    
    try:
        # First, check if table exists
        if table_exists('jobs'):
            print("✅ Database tables already exist")
            return
        
        # Table doesn't exist, create it
        print("📝 Creating 'jobs' table...")
        Base.metadata.create_all(bind=engine)
        
        # Verify it was created
        if table_exists('jobs'):
            print("✅ Database tables created successfully")
        else:
            # Try one more time - sometimes there's a race condition
            print("⚠️  Table not found after creation, retrying...")
            Base.metadata.create_all(bind=engine, checkfirst=True)
            if table_exists('jobs'):
                print("✅ Database tables created successfully (on retry)")
            else:
                raise RuntimeError("Failed to create jobs table after retry")
                
    except (ProgrammingError, OperationalError) as e:
        # Table might already exist (race condition or concurrent request)
        if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower():
            print("✅ Table already exists (detected during creation)")
        else:
            print(f"❌ Database error: {e}")
            raise
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        raise
