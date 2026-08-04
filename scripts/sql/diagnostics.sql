-- Read-only database size and ingestion diagnostics.
SELECT pg_size_pretty(pg_database_size(current_database()));

SELECT COUNT(*) FROM transit.stops;
SELECT COUNT(*) FROM transit.stop_times;
SELECT COUNT(*) FROM transit.delay_observations;
SELECT COUNT(*) FROM transit.trip_reliability_samples;
SELECT COUNT(*) FROM transit.route_direction_reliability;
