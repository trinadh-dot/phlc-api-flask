#!/usr/bin/env python3
"""
Script to list files in S3 bucket to find the TA_Dashboard_v4.xlsx file
"""
import os
import boto3
from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
S3_BUCKET = os.getenv('S3_BUCKET', 'phlc')

try:
    s3_client = boto3.client('s3', region_name=AWS_REGION)
    
    print(f"📦 Listing files in S3 bucket: {S3_BUCKET}")
    print("=" * 60)
    
    # List all objects
    paginator = s3_client.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=S3_BUCKET)
    
    ta_dashboard_files = []
    all_files = []
    
    for page in pages:
        if 'Contents' in page:
            for obj in page['Contents']:
                key = obj['Key']
                size = obj['Size']
                all_files.append((key, size))
                
                # Check if it's a TA_Dashboard file
                if 'TA_Dashboard' in key or 'ta_dashboard' in key.lower():
                    ta_dashboard_files.append((key, size))
    
    if ta_dashboard_files:
        print("\n📊 TA_Dashboard files found:")
        print("-" * 60)
        for key, size in ta_dashboard_files:
            size_kb = size / 1024
            print(f"  • {key} ({size_kb:.2f} KB)")
        print("\n✅ Use one of these S3 keys for re-ingestion")
    else:
        print("\n⚠️  No TA_Dashboard files found")
    
    print(f"\n📋 Total files in bucket: {len(all_files)}")
    if len(all_files) > 0 and len(all_files) <= 20:
        print("\nAll files:")
        print("-" * 60)
        for key, size in all_files[:20]:
            size_kb = size / 1024
            print(f"  • {key} ({size_kb:.2f} KB)")
    elif len(all_files) > 20:
        print(f"\n(Showing first 20 of {len(all_files)} files)")
        print("-" * 60)
        for key, size in all_files[:20]:
            size_kb = size / 1024
            print(f"  • {key} ({size_kb:.2f} KB)")
    
except Exception as e:
    print(f"❌ Error listing S3 files: {e}")
    print("\nMake sure:")
    print("  1. AWS credentials are configured")
    print("  2. S3_BUCKET environment variable is set")
    print("  3. You have permissions to list S3 bucket contents")

