"""Shared fixtures: repo paths, a live-broker gate, and an MQTT observer with retained cleanup.

Integration tests need a broker on localhost:1883 (deploy/docker-compose.yml provides one);
they skip cleanly when none is running. Schema/unit tests run everywhere.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sdk"))

import paho.mqtt.client as mqtt  # noqa: E402


def wait_for(cond, timeout: float = 8.0) -> bool:
    """Poll `cond` until true or timeout — keeps tests fast without racy fixed sleeps."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _broker_available() -> bool:
    probe = mqtt.Client(client_id=f"probe-{uuid.uuid4().hex[:6]}")
    try:
        probe.connect("localhost", 1883, keepalive=5)
        probe.disconnect()
        return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def broker():
    if not _broker_available():
        pytest.skip("no MQTT broker on localhost:1883 (start deploy/docker-compose.yml)")
    return ("localhost", 1883)


class Observer:
    """Collects retained+live messages on the given filters; clears retained on teardown."""

    def __init__(self, *filters: str):
        self.seen: dict[str, str] = {}
        self.c = mqtt.Client(client_id=f"obs-{uuid.uuid4().hex[:6]}")
        self.c.on_message = lambda cl, u, m: self.seen.__setitem__(
            m.topic, m.payload.decode(errors="replace"))
        self.c.connect("localhost", 1883)
        for f in filters:
            self.c.subscribe(f, qos=1)
        self.c.loop_start()

    def env(self, topic: str):
        return json.loads(self.seen[topic]) if topic in self.seen else None

    def publish(self, topic: str, payload, **kw):
        self.c.publish(topic, payload, **kw)

    def stop(self):
        for t in list(self.seen):
            self.c.publish(t, payload=None, retain=True)
        time.sleep(0.3)
        self.c.loop_stop()
        self.c.disconnect()


@pytest.fixture
def observer(broker):
    made: list[Observer] = []

    def make(*filters: str) -> Observer:
        o = Observer(*filters)
        made.append(o)
        time.sleep(0.2)  # let subscriptions settle before the test publishes
        return o

    yield make
    for o in made:
        o.stop()
