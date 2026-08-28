# Historian — the data platform above ingestion (Phase 3)

Three pieces, deployed by `deploy/historian/docker-compose.yml`:

- **TimescaleDB** — Postgres + hypertables. Chosen over Influx because the analytics
  hand-off is SQL (the contract SBE builds EMIS/carbon on) and device metadata joins
  come free.
- **`sink.py`** — subscribes `ms/#`, writes envelopes to the `readings` hypertable and
  `$meta`/`$online` to `devices`. Ingest is idempotent (unique index over
  site/device/channel/metric/time/seq), so `q:stale` outage replays and MQTT
  redeliveries collapse into one row. State values land in `value_text`, numerics in
  `value`; the `src`/`medium`/`q` dimensions are first-class columns.
- **`api.py`** — the read contract: device registry, latest-per-point, raw or
  `time_bucket`-aggregated history, and the carbon endpoint.

## The carbon endpoint

`GET /{site}/energy?device=main-meter&bucket=1 hour` returns, per bucket:

- `by_source_wh` — active-power samples grouped by their `src` tag, each sample
  accounting for an equal share of the bucket (assumes the adapter's steady poll
  cadence). Tagged with scope: grid → **Scope 2**, generator → **Scope 1**.
- `energy_counter_wh` — delta of the meter's cumulative counter: the audit total the
  estimate must reconcile against.
- `fuel_scope1_litres` — delta of `fuel.consumed` counters on the site.

`device` is required — summing overlapping meters (main + sub-meters) would double
count; the consumer chooses the metering point.

## Auth

Set `API_TOKEN` to require `Authorization: Bearer <token>` on all data endpoints.
The API is read-only by construction (no write endpoints; SQL is parameterized and
`bucket` is validated against a strict pattern).
