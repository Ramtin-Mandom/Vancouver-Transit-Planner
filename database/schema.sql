   /* ===================================
   VANCOUVER TRANSIT PLANNER
   PostgreSQL database schema
   ============================================================ */


/* ============================================================
   1. RESET DEVELOPMENT DATABASE

   WARNING:
   This deletes all existing tables and data inside transit.
   ============================================================ */

DROP SCHEMA IF EXISTS transit CASCADE;

CREATE SCHEMA transit;

SET search_path TO transit, public;


/* ============================================================
   2. GTFS AGENCY
   Source: agency.txt
   ============================================================ */

CREATE TABLE agency (
    agency_id          TEXT PRIMARY KEY,
    agency_name        TEXT NOT NULL,
    agency_url         TEXT NOT NULL,
    agency_timezone    TEXT NOT NULL,
    agency_lang        TEXT,
    agency_phone       TEXT,
    agency_fare_url    TEXT
);


/* ============================================================
   3. SERVICE CALENDAR
   Source: calendar.txt
   ============================================================ */

CREATE TABLE calendar (
    service_id    TEXT PRIMARY KEY,

    monday        BOOLEAN NOT NULL,
    tuesday       BOOLEAN NOT NULL,
    wednesday     BOOLEAN NOT NULL,
    thursday      BOOLEAN NOT NULL,
    friday        BOOLEAN NOT NULL,
    saturday      BOOLEAN NOT NULL,
    sunday        BOOLEAN NOT NULL,

    start_date    DATE NOT NULL,
    end_date      DATE NOT NULL,

    CONSTRAINT valid_calendar_dates
        CHECK (end_date >= start_date)
);


/* ============================================================
   4. CALENDAR EXCEPTIONS
   Source: calendar_dates.txt

   exception_type:
   1 = service added
   2 = service removed
   ============================================================ */

CREATE TABLE calendar_dates (
    service_id       TEXT NOT NULL,
    service_date     DATE NOT NULL,
    exception_type   SMALLINT NOT NULL,

    PRIMARY KEY (service_id, service_date),

    CONSTRAINT calendar_dates_service_fk
        FOREIGN KEY (service_id)
        REFERENCES calendar(service_id)
        ON DELETE CASCADE,

    CONSTRAINT valid_exception_type
        CHECK (exception_type IN (1, 2))
);


/* ============================================================
   5. FEED INFORMATION
   Source: feed_info.txt
   ============================================================ */

CREATE TABLE feed_info (
    feed_info_id          SMALLINT GENERATED ALWAYS AS IDENTITY
                          PRIMARY KEY,

    feed_publisher_name   TEXT NOT NULL,
    feed_publisher_url    TEXT NOT NULL,
    feed_lang             TEXT NOT NULL,
    feed_start_date       DATE,
    feed_end_date         DATE,
    feed_version          TEXT,

    CONSTRAINT valid_feed_dates
        CHECK (
            feed_end_date IS NULL
            OR feed_start_date IS NULL
            OR feed_end_date >= feed_start_date
        )
);


/* ============================================================
   6. ROUTES
   Source: routes.txt
   ============================================================ */

CREATE TABLE routes (
    route_id           TEXT PRIMARY KEY,
    agency_id          TEXT NOT NULL,
    route_short_name   TEXT,
    route_long_name    TEXT,
    route_desc         TEXT,
    route_type         SMALLINT NOT NULL,
    route_url          TEXT,
    route_color        VARCHAR(6),
    route_text_color   VARCHAR(6),

    CONSTRAINT routes_agency_fk
        FOREIGN KEY (agency_id)
        REFERENCES agency(agency_id)
        ON DELETE RESTRICT
);


/* ============================================================
   7. STOPS AND STATIONS
   Source: stops.txt
   ============================================================ */

