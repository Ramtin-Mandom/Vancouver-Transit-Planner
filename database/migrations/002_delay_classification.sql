/* Add complete early/on-time/late profile classification.

   This migration does not modify transit.delay_observations. Existing derived
   profiles receive nullable columns for compatibility. Run
   `python -m src.reliability.cli aggregate` immediately afterward to rebuild
   every profile with the new definitions and populate these columns.
*/
BEGIN;

ALTER TABLE IF EXISTS transit.route_reliability
    ADD COLUMN IF NOT EXISTS mean_absolute_delay_seconds DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS early_probability DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS late_probability DOUBLE PRECISION;

ALTER TABLE IF EXISTS transit.route_reliability
    DROP CONSTRAINT IF EXISTS valid_mean_absolute_delay;
ALTER TABLE IF EXISTS transit.route_reliability
    ADD CONSTRAINT valid_mean_absolute_delay
        CHECK (
            mean_absolute_delay_seconds IS NULL
            OR mean_absolute_delay_seconds >= 0
        );

ALTER TABLE IF EXISTS transit.route_reliability
    DROP CONSTRAINT IF EXISTS valid_route_early_probability;
ALTER TABLE IF EXISTS transit.route_reliability
    ADD CONSTRAINT valid_route_early_probability
        CHECK (
            early_probability IS NULL
            OR early_probability BETWEEN 0.0 AND 1.0
        );

ALTER TABLE IF EXISTS transit.route_reliability
    DROP CONSTRAINT IF EXISTS valid_route_late_probability;
ALTER TABLE IF EXISTS transit.route_reliability
    ADD CONSTRAINT valid_route_late_probability
        CHECK (
            late_probability IS NULL
            OR late_probability BETWEEN 0.0 AND 1.0
        );

ALTER TABLE IF EXISTS transit.route_reliability
    DROP CONSTRAINT IF EXISTS complete_delay_classification;
ALTER TABLE IF EXISTS transit.route_reliability
    ADD CONSTRAINT complete_delay_classification
        CHECK (
            early_probability IS NULL
            OR on_time_probability IS NULL
            OR late_probability IS NULL
            OR ABS(
                early_probability
                + on_time_probability
                + late_probability
                - 1.0
            ) < 0.000001
        );

COMMIT;
