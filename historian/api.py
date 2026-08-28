"""
Historian read API — the stable HTTP contract above ingestion. Dashboard charts and
downstream analytics (the SBE EMIS/carbon platform) consume this, never the DB directly.

    PG_DSN=... uvicorn historian.api:app --host 0.0.0.0 --port 8090

Endpoints (all JSON; optional bearer auth via API_TOKEN env):
    GET /health
    GET /{site}/devices                       device registry ($meta + online)
    GET /{site}/latest                        latest reading per point
    GET /{site}/readings?metric=&device=&channel=&start=&end=&bucket=
                                              raw rows, or time_bucket aggregates
    GET /{site}/energy?device=&start=&end=&bucket=
                                              per-source energy + carbon scopes + fuel

Carbon method: within each bucket, active-power samples are grouped by their `src` tag
and each sample accounts for an equal share of the bucket (assumes steady sampling —
true for polled adapters). grid → Scope 2, generator → Scope 1 (site combustion);
`energy_counter_wh` (delta of the meter's cumulative counter) is the audit total.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

app = FastAPI(title="Microscale Historian", version="0.1")
# the dashboard PWA fetches charts from another origin; the API is read-only
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["Authorization"])

BUCKET_RE = re.compile(r"^\d+ (second|minute|hour|day|week)s?$")
SCOPE = {"grid": "scope2", "generator": "scope1", "solar": "none", "battery": "none"}


def _conn():
    return psycopg.connect(
        os.environ.get("PG_DSN", "postgresql://postgres:postgres@localhost:5432/iotcore"),
        row_factory=dict_row)


def auth(authorization: str | None = Header(None)) -> None:
    token = os.environ.get("API_TOKEN")
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="bad or missing bearer token")


def _bucket(bucket: str | None) -> str | None:
    if bucket is not None and not BUCKET_RE.match(bucket):
        raise HTTPException(status_code=422, detail="bucket must look like '15 minutes'")
    return bucket


def _window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=24)
    return start, end


@app.get("/health")
def health():
    with _conn() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/{site}/devices", dependencies=[Depends(auth)])
def devices(site: str):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT device, meta, online, updated_at FROM devices WHERE site = %s "
            "ORDER BY device", (site,)).fetchall()
    return {"site": site, "devices": rows}


@app.get("/{site}/latest", dependencies=[Depends(auth)])
def latest(site: str):
    with _conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT ON (device, channel, metric)
                   device, channel, metric, time, value, value_text, quality, src, medium
            FROM readings WHERE site = %s
            ORDER BY device, channel, metric, time DESC""", (site,)).fetchall()
    return {"site": site, "points": rows}


@app.get("/{site}/readings", dependencies=[Depends(auth)])
def readings(site: str, metric: str, device: str | None = None, channel: str = "main",
             start: datetime | None = None, end: datetime | None = None,
             bucket: str | None = None, limit: int = Query(default=5000, le=50000)):
    start, end = _window(start, end)
    bucket = _bucket(bucket)
    where = "site = %s AND metric = %s AND channel = %s AND time >= %s AND time < %s"
    params: list = [site, metric, channel, start, end]
    if device is not None:
        where += " AND device = %s"
        params.append(device)
    with _conn() as conn:
        if bucket is None:
            rows = conn.execute(
                f"SELECT device, time, value, value_text, quality, src FROM readings "
                f"WHERE {where} ORDER BY time LIMIT %s", (*params, limit)).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT time_bucket(%s::interval, time) AS bucket, device,
                       avg(value) AS avg, min(value) AS min, max(value) AS max,
                       count(*) AS samples
                FROM readings WHERE {where}
                GROUP BY 1, 2 ORDER BY 1 LIMIT %s""",
                (bucket, *params, limit)).fetchall()
    return {"site": site, "metric": metric, "channel": channel,
            "start": start, "end": end, "bucket": bucket, "rows": rows}


@app.get("/{site}/energy", dependencies=[Depends(auth)])
def energy(site: str, device: str, start: datetime | None = None,
           end: datetime | None = None, bucket: str = "1 hour"):
    """Per-source energy rollup for ONE metering device (explicit device avoids
    double-counting overlapping meters), with carbon scopes and site fuel burn."""
    start, end = _window(start, end)
    bucket = _bucket(bucket)
    with _conn() as conn:
        by_src = conn.execute("""
            WITH samples AS (
                SELECT time_bucket(%s::interval, time) AS b,
                       coalesce(src, 'unknown') AS src, value,
                       count(*) OVER (PARTITION BY time_bucket(%s::interval, time)) AS n
                FROM readings
                WHERE site = %s AND device = %s AND channel = 'main'
                  AND metric = 'electrical.active_power' AND time >= %s AND time < %s)
            SELECT b AS bucket, src,
                   sum(value * extract(epoch FROM %s::interval) / n / 3600.0) AS wh,
                   count(*) AS samples
            FROM samples GROUP BY b, src ORDER BY b, src""",
            (bucket, bucket, site, device, start, end, bucket)).fetchall()
        counter = conn.execute("""
            SELECT time_bucket(%s::interval, time) AS bucket,
                   max(value) - min(value) AS wh
            FROM readings
            WHERE site = %s AND device = %s AND metric = 'electrical.energy'
              AND time >= %s AND time < %s
            GROUP BY 1 ORDER BY 1""", (bucket, site, device, start, end)).fetchall()
        fuel = conn.execute("""
            SELECT time_bucket(%s::interval, time) AS bucket,
                   max(value) - min(value) AS litres
            FROM readings
            WHERE site = %s AND metric = 'fuel.consumed' AND time >= %s AND time < %s
            GROUP BY 1 ORDER BY 1""", (bucket, site, start, end)).fetchall()
    for row in by_src:
        row["scope"] = SCOPE.get(row["src"], "unknown")
    return {"site": site, "device": device, "start": start, "end": end, "bucket": bucket,
            "by_source_wh": by_src, "energy_counter_wh": counter,
            "fuel_scope1_litres": fuel}
