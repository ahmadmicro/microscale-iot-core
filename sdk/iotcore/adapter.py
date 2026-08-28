"""
Adapter base — the reusable heart an adapter author builds on.

Responsibilities handled here (so a device adapter doesn't repeat them):
  - connect to MQTT (LWT clears $online on unexpected drop),
  - publish the retained $meta device descriptor on connect,
  - poll `read()` every `poll_interval`, wrap each value in a canonical envelope, publish RETAINED
    to  ms/<site>/<device>/<channel>/<metric>,
  - subscribe writable points' /cmd topics and dispatch to `on_command`, echoing state back,
  - buffer readings while offline and replay them (in seq order, marked q:stale) on
    reconnect — in memory by default, or on disk when constructed with spool_dir=
    (edge-gateway mode: the buffer survives restarts and power cuts).

An adapter subclass declares its channel/points and implements `read()` (and `on_command` if it has
writable points). That terse authoring experience is the DX harvested from NodeWire — now over
plain MQTT + the canonical schema, with no bespoke broker or protocol.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import paho.mqtt.client as mqtt

from . import schema


@dataclass
class Point:
    metric: str
    channel: str = "main"
    source: str | None = None  # per-point override of the device source tag
    medium: str | None = None  # per-point override of the device medium tag (flow.* metrics)
    extra: dict[str, Any] = field(default_factory=dict)  # transport metadata
    # (register=, scale=, type=...) consumed by declarative bases like ModbusAdapter


@dataclass
class Command:
    metric: str
    channel: str = "main"
    handler: Callable[[Any], None] | None = None


def point(metric: str, *, channel: str = "main", source: str | None = None,
          medium: str | None = None, **extra: Any) -> Point:
    """Declare an output point. Extra kwargs (register=, topic=, scale=...) are kept on
    Point.extra for declarative transport adapters (e.g. ModbusAdapter) to consume."""
    if not schema.is_metric(metric):
        raise schema.SchemaError(f"unknown metric {metric!r}")
    return Point(metric=metric, channel=channel, source=source, medium=medium, extra=extra)


def command(metric: str, *, channel: str = "main", **_ignored: Any) -> Command:
    if not schema.is_metric(metric):
        raise schema.SchemaError(f"unknown metric {metric!r}")
    if not schema.is_writable(metric):
        raise schema.SchemaError(f"metric {metric!r} is not writable in the registry")
    return Command(metric=metric, channel=channel)


class Adapter:
    # --- declared by subclasses ---
    model: str = "generic.device"
    poll_interval: float = 5.0
    source: str | None = None
    medium: str | None = None
    accuracy_class: str | None = None
    points: list[Point] = []
    commands: list[Command] = []

    def __init__(self, *, site: str, device_id: str, host: str = "localhost", port: int = 1883,
                 spool_dir: str | None = None):
        self.site = site
        self.device_id = device_id
        self.host = host
        self.port = port
        self._seq: dict[tuple[str, str], int] = {}
        self._buffer: deque[tuple[str, dict[str, Any]]] = deque(maxlen=10000)
        # spool_dir enables DISK-backed store-and-forward (edge-gateway mode): readings
        # taken while offline survive a process restart / power cut and replay on reconnect
        self._spool: Path | None = None
        if spool_dir is not None:
            self._spool = Path(spool_dir) / f"{site}.{device_id}.spool.jsonl"
            self._spool.parent.mkdir(parents=True, exist_ok=True)
        self._connected = False
        self._client = mqtt.Client(client_id=f"{site}.{device_id}")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.will_set(self._topic_meta_online(), payload="false", qos=1, retain=True)

    # ---- topic helpers ----
    def _base(self) -> str:
        return f"ms/{self.site}/{self.device_id}"

    def _topic_point(self, metric: str, channel: str) -> str:
        return f"{self._base()}/{channel}/{metric}"

    def _topic_cmd(self, metric: str, channel: str) -> str:
        return f"{self._base()}/{channel}/{metric}/cmd"

    def _topic_meta(self) -> str:
        return f"{self._base()}/$meta"

    def _topic_meta_online(self) -> str:
        return f"{self._base()}/$online"

    # ---- descriptor ----
    def _descriptor(self) -> dict[str, Any]:
        channels: dict[str, list[str]] = {}
        for item in (*self.points, *self.commands):
            pts = channels.setdefault(item.channel, [])
            if item.metric not in pts:          # a writable point is both a point + a command
                pts.append(item.metric)
        desc = {
            "schema_version": schema.SCHEMA_VERSION,
            "device_id": self.device_id,
            "site": self.site,
            "model": self.model,
            "adapter": f"{self.__class__.__name__.lower()}@0.1",
            "source": self.source,
            "accuracy_class": self.accuracy_class,
            "online": True,
            "channels": [{"id": cid, "points": pts} for cid, pts in channels.items()],
        }
        # unset optionals are OMITTED, not null — the descriptor schema has no null members
        return {k: v for k, v in desc.items() if v is not None}

    # ---- lifecycle ----
    def run(self) -> None:
        self._client.connect(self.host, self.port, keepalive=30)
        self._client.loop_start()
        try:
            while True:
                if self._connected:
                    self._tick()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self._publish(self._topic_meta_online(), "false", retain=True)
            self._client.loop_stop()
            self._client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):  # noqa: ANN001
        self._connected = True
        self._publish(self._topic_meta(), json.dumps(self._descriptor()), retain=True)
        self._publish(self._topic_meta_online(), "true", retain=True)
        for c in self.commands:
            client.subscribe(self._topic_cmd(c.metric, c.channel), qos=1)
        self._replay_buffered()

    def _replay_buffered(self) -> None:
        """Replay disk-spooled then in-memory readings, oldest first, marked q:stale so
        consumers can tell replayed history from live data. The spool is deleted only
        after full replay — a crash mid-replay re-sends (seq lets consumers dedupe)."""
        def send(topic: str, env: dict[str, Any]) -> None:
            env.setdefault("q", "stale")
            self._client.publish(topic, json.dumps(env), qos=1, retain=True)

        if self._spool is not None and self._spool.exists():
            for line in self._spool.read_text().splitlines():
                try:
                    topic, env = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                send(topic, env)
            self._spool.unlink()
        while self._buffer:
            send(*self._buffer.popleft())

    def _on_message(self, client, userdata, msg):  # noqa: ANN001
        parts = msg.topic.split("/")
        if parts[-1] != "cmd":
            return
        metric, channel = parts[-2], parts[-3]
        try:
            value = json.loads(msg.payload.decode())
        except json.JSONDecodeError:
            value = msg.payload.decode()
        self.on_command(metric, value)
        # echo resulting state back on the point topic (optimistic; real devices confirm)
        self.publish(metric, self.on_command_state(metric, value), channel=channel)

    def _tick(self) -> None:
        # read() may key by metric, or by (metric, channel) for multi-channel devices
        # (e.g. a 3-phase meter with electrical.voltage on L1/L2/L3)
        readings = self.read() or {}
        for p in self.points:
            value = readings.get((p.metric, p.channel), readings.get(p.metric))
            if value is not None:
                self.publish(p.metric, value, channel=p.channel,
                             source=p.source or self.source,
                             medium=p.medium or self.medium)

    # ---- publishing ----
    def publish(self, metric: str, value: Any, *, channel: str = "main",
                source: str | None = None, medium: str | None = None,
                quality: str = "good") -> None:
        key = (channel, metric)
        self._seq[key] = self._seq.get(key, 0) + 1
        env = schema.build_envelope(
            metric, value, quality=quality,
            source=source if source is not None else self.source,
            medium=medium if medium is not None else self.medium,
            seq=self._seq[key],
        )
        topic = self._topic_point(metric, channel)
        if self._connected:
            self._client.publish(topic, json.dumps(env), qos=1, retain=True)
        elif self._spool is not None:
            with self._spool.open("a") as fh:
                fh.write(json.dumps([topic, env]) + "\n")
        else:
            self._buffer.append((topic, env))

    def _publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self._client.publish(topic, payload, qos=1, retain=retain)

    # ---- subclass hooks ----
    def read(self) -> dict[str, Any]:
        """Return {metric: value} in canonical units. Override in a device adapter."""
        raise NotImplementedError

    def on_command(self, metric: str, value: Any) -> None:
        """Handle a write to a writable point. Override if the device has commands."""

    def on_command_state(self, metric: str, value: Any) -> Any:
        """The state to echo back after a command. Default: the commanded value."""
        return value
