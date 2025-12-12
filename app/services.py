import os
import io
import hashlib
import pathlib
import pandas as pd
import re
import zipfile
import time
from uuid import uuid4
from typing import Optional, List, Dict

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from sqlalchemy import text
from .db import engine, SessionLocal
from .crud import update_job_status, get_job
from sqlalchemy.exc import SQLAlchemyError

UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', '/tmp/phlc_uploads')
pathlib.Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
S3_BUCKET = os.getenv('S3_BUCKET', 'phlc')

try:
    s3_client = boto3.client('s3', region_name=AWS_REGION)
except Exception as s3_error:
    # Defer failure until first call; makes local dev without AWS creds easier
    s3_client = None
    s3_client_error = s3_error
else:
    s3_client_error = None

def compute_sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def is_yyyymm_pattern(sheet_name: str) -> bool:
    """Check if sheet name matches yyyymm pattern (e.g., 202401, 202402)"""
    pattern = r'^\d{6}$'  # Exactly 6 digits
    return bool(re.match(pattern, str(sheet_name).strip()))

def parse_deliverable_statuses_date(filename: str) -> tuple:
    """
    Parse date from Deliverable_Statuses filename.
    Supports formats:
    - Deliverable_Statuses_10192025 (MMDDYYYY)
    - Deliverable_Statuses_20251104 (YYYYMMDD)
    
    Returns (month, year) or (None, None) if parsing fails
    """
    # Extract date part after last underscore
    parts = filename.rsplit('_', 1)
    if len(parts) != 2:
        return (None, None)
    
    date_str = parts[1].replace('.xlsx', '').replace('.xls', '').strip()
    
    # Try YYYYMMDD format first (8 digits)
    if len(date_str) == 8 and date_str.isdigit():
        # Check if starts with 20xx (likely YYYYMMDD)
        if date_str.startswith('20'):
            year = int(date_str[:4])
            month = int(date_str[4:6])
            if 1 <= month <= 12:
                return (month, year)
    
    # Try MMDDYYYY format (8 digits)
    if len(date_str) == 8 and date_str.isdigit():
        month = int(date_str[:2])
        year = int(date_str[4:8])
        if 1 <= month <= 12 and year >= 2000:
            return (month, year)
    
    return (None, None)

def is_deliverable_statuses_file(filename: str) -> bool:
    """Check if filename matches Deliverable_Statuses pattern"""
    filename_lower = filename.lower()
    return filename_lower.startswith('deliverable_statuses_') and (
        filename_lower.endswith('.xlsx') or filename_lower.endswith('.xls')
    )

def is_hubspot_file(filename: str) -> bool:
    """Check if filename matches hubspot pattern"""
    filename_lower = filename.lower()
    return filename_lower.startswith('hubspot') and (
        filename_lower.endswith('.xlsx') or filename_lower.endswith('.xls')
    )

def is_practices_file(filename: str) -> bool:
    """Check if filename starts with practices (but not TA Dashboard)"""
    filename_lower = filename.lower()
    # Exclude TA Dashboard files
    normalized_name = filename_lower.replace('_', ' ').replace('-', ' ')
    is_ta_dashboard = normalized_name in ['ta dashboard v4.xlsx', 'ta dashboard v4.xls']
    
    # Check if it starts with practices and is not TA Dashboard
    return filename_lower.startswith('practices') and not is_ta_dashboard and (
        filename_lower.endswith('.xlsx') or filename_lower.endswith('.xls') or filename_lower.endswith('.csv')
    )

def parse_hubspot_table_name(filename: str) -> str:
    """
    Extract table name from hubspot filename.
    Example: 'hubspot-custom-report-september-ta-2025-09-30.xls' -> 'hubspot_ta'
    """
    filename_lower = filename.lower()
    # Remove extension
    base = os.path.splitext(filename_lower)[0]
    # Split by hyphens
    parts = base.split('-')
    
    # Find 'hubspot' and 'ta' in the parts
    hubspot_idx = None
    ta_idx = None
    
    for i, part in enumerate(parts):
        if part == 'hubspot':
            hubspot_idx = i
        elif part == 'ta':
            ta_idx = i
    
    if hubspot_idx is not None and ta_idx is not None:
        return 'hubspot_ta'
    elif hubspot_idx is not None:
        # If only hubspot found, use hubspot as table name
        return 'hubspot'
    else:
        # Fallback: use first part
        return parts[0] if parts else 'hubspot'

def parse_hubspot_date(filename: str) -> tuple:
    """
    Parse month and year from hubspot filename.
    Example: 'hubspot-custom-report-september-ta-2025-09-30.xls' -> (9, 2025)
    Returns (month, year) or (None, None) if parsing fails
    """
    filename_lower = filename.lower()
    # Remove extension
    base = os.path.splitext(filename_lower)[0]
    
    # Try to find date pattern YYYY-MM-DD at the end
    # Pattern: -YYYY-MM-DD
    date_pattern = r'(\d{4})-(\d{2})-(\d{2})$'
    match = re.search(date_pattern, base)
    
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        if 1 <= month <= 12:
            return (month, year)
    
    # Try alternative pattern: YYYYMMDD
    date_pattern2 = r'(\d{4})(\d{2})(\d{2})$'
    match2 = re.search(date_pattern2, base)
    
    if match2:
        year = int(match2.group(1))
        month = int(match2.group(2))
        if 1 <= month <= 12:
            return (month, year)
    
    return (None, None)

def sanitize_column_name(name: str) -> str:
    """
    Sanitize column name for PostgreSQL best practices:
    - Convert to lowercase
    - Replace all whitespace (spaces, tabs, newlines) and separators with underscores
    - Remove special characters (keep only alphanumeric and underscore)
    - Handle reserved keywords
    - Ensure doesn't start with number
    """
    if not name or not isinstance(name, str):
        return 'unnamed_column'
    
    # Convert to lowercase and strip whitespace
    name = str(name).lower().strip()
    
    # Replace all whitespace characters (spaces, tabs, newlines, etc.) with underscores
    name = re.sub(r'\s+', '_', name)
    
    # Replace hyphens, dots, and other common separators with underscores
    name = re.sub(r'[\-\.]+', '_', name)
    
    # Remove special characters (keep only alphanumeric and underscore)
    # This handles: @, #, $, %, ^, &, *, (, ), [, ], {, }, |, \, /, etc.
    name = re.sub(r'[^a-z0-9_]', '_', name)
    
    # Remove multiple consecutive underscores
    name = re.sub(r'_+', '_', name)
    
    # Remove leading/trailing underscores
    name = name.strip('_')
    
    # If starts with number, prefix with 'col_'
    if name and name[0].isdigit():
        name = 'col_' + name
    
    # Handle reserved keywords (common PostgreSQL reserved words)
    reserved_keywords = {
        'select', 'from', 'where', 'table', 'index', 'order', 'group', 
        'by', 'having', 'insert', 'update', 'delete', 'create', 'drop',
        'alter', 'grant', 'revoke', 'user', 'role', 'database', 'schema',
        'view', 'trigger', 'function', 'procedure', 'type', 'constraint',
        'column', 'as', 'on', 'join', 'left', 'right', 'inner', 'outer',
        'union', 'distinct', 'limit', 'offset', 'case', 'when', 'then', 'else', 'end'
    }
    if name in reserved_keywords:
        name = name + '_col'
    
    # If empty after sanitization, use default name
    if not name:
        name = 'unnamed_column'
    
    return name

