/* Safe reliability migration for an existing populated transit schema.

   Review the stop_sequence backfill before applying. If an old observation
   cannot be matched to stop_times, this transaction raises an error and rolls
   back without deleting imported GTFS or observation data.
*/
BEGIN;

ALTER TABLE IF EXISTS transit.delay_observations
    ADD COLUMN IF NOT EXISTS stop_sequence INTEGER;

UPDATE transit.delay_observations AS observation
SET stop_sequence = (
    SELECT stop_time.stop_sequence
    FROM transit.stop_times AS stop_time
    WHERE stop_time.trip_id = observation.trip_id
      AND stop_time.stop_id = observation.stop_id
    ORDER BY
        ABS(EXTRACT(EPOCH FROM (
            COALESCE(stop_time.arrival_time, stop_time.departure_time)
            - observation.scheduled_arrival
        ))),
        stop_time.stop_sequence
    LIMIT 1
)
WHERE observation.stop_sequence IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM transit.delay_observations WHERE stop_sequence IS NULL
    ) THEN
        RAISE EXCEPTION
            'Unmatched delay observations remain; review before retrying migration';
    END IF;
END
$$;

ALTER TABLE transit.delay_observations
    ALTER COLUMN stop_sequence SET NOT NULL;
ALTER TABLE transit.delay_observations
    DROP CONSTRAINT IF EXISTS unique_delay_observation;
ALTER TABLE transit.delay_observations
    DROP CONSTRAINT IF EXISTS valid_delay_stop_sequence;
ALTER TABLE transit.delay_observations
    ADD CONSTRAINT valid_delay_stop_sequence CHECK (stop_sequence >= 0);
ALTER TABLE transit.delay_observations
    ADD CONSTRAINT unique_delay_observation UNIQUE (
        trip_id, stop_id, stop_sequence, service_date, observed_at
    );

CREATE INDEX IF NOT EXISTS idx_delay_latest
    ON transit.delay_observations (
        trip_id, stop_id, stop_sequence, service_date, observed_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_delay_aggregation
    ON transit.delay_observations (service_date, scheduled_arrival);

CREATE TABLE IF NOT EXISTS transit.route_reliability (
    route_id TEXT NOT NULL REFERENCES transit.routes(route_id) ON DELETE CASCADE,
    stop_id TEXT NOT NULL REFERENCES transit.stops(stop_id) ON DELETE CASCADE,
    weekday SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    hour_of_day SMALLINT NOT NULL CHECK (hour_of_day BETWEEN 0 AND 23),
    sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
    mean_delay_seconds DOUBLE PRECISION NOT NULL,
    delay_stddev_seconds DOUBLE PRECISION,
    p50_delay_seconds DOUBLE PRECISION NOT NULL,
    p90_delay_seconds DOUBLE PRECISION NOT NULL,
    on_time_probability DOUBLE PRECISION NOT NULL
        CHECK (on_time_probability BETWEEN 0.0 AND 1.0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (route_id, stop_id, weekday, hour_of_day)
);

CREATE INDEX IF NOT EXISTS idx_route_reliability_lookup
    ON transit.route_reliability (route_id, stop_id, weekday, hour_of_day);

COMMIT;
