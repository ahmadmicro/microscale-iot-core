"""Author-time dashboard validation: the build fails on specs that violate the frozen
contract, and theming produces a branded spec + manifest."""
import json
import sys

import pytest
import yaml

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
from build_dashboard import build, validate  # noqa: E402

GOOD = ROOT / "dashboard" / "site.example.yaml"


def _spec(**overrides):
    spec = yaml.safe_load(GOOD.read_text())
    spec.update(overrides)
    return spec


def test_example_spec_is_valid_and_builds(tmp_path):
    out = build(GOOD, None, tmp_path)
    spec = json.loads(out.read_text())
    assert spec["site"] == "acme-lagos"
    assert (tmp_path / "manifest.webmanifest").exists()


def test_theme_brands_spec_and_manifest(tmp_path):
    out = build(GOOD, ROOT / "dashboard" / "themes" / "example-client.yaml", tmp_path)
    spec = json.loads(out.read_text())
    assert spec["title"] == "SchimaticBlue Energy"
    assert spec["theme"]["brand"] == "#1d4ed8"
    manifest = json.loads((tmp_path / "manifest.webmanifest").read_text())
    assert manifest["theme_color"] == "#1d4ed8"
    assert manifest["name"] == "SchimaticBlue Energy"


def _first_card(spec):
    return spec["pages"][0]["cards"][0]


def test_unknown_metric_fails():
    spec = _spec()
    _first_card(spec)["bind"] = "ms/acme-lagos/x/main/electrical.wattage"
    assert any("electrical.wattage" in e for e in validate(spec))


def test_wrong_site_fails():
    spec = _spec(site="other-site")
    assert any("!= spec site" in e for e in validate(spec))


def test_write_to_non_writable_metric_fails():
    spec = _spec()
    card = _first_card(spec)
    card["write"] = "ms/acme-lagos/x/main/electrical.voltage/cmd"
    assert any("not writable" in e for e in validate(spec))


def test_chart_needs_registry_metric_and_device():
    spec = _spec()
    chart = spec["pages"][1]["cards"][0]
    chart["metric"] = "electrical.nope"
    del chart["device"]
    errors = validate(spec)
    assert any("electrical.nope" in e for e in errors)
    assert any("explicit `device`" in e for e in errors)


def test_unknown_card_type_fails():
    spec = _spec()
    _first_card(spec)["type"] = "dial"
    assert any("unknown card type" in e for e in validate(spec))


def test_runtime_json_is_built_from_example():
    """dashboard.json must stay the compiled artifact of the authoring spec."""
    built = yaml.safe_load(GOOD.read_text())
    current = json.loads((ROOT / "dashboard" / "dashboard.json").read_text())
    assert current == built
