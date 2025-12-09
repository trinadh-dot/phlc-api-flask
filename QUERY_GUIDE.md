# PHLC Database Query Guide

This guide provides example SQL queries for the PHLC ingestion tables.

## Table Overview

### 1. `hubspot_ta`
HubSpot custom report data with monthly/yearly tracking.

**Columns:**
- `id` (UUID, Primary Key)
- `month` (INTEGER) - Month (1-12)
- `year` (INTEGER) - Year (e.g., 2025)
- `company_name` (TEXT)
- `name` (TEXT)
- `distinct_count_start_date` (TEXT)
- `ept_id` (TEXT)
- `created_at` (TIMESTAMP)
- `updated_at` (TIMESTAMP)

**Unique Constraint:** `(month, year, company_name)`

### 2. `practices_hours`
Practice hours data with metrics tracked by month/year.

**Columns:**
- `id` (UUID, Primary Key)
- `ept_appid` (VARCHAR) - Practice identifier
- `month` (INTEGER) - Month (1-12)
- `year` (INTEGER) - Year (e.g., 2025)
- `column_description` (TEXT) - Metric/description name
- `column_value` (NUMERIC) - Metric value
- `created_at` (TIMESTAMP)
- `updated_at` (TIMESTAMP)

**Unique Constraint:** `(ept_appid, month, year, column_description)`

### 3. `practices`
Practice master data.

**Columns:**
- `id` (UUID, Primary Key)
- `ept_appid` (VARCHAR, UNIQUE) - Practice identifier
- `practice_name` (TEXT) - Practice name
- `created_at` (TIMESTAMP)
- `updated_at` (TIMESTAMP)

### 4. `deliverable_statuses`
Deliverable status tracking by month/year.

**Columns:**
- `id` (UUID, Primary Key)
- `month` (INTEGER) - Month (1-12)
- `year` (INTEGER) - Year (e.g., 2025)
- `created_at` (TIMESTAMP)
- `updated_at` (TIMESTAMP)
- Plus all original Excel columns (stored as TEXT)

**Unique Constraint:** `(month, year, first_data_column)`

---

## Quick Reference Queries

### HubSpot TA Queries

```sql
-- Get data for September 2025
SELECT * FROM public.hubspot_ta
WHERE month = 9 AND year = 2025
ORDER BY company_name;

-- Get latest month's data
SELECT * FROM public.hubspot_ta
WHERE (year, month) = (
    SELECT year, month FROM public.hubspot_ta 
    ORDER BY year DESC, month DESC LIMIT 1
);
```

### Practices Hours Queries

```sql
-- Get practices hours for September 2025, ordered by metric
SELECT * FROM public.practices_hours
WHERE month = 9 AND year = 2025
ORDER BY column_description;

-- Get practices hours with practice names
SELECT 
    ph.month, ph.year, p.practice_name,
    ph.column_description, ph.column_value
FROM public.practices_hours ph
JOIN public.practices p ON ph.ept_appid = p.ept_appid
WHERE ph.month = 9 AND ph.year = 2025
ORDER BY p.practice_name, ph.column_description;
```

### Deliverable Statuses Queries

```sql
-- Get deliverable statuses for October
SELECT * FROM public.deliverable_statuses
WHERE month = 10
ORDER BY id;

-- Get deliverable statuses for October 2025
SELECT * FROM public.deliverable_statuses
WHERE month = 10 AND year = 2025;
```

---

## Common Patterns

### Filter by Month/Year
```sql
WHERE month = 9 AND year = 2025
```

### Date Range
```sql
WHERE year = 2025 AND month BETWEEN 1 AND 9
```

### Latest Data
```sql
WHERE (year, month) = (
    SELECT year, month FROM table_name 
    ORDER BY year DESC, month DESC LIMIT 1
)
```

### Join Practices with Hours
```sql
SELECT p.practice_name, ph.*
FROM public.practices p
JOIN public.practices_hours ph ON p.ept_appid = ph.ept_appid
WHERE ph.month = 9 AND ph.year = 2025;
```

---

## Tips

1. **Always filter by month/year** for better performance on large datasets
2. **Use JOINs** to get practice names with hours data
3. **Check `updated_at`** to see when data was last refreshed
4. **Use `DISTINCT`** when counting unique values
5. **Use `ILIKE`** for case-insensitive text searches in PostgreSQL

---

## Full Query Examples

See `example_queries.sql` for 26+ comprehensive query examples including:
- Basic SELECT queries
- Filtering and sorting
- Aggregations and summaries
- Cross-table joins
- Analytics and reporting queries