CREATE TABLE stops (
    stop_id               TEXT PRIMARY KEY,
    stop_code             TEXT,
    stop_name             TEXT NOT NULL,
    stop_desc             TEXT,
    stop_lat              NUMERIC(9, 6),
    stop_lon              NUMERIC(10, 6),
    zone_id               TEXT,
    stop_url              TEXT,
    location_type         SMALLINT DEFAULT 0,
    parent_station        TEXT,
    wheelchair_boarding   SMALLINT DEFAULT 0,

    CONSTRAINT valid_stop_latitude
        CHECK (stop_lat IS NULL OR stop_lat BETWEEN -90 AND 90),

    CONSTRAINT valid_stop_longitude
        CHECK (stop_lon IS NULL OR stop_lon BETWEEN -180 AND 180),

    CONSTRAINT valid_location_type
        CHECK (
            location_type IS NULL
            OR location_type BETWEEN 0 AND 4
        ),

    CONSTRAINT valid_wheelchair_boarding
        CHECK (
            wheelchair_boarding IS NULL
            OR wheelchair_boarding IN (0, 1, 2)
        ),

    CONSTRAINT stops_parent_station_fk
        FOREIGN KEY (parent_station)
        REFERENCES stops(stop_id)
        DEFERRABLE INITIALLY DEFERRED
);


/* ============================================================
   8. SHAPE POINTS
   Source: shapes.txt

   A shape consists of multiple points, so its primary key is:
   shape_id + shape_pt_sequence
   ============================================================ */

CREATE TABLE shapes (
    shape_id               TEXT NOT NULL,
    shape_pt_lat           NUMERIC(9, 6) NOT NULL,
    shape_pt_lon           NUMERIC(10, 6) NOT NULL,
    shape_pt_sequence      INTEGER NOT NULL,
    shape_dist_traveled    NUMERIC(12, 4),

    PRIMARY KEY (shape_id, shape_pt_sequence),

    CONSTRAINT valid_shape_latitude
        CHECK (shape_pt_lat BETWEEN -90 AND 90),

    CONSTRAINT valid_shape_longitude
        CHECK (shape_pt_lon BETWEEN -180 AND 180),

    CONSTRAINT valid_shape_sequence
        CHECK (shape_pt_sequence >= 0),

    CONSTRAINT valid_shape_distance
        CHECK (
            shape_dist_traveled IS NULL
            OR shape_dist_traveled >= 0
        )
);


/* ============================================================
   9. TRIPS
   Source: trips.txt
   ============================================================ */

CREATE TABLE trips (
    route_id                TEXT NOT NULL,
    service_id              TEXT NOT NULL,
    trip_id                 TEXT PRIMARY KEY,
    trip_headsign           TEXT,
    trip_short_name         TEXT,
    direction_id            SMALLINT,
    block_id                TEXT,
    shape_id                TEXT,
    wheelchair_accessible   SMALLINT DEFAULT 0,
    bikes_allowed           SMALLINT DEFAULT 0,

    CONSTRAINT trips_route_fk
        FOREIGN KEY (route_id)
        REFERENCES routes(route_id)
        ON DELETE CASCADE,

    CONSTRAINT trips_service_fk
        FOREIGN KEY (service_id)
        REFERENCES calendar(service_id)
        ON DELETE RESTRICT,

    CONSTRAINT valid_trip_direction
        CHECK (direction_id IS NULL OR direction_id IN (0, 1)),

    CONSTRAINT valid_trip_wheelchair
        CHECK (
            wheelchair_accessible IS NULL
            OR wheelchair_accessible IN (0, 1, 2)
        ),

    CONSTRAINT valid_bikes_allowed
        CHECK (
            bikes_allowed IS NULL
            OR bikes_allowed IN (0, 1, 2)
        )
);


/* ============================================================
   10. STOP TIMES
   Source: stop_times.txt

   INTERVAL is used instead of TIME because GTFS times can be
   greater than 24:00:00, such as 25:15:00.
   ============================================================ */

CREATE TABLE stop_times (
    trip_id                TEXT NOT NULL,
    arrival_time           INTERVAL,
    departure_time         INTERVAL,
    stop_id                TEXT NOT NULL,
    stop_sequence          INTEGER NOT NULL,
    stop_headsign          TEXT,
    pickup_type            SMALLINT DEFAULT 0,
    drop_off_type          SMALLINT DEFAULT 0,
    shape_dist_traveled    NUMERIC(12, 4),
    timepoint              SMALLINT DEFAULT 1,

    PRIMARY KEY (trip_id, stop_sequence),

    CONSTRAINT stop_times_trip_fk
        FOREIGN KEY (trip_id)
        REFERENCES trips(trip_id)
        ON DELETE CASCADE,

    CONSTRAINT stop_times_stop_fk
        FOREIGN KEY (stop_id)
        REFERENCES stops(stop_id)
        ON DELETE RESTRICT,

    CONSTRAINT valid_stop_sequence
        CHECK (stop_sequence >= 0),

    CONSTRAINT valid_pickup_type
        CHECK (
            pickup_type IS NULL
            OR pickup_type BETWEEN 0 AND 3
        ),

    CONSTRAINT valid_drop_off_type
        CHECK (
            drop_off_type IS NULL
            OR drop_off_type BETWEEN 0 AND 3
        ),

    CONSTRAINT valid_timepoint
        CHECK (timepoint IS NULL OR timepoint IN (0, 1))
);


