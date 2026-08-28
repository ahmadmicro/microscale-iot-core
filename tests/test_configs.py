"""Repo hygiene: every YAML ships parseable, and dashboard bindings + example configs
stay consistent with the canonical contract."""
import json

import yaml

from conftest import ROOT
from iotcore import schema


def test_all_yaml_files_parse():
    for path in ROOT.rglob("*.yaml"):
        if ".venv" in path.parts:
            continue
        yaml.safe_load(path.read_text())


def test_catalog_entries_have_required_fields():
    for path in (ROOT / "catalog").glob("*.yaml"):
        doc = yaml.safe_load(path.read_text())
        for field in ("model", "class", "interface", "gate", "adapter"):
            assert field in doc, f"{path.name} missing {field}"


def test_dashboard_bindings_use_prefix_and_known_metrics():
    doc = json.loads((ROOT / "dashboard" / "dashboard.json").read_text())
    for page in doc["pages"]:
        for card in page["cards"]:
            for key in ("bind", "write"):
                if key not in card:
                    continue
                topic = card[key]
                assert topic.startswith("ms/"), topic
                parts = topic.split("/")
                metric = parts[4]
                assert schema.is_metric(metric), f"{topic} -> unknown metric {metric}"


def test_bridge_example_configs_load():
    from adapters.tasmota_bridge import DEFAULT_CONFIG as TAS
    from adapters.zigbee2mqtt_bridge import DEFAULT_CONFIG as Z2M
    for cfg_path in (TAS, Z2M):
        cfg = yaml.safe_load(cfg_path.read_text())
        assert cfg["site"]
        assert cfg["devices"]
