"""The MicroPython firmware SDK honors the same frozen contract as everything else —
run under CPython with a fake umqtt client (no hardware, no broker), envelopes and
descriptors validated against the canonical JSON Schemas."""
import json
import subprocess
import sys

import jsonschema
import pytest

from conftest import ROOT

FW = ROOT / "firmware" / "micropython"
sys.path.insert(0, str(FW))  # for the generated iotcore_registry import

# the firmware module is also named `iotcore` (on-device there IS no SDK package) —
# load it by path so it can't collide with sdk/iotcore in this process
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("fw_iotcore", FW / "iotcore.py")
_fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fw)
Device, build_envelope = _fw.Device, _fw.build_envelope

ENVELOPE_SCHEMA = json.loads(
    (ROOT / "schema" / "canonical" / "reading.envelope.schema.json").read_text())
DESCRIPTOR_SCHEMA = json.loads(
    (ROOT / "schema" / "canonical" / "device-descriptor.schema.json").read_text())


class FakeMqtt:
    def __init__(self):
        self.published = []       # (topic, payload, retain, qos)
        self.subscribed = []
        self.lwt = None
        self.cb = None

    def set_last_will(self, topic, msg, retain=False, qos=0):
        self.lwt = (topic, msg, retain, qos)

    def set_callback(self, cb):
        self.cb = cb

    def connect(self):
        pass

    def publish(self, topic, msg, retain=False, qos=0):
        self.published.append((topic, json.loads(msg) if msg not in (b"true", b"false")
                               else msg, retain, qos))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def check_msg(self):
        pass


def make_device(**kw):
    c = FakeMqtt()
    d = Device(c, site="s1", device_id="fw1", model="microscale.test", source="grid",
               points=["electrical.active_power", ("electrical.voltage", "L1"), "device.relay"],
               commands=["device.relay"], **kw)
    return d, c


def by_topic(c):
    return {t: p for t, p, _r, _q in c.published}


def test_envelope_matches_frozen_schema():
    env = build_envelope("electrical.active_power", 1500.0, source="grid", seq=1)
    jsonschema.validate(env, ENVELOPE_SCHEMA)
    assert env["u"] == "W"
    assert env["t"] > 1_700_000_000_000  # real epoch ms, not a year-2000 port epoch


def test_rejects_contract_violations():
    with pytest.raises(ValueError):
        build_envelope("electrical.wattage", 1)
    with pytest.raises(ValueError):
        build_envelope("electrical.voltage", 230, quality="excellent")
    with pytest.raises(ValueError):
        Device(FakeMqtt(), site="s", device_id="d", model="m",
               points=[], commands=["electrical.voltage"])  # not writable


def test_connect_publishes_meta_online_and_lwt():
    d, c = make_device()
    d.connect()
    assert c.lwt == ("ms/s1/fw1/$online", b"false", True, 1)
    topics = by_topic(c)
    jsonschema.validate(topics["ms/s1/fw1/$meta"], DESCRIPTOR_SCHEMA)
    assert topics["ms/s1/fw1/$online"] == b"true"
    assert {ch["id"] for ch in topics["ms/s1/fw1/$meta"]["channels"]} == {"main", "L1"}
    assert c.subscribed == ["ms/s1/fw1/main/device.relay/cmd"]


def test_publish_reading_with_seq_and_source():
    d, c = make_device()
    d.connect()
    d.publish("electrical.active_power", 1234.5)
    d.publish("electrical.active_power", 1300.0)
    d.publish("electrical.voltage", 231.2, channel="L1")
    envs = [p for t, p, r, q in c.published if t == "ms/s1/fw1/main/electrical.active_power"]
    assert [e["seq"] for e in envs] == [1, 2]
    assert all(e["src"] == "grid" for e in envs)
    for _t, p, retain, qos in c.published[2:]:
        assert retain and qos == 1
        jsonschema.validate(p, ENVELOPE_SCHEMA)
    assert by_topic(c)["ms/s1/fw1/L1/electrical.voltage"]["v"] == 231.2


def test_offline_buffer_replays_stale_in_order():
    d, c = make_device()
    d.publish("electrical.active_power", 100.0)   # not connected yet -> buffered
    d.publish("electrical.active_power", 200.0)
    assert c.published == []
    d.connect()
    envs = [p for t, p, _r, _q in c.published if t.endswith("electrical.active_power")]
    assert [e["v"] for e in envs] == [100.0, 200.0]
    assert all(e["q"] == "stale" for e in envs)
    assert [e["seq"] for e in envs] == [1, 2]


def test_command_dispatch_echoes_actual_state():
    got = []

    def on_command(metric, channel, value):
        got.append((metric, channel, value))
        return "off"

    d, c = make_device(on_command=on_command)
    d.connect()
    c.cb(b"ms/s1/fw1/main/device.relay/cmd", b'"off"')
    assert got == [("device.relay", "main", "off")]
    relay = by_topic(c)["ms/s1/fw1/main/device.relay"]
    assert relay["v"] == "off"
    jsonschema.validate(relay, ENVELOPE_SCHEMA)


def test_generated_registry_in_sync_with_metrics_yaml():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_fw_registry.py"), "--check"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
