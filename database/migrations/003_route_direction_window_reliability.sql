BEGIN;

-- Canonical database counterpart of src.reliability.classification.time_window.
-- MOD preserves the GTFS service date while mapping 24:00+ into a clock window.
CREATE OR REPLACE FUNCTION transit.reliability_time_window(value INTERVAL)
RETURNS TEXT LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT CASE
        WHEN MOD(FLOOR(EXTRACT(EPOCH FROM value) / 3600)::integer, 24) < 6
            THEN 'overnight'
        WHEN MOD(FLOOR(EXTRACT(EPOCH FROM value) / 3600)::integer, 24) < 10
            THEN 'morning_peak'
        WHEN MOD(FLOOR(EXTRACT(EPOCH FROM value) / 3600)::integer, 24) < 15
            THEN 'midday'
        WHEN MOD(FLOOR(EXTRACT(EPOCH FROM value) / 3600)::integer, 24) < 19
            THEN 'afternoon_peak'
        ELSE 'evening'
    END
$$;

-- One independent sample is the median of the newest observation at each stop
-- for one operated trip, service date and window.
CREATE TABLE IF NOT EXISTS transit.trip_reliability_samples (
    trip_id TEXT NOT NULL REFERENCES transit.trips(trip_id) ON DELETE CASCADE,
    service_date DATE NOT NULL,
    time_window TEXT NOT NULL,
    route_id TEXT NOT NULL REFERENCES transit.routes(route_id) ON DELETE CASCADE,
    direction_id SMALLINT,
    representative_delay_seconds DOUBLE PRECISION NOT NULL,
    eligible_stop_count INTEGER NOT NULL CHECK (eligible_stop_count > 0),
    source_max_observed_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trip_id, service_date, time_window),
    CHECK (direction_id IS NULL OR direction_id IN (0, 1)),
    CHECK (time_window IN (
        'overnight', 'morning_peak', 'midday', 'afternoon_peak', 'evening'
    ))
);

CREATE INDEX IF NOT EXISTS idx_trip_samples_profile
    ON transit.trip_reliability_samples
       (route_id, direction_id, time_window, service_date);

CREATE TABLE IF NOT EXISTS transit.route_direction_reliability (
    route_id TEXT NOT NULL REFERENCES transit.routes(route_id) ON DELETE CASCADE,
    direction_key SMALLINT NOT NULL,
    direction_id SMALLINT,
    time_window TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    distinct_service_dates INTEGER NOT NULL,
    mean_delay_seconds DOUBLE PRECISION NOT NULL,
    mean_absolute_delay_seconds DOUBLE PRECISION NOT NULL,
    delay_stddev_seconds DOUBLE PRECISION,
    p50_delay_seconds DOUBLE PRECISION NOT NULL,
    p90_absolute_delay_seconds DOUBLE PRECISION NOT NULL,
    early_probability DOUBLE PRECISION NOT NULL,
    on_time_probability DOUBLE PRECISION NOT NULL,
    late_probability DOUBLE PRECISION NOT NULL,
    reliability_probability DOUBLE PRECISION NOT NULL,
    fallback_level TEXT NOT NULL,
    insufficient_data BOOLEAN NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (route_id, direction_key, time_window),
    CHECK (direction_key = COALESCE(direction_id, -1)),
    CHECK (direction_id IS NULL OR direction_id IN (0, 1)),
    CHECK (sample_count > 0 AND distinct_service_dates > 0),
    CHECK (early_probability BETWEEN 0 AND 1),
    CHECK (on_time_probability BETWEEN 0 AND 1),
    CHECK (late_probability BETWEEN 0 AND 1),
    CHECK (reliability_probability BETWEEN 0 AND 1),
    CHECK (ABS(early_probability + on_time_probability + late_probability - 1) < 0.000001)
);

CREATE INDEX IF NOT EXISTS idx_route_direction_reliability_lookup
    ON transit.route_direction_reliability
       (route_id, direction_key, time_window)
    INCLUDE (reliability_probability, sample_count, fallback_level);

-- Precomputed parents used for shrinkage and constant-cost search lookup.
CREATE TABLE IF NOT EXISTS transit.reliability_fallback_profiles (
    profile_level TEXT NOT NULL,
    route_key TEXT NOT NULL,
    direction_key SMALLINT NOT NULL,
    sample_count INTEGER NOT NULL,
    distinct_service_dates INTEGER NOT NULL,
    on_time_probability DOUBLE PRECISION NOT NULL,
    reliability_probability DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile_level, route_key, direction_key),
    CHECK (profile_level IN ('route_direction', 'route', 'network')),
    CHECK (reliability_probability BETWEEN 0 AND 1)
);

-- transit.route_reliability is intentionally retained as a deprecated,
-- read-only compatibility artifact until the new pipeline is validated.
COMMIT;
