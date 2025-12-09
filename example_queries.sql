-- ============================================================================
-- Example SQL Queries for PHLC Database Tables
-- ============================================================================
-- This file contains example queries to showcase to clients for using
-- the PHLC ingestion tables: hubspot_ta, practices_hours, practices, and deliverable_statuses
-- ============================================================================

-- ============================================================================
-- HUBSPOT_TA TABLE QUERIES
-- ============================================================================

-- 1. Get all HubSpot data for a specific month and year
SELECT * 
FROM public.hubspot_ta
WHERE month = 9 AND year = 2025
ORDER BY company_name;

-- 2. Get HubSpot data with date range filter
SELECT * 
FROM public.hubspot_ta
WHERE year = 2025 AND month BETWEEN 1 AND 9
ORDER BY year, month, company_name;

-- 3. Count records per month/year in HubSpot data
SELECT 
    year,
    month,
    COUNT(*) as total_records,
    COUNT(DISTINCT company_name) as unique_companies
FROM public.hubspot_ta
GROUP BY year, month
ORDER BY year DESC, month DESC;

-- 4. Get latest HubSpot data (most recent month/year)
SELECT * 
FROM public.hubspot_ta
WHERE (year, month) = (
    SELECT year, month 
    FROM public.hubspot_ta 
    ORDER BY year DESC, month DESC 
    LIMIT 1
)
ORDER BY company_name;

-- 5. Get HubSpot data for specific company across all months
SELECT 
    year,
    month,
    company_name,
    name,
    ept_id,
    created_at,
    updated_at
FROM public.hubspot_ta
WHERE company_name ILIKE '%your_company_name%'
ORDER BY year DESC, month DESC;

-- 6. Get distinct companies in HubSpot data
SELECT DISTINCT company_name
FROM public.hubspot_ta
ORDER BY company_name;

-- ============================================================================
-- PRACTICES_HOURS TABLE QUERIES
-- ============================================================================

-- 7. Get practices hours data for specific month and year (ordered by column description)
SELECT * 
FROM public.practices_hours
WHERE month = 9 AND year = 2025
ORDER BY column_description;

-- 8. Get practices hours data for a specific practice (ept_appid) across all months
SELECT 
    month,
    year,
    column_description,
    column_value,
    created_at,
    updated_at
FROM public.practices_hours
WHERE ept_appid = 'your_ept_appid'
ORDER BY year DESC, month DESC, column_description;

-- 9. Get practices hours summary by month/year
SELECT 
    year,
    month,
    COUNT(*) as total_records,
    COUNT(DISTINCT ept_appid) as unique_practices,
    COUNT(DISTINCT column_description) as unique_metrics,
    SUM(column_value) as total_value
FROM public.practices_hours
GROUP BY year, month
ORDER BY year DESC, month DESC;

-- 10. Get practices hours for specific metric (column_description) across all practices
SELECT 
    ept_appid,
    month,
    year,
    column_value,
    updated_at
FROM public.practices_hours
WHERE column_description = 'your_metric_name'
ORDER BY year DESC, month DESC, ept_appid;

-- 11. Get practices hours with practice names (JOIN with practices table)
SELECT 
    ph.month,
    ph.year,
    p.practice_name,
    ph.column_description,
    ph.column_value,
    ph.updated_at
FROM public.practices_hours ph
JOIN public.practices p ON ph.ept_appid = p.ept_appid
WHERE ph.month = 9 AND ph.year = 2025
ORDER BY p.practice_name, ph.column_description;

-- 12. Get latest practices hours data (most recent month/year)
SELECT * 
FROM public.practices_hours
WHERE (year, month) = (
    SELECT year, month 
    FROM public.practices_hours 
    ORDER BY year DESC, month DESC 
    LIMIT 1
)
ORDER BY ept_appid, column_description;

-- ============================================================================
-- PRACTICES TABLE QUERIES
-- ============================================================================

-- 13. Get all practices
SELECT * 
FROM public.practices
ORDER BY practice_name;

-- 14. Get practice by ept_appid
SELECT * 
FROM public.practices
WHERE ept_appid = 'your_ept_appid';

-- 15. Search practices by name
SELECT * 
FROM public.practices
WHERE practice_name ILIKE '%search_term%'
ORDER BY practice_name;

-- 16. Get practices with their latest hours data
SELECT 
    p.ept_appid,
    p.practice_name,
    ph.month,
    ph.year,
    COUNT(ph.column_description) as metrics_count,
    SUM(ph.column_value) as total_hours
FROM public.practices p
LEFT JOIN public.practices_hours ph ON p.ept_appid = ph.ept_appid
WHERE ph.year = 2025 AND ph.month = 9
GROUP BY p.ept_appid, p.practice_name, ph.month, ph.year
ORDER BY p.practice_name;

-- ============================================================================
-- DELIVERABLE_STATUSES TABLE QUERIES
-- ============================================================================

-- 17. Get deliverable statuses for specific month
SELECT * 
FROM public.deliverable_statuses
WHERE month = 10
ORDER BY id;

