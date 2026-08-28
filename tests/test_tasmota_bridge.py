"""Integration: fake Tasmota plug -> bridge -> canonical topics -> command round trip."""
import json
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import pytest

from adapters.tasmota_bridge import TasmotaBridge
from conftest import wait_for

SITE = "t-tas"
BASE = f"ms/{SITE}/plug-t1"


class FakePlug:
    def __init__(self, topic):
        self.topic = topic
        self.state = "ON"
        self.got_cmnd = []
        self.c = mqtt.Client(client_id=f"fake-tas-{uuid.uuid4().hex[:6]}")
        self.c.on_message = self._on_message
        self.c.connect("localhost", 1883)
        self.c.subscribe(f"cmnd/{topic}/POWER", qos=1)
        self.c.loop_start()

    def _on_message(self, client, userdata, msg):
        self.got_cmnd.append(msg.payload.decode())
        self.state = self.got_cmnd[-1]
        self.c.publish(f"stat/{self.topic}/POWER", self.state, qos=1)

    def stop(self):
        self.c.publish(f"tele/{self.topic}/LWT", payload=None, retain=True)
        self.c.loop_stop()
        self.c.disconnect()


@pytest.fixture
def rig(broker, observer, tmp_path):
    topic = f"tastest_{uuid.uuid4().hex[:6]}"
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"""
site: {SITE}
devices:
  plug-t1: {{ topic: {topic}, model: sonoff.s26r2, source: grid, relay: true }}
""")
    bridge = TasmotaBridge(cfg)
    threading.Thread(target=bridge.run, daemon=True).start()
    obs = observer(f"ms/{SITE}/#")
    plug = FakePlug(topic)
    time.sleep(0.5)  # bridge + plug subscriptions settle
    yield obs, plug
    plug.stop()
    bridge._client.disconnect()


def test_telemetry_commands_and_lwt(rig):
    obs, plug = rig
    plug.c.publish(f"tele/{plug.topic}/LWT", "Online", qos=1, retain=True)
    plug.c.publish(f"tele/{plug.topic}/STATE", json.dumps(
        {"POWER": "ON", "Wifi": {"Signal": -58}}), qos=1)
    plug.c.publish(f"tele/{plug.topic}/SENSOR", json.dumps({"ENERGY": {
        "Total": 1.234, "Power": 56, "Factor": 0.93, "Voltage": 231.4, "Current": 0.26}}), qos=1)

    assert wait_for(lambda: obs.env(f"{BASE}/main/electrical.energy"))
    power = obs.env(f"{BASE}/main/electrical.active_power")
    assert power["v"] == 56 and power["u"] == "W" and power["src"] == "grid"
    assert obs.env(f"{BASE}/main/electrical.energy")["v"] == pytest.approx(1234.0)  # kWh -> W.h
    assert obs.env(f"{BASE}/main/device.rssi")["v"] == -58
    relay = obs.env(f"{BASE}/main/device.relay")
    assert relay["v"] == "on" and "src" not in relay
    assert obs.seen.get(f"{BASE}/$online") == "true"
    assert obs.env(f"{BASE}/$meta")["adapter"] == "tasmota_bridge@0.1"

    # command round trip: canonical cmd -> cmnd POWER -> stat echo -> canonical state
    obs.publish(f"{BASE}/main/device.relay/cmd", json.dumps("off"), qos=1)
    assert wait_for(lambda: plug.got_cmnd == ["OFF"])
    assert wait_for(lambda: (obs.env(f"{BASE}/main/device.relay") or {}).get("v") == "off")

    plug.c.publish(f"tele/{plug.topic}/LWT", "Offline", qos=1, retain=True)
    assert wait_for(lambda: obs.seen.get(f"{BASE}/$online") == "false")