def process_uploaded_file(job_id, file_bytes, filename, retry_count=0):
    """
    Process uploaded file with retry mechanism.
    Will retry up to 5 times if processing fails.
    """
    MAX_RETRIES = 5
    
    db = None
    try:
        filename_base = os.path.basename(filename)

        # Special handling for TA Dashboard v4.xlsx (with space) or TA_Dashboard_v4.xlsx (with underscore)
        # Normalize filename for comparison (handle both space and underscore versions)
        normalized_name = filename_base.replace('_', ' ').replace('-', ' ')
        is_ta_dashboard = normalized_name.lower() in ['ta dashboard v4.xlsx', 'ta dashboard v4.xls']
        
        # Special handling for Deliverable_Statuses files
        is_deliverable_statuses = is_deliverable_statuses_file(filename_base)
        
        # Special handling for HubSpot files
        is_hubspot = is_hubspot_file(filename_base)
        
        # Special handling for Practices files (not TA Dashboard)
        is_practices = is_practices_file(filename_base)
        
        if is_ta_dashboard:
            # Special processing for TA_Dashboard_v4.xlsx
            # Reset file pointer to beginning in case it was read before
            if hasattr(file_bytes, 'seek'):
                file_bytes.seek(0)
            excel_file = pd.ExcelFile(file_bytes)
            all_sheets = excel_file.sheet_names

            # Separate sheets into yyyymm and non-yyyymm
            yyyymm_sheets = [sheet for sheet in all_sheets if is_yyyymm_pattern(sheet)]
            non_yyyymm_sheets = [sheet for sheet in all_sheets if not is_yyyymm_pattern(sheet)]
            
            if len(yyyymm_sheets) < 4:
                raise ValueError(f"Need at least 4 sheets matching yyyymm pattern. Found {len(yyyymm_sheets)} matching sheets: {yyyymm_sheets}")
            
            # Take last 4 yyyymm sheets for practice tables
            last_4_yyyymm_sheets = yyyymm_sheets[-4:]
            
            print(f"📋 Processing {len(all_sheets)} total sheets")
            print(f"   Non-yyyymm sheets ({len(non_yyyymm_sheets)}): {non_yyyymm_sheets} → individual tables")
            print(f"   yyyymm sheets ({len(yyyymm_sheets)}): {yyyymm_sheets}")
            print(f"   Last 4 yyyymm sheets (practices tables): {last_4_yyyymm_sheets}")
            
            tables_created = []
            total_inserted = 0

            with engine.begin() as conn:
                from sqlalchemy import inspect as sql_inspect, text
                inspector = sql_inspect(conn)
                
                # Process ALL non-yyyymm sheets as separate tables
                for sheet_name in non_yyyymm_sheets:
                    df = pd.read_excel(excel_file, sheet_name=sheet_name)
                    
                    # Sanitize column names
                    original_columns = df.columns.tolist()
                    sanitized_columns = [sanitize_column_name(col) for col in df.columns]
                    df.columns = sanitized_columns
                    
                    # Log column name changes
                    if original_columns != sanitized_columns:
                        print(f"📝 Column names sanitized for sheet '{sheet_name}':")
                        for orig, sanitized in zip(original_columns, sanitized_columns):
                            if orig != sanitized:
                                print(f"   '{orig}' -> '{sanitized}'")
                    
                    table_name = f"TA_Dashboard_v4_{sheet_name}"
                    
                    # Check if table exists and has different column structure
                    table_exists = inspector.has_table(table_name)
                    
                    if not table_exists:
                        # Table doesn't exist: create and insert all records
                        df.to_sql(table_name, conn, if_exists="append", index=False)
                        inserted = len(df)
                        updated = 0
                        print(f"📊 Table '{table_name}' created with columns: {', '.join(sanitized_columns)}")
                        print(f"✅ Created table {table_name} with {inserted} rows from sheet '{sheet_name}'")
                    else:
                        # Table exists: check column structure
                        existing_columns = [col['name'].lower() for col in inspector.get_columns(table_name)]
                        new_columns = [col.lower() for col in sanitized_columns]
                        
                        if set(existing_columns) != set(new_columns):
                            print(f"⚠️  Table '{table_name}' exists with different columns. Replacing table...")
                            df.to_sql(table_name, conn, if_exists='replace', index=False)
                            inserted = len(df)
                            updated = 0
                        else:
                            # Same columns: implement upsert logic
                            # Try to find primary key or unique constraint
                            pk_constraint = inspector.get_pk_constraint(table_name)
                            unique_constraints = inspector.get_unique_constraints(table_name)
                            
                            if pk_constraint and pk_constraint.get('constrained_columns'):
                                # Use primary key for upsert
                                pk_columns = pk_constraint['constrained_columns']
                                print(f"📝 Using primary key {pk_columns} for upsert in {table_name}")
                                
                                # Build ON CONFLICT clause
                                pk_cols_str = ', '.join(pk_columns)
                                update_cols = [col for col in sanitized_columns if col.lower() not in [c.lower() for c in pk_columns]]
                                
                                if update_cols:
                                    # Use temporary table approach for upsert
                                    temp_table = f"{table_name}_temp_{job_id.hex[:8]}"
                                    df.to_sql(temp_table, conn, if_exists='replace', index=False)
                                    
                                    # Build UPDATE SET clause
                                    set_clause = ', '.join([f"{col} = EXCLUDED.{col}" for col in update_cols])
                                    
                                    # Insert with ON CONFLICT
                                    upsert_sql = text(f"""
                                        INSERT INTO {table_name} ({', '.join(sanitized_columns)})
                                        SELECT {', '.join(sanitized_columns)} FROM {temp_table}
                                        ON CONFLICT ({pk_cols_str}) 
                                        DO UPDATE SET {set_clause}
                                    """)
                                    result = conn.execute(upsert_sql)
                                    
                                    # Drop temp table
                                    conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                                    
                                    inserted = result.rowcount
                                    updated = inserted  # Approximate, as we can't distinguish easily
                                    print(f"✅ Upserted {inserted} rows into {table_name} (updated existing, inserted new)")
                                else:
                                    # No columns to update, just insert new
                                    df.to_sql(table_name, conn, if_exists="append", index=False)
                                    inserted = len(df)
                                    updated = 0
                            elif unique_constraints:
                                # Use first unique constraint
                                unique_cols = unique_constraints[0]['column_names']
                                print(f"📝 Using unique constraint {unique_cols} for upsert in {table_name}")
                                
                                # Similar logic as primary key
                                temp_table = f"{table_name}_temp_{job_id.hex[:8]}"
                                df.to_sql(temp_table, conn, if_exists='replace', index=False)
                                
                                update_cols = [col for col in sanitized_columns if col.lower() not in [c.lower() for c in unique_cols]]
                                if update_cols:
                                    set_clause = ', '.join([f"{col} = EXCLUDED.{col}" for col in update_cols])
                                    unique_cols_str = ', '.join(unique_cols)
                                    
                                    upsert_sql = text(f"""
                                        INSERT INTO {table_name} ({', '.join(sanitized_columns)})
                                        SELECT {', '.join(sanitized_columns)} FROM {temp_table}
                                        ON CONFLICT ({unique_cols_str}) 
                                        DO UPDATE SET {set_clause}
                                    """)
                                    result = conn.execute(upsert_sql)
                                    conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                                    
                                    inserted = result.rowcount
                                    updated = inserted
                                    print(f"✅ Upserted {inserted} rows into {table_name} (updated existing, inserted new)")
                                else:
                                    df.to_sql(table_name, conn, if_exists='append', index=False)
                                    inserted = len(df)
                                    updated = 0
                            else:
                                # No primary key or unique constraint: just append (might create duplicates)
                                print(f"⚠️  No primary key or unique constraint found for {table_name}, appending records (may create duplicates)")
                                df.to_sql(table_name, conn, if_exists='append', index=False)
                                inserted = len(df)
                                updated = 0
                    
                    tables_created.append(f"{table_name} ({inserted} rows)")
                    total_inserted += inserted
                
                # Process last 4 yyyymm sheets into practices_hours and practices
                if last_4_yyyymm_sheets:
                    # Create practices if not exists, or alter if exists with old schema
                    if not inspector.has_table('practices'):
                        practices_table_sql = """
                        CREATE TABLE practices (
                            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            ept_appid VARCHAR(255) UNIQUE NOT NULL,
                            practice_name TEXT,
                            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                        conn.execute(text(practices_table_sql))
                        print("✅ Created practices")
                    else:
                        # Check if practice_name is VARCHAR and needs to be altered to TEXT
                        columns = inspector.get_columns('practices')
                        for col in columns:
                            if col['name'] == 'practice_name' and 'varchar' in str(col['type']).lower():
                                alter_sql = text("ALTER TABLE practices ALTER COLUMN practice_name TYPE TEXT")
                                conn.execute(alter_sql)
                                print("✅ Altered practices.practice_name to TEXT")
                                break
                        print("✅ practices already exists")
                    
                    # Create practices_hours if not exists, or alter if exists with old schema
                    if not inspector.has_table('practices_hours'):
                        practices_hours_table_sql = """
                        CREATE TABLE practices_hours (
                            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            ept_appid VARCHAR(255) NOT NULL,
                            month INTEGER,
                            year INTEGER,
                            column_description TEXT,
                            column_value NUMERIC,
                            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(ept_appid, month, year, column_description)
                        )
                        """
                        conn.execute(text(practices_hours_table_sql))
                        print("✅ Created practices_hours with unique constraint on (ept_appid, month, year, column_description)")
                    else:
                        # Check if column_description is VARCHAR and needs to be altered to TEXT
                        columns = inspector.get_columns('practices_hours')
                        for col in columns:
                            if col['name'] == 'column_description' and 'varchar' in str(col['type']).lower():
                                alter_sql = text("ALTER TABLE practices_hours ALTER COLUMN column_description TYPE TEXT")
                                conn.execute(alter_sql)
                                print("✅ Altered practices_hours.column_description to TEXT")
                                break
                        
                        # Check if total column exists and drop it if present
                        column_names = [col['name'] for col in columns]
                        if 'total' in column_names:
                            try:
                                drop_total_sql = text("ALTER TABLE practices_hours DROP COLUMN IF EXISTS total")
                                conn.execute(drop_total_sql)
                                print("✅ Removed 'total' column from practices_hours")
                            except Exception as e:
                                print(f"⚠️  Could not drop 'total' column: {e}")
                        
                        # Check if unique constraint exists, if not add it
                        unique_constraints = inspector.get_unique_constraints('practices_hours')
                        has_unique = False
                        for uc in unique_constraints:
                            if set(uc['column_names']) == {'ept_appid', 'month', 'year', 'column_description'}:
                                has_unique = True
                                break
                        
                        if not has_unique:
                            try:
                                add_unique_sql = text("""
                                    ALTER TABLE practices_hours 
                                    ADD CONSTRAINT practices_hours_unique 
                                    UNIQUE(ept_appid, month, year, column_description)
                                """)
                                conn.execute(add_unique_sql)
                                print("✅ Added unique constraint to practices_hours")
                            except Exception as e:
                                print(f"⚠️  Could not add unique constraint (may already exist or have duplicates): {e}")
                        
                        print("✅ practices_hours already exists")
                    
                    # Process each of the last 4 yyyymm sheets
                    practices_hours_rows = []
                    practice_records = {}  # Track unique ept_appid
                    
                    for sheet_name in last_4_yyyymm_sheets:
                        df = pd.read_excel(excel_file, sheet_name=sheet_name)
                        
                        # Extract year and month from sheet name (yyyymm format)
                        year = int(sheet_name[:4])
                        month = int(sheet_name[4:6])
                        
                        # Sanitize column names
                        original_columns = df.columns.tolist()
                        sanitized_columns = [sanitize_column_name(col) for col in df.columns]
                        df.columns = sanitized_columns
                        
                        # First 2 columns are ept_appid and practice_name
                        if len(sanitized_columns) < 2:
                            print(f"⚠️  Sheet '{sheet_name}' has less than 2 columns, skipping...")
                            continue
                        
                        ept_appid_col = sanitized_columns[0]
                        practice_name_col = sanitized_columns[1]
                        
                        # Columns from 3rd onwards become rows
                        hour_columns = sanitized_columns[2:]
                        
                        # Process each row
                        for idx, row in df.iterrows():
                            # Handle ept_appid - convert to string and remove .0 if it's a float
                            ept_appid_raw = row[ept_appid_col] if pd.notna(row[ept_appid_col]) else None
                            if ept_appid_raw is None:
                                continue
                            
                            # Convert to string, handle float case (remove .0)
                            if isinstance(ept_appid_raw, float):
                                ept_appid = str(int(ept_appid_raw)) if ept_appid_raw.is_integer() else str(ept_appid_raw)
                            else:
                                ept_appid = str(ept_appid_raw).strip()
                            
                            if not ept_appid or ept_appid == '' or ept_appid.lower() == 'nan':
                                continue
                            
                            # Handle practice_name - can be long text
                            practice_name_raw = row[practice_name_col] if pd.notna(row[practice_name_col]) else None
                            practice_name = str(practice_name_raw).strip() if practice_name_raw is not None else None
                            
                            # Track practice for practices (only if we have a practice_name)
                            if ept_appid not in practice_records and practice_name:
                                practice_records[ept_appid] = practice_name
                            
                            # Process hour columns (from 3rd column onwards) - these become rows
                            for hour_col in hour_columns:
                                column_description = hour_col
                                column_value = float(row[hour_col]) if pd.notna(row[hour_col]) else None
                                
                                # Only add row if column_value is not null
                                if column_value is not None:
                                    practices_hours_rows.append({
                                        'ept_appid': ept_appid,
                                        'month': month,
                                        'year': year,
                                        'column_description': column_description,
                                        'column_value': column_value
                                    })
                    
                    # Upsert into practices (update if exists, insert if new)
                    if practice_records:
                        # Check which records exist before upsert (for reporting)
                        existing_ept_appids = set()
                        for ept_appid in practice_records.keys():
                            check_sql = text("SELECT COUNT(*) FROM practices WHERE ept_appid = :ept_appid")
                            count = conn.execute(check_sql, {'ept_appid': ept_appid}).scalar()
                            if count > 0:
                                existing_ept_appids.add(ept_appid)
                        
                        # Use INSERT ... ON CONFLICT DO UPDATE for upsert
                        for ept_appid, practice_name in practice_records.items():
                            upsert_sql = text("""
                                INSERT INTO practices (ept_appid, practice_name, updated_at)
                                VALUES (:ept_appid, :practice_name, CURRENT_TIMESTAMP)
                                ON CONFLICT (ept_appid) 
                                DO UPDATE SET 
                                    practice_name = EXCLUDED.practice_name,
                                    updated_at = CURRENT_TIMESTAMP
                            """)
                            conn.execute(upsert_sql, {
                                'ept_appid': ept_appid,
                                'practice_name': practice_name
                            })
                        
                        inserted_practices = len(practice_records) - len(existing_ept_appids)
                        updated_practices = len(existing_ept_appids)
                        print(f"✅ Updated practices: {inserted_practices} new practices added, {updated_practices} practices updated, {len(practice_records)} total processed")
                    
                    # Upsert into practices_hours (update if exists, insert if new)
                    if practices_hours_rows:
                        practices_hours_df = pd.DataFrame(practices_hours_rows)
                        
                        # Use temporary table for upsert
                        temp_table = f"practices_hours_temp_{job_id.hex[:8]}"
                        practices_hours_df.to_sql(temp_table, conn, if_exists='replace', index=False)
                        
                        # Upsert using ON CONFLICT
                        upsert_sql = text(f"""
                            INSERT INTO practices_hours (ept_appid, month, year, column_description, column_value, updated_at)
                            SELECT ept_appid, month, year, column_description, column_value, CURRENT_TIMESTAMP
                            FROM {temp_table}
                            ON CONFLICT (ept_appid, month, year, column_description)
                            DO UPDATE SET
                                column_value = EXCLUDED.column_value,
                                updated_at = CURRENT_TIMESTAMP
                        """)
                        
                        result = conn.execute(upsert_sql)
                        inserted_hours = result.rowcount
                        
                        # Drop temp table
                        conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                        
                        total_inserted += inserted_hours
                        tables_created.append(f"practices_hours ({inserted_hours} rows upserted)")
                        print(f"✅ Upserted {inserted_hours} rows into practices_hours from last 4 sheets (updated existing, inserted new)")
            
            # Update job with all tables created
            db = SessionLocal()
            from .crud import get_job, update_job_status
            try:
                job = get_job(db, job_id)
                if job:
                    table_names_str = ", ".join([t.split()[0] for t in tables_created])
                    message = f"OK - Created {len(tables_created)} tables: {table_names_str}"
                    update_job_status(db, job, 
                        status='completed',
                        table_name=table_names_str,  # Store all table names
                        inserted_count=total_inserted,
                        message=message
                    )
                    print(f"✅ Job {job_id} completed - {len(tables_created)} tables, {total_inserted} total rows")
                else:
                    print(f"⚠️  Job {job_id} not found when trying to update to completed")
            except Exception as update_error:
                print(f"❌ Error updating job to completed: {update_error}")
                db.rollback()
                raise
        elif is_deliverable_statuses:
            # Special processing for Deliverable_Statuses files
            # Reset file pointer to beginning in case it was read before
            if hasattr(file_bytes, 'seek'):
                file_bytes.seek(0)
            
            # Parse month and year from filename
            month, year = parse_deliverable_statuses_date(filename_base)
            if month is None or year is None:
                raise ValueError(f"Could not parse date from filename: {filename_base}. Expected format: Deliverable_Statuses_MMDDYYYY or Deliverable_Statuses_YYYYMMDD")
            
            print(f"📅 Processing Deliverable_Statuses file: {filename_base} (Month: {month}, Year: {year})")
            
            # Read excel into DataFrame (if multiple sheets, take first)
            df = pd.read_excel(file_bytes, sheet_name=0)
            
            # Sanitize column names for PostgreSQL best practices
            original_columns = df.columns.tolist()
            sanitized_columns = [sanitize_column_name(col) for col in df.columns]
            df.columns = sanitized_columns
            
            # Log column name changes for debugging
            if original_columns != sanitized_columns:
                print(f"📝 Column names sanitized:")
                for orig, sanitized in zip(original_columns, sanitized_columns):
                    if orig != sanitized:
                        print(f"   '{orig}' -> '{sanitized}'")
            
            # Table name is always deliverable_statuses
            table_name = 'deliverable_statuses'
            
            # Add month and year columns to the dataframe
            df['month'] = month
            df['year'] = year
            
            # Reorder columns: id, month, year, then all other columns
            # Note: id will be added by database as primary key
            column_order = ['month', 'year'] + [col for col in sanitized_columns if col not in ['month', 'year']]
            df = df[column_order]
            
            with engine.begin() as conn:
                from sqlalchemy import inspect as sql_inspect, text
                inspector = sql_inspect(conn)
                
                # Check if table exists
                table_exists = inspector.has_table(table_name)
                
                if not table_exists:
                    # Create table with id, month, year, and all data columns
                    # First, create the table structure
                    create_table_sql = f"""
                    CREATE TABLE {table_name} (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        month INTEGER NOT NULL,
                        year INTEGER NOT NULL"""
                    
                    # Add all data columns (excluding month and year which are already added)
                    for col in sanitized_columns:
                        if col not in ['month', 'year']:
                            # Determine column type (simplified - use TEXT for now, can be improved)
                            create_table_sql += f",\n                        {col} TEXT"
                    
                    create_table_sql += """,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP"""
                    
                    # Add unique constraint on month, year, and first data column (if exists)
                    # This ensures we can identify duplicate records
                    if sanitized_columns:
                        first_col = sanitized_columns[0]
                        create_table_sql += f",\n                        UNIQUE(month, year, {first_col})"
                    else:
                        create_table_sql += ",\n                        UNIQUE(month, year)"
                    
                    create_table_sql += "\n                    )"
                    
                    conn.execute(text(create_table_sql))
                    print(f"✅ Created table {table_name}")
                else:
                    # Table exists: check if columns match
                    existing_columns = [col['name'].lower() for col in inspector.get_columns(table_name)]
                    new_columns = ['month', 'year'] + [col.lower() for col in sanitized_columns if col not in ['month', 'year']]
                    
                    # Check if we need to add new columns
                    missing_columns = [col for col in new_columns if col not in existing_columns and col not in ['id', 'created_at', 'updated_at']]
                    if missing_columns:
                        for col in missing_columns:
                            try:
                                alter_sql = text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col} TEXT")
                                conn.execute(alter_sql)
                                print(f"✅ Added column {col} to {table_name}")
                            except Exception as e:
                                print(f"⚠️  Could not add column {col}: {e}")
                
                # Use temporary table for upsert
                temp_table = f"deliverable_statuses_temp_{job_id.hex[:8]}"
                df.to_sql(temp_table, conn, if_exists='replace', index=False)
                
                # Build upsert SQL
                # Use month, year, and first data column as unique constraint
                data_columns = [col for col in sanitized_columns if col not in ['month', 'year']]
                all_columns = ['month', 'year'] + data_columns
                
                if data_columns:
                    # Use first data column as part of unique constraint
                    unique_cols = ['month', 'year', data_columns[0]]
                    unique_cols_str = ', '.join(unique_cols)
                    
                    # Build UPDATE SET clause for all data columns
                    update_cols = [f"{col} = EXCLUDED.{col}" for col in data_columns]
                    set_clause = ', '.join(update_cols) + ", updated_at = CURRENT_TIMESTAMP"
                    
                    upsert_sql = text(f"""
                        INSERT INTO {table_name} ({', '.join(all_columns)})
                        SELECT {', '.join(all_columns)} FROM {temp_table}
                        ON CONFLICT ({unique_cols_str})
                        DO UPDATE SET {set_clause}
                    """)
                else:
                    # No data columns, just insert
                    upsert_sql = text(f"""
                        INSERT INTO {table_name} ({', '.join(all_columns)})
                        SELECT {', '.join(all_columns)} FROM {temp_table}
                        ON CONFLICT (month, year)
                        DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    """)
                
                result = conn.execute(upsert_sql)
                inserted_count = result.rowcount
                
                # Drop temp table
                conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                
                print(f"✅ Upserted {inserted_count} rows into {table_name} (Month: {month}, Year: {year})")
            
            # Update job status
            db = SessionLocal()
            from .crud import get_job, update_job_status
            try:
                job = get_job(db, job_id)
                if job:
                    update_job_status(db, job,
                        status='completed',
                        table_name=table_name,
                        inserted_count=inserted_count,
                        message=f'OK - Upserted {inserted_count} rows into {table_name} (Month: {month}, Year: {year})'
                    )
                    print(f"✅ Job {job_id} completed - {inserted_count} rows upserted into {table_name}")
                else:
                    print(f"⚠️  Job {job_id} not found when trying to update to completed")
            except Exception as update_error:
                print(f"❌ Error updating job to completed: {update_error}")
                db.rollback()
                raise
        elif is_hubspot:
            # Special processing for HubSpot files
            # Reset file pointer to beginning in case it was read before
            if hasattr(file_bytes, 'seek'):
                file_bytes.seek(0)
            
            # Parse month and year from filename
            month, year = parse_hubspot_date(filename_base)
            if month is None or year is None:
                raise ValueError(f"Could not parse date from filename: {filename_base}. Expected format: hubspot-*-ta-YYYY-MM-DD.xls")
            
            print(f"📅 Processing HubSpot file: {filename_base} (Month: {month}, Year: {year})")
            
            # Read excel into DataFrame (if multiple sheets, take first)
            df = pd.read_excel(file_bytes, sheet_name=0)
            
            # Sanitize column names for PostgreSQL best practices
            original_columns = df.columns.tolist()
            sanitized_columns = [sanitize_column_name(col) for col in df.columns]
            df.columns = sanitized_columns
            
            # Log column name changes for debugging
            if original_columns != sanitized_columns:
                print(f"📝 Column names sanitized:")
                for orig, sanitized in zip(original_columns, sanitized_columns):
                    if orig != sanitized:
                        print(f"   '{orig}' -> '{sanitized}'")
            
            # Get table name from filename pattern
            table_name = parse_hubspot_table_name(filename_base)
            
            # Add month and year columns to the dataframe
            df['month'] = month
            df['year'] = year
            
            # Reorder columns: month, year, then all other columns
            column_order = ['month', 'year'] + [col for col in sanitized_columns if col not in ['month', 'year']]
            df = df[column_order]
            
            # Deduplicate rows based on unique constraint columns to prevent ON CONFLICT errors
            # Use the first data column (if exists) along with month and year as the unique key
            if sanitized_columns:
                unique_key_cols = ['month', 'year', sanitized_columns[0]]
                # Drop duplicates, keeping the first occurrence
                df = df.drop_duplicates(subset=unique_key_cols, keep='first')
                print(f"📊 Deduplicated data: {len(df)} unique rows (based on {', '.join(unique_key_cols)})")
            else:
                # If no data columns, just deduplicate on month and year
                df = df.drop_duplicates(subset=['month', 'year'], keep='first')
                print(f"📊 Deduplicated data: {len(df)} unique rows (based on month, year)")
            
            with engine.begin() as conn:
                from sqlalchemy import inspect as sql_inspect, text
                inspector = sql_inspect(conn)
                
                # Check if table exists
                table_exists = inspector.has_table(table_name)
                
                if not table_exists:
                    # Create table with id, month, year, and all data columns
                    create_table_sql = f"""
                    CREATE TABLE {table_name} (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        month INTEGER NOT NULL,
                        year INTEGER NOT NULL"""
                    
                    # Add all data columns (excluding month and year which are already added)
                    for col in sanitized_columns:
                        if col not in ['month', 'year']:
                            create_table_sql += f",\n                        {col} TEXT"
                    
                    create_table_sql += """,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP"""
                    
                    # Add unique constraint on month, year, and first data column (if exists)
                    if sanitized_columns:
                        first_col = sanitized_columns[0]
                        create_table_sql += f",\n                        UNIQUE(month, year, {first_col})"
                    else:
                        create_table_sql += ",\n                        UNIQUE(month, year)"
                    
                    create_table_sql += "\n                    )"
                    
                    conn.execute(text(create_table_sql))
                    print(f"✅ Created table {table_name}")
                else:
                    # Table exists: check if columns match
                    existing_columns = [col['name'].lower() for col in inspector.get_columns(table_name)]
                    new_columns = ['month', 'year'] + [col.lower() for col in sanitized_columns if col not in ['month', 'year']]
                    
                    # Check if we need to add new columns
                    missing_columns = [col for col in new_columns if col not in existing_columns and col not in ['id', 'created_at', 'updated_at']]
                    if missing_columns:
                        for col in missing_columns:
                            try:
                                alter_sql = text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col} TEXT")
                                conn.execute(alter_sql)
                                print(f"✅ Added column {col} to {table_name}")
                            except Exception as e:
                                print(f"⚠️  Could not add column {col}: {e}")
                
                # Use temporary table for upsert
                temp_table = f"{table_name}_temp_{job_id.hex[:8]}"
                df.to_sql(temp_table, conn, if_exists='replace', index=False)
                
                # Build upsert SQL
                # Use month, year, and first data column as unique constraint
                data_columns = [col for col in sanitized_columns if col not in ['month', 'year']]
                all_columns = ['month', 'year'] + data_columns
                
                if data_columns:
                    # Use first data column as part of unique constraint
                    unique_cols = ['month', 'year', data_columns[0]]
                    unique_cols_str = ', '.join(unique_cols)
                    
                    # Build UPDATE SET clause for all data columns
                    update_cols = [f"{col} = EXCLUDED.{col}" for col in data_columns]
                    set_clause = ', '.join(update_cols) + ", updated_at = CURRENT_TIMESTAMP"
                    
                    # Use DISTINCT ON to handle duplicates in source data
                    # This ensures we only insert one row per unique constraint combination
                    upsert_sql = text(f"""
                        INSERT INTO {table_name} ({', '.join(all_columns)})
                        SELECT DISTINCT ON ({unique_cols_str}) {', '.join(all_columns)}
                        FROM {temp_table}
                        ORDER BY {unique_cols_str}
                        ON CONFLICT ({unique_cols_str})
                        DO UPDATE SET {set_clause}
                    """)
                else:
                    # No data columns, just insert
                    # Use DISTINCT to handle duplicates
                    upsert_sql = text(f"""
                        INSERT INTO {table_name} ({', '.join(all_columns)})
                        SELECT DISTINCT {', '.join(all_columns)} FROM {temp_table}
                        ON CONFLICT (month, year)
                        DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    """)
                
                result = conn.execute(upsert_sql)
                inserted_count = result.rowcount
                
                # Drop temp table
                conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                
                print(f"✅ Upserted {inserted_count} rows into {table_name} (Month: {month}, Year: {year})")
            
            # Update job status
            db = SessionLocal()
            from .crud import get_job, update_job_status
            try:
                job = get_job(db, job_id)
                if job:
                    update_job_status(db, job,
                        status='completed',
                        table_name=table_name,
                        inserted_count=inserted_count,
                        message=f'OK - Upserted {inserted_count} rows into {table_name} (Month: {month}, Year: {year})'
                    )
                    print(f"✅ Job {job_id} completed - {inserted_count} rows upserted into {table_name}")
                else:
                    print(f"⚠️  Job {job_id} not found when trying to update to completed")
            except Exception as update_error:
                print(f"❌ Error updating job to completed: {update_error}")
                db.rollback()
                raise
        elif is_practices:
            # Special processing for Practices files
            # Reset file pointer to beginning in case it was read before
            if hasattr(file_bytes, 'seek'):
                file_bytes.seek(0)
            
            print(f"📅 Processing Practices file: {filename_base}")
            
            # Read file into DataFrame (support both Excel and CSV)
            if filename_base.lower().endswith('.csv'):
                df = pd.read_csv(file_bytes)
            else:
                # Excel file (if multiple sheets, take first)
                df = pd.read_excel(file_bytes, sheet_name=0)
            
            # Sanitize column names for PostgreSQL best practices
            original_columns = df.columns.tolist()
            sanitized_columns = [sanitize_column_name(col) for col in df.columns]
            df.columns = sanitized_columns
            
            # Log column name changes for debugging
            if original_columns != sanitized_columns:
                print(f"📝 Column names sanitized:")
                for orig, sanitized in zip(original_columns, sanitized_columns):
                    if orig != sanitized:
                        print(f"   '{orig}' -> '{sanitized}'")
            
            # Table name is always practices
            table_name = 'practices'
            
            with engine.begin() as conn:
                from sqlalchemy import inspect as sql_inspect, text
                inspector = sql_inspect(conn)
                
                # Check if table exists
                table_exists = inspector.has_table(table_name)
                
                if not table_exists:
                    # Create table with id, all data columns, created_at, updated_at
                    create_table_sql = f"""
                    CREATE TABLE {table_name} (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid()"""
                    
                    # Add all data columns
                    for col in sanitized_columns:
                        # Determine column type (use TEXT for now, can be improved)
                        create_table_sql += f",\n                        {col} TEXT"
                    
                    create_table_sql += """,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP"""
                    
                    # No additional unique constraints - using id (UUID) as the unique key
                    
                    create_table_sql += "\n                    )"
                    
                    conn.execute(text(create_table_sql))
                    print(f"✅ Created table {table_name}")
                else:
                    # Table exists: check if columns match
                    existing_columns = [col['name'].lower() for col in inspector.get_columns(table_name)]
                    new_columns = [col.lower() for col in sanitized_columns]
                    
                    # Check if we need to add new columns
                    missing_columns = [col for col in new_columns if col not in existing_columns and col not in ['id', 'created_at', 'updated_at']]
                    if missing_columns:
                        for col in missing_columns:
                            try:
                                alter_sql = text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col} TEXT")
                                conn.execute(alter_sql)
                                print(f"✅ Added column {col} to {table_name}")
                            except Exception as e:
                                print(f"⚠️  Could not add column {col}: {e}")
                
                # Use temporary table for insert
                temp_table = f"practices_temp_{job_id.hex[:8]}"
                df.to_sql(temp_table, conn, if_exists='replace', index=False)
                
                # Build insert SQL - using id (UUID) as unique key, so just insert all rows
                if sanitized_columns:
                    insert_sql = text(f"""
                        INSERT INTO {table_name} ({', '.join(sanitized_columns)})
                        SELECT {', '.join(sanitized_columns)} FROM {temp_table}
                    """)
                else:
                    # No columns, just insert default
                    insert_sql = text(f"""
                        INSERT INTO {table_name} DEFAULT VALUES
                    """)
                
                result = conn.execute(insert_sql)
                inserted_count = result.rowcount
                
                # Drop temp table
                conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                
                print(f"✅ Upserted {inserted_count} rows into {table_name}")
            
            # Update job status
            db = SessionLocal()
            from .crud import get_job, update_job_status
            try:
                job = get_job(db, job_id)
                if job:
                    update_job_status(db, job,
                        status='completed',
                        table_name=table_name,
                        inserted_count=inserted_count,
                        message=f'OK - Upserted {inserted_count} rows into {table_name}'
                    )
                    print(f"✅ Job {job_id} completed - {inserted_count} rows upserted into {table_name}")
                else:
                    print(f"⚠️  Job {job_id} not found when trying to update to completed")
            except Exception as update_error:
                print(f"❌ Error updating job to completed: {update_error}")
                db.rollback()
                raise
        else:
            # Default behavior for all other files
            # Reset file pointer to beginning in case it was read before
            if hasattr(file_bytes, 'seek'):
                file_bytes.seek(0)
            # Read excel into DataFrame (if multiple sheets, take first)
            df = pd.read_excel(file_bytes, sheet_name=0)
            
            # Sanitize column names for PostgreSQL best practices
            original_columns = df.columns.tolist()
            sanitized_columns = [sanitize_column_name(col) for col in df.columns]
            df.columns = sanitized_columns
            
            # Log column name changes for debugging
            if original_columns != sanitized_columns:
                print(f"📝 Column names sanitized:")
                for orig, sanitized in zip(original_columns, sanitized_columns):
                    if orig != sanitized:
                        print(f"   '{orig}' -> '{sanitized}'")
            
            # sanitize table name from filename
            base = os.path.splitext(os.path.basename(filename))[0]
            table_name = f"phlc_{base}".lower().replace('-', '_').replace(' ', '_')

            # write df to sql; this will create table if not exists
            with engine.begin() as conn:
                # Check if table exists and has different column structure
                from sqlalchemy import inspect as sql_inspect
                inspector = sql_inspect(conn)
                table_exists = inspector.has_table(table_name)
                
                if table_exists:
                    # Get existing column names
                    existing_columns = [col['name'].lower() for col in inspector.get_columns(table_name)]
                    new_columns = [col.lower() for col in sanitized_columns]
                    
                    # If column names don't match, replace the table
                    if set(existing_columns) != set(new_columns):
                        print(f"⚠️  Table '{table_name}' exists with different columns. Replacing table...")
                        df.to_sql(table_name, conn, if_exists='replace', index=False)
                    else:
                        df.to_sql(table_name, conn, if_exists='append', index=False)
                else:
                    # Table doesn't exist, create it with sanitized column names
                    df.to_sql(table_name, conn, if_exists='append', index=False)
                
                inserted = len(df)

                # Print column names that were created in the table
                print(f"📊 Table '{table_name}' created with columns: {', '.join(sanitized_columns)}")
            # update job to completed
            db = SessionLocal()
            from .crud import get_job, update_job_status
            try:
                job = get_job(db, job_id)
                if job:
                    update_job_status(db, job, 
                        status='completed',
                        table_name=table_name,
                        inserted_count=inserted,
                        message='OK'
                    )
                    print(f"✅ Job {job_id} updated to 'completed' - {inserted} rows inserted into {table_name}")
                else:
                    print(f"⚠️  Job {job_id} not found when trying to update to completed")
            except Exception as update_error:
                print(f"❌ Error updating job to completed: {update_error}")
                db.rollback()
                raise
    except Exception as e:
        # Check if we should retry
        if db is None:
            db = SessionLocal()
        from .crud import get_job, update_job_status
        try:
            job = get_job(db, job_id)
            if job:
                # Get current retry count from job or use the parameter
                current_retry_count = getattr(job, 'retry_count', None)
                if current_retry_count is None:
                    current_retry_count = retry_count
                
                if current_retry_count < MAX_RETRIES:
                    # Retry the job
                    new_retry_count = current_retry_count + 1
                    print(f"⚠️  Job {job_id} failed (attempt {new_retry_count}/{MAX_RETRIES}). Retrying...")
                    print(f"   Error: {str(e)}")
                    
                    # Update job with retry count and status
                    update_job_status(
                        db, 
                        job, 
                        status='running',  # Set back to running for retry
                        message=f"Retry {new_retry_count}/{MAX_RETRIES} - Previous error: {str(e)[:200]}",
                        retry_count=new_retry_count
                    )
                    
                    # Retry the processing
                    # Reset file pointer if it's a BytesIO object
                    if hasattr(file_bytes, 'seek'):
                        file_bytes.seek(0)
                    
                    # Recursively retry
                    return process_uploaded_file(job_id, file_bytes, filename, retry_count=new_retry_count)
                else:
                    # Max retries reached, mark as failed
                    print(f"❌ Job {job_id} failed after {MAX_RETRIES} attempts. Marking as failed.")
                    update_job_status(
                        db, 
                        job, 
                        status='failed', 
                        message=f"Failed after {MAX_RETRIES} retries. Last error: {str(e)[:500]}",
                        retry_count=current_retry_count
                    )
                    raise  # Re-raise the exception after marking as failed
        except Exception as update_error:
            print(f"❌ Error updating job status: {update_error}")
            raise  # Re-raise the original exception if we can't update status
    finally:
        if db:
            db.close()

# =====================================================================
# S3 Upload Functions
# =====================================================================

def process_s3_upload(job_id, contents: bytes, filename: str, upload_type: str, content_type: Optional[str] = None, preserve_filename: bool = False):
    """Background task to validate, upload to S3, and update job status."""
    db = None
    try:
        key = upload_bytes_to_s3(contents, filename, upload_type, content_type, preserve_filename)
        db = SessionLocal()
        job = get_job(db, job_id)
        if job:
            message = f"Uploaded to s3://{S3_BUCKET}/{key}"
            # For single file: store file_name
            update_job_status(
                db,
                job,
                status='completed',
                table_name=key,
                inserted_count=len(contents),
                file_names=filename,  # Store single filename
                file_count=1,
                message=message
            )
            print(f"✅ Job {job_id} uploaded {len(contents)} bytes to {message}")
        else:
            print(f"⚠️  Job {job_id} not found when trying to update S3 status")
    except Exception as exc:
        if db is None:
            db = SessionLocal()
        try:
            job = get_job(db, job_id)
            if job:
                update_job_status(db, job, status='failed', message=str(exc))
        except Exception as update_error:
            print(f"Error updating S3 job status to failed: {update_error}")
        raise
    finally:
        if db:
            db.close()

def _ensure_s3_client():
    if s3_client is None:
        raise RuntimeError(f"S3 client is not configured: {s3_client_error}")
    return s3_client

def download_file_from_s3(s3_key: str) -> bytes:
    """Download a file from S3 bucket and return its contents as bytes"""
    client = _ensure_s3_client()
    try:
        response = client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        contents = response['Body'].read()
        return contents
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == 'NoSuchKey':
            raise ValueError(f"File '{s3_key}' not found in S3 bucket '{S3_BUCKET}'")
        elif error_code == 'NoSuchBucket':
            raise ValueError(f"S3 bucket '{S3_BUCKET}' not found")
        else:
            raise RuntimeError(f"Error downloading file from S3: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error downloading from S3: {e}") from e

def _validate_directory_payload(contents: bytes):
    bio = io.BytesIO(contents)
    if not zipfile.is_zipfile(bio):
        raise ValueError("Directory uploads must be provided as a .zip archive")
    bio.seek(0)
    try:
        with zipfile.ZipFile(bio) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member:
                raise ValueError(f"Directory archive is corrupted at '{corrupt_member}'")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Directory archive is corrupted: {exc}") from exc

def _validate_file_payload(contents: bytes):
    if not contents:
        raise ValueError("Uploaded file is empty")

def validate_upload_payload(contents: bytes, upload_type: str):
    upload_type = (upload_type or 'file').lower()
    if upload_type not in {'file', 'directory'}:
        raise ValueError("upload_type must be 'file' or 'directory'")
    if upload_type == 'directory':
        _validate_directory_payload(contents)
    else:
        _validate_file_payload(contents)
    return upload_type

def _build_s3_key(filename: str, upload_type: str, preserve_filename: bool = False) -> str:
    """Build S3 key. If preserve_filename is True, use original filename; otherwise use UUID prefix."""
    if preserve_filename:
        # Use original filename/path (may overwrite existing files)
        sanitized = re.sub(r'[^A-Za-z0-9._/-]+', '_', filename or 'upload')
        return sanitized.lstrip('/')
    else:
        # Use UUID prefix for uniqueness
        sanitized = re.sub(r'[^A-Za-z0-9._-]+', '_', filename or 'upload')
    return f"{uuid4()}_{upload_type}_{sanitized}"

def upload_bytes_to_s3(contents: bytes, filename: str, upload_type: str, content_type: Optional[str] = None, preserve_filename: bool = False) -> str:
    upload_type = validate_upload_payload(contents, upload_type)
    client = _ensure_s3_client()
    key = _build_s3_key(filename, upload_type, preserve_filename)
    extra_args = {}
    if content_type:
        extra_args['ContentType'] = content_type
    
    # Retry mechanism with post-copy validation (up to 5 times)
    max_retries = 5
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        try:
            # Upload to S3
            client.put_object(Bucket=S3_BUCKET, Key=key, Body=contents, **extra_args)
            
            # Post-copy validation
            try:
                validate_post_copy(contents, key, filename)
                print(f"✅ Post-copy validation passed for '{key}' (attempt {attempt})")
                return key
            except ValueError as validation_error:
                # Validation failed, will retry
                last_error = validation_error
                print(f"⚠️  Post-copy validation failed for '{key}' (attempt {attempt}/{max_retries}): {validation_error}")
                if attempt < max_retries:
                    # Small delay before retry
                    time.sleep(2)
                    continue
                else:
                    raise RuntimeError(f"Post-copy validation failed after {max_retries} attempts: {validation_error}") from validation_error
        except (ClientError, BotoCoreError) as exc:
            last_error = exc
            if attempt < max_retries:
                print(f"⚠️  Upload failed for '{key}' (attempt {attempt}/{max_retries}): {exc}")
                time.sleep(2)
                continue
            else:
                raise RuntimeError(f"Unable to upload to S3 bucket '{S3_BUCKET}' after {max_retries} attempts: {exc}") from exc
    
    # Should not reach here, but just in case
    if last_error:
        raise RuntimeError(f"Upload failed after {max_retries} attempts: {last_error}") from last_error
    return key

def normalize_relative_path(filename: str) -> str:
    path = (filename or '').replace('\\', '/').strip()
    if not path:
        raise ValueError("Each file must include a relative path or filename")
    if path.startswith('/'):
        path = path.lstrip('/')
    segments = []
    for segment in path.split('/'):
        if segment in ('', '.'):
            continue
        if segment == '..':
            raise ValueError("Directory traversal sequences ('..') are not allowed in file paths")
        segments.append(segment)
    normalized = '/'.join(segments)
    if not normalized:
        raise ValueError("Invalid relative path provided")
    return normalized

def compute_directory_hash(entries: List[Dict[str, bytes]]) -> str:
    if not entries:
        raise ValueError("No files provided to compute directory hash")
    h = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: e['path']):
        h.update(entry['path'].encode('utf-8'))
        content = entry['content']
        h.update(len(content).to_bytes(8, 'big'))
        h.update(content)
    return h.hexdigest()

def process_s3_directory_upload(job_id, entries: List[Dict[str, bytes]], preserve_filename: bool = False):
    """Upload multiple files representing a directory structure."""
    db = None
    uploaded_keys = []
    total_bytes = 0
    file_names = []
    try:
        client = _ensure_s3_client()
        for entry in entries:
            if preserve_filename:
                # Use original path
                key = entry['path']
            else:
                # Use UUID prefix
                key = f"{uuid4()}_{entry['path']}"
            
            extra_args = {}
            if entry.get('content_type'):
                extra_args['ContentType'] = entry['content_type']
            
            # Retry mechanism with post-copy validation (up to 5 times)
            max_retries = 5
            last_error = None
            upload_success = False
            
            for attempt in range(1, max_retries + 1):
                try:
                    # Upload to S3
                    client.put_object(Bucket=S3_BUCKET, Key=key, Body=entry['content'], **extra_args)
                    
                    # Post-copy validation
                    try:
                        validate_post_copy(entry['content'], key, entry['path'])
                        print(f"✅ Post-copy validation passed for '{key}' (attempt {attempt})")
                        upload_success = True
                        break
                    except ValueError as validation_error:
                        # Validation failed, will retry
                        last_error = validation_error
                        print(f"⚠️  Post-copy validation failed for '{key}' (attempt {attempt}/{max_retries}): {validation_error}")
                        if attempt < max_retries:
                            # Small delay before retry
                            time.sleep(0.5)
                            continue
                        else:
                            raise RuntimeError(f"Post-copy validation failed for '{key}' after {max_retries} attempts: {validation_error}") from validation_error
                except (ClientError, BotoCoreError) as exc:
                    last_error = exc
                    if attempt < max_retries:
                        print(f"⚠️  Upload failed for '{key}' (attempt {attempt}/{max_retries}): {exc}")
                        time.sleep(0.5)
                        continue
                    else:
                        raise RuntimeError(f"Unable to upload '{key}' to S3 bucket '{S3_BUCKET}' after {max_retries} attempts: {exc}") from exc
            
            if not upload_success and last_error:
                raise RuntimeError(f"Upload failed for '{key}' after {max_retries} attempts: {last_error}") from last_error
            
            uploaded_keys.append(key)
            file_names.append(entry['path'])
            total_bytes += len(entry['content'])

        db = SessionLocal()
        job = get_job(db, job_id)
        if job:
            # For multiple files: show file_names and file_count
            message = f"Uploaded {len(uploaded_keys)} objects to s3://{S3_BUCKET}"
            table_name = uploaded_keys[0][:128] if uploaded_keys else None
            # Store file names as JSON string (comma-separated for simplicity)
            import json
            file_names_str = json.dumps(file_names) if file_names else None
            update_job_status(
                db,
                job,
                status='completed',
                table_name=table_name,
                inserted_count=total_bytes,
                file_names=file_names_str,  # Store all filenames
                file_count=len(uploaded_keys),
                message=message
            )
            print(f"✅ Job {job_id} uploaded {len(uploaded_keys)} objects ({total_bytes} bytes) to s3://{S3_BUCKET}")
        else:
            print(f"⚠️  Job {job_id} not found after directory upload")
    except Exception as exc:
        if db is None:
            db = SessionLocal()
        try:
            job = get_job(db, job_id)
            if job:
                update_job_status(db, job, status='failed', message=str(exc))
        except Exception as update_error:
            print(f"Error updating S3 directory job status to failed: {update_error}")
        raise
    finally:
        if db:
            db.close()

# =====================================================================
# Post-Copy Validation Functions
# =====================================================================

def _validate_csv_row_count(source_bytes: bytes, dest_bytes: bytes, filename: str) -> bool:
    """Validate row count for CSV files."""
    try:
        if not filename.lower().endswith('.csv'):
            return True  # Skip validation for non-CSV files
        
        source_df = pd.read_csv(io.BytesIO(source_bytes))
        dest_df = pd.read_csv(io.BytesIO(dest_bytes))
        
        source_rows = len(source_df)
        dest_rows = len(dest_df)
        
        if source_rows != dest_rows:
            raise ValueError(f"Row count mismatch: source has {source_rows} rows, destination has {dest_rows} rows")
        
        # Schema validation: check column names match
        source_cols = set(source_df.columns)
        dest_cols = set(dest_df.columns)
        if source_cols != dest_cols:
            raise ValueError(f"Schema mismatch: source columns {source_cols} != destination columns {dest_cols}")
        
        return True
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        # If it's not a CSV or can't be parsed, skip row count validation
        return True

def _validate_parquet_row_count(source_bytes: bytes, dest_bytes: bytes, filename: str) -> bool:
    """Validate row count for Parquet files."""
    try:
        if not filename.lower().endswith('.parquet'):
            return True  # Skip validation for non-Parquet files
        
        source_df = pd.read_parquet(io.BytesIO(source_bytes))
        dest_df = pd.read_parquet(io.BytesIO(dest_bytes))
        
        source_rows = len(source_df)
        dest_rows = len(dest_df)
        
        if source_rows != dest_rows:
            raise ValueError(f"Row count mismatch: source has {source_rows} rows, destination has {dest_rows} rows")
        
        # Schema validation: check column names match
        source_cols = set(source_df.columns)
        dest_cols = set(dest_df.columns)
        if source_cols != dest_cols:
            raise ValueError(f"Schema mismatch: source columns {source_cols} != destination columns {dest_cols}")
        
        return True
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        # If it's not a Parquet or can't be parsed, skip row count validation
        return True

def _validate_business_rules(source_bytes: bytes, filename: str) -> bool:
    """Validate business rules (e.g., no nulls in primary key column)."""
    try:
        # For CSV files, check for nulls in first column (assuming it's a primary key)
        if filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(source_bytes))
            if len(df.columns) > 0:
                first_col = df.columns[0]
                null_count = df[first_col].isna().sum()
                if null_count > 0:
                    raise ValueError(f"Business rule violation: {null_count} null values found in primary key column '{first_col}'")
        
        # For Parquet files, similar check
        elif filename.lower().endswith('.parquet'):
            df = pd.read_parquet(io.BytesIO(source_bytes))
            if len(df.columns) > 0:
                first_col = df.columns[0]
                null_count = df[first_col].isna().sum()
                if null_count > 0:
                    raise ValueError(f"Business rule violation: {null_count} null values found in primary key column '{first_col}'")
        
        return True
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        # If validation can't be performed, skip it
        return True

def validate_post_copy(source_bytes: bytes, s3_key: str, filename: str) -> bool:
    """
    Post-copy validation: checksum, file size, row count/schema, and business rules.
    Raises ValueError if validation fails.
    """
    # Download file from S3
    dest_bytes = download_file_from_s3(s3_key)
    
    # 1. Checksum validation
    source_checksum = compute_sha256_bytes(source_bytes)
    dest_checksum = compute_sha256_bytes(dest_bytes)
    if source_checksum != dest_checksum:
        raise ValueError(f"Checksum mismatch: source={source_checksum[:16]}..., destination={dest_checksum[:16]}...")
    
    # 2. File size validation
    source_size = len(source_bytes)
    dest_size = len(dest_bytes)
    if source_size != dest_size:
        raise ValueError(f"File size mismatch: source={source_size} bytes, destination={dest_size} bytes")
    
    # 3. Row count / schema validation (for structured data)
    _validate_csv_row_count(source_bytes, dest_bytes, filename)
    _validate_parquet_row_count(source_bytes, dest_bytes, filename)
    
    # 4. Business rules validation
    _validate_business_rules(source_bytes, filename)
    
    return True