-- 18. Get deliverable statuses for specific month and year
SELECT * 
FROM public.deliverable_statuses
WHERE month = 10 AND year = 2025
ORDER BY id;

-- 19. Get deliverable statuses with date range
SELECT * 
FROM public.deliverable_statuses
WHERE year = 2025 AND month BETWEEN 1 AND 10
ORDER BY year, month, id;

-- 20. Count deliverable statuses by month/year
SELECT 
    year,
    month,
    COUNT(*) as total_deliverables,
    MIN(created_at) as first_created,
    MAX(updated_at) as last_updated
FROM public.deliverable_statuses
GROUP BY year, month
ORDER BY year DESC, month DESC;

-- 21. Get latest deliverable statuses (most recent month/year)
SELECT * 
FROM public.deliverable_statuses
WHERE (year, month) = (
    SELECT year, month 
    FROM public.deliverable_statuses 
    ORDER BY year DESC, month DESC 
    LIMIT 1
)
ORDER BY id;

-- ============================================================================
-- CROSS-TABLE QUERIES (JOINS)
-- ============================================================================

-- 22. Get practices with their hours data and deliverable statuses summary
SELECT 
    p.ept_appid,
    p.practice_name,
    ph.year,
    ph.month,
    COUNT(DISTINCT ph.column_description) as hours_metrics_count,
    SUM(ph.column_value) as total_hours_value,
    (SELECT COUNT(*) 
     FROM public.deliverable_statuses ds 
     WHERE ds.year = ph.year AND ds.month = ph.month) as deliverable_count
FROM public.practices p
JOIN public.practices_hours ph ON p.ept_appid = ph.ept_appid
WHERE ph.year = 2025 AND ph.month = 9
GROUP BY p.ept_appid, p.practice_name, ph.year, ph.month
ORDER BY p.practice_name;

-- ============================================================================
-- AGGREGATION AND ANALYTICS QUERIES
-- ============================================================================

-- 23. Monthly summary across all tables
SELECT 
    'hubspot_ta' as table_name,
    year,
    month,
    COUNT(*) as record_count
FROM public.hubspot_ta
GROUP BY year, month

UNION ALL

SELECT 
    'practices_hours' as table_name,
    year,
    month,
    COUNT(*) as record_count
FROM public.practices_hours
GROUP BY year, month

UNION ALL

SELECT 
    'deliverable_statuses' as table_name,
    year,
    month,
    COUNT(*) as record_count
FROM public.deliverable_statuses
GROUP BY year, month

ORDER BY year DESC, month DESC, table_name;

-- 24. Get data freshness (last updated timestamps)
SELECT 
    'hubspot_ta' as table_name,
    MAX(updated_at) as last_updated,
    COUNT(*) as total_records
FROM public.hubspot_ta

UNION ALL

SELECT 
    'practices_hours' as table_name,
    MAX(updated_at) as last_updated,
    COUNT(*) as total_records
FROM public.practices_hours

UNION ALL

SELECT 
    'practices' as table_name,
    MAX(updated_at) as last_updated,
    COUNT(*) as total_records
FROM public.practices

UNION ALL

SELECT 
    'deliverable_statuses' as table_name,
    MAX(updated_at) as last_updated,
    COUNT(*) as total_records
FROM public.deliverable_statuses

ORDER BY last_updated DESC;

-- ============================================================================
-- USEFUL FILTERING PATTERNS
-- ============================================================================

-- 25. Get all data for current year (2025)
SELECT 'hubspot_ta' as source, year, month, COUNT(*) as records
FROM public.hubspot_ta
WHERE year = 2025
GROUP BY year, month

UNION ALL

SELECT 'practices_hours' as source, year, month, COUNT(*) as records
FROM public.practices_hours
WHERE year = 2025
GROUP BY year, month

UNION ALL

SELECT 'deliverable_statuses' as source, year, month, COUNT(*) as records
FROM public.deliverable_statuses
WHERE year = 2025
GROUP BY year, month

ORDER BY source, month;

-- 26. Get data for last N months (example: last 3 months)
SELECT 
    'hubspot_ta' as source,
    year,
    month,
    COUNT(*) as records
FROM public.hubspot_ta
WHERE (year * 12 + month) >= (
    SELECT (year * 12 + month) - 3
    FROM public.hubspot_ta
    ORDER BY year DESC, month DESC
    LIMIT 1
)
GROUP BY year, month
ORDER BY year DESC, month DESC;

-- ============================================================================
-- NOTES:
-- ============================================================================
-- 1. Replace 'your_ept_appid', 'your_company_name', 'your_metric_name', etc. 
--    with actual values from your data
-- 2. Adjust year and month values (2025, 9, 10, etc.) based on your data
-- 3. All timestamps (created_at, updated_at) are in UTC timezone
-- 4. Use ILIKE for case-insensitive text searches in PostgreSQL
-- 5. Table names: practices_hours (not practices_hours_table), practices (not practice_table)
-- ============================================================================

