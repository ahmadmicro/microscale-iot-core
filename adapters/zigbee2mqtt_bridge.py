"""
Generic Zigbee2MQTT bridge — path B in docs/device-integration.md, for the whole Zigbee family.

Zigbee2MQTT (running on the edge host with an EFR32 coordinator — ZB-GW04 dongle or a flashed
ZB-GW03) publishes every paired Zigbee device as JSON on `zigbee2mqtt/<friendly_name>`. This
bridge maps those payloads to canonical `ms/<site>/<device>/<channel>/<metric>` envelopes, so
Aqara sensors, wall switches, smart plugs, terminal-block relays, and bulbs all become config
rows — no per-device code.

    python -m adapters.zigbee2mqtt_bridge                 # uses adapters/zigbee2mqtt_bridge.yaml
    python -m adapters.zigbee2mqtt_bridge path/to/config.yaml

Relay writes (`.../device.relay/cmd`) are forwarded as `zigbee2mqtt/<name>/set {"state": ...}`;
the confirmed state comes back in the device's next Z2M publish (never echoed optimistically).
Enable `availability` in Zigbee2MQTT so `$online` tracks battery devices dropping off.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# allow running from the repo root with the sdk on the path
sys.path.insert(0, "sdk")

import paho.mqtt.client as mqtt  # noqa: E402
import yaml  # noqa: E402

from iotcore import schema  # noqa: E402
from iotcore.broker import connect_kwargs, make_client  # noqa: E402

DEFAULT_CONFIG = Path(__file__).with_name("zigbee2mqtt_bridge.yaml")

# Z2M sensor field → (canonical metric, converter). Applied to every device.
SENSOR_MAP: dict[str, tuple[str, Any]] = {
    "temperature":     ("environment.temperature", float),
    "humidity":        ("environment.humidity", float),
    "pressure":        ("environment.pressure", float),          # Z2M reports hPa
    "illuminance_lux": ("environment.illuminance", float),
    "occupancy":       ("environment.occupancy", lambda v: 1 if v else 0),
    "contact":         ("environment.contact", lambda v: "closed" if v else "open"),
    "water_leak":      ("environment.leak", bool),
    "battery":         ("device.battery", float),
}

# Mains metering fields — only mapped when the config row sets `power_metering: true`,
# because battery sensors also report a `voltage` (their cell, in mV) that must NOT
# land on electrical.voltage.
ELECTRICAL_MAP: dict[str, tuple[str, float]] = {
    "power":   ("electrical.active_power", 1.0),
    "voltage": ("electrical.voltage", 1.0),
    "current": ("electrical.current", 1.0),
    "energy":  ("electrical.energy", 1000.0),  # Z2M reports kWh; canonical is W.h
}


class Device:
    def __init__(self, device_id: str, cfg: dict[str, Any]):
        self.device_id = device_id
        self.name = str(cfg["name"])                     # Z2M friendly_name
        self.model = str(cfg.get("model", "zigbee.generic"))
        self.source = cfg.get("source")
        self.relay = bool(cfg.get("relay", False))
        self.power_metering = bool(cfg.get("power_metering", False))
        self.light = bool(cfg.get("light", False))
        # Metrics to withhold for this device. Z2M's auto-generated definitions can surface
        # a raw ZCL counter without the device's divisor applied — publishing that as a
        # canonical reading puts a number into history that is simply untrue. Withhold it
        # until the device is benched, rather than shipping a plausible-looking lie.
        self.exclude = set(cfg.get("exclude") or ())
        # Sensor metrics are not knowable from config — any Zigbee device may report any
        # of them. Learn them from what the device actually sends, so $meta describes the
        # device rather than the union of everything the bridge could theoretically map.
        self.seen: set[str] = set()


class Zigbee2MqttBridge:
    def __init__(self, config_path: Path):
        cfg = yaml.safe_load(config_path.read_text())
        self.site: str = cfg["site"]
        self.host: str = cfg.get("broker", {}).get("host", "localhost")
        self.port: int = int(cfg.get("broker", {}).get("port", 1883))
        self.z2m_base: str = cfg.get("z2m_base", "zigbee2mqtt")
        self.devices = [Device(did, dcfg) for did, dcfg in cfg["devices"].items()]
        self._by_name = {d.name: d for d in self.devices}
        self._by_id = {d.device_id: d for d in self.devices}
        self._seq: dict[tuple[str, str, str], int] = {}
        # `broker` is where CANONICAL envelopes go (a cloud broker, typically). Zigbee2MQTT
        # publishes its vendor topics on the gateway's own broker, usually a different one:
        # an edge token has no business publishing raw zigbee2mqtt/# to the cloud, and the
        # cloud never sees it anyway. `source_broker` names that local one; omit it and a
        # single connection serves both, as before.
        broker = cfg.get("broker", {})
        self._broker = connect_kwargs(broker.get("url"), self.host, self.port)
        self.host, self.port = self._broker.host, self._broker.port
        self._client = make_client(f"{self.site}.z2m-bridge", self._broker,
                                   username=broker.get("username"),
                                   password=broker.get("password"))
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        src = cfg.get("source_broker")
        if src:
            self._src_broker = connect_kwargs(src.get("url"), src.get("host", "localhost"),
                                              int(src.get("port", 1883)))
            self._src = make_client(f"{self.site}.z2m-bridge-src", self._src_broker,
                                    username=src.get("username"), password=src.get("password"))
            self._src.on_connect = self._on_src_connect
            self._src.on_message = self._on_message
        else:
            self._src_broker = self._broker
            self._src = self._client

    # ---- canonical side ----
    def _base(self, d: Device) -> str:
        return f"ms/{self.site}/{d.device_id}"

    def _publish_reading(self, d: Device, metric: str, value: Any, *,
                         channel: str = "main") -> None:
        if metric in d.exclude:
            return
        if metric in (m for m, _ in SENSOR_MAP.values()) and metric not in d.seen:
            d.seen.add(metric)
            self._client.publish(f"{self._base(d)}/$meta", json.dumps(self._descriptor(d)),
                                 qos=1, retain=True)   # $meta must stay truthful as we learn
        key = (d.device_id, channel, metric)
        self._seq[key] = self._seq.get(key, 0) + 1
        # src is an energy-attribution dimension — stamp it on electrical readings only
        source = d.source if metric.startswith("electrical.") else None
        env = schema.build_envelope(metric, value, source=source, seq=self._seq[key])
        self._client.publish(f"{self._base(d)}/{channel}/{metric}",
                             json.dumps(env), qos=1, retain=True)

    def _descriptor(self, d: Device) -> dict[str, Any]:
        points = sorted(d.seen)
        if d.power_metering:
            points += [m for m, _ in ELECTRICAL_MAP.values()]
        if d.relay:
            points.append("device.relay")
        if d.light:
            points += ["light.brightness", "light.color_temp", "light.color"]
        points = [m for m in points if m not in d.exclude]
        desc = {
            "schema_version": schema.SCHEMA_VERSION,
            "device_id": d.device_id,
            "site": self.site,
            "model": d.model,
            "adapter": "zigbee2mqtt_bridge@0.1",
            "source": d.source,
            "online": True,
            "channels": [{"id": "main", "points": points}],
        }
        return {k: v for k, v in desc.items() if v is not None}

    # ---- lifecycle ----
    def run(self) -> None:
        if self._src is not self._client:
            self._src.connect(self._src_broker.host, self._src_broker.port, keepalive=30)
            self._src.loop_start()
        self._client.connect(self.host, self.port, keepalive=30)
        print(f"zigbee2mqtt bridge: site={self.site} devices={[d.device_id for d in self.devices]} "
              f"source={self._src_broker.host}:{self._src_broker.port} "
              f"canonical={self.host}:{self.port}")
        self._client.loop_forever()

    def _subscribe_source(self, client) -> None:  # noqa: ANN001
        for d in self.devices:
            # exact subscriptions (not a wildcard) so our own /set writes don't loop back
            client.subscribe(f"{self.z2m_base}/{d.name}", qos=1)
            client.subscribe(f"{self.z2m_base}/{d.name}/availability", qos=1)

    def _on_src_connect(self, client, userdata, flags, rc):  # noqa: ANN001
        self._subscribe_source(client)

    def _on_connect(self, client, userdata, flags, rc):  # noqa: ANN001
        for d in self.devices:
            client.publish(f"{self._base(d)}/$meta", json.dumps(self._descriptor(d)),
                           qos=1, retain=True)
            writable = (["device.relay"] if d.relay else []) + (
                ["light.brightness", "light.color_temp", "light.color"] if d.light else [])
            for metric in writable:
                client.subscribe(f"{self._base(d)}/main/{metric}/cmd", qos=1)
        if self._src is client:          # single-broker mode: this client reads Z2M too
            self._subscribe_source(client)

    # ---- message routing ----
    def _on_message(self, client, userdata, msg):  # noqa: ANN001
        payload = msg.payload.decode(errors="replace")
        if msg.topic.startswith("ms/") and msg.topic.endswith("/cmd"):
            self._handle_cmd(msg.topic.split("/"), payload)
            return
        rel = msg.topic[len(self.z2m_base) + 1:]
        if rel.endswith("/availability"):
            d = self._by_name.get(rel[: -len("/availability")])
            if d is not None:
                self._handle_availability(d, payload)
        else:
            d = self._by_name.get(rel)
            if d is not None:
                self._handle_state(d, payload)

    def _handle_availability(self, d: Device, payload: str) -> None:
        # Z2M publishes either a bare "online"/"offline" or {"state": "online"} by version
        try:
            state = json.loads(payload).get("state", "")
        except (json.JSONDecodeError, AttributeError):
            state = payload.strip()
        self._client.publish(f"{self._base(d)}/$online",
                             "true" if state.lower() == "online" else "false",
                             qos=1, retain=True)

    def _handle_state(self, d: Device, payload: str) -> None:
        try:
            doc = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(doc, dict):
            return
        for field, (metric, conv) in SENSOR_MAP.items():
            if field in doc and doc[field] is not None:
                self._publish_reading(d, metric, conv(doc[field]))
        # older Z2M exposes lux as `illuminance` when no raw variant exists
        if "illuminance_lux" not in doc and isinstance(doc.get("illuminance"), (int, float)):
            self._publish_reading(d, "environment.illuminance", float(doc["illuminance"]))
        if d.power_metering:
            for field, (metric, scale) in ELECTRICAL_MAP.items():
                if isinstance(doc.get(field), (int, float)):
                    self._publish_reading(d, metric, doc[field] * scale)
        if d.relay and isinstance(doc.get("state"), str):
            self._publish_reading(d, "device.relay",
                                  "on" if doc["state"].upper() == "ON" else "off")
        if d.light:
            if isinstance(doc.get("brightness"), (int, float)):
                self._publish_reading(d, "light.brightness",
                                      round(doc["brightness"] / 254 * 100, 1))
            if isinstance(doc.get("color_temp"), (int, float)) and doc["color_temp"] > 0:
                # Z2M reports mireds; canonical is Kelvin
                self._publish_reading(d, "light.color_temp",
                                      round(1_000_000 / doc["color_temp"]))
            # color is write-only in 0.1: Z2M reports xy/hs coordinates, and converting
            # them back to an rgb-hex reading isn't worth the code until a dashboard needs it

    def _handle_cmd(self, parts: list[str], payload: str) -> None:
        # ms/<site>/<device>/<channel>/<metric>/cmd
        device_id, metric = parts[2], parts[4]
        d = self._by_id.get(device_id)
        if d is None:
            return
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            value = payload.strip()
        try:
            if metric == "device.relay" and d.relay:
                on = value in ("on", "ON", 1, True, "1")
                doc: dict[str, Any] = {"state": "ON" if on else "OFF"}
            elif metric == "light.brightness" and d.light:
                doc = {"brightness": max(0, min(254, round(float(value) * 254 / 100)))}
            elif metric == "light.color_temp" and d.light:
                doc = {"color_temp": round(1_000_000 / float(value))}  # Kelvin -> mireds
            elif metric == "light.color" and d.light:
                doc = {"color": {"hex": str(value)}}
            else:
                return
        except (TypeError, ValueError, ZeroDivisionError):
            return
        # writes go to the broker Zigbee2MQTT is actually listening on
        self._src.publish(f"{self.z2m_base}/{d.name}/set", json.dumps(doc), qos=1)


def main() -> None:
    config = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    try:
        Zigbee2MqttBridge(config).run()
    except KeyboardInterrupt:
        print("\nstopping")


if __name__ == "__main__":
    main()
