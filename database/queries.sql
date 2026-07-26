SELECT 'agency' AS table_name, COUNT(*) AS row_count
FROM transit.agency

UNION ALL

SELECT 'routes', COUNT(*)
FROM transit.routes

UNION ALL

SELECT 'stops', COUNT(*)
FROM transit.stops

UNION ALL

SELECT 'trips', COUNT(*)
FROM transit.trips

UNION ALL

SELECT 'stop_times', COUNT(*)
FROM transit.stop_times;