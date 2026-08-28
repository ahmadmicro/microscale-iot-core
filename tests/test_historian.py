"""End-to-end data platform test: envelopes on the broker -> sink -> TimescaleDB ->
read API, including the carbon rollup reconciling against the meter's counter.

Needs the MQTT broker (localhost:1883) AND a TimescaleDB on localhost:5435:

    docker run -d --rm --name tsdb-test -p 5435:5432 \
      -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=iotcore timescale/timescaledb:latest-pg16
"""
import json
import socket
import threading
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
fastapi = pytest.importorskip("fastapi")

import paho.mqtt.client as mqtt  # noqa: E402

from conftest import wait_for  # noqa: E402

DSN = "postgresql://postgres:postgres@localhost:5435/iotcore"
SITE = "t-hist"
# an exact hour boundary in epoch ms, so every sample lands in ONE '1 hour' bucket
T0 = 1_787_904_000_000
HOUR = 3_600_000


def _tsdb_up() -> bool:
    try:
        socket.create_connection(("localhost", 5435), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _tsdb_up(), reason="no TimescaleDB on localhost:5435")


@pytest.fixture(scope="module")
def platform(request):
    if not wait_for(lambda: _pg_ready(), timeout=30):
        pytest.skip("TimescaleDB not accepting connections")
    import os
    os.environ["PG_DSN"] = DSN
    from historian.sink import Sink
    sink = Sink(dsn=DSN, mqtt_host="localhost")
    with sink.conn.cursor() as cur:
        cur.execute("DELETE FROM readings WHERE site = %s", (SITE,))
        cur.execute("DELETE FROM devices WHERE site = %s", (SITE,))
    subscribed = threading.Event()
    _orig = sink.client.on_connect
    sink.client.on_connect = lambda c, u, f, rc: (_orig(c, u, f, rc), subscribed.set())
    threading.Thread(target=sink.run, daemon=True).start()
    # the test's publishes are NOT retained — wait until the sink is actually subscribed,
    # or they are delivered to nobody and the assertions race
    assert subscribed.wait(timeout=10), "sink never connected to the broker"

    pub = mqtt.Client(client_id=f"hist-pub-{uuid.uuid4().hex[:6]}")
    pub.connect("localhost", 1883)
    pub.loop_start()

    from fastapi.testclient import TestClient
    from historian.api import app
    client = TestClient(app)

    def fin():
        pub.loop_stop()
        pub.disconnect()
        sink.client.disconnect()
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM readings WHERE site = %s", (SITE,))
            conn.execute("DELETE FROM devices WHERE site = %s", (SITE,))
    request.addfinalizer(fin)
    return sink, pub, client


def _pg_ready() -> bool:
    try:
        psycopg.connect(DSN, connect_timeout=2).close()
        return True
    except psycopg.Error:
        return False


def _rows(sql, params=()):
    with psycopg.connect(DSN) as conn:
        return conn.execute(sql, params).fetchall()


def env(t, v, seq, src=None):
    e = {"t": t, "v": v, "seq": seq}
    if src:
        e["src"] = src
    return json.dumps(e)


