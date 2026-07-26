-- See the first 100 routes
SELECT *
FROM transit.routes
LIMIT 100;

-- Count the stops
SELECT COUNT(*)
FROM transit.stops;

-- See table names
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'transit'
ORDER BY table_name;