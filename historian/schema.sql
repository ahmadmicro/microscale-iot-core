-- Historian schema — TimescaleDB. Idempotent; applied by the sink at startup and by
-- the timescale image's initdb hook in deploy/historian/.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS readings (
    time        timestamptz      NOT NULL,   -- envelope t (measured at source)
    site        text             NOT NULL,
    device      text             NOT NULL,
    channel     text             NOT NULL,
    metric      text             NOT NULL,
    value       double precision,            -- numeric readings
    value_text  text,                        -- state readings (enum/bool as text)
    quality     text,                        -- envelope q (null = good)
    src         text,                        -- carbon source dimension
    medium      text,                        -- utility dimension on flow.*
    seq         bigint
);

SELECT create_hypertable('readings', 'time', if_not_exists => TRUE);

-- Idempotent ingest: q:stale replays and MQTT redeliveries collapse into one row.
CREATE UNIQUE INDEX IF NOT EXISTS readings_dedupe
    ON readings (site, device, channel, metric, time, coalesce(seq, -1));

CREATE INDEX IF NOT EXISTS readings_query
    ON readings (site, metric, device, channel, time DESC);

-- Latest $meta / $online per device.
CREATE TABLE IF NOT EXISTS devices (
    site        text NOT NULL,
    device      text NOT NULL,
    meta        jsonb,
    online      boolean,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site, device)
);