def test_sink_ingests_and_dedupes(platform):
    _, pub, _ = platform
    base = f"ms/{SITE}/meter-1/main"
    # 4 grid + 4 generator power samples spread across one hour
    for i, off in enumerate((0, 600, 1200, 1800)):
        pub.publish(f"{base}/electrical.active_power",
                    env(T0 + off * 1000, 1000, i + 1, "grid"), qos=1)
    for i, off in enumerate((2100, 2400, 2700, 3000)):
        pub.publish(f"{base}/electrical.active_power",
                    env(T0 + off * 1000, 2000, i + 5, "generator"), qos=1)
    # cumulative counters: energy 1500 Wh burned, fuel 2.5 L
    pub.publish(f"{base}/electrical.energy", env(T0, 100000.0, 1, "grid"), qos=1)
    pub.publish(f"{base}/electrical.energy", env(T0 + 3000 * 1000, 101500.0, 2, "generator"), qos=1)
    pub.publish(f"ms/{SITE}/tank-1/main/fuel.consumed", env(T0, 50.0, 1), qos=1)
    pub.publish(f"ms/{SITE}/tank-1/main/fuel.consumed", env(T0 + 3000 * 1000, 52.5, 2), qos=1)
    # a state reading, $meta, $online
    pub.publish(f"ms/{SITE}/plug-1/main/device.relay", env(T0, "on", 1), qos=1)
    pub.publish(f"ms/{SITE}/meter-1/$meta",
                json.dumps({"schema_version": "1.0", "device_id": "meter-1"}), qos=1)
    pub.publish(f"ms/{SITE}/meter-1/$online", "true", qos=1)
    # exact duplicate (same t + seq) -> must dedupe
    pub.publish(f"{base}/electrical.active_power", env(T0, 1000, 1, "grid"), qos=1)

    assert wait_for(lambda: len(_rows(
        "SELECT 1 FROM readings WHERE site=%s AND metric='electrical.active_power'",
        (SITE,))) >= 8, timeout=10)
    assert wait_for(lambda: _rows(
        "SELECT online FROM devices WHERE site=%s AND device='meter-1'", (SITE,))
        == [(True,)], timeout=10)
    # dedupe held: exactly 8 power rows despite 9 publishes
    assert len(_rows("SELECT 1 FROM readings WHERE site=%s AND metric='electrical.active_power'",
                     (SITE,))) == 8
    # state reading landed as text
    assert _rows("SELECT value, value_text FROM readings WHERE site=%s AND metric='device.relay'",
                 (SITE,)) == [(None, "on")]


def test_api_latest_and_history(platform):
    _, _, client = platform
    latest = client.get(f"/{SITE}/latest").json()
    points = {(p["device"], p["metric"]): p for p in latest["points"]}
    assert points[("plug-1", "device.relay")]["value_text"] == "on"
    assert points[("meter-1", "electrical.energy")]["value"] == 101500.0

    hist = client.get(f"/{SITE}/readings", params={
        "metric": "electrical.active_power", "device": "meter-1", "bucket": "1 hour",
        "start": "2026-08-28T00:00:00Z", "end": "2026-08-29T00:00:00Z"}).json()
    assert len(hist["rows"]) == 1
    assert hist["rows"][0]["samples"] == 8
    assert hist["rows"][0]["avg"] == pytest.approx(1500.0)

    assert client.get(f"/{SITE}/readings", params={
        "metric": "x", "bucket": "1 hour; DROP TABLE readings"}).status_code == 422


def test_carbon_rollup_reconciles(platform):
    _, _, client = platform
    r = client.get(f"/{SITE}/energy", params={
        "device": "meter-1", "bucket": "1 hour",
        "start": "2026-08-28T00:00:00Z", "end": "2026-08-29T00:00:00Z"}).json()
    by_src = {row["src"]: row for row in r["by_source_wh"]}
    # 8 samples share the hour equally: grid 4x1000W -> 500 Wh, generator 4x2000W -> 1000 Wh
    assert by_src["grid"]["wh"] == pytest.approx(500.0)
    assert by_src["generator"]["wh"] == pytest.approx(1000.0)
    assert by_src["grid"]["scope"] == "scope2"
    assert by_src["generator"]["scope"] == "scope1"
    # ...and the estimate reconciles exactly with the meter's cumulative counter
    assert r["energy_counter_wh"][0]["wh"] == pytest.approx(1500.0)
    assert r["fuel_scope1_litres"][0]["litres"] == pytest.approx(2.5)


def test_api_token_auth(platform):
    import os
    _, _, client = platform
    os.environ["API_TOKEN"] = "sekrit"
    try:
        assert client.get(f"/{SITE}/latest").status_code == 401
        ok = client.get(f"/{SITE}/latest", headers={"Authorization": "Bearer sekrit"})
        assert ok.status_code == 200
    finally:
        del os.environ["API_TOKEN"]
