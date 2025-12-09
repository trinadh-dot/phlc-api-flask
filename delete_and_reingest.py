#!/usr/bin/env python3
"""
Script to delete rows from practices and practices_hours tables
and optionally delete related jobs, then re-ingest from S3.
"""
import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL is None:
    print("❌ Error: DATABASE_URL not found in .env file")
    sys.exit(1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

def table_exists(conn, table_name):
    """Check if a table exists"""
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = :table_name
        )
    """), {'table_name': table_name})
    return result.scalar()

def delete_table_rows():
    """Delete all rows from practices and practices_hours tables"""
    print("🗑️  Deleting rows from practices and practices_hours tables...")
    
    with engine.begin() as conn:
        try:
            deleted_practices = 0
            deleted_hours = 0
            
            # Check for new table names first, then old names
            practices_table = None
            hours_table = None
            
            if table_exists(conn, 'practices'):
                practices_table = 'practices'
            elif table_exists(conn, 'practice_table'):
                practices_table = 'practice_table'
                print("⚠️  Found old table name 'practice_table', will delete from it")
            
            if table_exists(conn, 'practices_hours'):
                hours_table = 'practices_hours'
            elif table_exists(conn, 'practices_hours_table'):
                hours_table = 'practices_hours_table'
                print("⚠️  Found old table name 'practices_hours_table', will delete from it")
            
            # Delete from practices_hours first (due to potential foreign key constraints)
            if hours_table:
                result_hours = conn.execute(text(f"DELETE FROM {hours_table}"))
                deleted_hours = result_hours.rowcount
                print(f"✅ Deleted {deleted_hours} rows from {hours_table}")
            else:
                print("⚠️  practices_hours table does not exist, skipping...")
            
            # Delete from practices
            if practices_table:
                result_practices = conn.execute(text(f"DELETE FROM {practices_table}"))
                deleted_practices = result_practices.rowcount
                print(f"✅ Deleted {deleted_practices} rows from {practices_table}")
            else:
                print("⚠️  practices table does not exist, skipping...")
            
            if deleted_practices == 0 and deleted_hours == 0:
                print("\n⚠️  No rows were deleted. Tables may not exist or are already empty.")
            else:
                print(f"\n✅ Successfully deleted {deleted_practices} practices and {deleted_hours} practices_hours rows")
            
            return deleted_practices, deleted_hours
        except Exception as e:
            print(f"❌ Error deleting rows: {e}")
            raise

def delete_related_jobs(delete_jobs=False):
    """Optionally delete jobs related to TA_Dashboard_v4 ingestion"""
    if not delete_jobs:
        print("\n⚠️  Skipping job deletion (use --delete-jobs to also delete related jobs)")
        return
    
    print("\n🗑️  Deleting related jobs from jobs table...")
    
    with engine.begin() as conn:
        try:
            # Find jobs related to TA_Dashboard_v4 files
            result = conn.execute(text("""
                DELETE FROM jobs 
                WHERE table_name LIKE '%practices%' 
                   OR table_name LIKE '%TA_Dashboard_v4%'
                   OR file_names LIKE '%TA_Dashboard%'
            """))
            deleted_jobs = result.rowcount
            print(f"✅ Deleted {deleted_jobs} related jobs")
        except Exception as e:
            print(f"❌ Error deleting jobs: {e}")
            raise

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Delete rows from practices tables and optionally re-ingest')
    parser.add_argument('--delete-jobs', action='store_true', 
                       help='Also delete related jobs from jobs table')
    parser.add_argument('--s3-key', type=str, 
                       help='S3 key of the file to re-ingest (e.g., TA_Dashboard_v4.xlsx)')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🗑️  Deleting rows from practices and practices_hours tables")
    print("=" * 60)
    
    # Delete rows
    delete_table_rows()
    
    # Optionally delete jobs
    if args.delete_jobs:
        delete_related_jobs(delete_jobs=True)
    
    print("\n" + "=" * 60)
    print("✅ Deletion complete!")
    print("=" * 60)
    
    if args.s3_key:
        print(f"\n📥 To re-ingest from S3, use the following API call:")
        print(f"\n   POST http://localhost:8000/api/ingest/postgres/from-s3")
        print(f"   Body: {{ \"s3_key\": \"{args.s3_key}\" }}")
        print(f"\n   Or use curl:")
        print(f"   curl -X POST http://localhost:8000/api/ingest/postgres/from-s3 \\")
        print(f"        -H 'Content-Type: application/json' \\")
        print(f"        -d '{{\"s3_key\": \"{args.s3_key}\"}}'")
    else:
        print("\n📥 To re-ingest from S3, use:")
        print("   POST http://localhost:8000/api/ingest/postgres/from-s3")
        print("   Body: { \"s3_key\": \"<your-s3-key>\" }")
        print("\n   Example:")
        print("   curl -X POST http://localhost:8000/api/ingest/postgres/from-s3 \\")
        print("        -H 'Content-Type: application/json' \\")
        print("        -d '{\"s3_key\": \"TA_Dashboard_v4.xlsx\"}'")

if __name__ == '__main__':
    main()

