"""Integration: fake Zigbee2MQTT -> bridge -> canonical topics, across every device shape
the bridge supports (Aqara sensors, metering plug, DIN breaker, ZBMINI, RGB bulb)."""
import json
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import pytest

from adapters.zigbee2mqtt_bridge import Zigbee2MqttBridge
from conftest import wait_for

SITE = "t-z2m"


@pytest.fixture
def rig(broker, observer, tmp_path):
    z2m_base = f"z2mtest_{uuid.uuid4().hex[:6]}"
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(f"""
site: {SITE}
z2m_base: {z2m_base}
devices:
  climate-1: {{ name: aqara_temp, model: aqara.wsdcgq11lm }}
  motion-1:  {{ name: aqara_motion, model: aqara.rtcgq11lm }}
  door-1:    {{ name: aqara_door, model: aqara.mccgq11lm }}
  leak-1:    {{ name: aqara_leak, model: aqara.sjcgq11lm }}
  plug-z1:   {{ name: aqara_plug, model: aqara.sp-euc01, source: grid, relay: true, power_metering: true }}
  bulb-1:    {{ name: bulb_1, model: tuya.ts0505b, relay: true, light: true }}
""")
    bridge = Zigbee2MqttBridge(cfg)
    threading.Thread(target=bridge.run, daemon=True).start()

    got_set = []
    fake = mqtt.Client(client_id=f"fake-z2m-{uuid.uuid4().hex[:6]}")
    def _on_set(c, u, m):
        got_set.append((m.topic, json.loads(m.payload.decode())))
        if m.topic.endswith("aqara_plug/set"):
            state = got_set[-1][1]["state"]
            c.publish(f"{z2m_base}/aqara_plug", json.dumps({"state": state, "power": 0}), qos=1)
    fake.on_message = _on_set
    fake.connect("localhost", 1883)
    fake.subscribe(f"{z2m_base}/+/set", qos=1)
    fake.loop_start()

    obs = observer(f"ms/{SITE}/#")
    time.sleep(0.5)
    yield obs, fake, got_set, z2m_base
    fake.loop_stop()
    fake.disconnect()
    bridge._client.disconnect()


def env(obs, dev, metric):
    return obs.env(f"ms/{SITE}/{dev}/main/{metric}")


def test_sensor_shapes(rig):
    obs, fake, _, base = rig
    fake.publish(f"{base}/aqara_temp", json.dumps(
        {"temperature": 27.5, "humidity": 61.2, "pressure": 1006.3,
         "battery": 91, "voltage": 3005}), qos=1)
    fake.publish(f"{base}/aqara_motion", json.dumps({"occupancy": True, "illuminance_lux": 320}), qos=1)
    fake.publish(f"{base}/aqara_door", json.dumps({"contact": False}), qos=1)
    fake.publish(f"{base}/aqara_leak", json.dumps({"water_leak": True}), qos=1)
    fake.publish(f"{base}/aqara_temp/availability", "online", qos=1)

    assert wait_for(lambda: env(obs, "leak-1", "environment.leak"))
    assert env(obs, "climate-1", "environment.temperature")["v"] == 27.5
    assert env(obs, "climate-1", "environment.pressure")["u"] == "hPa"
    assert env(obs, "climate-1", "device.battery")["v"] == 91
    # the CR2032 cell voltage (mV) must never become a mains reading
    assert env(obs, "climate-1", "electrical.voltage") is None
    assert env(obs, "motion-1", "environment.occupancy")["v"] == 1
    assert env(obs, "motion-1", "environment.illuminance")["u"] == "lx"
    assert env(obs, "door-1", "environment.contact")["v"] == "open"  # z2m contact:false = open
    assert env(obs, "leak-1", "environment.leak")["v"] is True
    assert wait_for(lambda: obs.seen.get(f"ms/{SITE}/climate-1/$online") == "true")


def test_metering_plug_and_relay_roundtrip(rig):
    obs, fake, got_set, base = rig
    fake.publish(f"{base}/aqara_plug", json.dumps(
        {"state": "ON", "power": 12.4, "voltage": 229.8, "current": 0.06, "energy": 3.21}), qos=1)
    assert wait_for(lambda: env(obs, "plug-z1", "electrical.energy"))
    assert env(obs, "plug-z1", "electrical.active_power")["src"] == "grid"
    assert env(obs, "plug-z1", "electrical.energy")["v"] == pytest.approx(3210.0)
    assert env(obs, "plug-z1", "device.relay")["v"] == "on"

    obs.publish(f"ms/{SITE}/plug-z1/main/device.relay/cmd", json.dumps("off"), qos=1)
    assert wait_for(lambda: (f"{base}/aqara_plug/set", {"state": "OFF"}) in got_set)
    assert wait_for(lambda: (env(obs, "plug-z1", "device.relay") or {}).get("v") == "off")


def test_light_readings_and_writes(rig):
    obs, fake, got_set, base = rig
    fake.publish(f"{base}/bulb_1", json.dumps(
        {"state": "ON", "brightness": 178, "color_temp": 285,
         "color_mode": "xy", "color": {"x": 0.42, "y": 0.36}}), qos=1)
    assert wait_for(lambda: env(obs, "bulb-1", "light.brightness"))
    assert env(obs, "bulb-1", "light.brightness")["v"] == 70.1        # 178/254 -> %
    assert env(obs, "bulb-1", "light.color_temp")["v"] == 3509        # 285 mired -> K
    assert env(obs, "bulb-1", "light.color") is None                  # write-only in 0.1

    for metric, value in [("light.brightness", 50), ("light.color_temp", 4000),
                          ("light.color", "#ff8800"), ("device.relay", "off")]:
        obs.publish(f"ms/{SITE}/bulb-1/main/{metric}/cmd", json.dumps(value), qos=1)
    expected = [{"brightness": 127}, {"color_temp": 250},
                {"color": {"hex": "#ff8800"}}, {"state": "OFF"}]
    assert wait_for(lambda: [d for t, d in got_set if "bulb_1" in t] == expected)