/* ============================================================
   11. TRANSFERS
   Source: transfers.txt
   ============================================================ */

CREATE TABLE transfers (
    transfer_id        BIGINT GENERATED ALWAYS AS IDENTITY
                       PRIMARY KEY,

    from_stop_id       TEXT NOT NULL,
    to_stop_id         TEXT NOT NULL,
    transfer_type      SMALLINT NOT NULL DEFAULT 0,
    min_transfer_time  INTEGER,
    from_trip_id       TEXT,
    to_trip_id         TEXT,

    CONSTRAINT transfers_from_stop_fk
        FOREIGN KEY (from_stop_id)
        REFERENCES stops(stop_id)
        ON DELETE CASCADE,

    CONSTRAINT transfers_to_stop_fk
        FOREIGN KEY (to_stop_id)
        REFERENCES stops(stop_id)
        ON DELETE CASCADE,

    CONSTRAINT transfers_from_trip_fk
        FOREIGN KEY (from_trip_id)
        REFERENCES trips(trip_id)
        ON DELETE CASCADE,

    CONSTRAINT transfers_to_trip_fk
        FOREIGN KEY (to_trip_id)
        REFERENCES trips(trip_id)
        ON DELETE CASCADE,

    CONSTRAINT valid_transfer_type
        CHECK (transfer_type >= 0),

    CONSTRAINT valid_minimum_transfer_time
        CHECK (
            min_transfer_time IS NULL
            OR min_transfer_time >= 0
        )
);


/* ============================================================
   12. TRANSLATIONS
   Source: translations.txt
   ============================================================ */

CREATE TABLE translations (
    table_name    TEXT NOT NULL,
    field_name    TEXT NOT NULL,
    language      TEXT NOT NULL,
    translation   TEXT NOT NULL,
    record_id     TEXT NOT NULL,

    PRIMARY KEY (
        table_name,
        field_name,
        language,
        record_id
    )
);


/* ============================================================
   13. TRANSLINK SIGNUP PERIODS
   Source: signup_periods.txt
   ============================================================ */

CREATE TABLE signup_periods (
    sign_id      TEXT PRIMARY KEY,
    from_date    DATE NOT NULL,
    to_date      DATE NOT NULL,

    CONSTRAINT valid_signup_period
        CHECK (to_date >= from_date)
);


/* ============================================================
   14. ROUTE-NAME EXCEPTIONS
   Source: route_names_exceptions.txt
   ============================================================ */

CREATE TABLE route_names_exceptions (
    route_id     TEXT PRIMARY KEY,
    route_name   TEXT NOT NULL,
    route_do     TEXT,
    name_type    TEXT,

    CONSTRAINT route_name_exception_route_fk
        FOREIGN KEY (route_id)
        REFERENCES routes(route_id)
        ON DELETE CASCADE
);


/* ============================================================
   15. DIRECTION-NAME EXCEPTIONS
   Source: direction_names_exceptions.txt
   ============================================================ */

CREATE TABLE direction_names_exceptions (
    route_name       TEXT NOT NULL,
    direction_id     SMALLINT NOT NULL,
    direction_name   TEXT NOT NULL,
    direction_do     INTEGER NOT NULL,

    PRIMARY KEY (route_name, direction_id),

    CONSTRAINT valid_exception_direction
        CHECK (direction_id IN (0, 1)),

    CONSTRAINT valid_direction_order
        CHECK (direction_do >= 0)
);


/* ============================================================
   16. DIRECTIONS
   Source: directions.txt

   Note:
   The file's header contains four names, but each data row has
   five values. The fifth value is represented by route_do.
   ============================================================ */

