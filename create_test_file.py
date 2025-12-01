#!/usr/bin/env python3
"""Create a test Excel file for API testing"""

import pandas as pd
from datetime import datetime

# Create sample data
data = {
    'Customer ID': ['C001', 'C002', 'C003', 'C004', 'C005'],
    'First Name': ['John', 'Jane', 'Bob', 'Alice', 'Charlie'],
    'Last Name': ['Doe', 'Smith', 'Johnson', 'Williams', 'Brown'],
    'Email': ['john.doe@email.com', 'jane.smith@email.com', 'bob.j@email.com', 'alice.w@email.com', 'charlie.b@email.com'],
    'Age': [30, 25, 35, 28, 32],
    'Salary': [50000.50, 60000.75, 55000.00, 65000.25, 58000.50],
    'Join Date': ['2024-01-15', '2024-02-20', '2023-12-10', '2024-03-05', '2024-01-30']
}

df = pd.DataFrame(data)
df['Join Date'] = pd.to_datetime(df['Join Date'])

# Save to Excel
output_file = 'test_data.xlsx'
df.to_excel(output_file, index=False, sheet_name='Customers')

print(f'✅ Excel file created: {output_file}')
print(f'📊 Rows: {len(df)}, Columns: {len(df.columns)}')
print(f'📁 Location: {output_file}')
print('\n📋 Sample data:')
print(df.to_string())

