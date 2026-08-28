"""
Historian sink — subscribes ms/# on the broker and writes canonical envelopes into
TimescaleDB. The dedupe index makes ingest idempotent, so q:stale replays after an
outage and MQTT redeliveries are safe.

    PG_DSN=postgresql://postgres:postgres@localhost:5432/iotcore \
    MQTT_HOST=localhost python -m historian.sink

Connect it to the production EMQX with an `edge`-role token (MQTT_USERNAME/PASSWORD).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import paho.mqtt.client as mqtt
import psycopg

INSERT_READING = """
INSERT INTO readings (time, site, device, channel, metric,
                      value, value_text, quality, src, medium, seq)
VALUES (to_timestamp(%s / 1000.0), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""

UPSERT_META = """
INSERT INTO devices (site, device, meta, updated_at) VALUES (%s, %s, %s, now())
ON CONFLICT (site, device) DO UPDATE SET meta = EXCLUDED.meta, updated_at = now()
"""

UPSERT_ONLINE = """
INSERT INTO devices (site, device, online, updated_at) VALUES (%s, %s, %s, now())
ON CONFLICT (site, device) DO UPDATE SET online = EXCLUDED.online, updated_at = now()
"""


class Sink:
    def __init__(self, dsn: str, mqtt_host: str = "localhost", mqtt_port: int = 1883,
                 username: str | None = None, password: str | None = None):
        self.conn = psycopg.connect(dsn, autocommit=True)
        with self.conn.cursor() as cur:
            cur.execute((Path(__file__).with_name("schema.sql")).read_text())
        self.client = mqtt.Client(client_id="historian-sink")
        if username or password:
            self.client.username_pw_set(username, password)
        self.client.on_connect = lambda c, u, f, rc: c.subscribe("ms/#", qos=1)
        self.client.on_message = self._on_message
        self._host, self._port = mqtt_host, mqtt_port

    def run(self) -> None:
        self.client.connect(self._host, self._port, keepalive=30)
        print(f"historian sink: mqtt={self._host}:{self._port} -> timescaledb")
        self.client.loop_forever()

    # ---- ingest ----
    def _on_message(self, client, userdata, msg):  # noqa: ANN001
        parts = msg.topic.split("/")
        payload = msg.payload.decode(errors="replace")
        try:
            if parts[-1] == "$meta" and len(parts) == 4:
                self._exec(UPSERT_META, (parts[1], parts[2], payload))
            elif parts[-1] == "$online" and len(parts) == 4:
                self._exec(UPSERT_ONLINE, (parts[1], parts[2], payload == "true"))
            elif len(parts) == 5 and not parts[-1].startswith("$"):
                self._ingest_reading(parts, payload)
            # /cmd topics (len 6) and anything else are not history
        except psycopg.Error as e:
            print(f"[sink] db error on {msg.topic}: {e}")

    def _ingest_reading(self, parts: list[str], payload: str) -> None:
        try:
            env = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(env, dict) or "t" not in env or "v" not in env:
            return
        v = env["v"]
        numeric = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        text = None if numeric is not None else json.dumps(v) if not isinstance(v, str) else v
        self._exec(INSERT_READING, (
            env["t"], parts[1], parts[2], parts[3], parts[4],
            numeric, text, env.get("q"), env.get("src"), env.get("medium"), env.get("seq"),
        ))

    def _exec(self, sql: str, params: tuple) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)


def main() -> None:
    Sink(
        dsn=os.environ.get("PG_DSN", "postgresql://postgres:postgres@localhost:5432/iotcore"),
        mqtt_host=os.environ.get("MQTT_HOST", "localhost"),
        mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
        username=os.environ.get("MQTT_USERNAME"),
        password=os.environ.get("MQTT_PASSWORD"),
    ).run()


if __name__ == "__main__":
    main()