CREATE TABLE directions (
    direction          TEXT NOT NULL,
    direction_id       SMALLINT NOT NULL,
    route_id           TEXT NOT NULL,
    route_short_name   TEXT,
    route_do           TEXT,

    PRIMARY KEY (route_id, direction_id),

    CONSTRAINT directions_route_fk
        FOREIGN KEY (route_id)
        REFERENCES routes(route_id)
        ON DELETE CASCADE,

    CONSTRAINT valid_direction_id
        CHECK (direction_id IN (0, 1))
);


/* ============================================================
   17. STOP-ORDER EXCEPTIONS
   Source: stop_order_exceptions.txt
   ============================================================ */

CREATE TABLE stop_order_exceptions (
    route_name       TEXT NOT NULL,
    direction_name   TEXT NOT NULL,
    direction_do     INTEGER NOT NULL,
    stop_id          TEXT NOT NULL,
    stop_name        TEXT NOT NULL,
    stop_do          INTEGER NOT NULL,

    PRIMARY KEY (route_name, direction_name, stop_do),

    CONSTRAINT stop_order_stop_fk
        FOREIGN KEY (stop_id)
        REFERENCES stops(stop_id)
        ON DELETE CASCADE,

    CONSTRAINT valid_stop_order
        CHECK (stop_do > 0)
);


/* ============================================================
   18. DELAY OBSERVATIONS
   Project-specific table.

   This will eventually hold historical or real-time delays.
   It is initially empty because your extracted GTFS files
   contain schedules, not actual historical delays.
   ============================================================ */

CREATE TABLE delay_observations (
    observation_id       BIGINT GENERATED ALWAYS AS IDENTITY
                         PRIMARY KEY,

    trip_id              TEXT NOT NULL,
    stop_id              TEXT NOT NULL,
    stop_sequence        INTEGER NOT NULL,
    service_date         DATE NOT NULL,
    scheduled_arrival    INTERVAL,
    observed_at          TIMESTAMPTZ NOT NULL,
    delay_seconds        INTEGER NOT NULL,
    source               TEXT NOT NULL DEFAULT 'gtfs-realtime',
    recorded_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT delay_trip_fk
        FOREIGN KEY (trip_id)
        REFERENCES trips(trip_id)
        ON DELETE CASCADE,

    CONSTRAINT delay_stop_fk
        FOREIGN KEY (stop_id)
        REFERENCES stops(stop_id)
        ON DELETE CASCADE,

    CONSTRAINT valid_delay_stop_sequence
        CHECK (stop_sequence >= 0),

    CONSTRAINT unique_delay_observation
        UNIQUE (
            trip_id, stop_id, stop_sequence, service_date, observed_at
        )
);

/* Historical route/stop profiles. "On time" means <= 300 seconds late. */
CREATE TABLE route_reliability (
    route_id                 TEXT NOT NULL,
    stop_id                  TEXT NOT NULL,
    weekday                  SMALLINT NOT NULL,
    hour_of_day              SMALLINT NOT NULL,
    sample_count             INTEGER NOT NULL,
    mean_delay_seconds       DOUBLE PRECISION NOT NULL,
    delay_stddev_seconds     DOUBLE PRECISION,
    p50_delay_seconds        DOUBLE PRECISION NOT NULL,
    p90_delay_seconds        DOUBLE PRECISION NOT NULL,
    on_time_probability      DOUBLE PRECISION NOT NULL,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (route_id, stop_id, weekday, hour_of_day),

    CONSTRAINT route_reliability_route_fk FOREIGN KEY (route_id)
        REFERENCES routes(route_id) ON DELETE CASCADE,
    CONSTRAINT route_reliability_stop_fk FOREIGN KEY (stop_id)
        REFERENCES stops(stop_id) ON DELETE CASCADE,
    CONSTRAINT valid_reliability_weekday CHECK (weekday BETWEEN 0 AND 6),
    CONSTRAINT valid_reliability_hour CHECK (hour_of_day BETWEEN 0 AND 23),
    CONSTRAINT valid_reliability_samples CHECK (sample_count >= 0),
    CONSTRAINT valid_route_on_time_probability
        CHECK (on_time_probability BETWEEN 0.0 AND 1.0)
);


