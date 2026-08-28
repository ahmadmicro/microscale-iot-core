"""
Generic Tasmota MQTT bridge — path B in docs/device-integration.md.

One bridge process serves EVERY Tasmota-flashed device on the broker: adding the next plug is a
config row in `tasmota_bridge.yaml`, not code. The bridge subscribes to the devices' native
Tasmota topics and republishes canonical envelopes on `ms/<site>/<device>/<channel>/<metric>`;
dashboard writes to `.../device.relay/cmd` are forwarded to Tasmota `cmnd/.../POWER`, and the
relay state is confirmed from the device's own `stat` echo (never optimistically).

    python -m adapters.tasmota_bridge                     # uses adapters/tasmota_bridge.yaml
    python -m adapters.tasmota_bridge path/to/config.yaml

Device-side setup, once per plug (Tasmota web console → Console):
    MqttHost <broker-ip>      # point the plug at our broker
    Topic    <topic>          # must match the config row's `topic`
    TelePeriod 10             # SENSOR/STATE cadence (default 300 s is too slow for live cards)
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

DEFAULT_CONFIG = Path(__file__).with_name("tasmota_bridge.yaml")

# Tasmota ENERGY JSON field → (canonical metric, scale-to-canonical-unit)
ENERGY_MAP: dict[str, tuple[str, float]] = {
    "Voltage":       ("electrical.voltage", 1.0),
    "Current":       ("electrical.current", 1.0),
    "Power":         ("electrical.active_power", 1.0),
    "ApparentPower": ("electrical.apparent_power", 1.0),
    "ReactivePower": ("electrical.reactive_power", 1.0),
    "Factor":        ("electrical.power_factor", 1.0),
    "Frequency":     ("electrical.frequency", 1.0),
    "Total":         ("electrical.energy", 1000.0),  # Tasmota reports kWh; canonical is W.h
}


class Device:
    def __init__(self, device_id: str, cfg: dict[str, Any]):
        self.device_id = device_id
        self.topic = str(cfg["topic"])
        self.model = str(cfg.get("model", "tasmota.generic"))
        self.source = cfg.get("source")
        self.relay = bool(cfg.get("relay", False))


class TasmotaBridge:
    def __init__(self, config_path: Path):
        cfg = yaml.safe_load(config_path.read_text())
        self.site: str = cfg["site"]
        self.host: str = cfg.get("broker", {}).get("host", "localhost")
        self.port: int = int(cfg.get("broker", {}).get("port", 1883))
        self.devices = [Device(did, dcfg) for did, dcfg in cfg["devices"].items()]
        self._by_topic = {d.topic: d for d in self.devices}
        self._by_id = {d.device_id: d for d in self.devices}
        self._seq: dict[tuple[str, str, str], int] = {}
        self._client = mqtt.Client(client_id=f"{self.site}.tasmota-bridge")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    # ---- canonical side ----
    def _base(self, d: Device) -> str:
        return f"ms/{self.site}/{d.device_id}"

    def _publish_reading(self, d: Device, metric: str, value: Any, *,
                         channel: str = "main") -> None:
        key = (d.device_id, channel, metric)
        self._seq[key] = self._seq.get(key, 0) + 1
        # src is an energy-attribution dimension — stamp it on electrical readings only
        source = d.source if metric.startswith("electrical.") else None
        env = schema.build_envelope(metric, value, source=source, seq=self._seq[key])
        self._client.publish(f"{self._base(d)}/{channel}/{metric}",
                             json.dumps(env), qos=1, retain=True)

    def _descriptor(self, d: Device) -> dict[str, Any]:
        points = [m for m, _ in ENERGY_MAP.values()] + ["device.rssi"]
        if d.relay:
            points.append("device.relay")
        return {
            "schema_version": schema.SCHEMA_VERSION,
            "device_id": d.device_id,
            "site": self.site,
            "model": d.model,
            "adapter": "tasmota_bridge@0.1",
            "source": d.source,
            "accuracy_class": None,
            "online": True,
            "channels": [{"id": "main", "points": points}],
        }

    # ---- lifecycle ----
    def run(self) -> None:
        self._client.connect(self.host, self.port, keepalive=30)
        print(f"tasmota bridge: site={self.site} devices={[d.device_id for d in self.devices]}")
        self._client.loop_forever()

    def _on_connect(self, client, userdata, flags, rc):  # noqa: ANN001
        for d in self.devices:
            client.publish(f"{self._base(d)}/$meta", json.dumps(self._descriptor(d)),
                           qos=1, retain=True)
            for prefix in ("tele", "stat"):
                client.subscribe(f"{prefix}/{d.topic}/#", qos=1)
            if d.relay:
                client.subscribe(f"{self._base(d)}/main/device.relay/cmd", qos=1)

    # ---- message routing ----
    def _on_message(self, client, userdata, msg):  # noqa: ANN001
        parts = msg.topic.split("/")
        payload = msg.payload.decode(errors="replace")
        if parts[0] in ("tele", "stat") and len(parts) == 3 and parts[1] in self._by_topic:
            self._handle_tasmota(self._by_topic[parts[1]], parts[0], parts[2], payload)
        elif parts[0] == "ms" and parts[-1] == "cmd":
            self._handle_cmd(parts, payload)

    def _handle_tasmota(self, d: Device, prefix: str, kind: str, payload: str) -> None:
        if kind == "LWT":
            online = payload.strip().lower() == "online"
            self._client.publish(f"{self._base(d)}/$online",
                                 "true" if online else "false", qos=1, retain=True)
            return
        try:
            doc = json.loads(payload)
        except json.JSONDecodeError:
            doc = payload.strip()
        if kind == "SENSOR" and isinstance(doc, dict):
            for field, raw in (doc.get("ENERGY") or {}).items():
                if field in ENERGY_MAP and isinstance(raw, (int, float)):
                    metric, scale = ENERGY_MAP[field]
                    self._publish_reading(d, metric, raw * scale)
        elif kind == "STATE" and isinstance(doc, dict):
            self._relay_state(d, doc)
            signal = (doc.get("Wifi") or {}).get("Signal")
            if isinstance(signal, (int, float)):
                self._publish_reading(d, "device.rssi", signal)
        elif kind == "RESULT" and isinstance(doc, dict):
            self._relay_state(d, doc)
        elif kind in ("POWER", "POWER1") and isinstance(doc, str):
            self._relay_state(d, {"POWER": doc})

    def _relay_state(self, d: Device, doc: dict[str, Any]) -> None:
        if not d.relay:
            return
        state = doc.get("POWER") or doc.get("POWER1")
        if isinstance(state, str):
            self._publish_reading(d, "device.relay", "on" if state.upper() == "ON" else "off")

    def _handle_cmd(self, parts: list[str], payload: str) -> None:
        # ms/<site>/<device>/<channel>/device.relay/cmd
        device_id, metric = parts[2], parts[4]
        d = self._by_id.get(device_id)
        if d is None or not d.relay or metric != "device.relay":
            return
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            value = payload.strip()
        on = value in ("on", "ON", 1, True, "1")
        self._client.publish(f"cmnd/{d.topic}/POWER", "ON" if on else "OFF", qos=1)


def main() -> None:
    config = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    try:
        TasmotaBridge(config).run()
    except KeyboardInterrupt:
        print("\nstopping")


if __name__ == "__main__":
    main()
