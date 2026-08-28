"""The v1.0 contract tests: registry sanity + envelopes/descriptors validate against
the frozen JSON Schemas. These are the CI gate for the schema freeze."""
import json

import jsonschema
import pytest

from conftest import ROOT
from iotcore import schema

ENVELOPE_SCHEMA = json.loads(
    (ROOT / "schema" / "canonical" / "reading.envelope.schema.json").read_text())
DESCRIPTOR_SCHEMA = json.loads(
    (ROOT / "schema" / "canonical" / "device-descriptor.schema.json").read_text())


# ---- registry sanity ----

def test_registry_loads_and_is_wellformed():
    assert schema.SCHEMA_VERSION == "1.0"
    for metric in schema._METRICS:
        assert schema.canonical_unit(metric), metric
        assert schema.kind(metric) in ("gauge", "counter", "state"), metric
        category, _, name = metric.partition(".")
        assert category and name, f"metric key must be category.name: {metric}"


def test_writable_metrics_are_the_control_plane():
    writable = {m for m in schema._METRICS if schema.is_writable(m)}
    assert writable == {"device.relay", "light.brightness", "light.color_temp", "light.color"}


# ---- envelope validation against the frozen JSON Schema ----

def _valid(env):
    jsonschema.validate(env, ENVELOPE_SCHEMA)


def test_minimal_envelope_validates():
    _valid(schema.build_envelope("electrical.active_power", 1500))


def test_full_envelope_validates():
    _valid(schema.build_envelope("electrical.energy", 125000.0, quality="stale",
                                 source="generator", seq=42))


def test_medium_envelope_validates():
    env = schema.build_envelope("flow.volumetric", 3.2, medium="water", seq=1)
    _valid(env)
    assert env["medium"] == "water"


def test_state_and_bool_values_validate():
    _valid(schema.build_envelope("device.relay", "on"))
    _valid(schema.build_envelope("environment.leak", True))
    _valid(schema.build_envelope("light.color", "#ff8800"))


def test_quality_good_is_omitted():
    assert "q" not in schema.build_envelope("electrical.voltage", 230.0)


def test_envelope_schema_rejects_unknown_fields():
    env = schema.build_envelope("electrical.voltage", 230.0)
    env["extra"] = 1
    with pytest.raises(jsonschema.ValidationError):
        _valid(env)


@pytest.mark.parametrize("kwargs", [
    {"quality": "excellent"},
    {"source": "nuclear"},
    {"medium": "beer"},
])
def test_build_envelope_rejects_bad_dimensions(kwargs):
    with pytest.raises(schema.SchemaError):
        schema.build_envelope("electrical.voltage", 230.0, **kwargs)


def test_build_envelope_rejects_unknown_metric():
    with pytest.raises(schema.SchemaError):
        schema.build_envelope("electrical.wattage", 1)


# ---- descriptor validation for every adapter family ----

def _valid_desc(desc):
    jsonschema.validate(desc, DESCRIPTOR_SCHEMA)
    assert None not in desc.values(), "unset optionals must be omitted, not null"


def test_sdk_adapter_descriptor_validates():
    from adapters.simulator import DieselTank, MainMeter
    _valid_desc(MainMeter(site="s", device_id="d").__class__._descriptor(
        MainMeter(site="s", device_id="d")))
    _valid_desc(DieselTank(site="s", device_id="d")._descriptor())


def test_modbus_adapter_descriptor_validates():
    from adapters.orno_we516 import OrnoWE516
    meter = OrnoWE516(site="s", device_id="d", modbus_tcp="localhost:1502")
    desc = meter._descriptor()
    _valid_desc(desc)
    assert {c["id"] for c in desc["channels"]} == {"main", "L1", "L2", "L3"}


def test_bridge_descriptors_validate(tmp_path):
    from adapters.tasmota_bridge import TasmotaBridge
    from adapters.zigbee2mqtt_bridge import Zigbee2MqttBridge
    tas = tmp_path / "t.yaml"
    tas.write_text("site: s\ndevices:\n  p1: { topic: t1, relay: true }\n")
    z2m = tmp_path / "z.yaml"
    z2m.write_text("site: s\ndevices:\n  b1: { name: n1, relay: true, light: true }\n")
    tb = TasmotaBridge(tas)
    zb = Zigbee2MqttBridge(z2m)
    _valid_desc(tb._descriptor(tb.devices[0]))
    _valid_desc(zb._descriptor(zb.devices[0]))


# ---- every metric named anywhere must exist in the registry ----

def test_bridge_maps_reference_known_metrics():
    from adapters.tasmota_bridge import ENERGY_MAP
    from adapters.zigbee2mqtt_bridge import ELECTRICAL_MAP, SENSOR_MAP
    for metric, _ in (*ENERGY_MAP.values(), *SENSOR_MAP.values(), *ELECTRICAL_MAP.values()):
        assert schema.is_metric(metric), metric


def test_orno_points_reference_known_metrics():
    from adapters.orno_we516 import OrnoWE516
    for p in OrnoWE516.points:
        assert schema.is_metric(p.metric), p.metric
        assert "register" in p.extra, p.metric