/* ============================================================
   19. CONNECTION RELIABILITY STATISTICS
   Project-specific derived table.

   Stores the calculated probability of successfully making a
   transfer under particular weekday/hour conditions.
   ============================================================ */

CREATE TABLE connection_reliability (
    connection_stat_id          BIGINT GENERATED ALWAYS AS IDENTITY
                                PRIMARY KEY,

    from_route_id               TEXT NOT NULL,
    to_route_id                 TEXT NOT NULL,
    transfer_stop_id            TEXT NOT NULL,

    weekday                     SMALLINT NOT NULL,
    hour_of_day                 SMALLINT NOT NULL,
    minimum_transfer_seconds    INTEGER NOT NULL,

    sample_count                INTEGER NOT NULL DEFAULT 0,
    successful_connections      INTEGER NOT NULL DEFAULT 0,
    mean_arrival_delay_seconds  DOUBLE PRECISION,
    delay_stddev_seconds        DOUBLE PRECISION,
    success_probability         DOUBLE PRECISION,

    updated_at                  TIMESTAMPTZ NOT NULL
                                DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT reliability_from_route_fk
        FOREIGN KEY (from_route_id)
        REFERENCES routes(route_id)
        ON DELETE CASCADE,

    CONSTRAINT reliability_to_route_fk
        FOREIGN KEY (to_route_id)
        REFERENCES routes(route_id)
        ON DELETE CASCADE,

    CONSTRAINT reliability_stop_fk
        FOREIGN KEY (transfer_stop_id)
        REFERENCES stops(stop_id)
        ON DELETE CASCADE,

    CONSTRAINT valid_weekday
        CHECK (weekday BETWEEN 0 AND 6),

    CONSTRAINT valid_hour
        CHECK (hour_of_day BETWEEN 0 AND 23),

    CONSTRAINT valid_transfer_seconds
        CHECK (minimum_transfer_seconds >= 0),

    CONSTRAINT valid_sample_count
        CHECK (sample_count >= 0),

    CONSTRAINT valid_successful_count
        CHECK (
            successful_connections >= 0
            AND successful_connections <= sample_count
        ),

    CONSTRAINT valid_success_probability
        CHECK (
            success_probability IS NULL
            OR success_probability BETWEEN 0.0 AND 1.0
        ),

    CONSTRAINT unique_connection_statistic
        UNIQUE (
            from_route_id,
            to_route_id,
            transfer_stop_id,
            weekday,
            hour_of_day,
            minimum_transfer_seconds
        )
);


/* ============================================================
   20. PERFORMANCE INDEXES
   ============================================================ */

/* Find stops geographically. */
CREATE INDEX idx_stops_coordinates
    ON stops (stop_lat, stop_lon);

/* Find all trips belonging to a route and service. */
CREATE INDEX idx_trips_route_service
    ON trips (route_id, service_id);

/* Match trips with their shapes. */
CREATE INDEX idx_trips_shape
    ON trips (shape_id);

/* Find departures from a stop. */
CREATE INDEX idx_stop_times_stop_departure
    ON stop_times (stop_id, departure_time);

/* Calendar lookups for a particular date. */
CREATE INDEX idx_calendar_dates_date
    ON calendar_dates (service_date);

/* Transfer searches. */
CREATE INDEX idx_transfers_from_stop
    ON transfers (from_stop_id);

CREATE INDEX idx_transfers_to_stop
    ON transfers (to_stop_id);

/* Historical delay-model queries. */
CREATE INDEX idx_delay_trip_date
    ON delay_observations (trip_id, service_date);

CREATE INDEX idx_delay_stop_date
    ON delay_observations (stop_id, service_date);

CREATE INDEX idx_delay_latest
    ON delay_observations (
        trip_id, stop_id, stop_sequence, service_date, observed_at DESC
    );

CREATE INDEX idx_delay_aggregation
    ON delay_observations (service_date, scheduled_arrival);

CREATE INDEX idx_route_reliability_lookup
    ON route_reliability (route_id, stop_id, weekday, hour_of_day);

/* Route-planning reliability lookups. */
CREATE INDEX idx_reliability_lookup
    ON connection_reliability (
        from_route_id,
        to_route_id,
        transfer_stop_id,
        weekday,
        hour_of_day
    );
