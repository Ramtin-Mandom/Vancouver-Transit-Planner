SELECT COUNT(*)
FROM transit.delay_observations;

/* Delay-observation health diagnostic.
   Classification defaults:
   early < -60 seconds; on time -60..300 seconds; late > 300 seconds.
*/
SELECT
    COUNT(*) AS total_observations,
    COUNT(*) FILTER (WHERE delay_seconds < 0) AS negative_observations,
    COUNT(*) FILTER (WHERE delay_seconds = 0) AS zero_observations,
    COUNT(*) FILTER (WHERE delay_seconds > 0) AS positive_observations,
    MIN(delay_seconds) AS minimum_delay_seconds,
    MAX(delay_seconds) AS maximum_delay_seconds,
    AVG(delay_seconds)::double precision AS average_delay_seconds,
    AVG(ABS(delay_seconds))::double precision
        AS average_absolute_delay_seconds,
    100.0 * COUNT(*) FILTER (WHERE delay_seconds < -60)
        / NULLIF(COUNT(*), 0) AS early_percentage,
    100.0 * COUNT(*) FILTER (WHERE delay_seconds BETWEEN -60 AND 300)
        / NULLIF(COUNT(*), 0) AS on_time_percentage,
    100.0 * COUNT(*) FILTER (WHERE delay_seconds > 300)
        / NULLIF(COUNT(*), 0) AS late_percentage
FROM transit.delay_observations;
