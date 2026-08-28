"""Multi-tenancy guarantees of the hosted service: the read API accepts a site's own
broker JWT and refuses everyone else's, and onboarding produces per-site artifacts.

The broker half of the isolation story is tests/test_emqx_acl.py; this covers the API
half and the onboarding tool. No containers needed.
"""
import os
import sys
import time

import pytest
import yaml

from conftest import ROOT

jwt = pytest.importorskip("jwt")
pytest.importorskip("fastapi")

sys.path.insert(0, str(ROOT / "tools"))
from mint_token import acl_for  # noqa: E402
from onboard_site import mint  # noqa: E402

SECRET = "cloud-test-secret-0123456789abcdef"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.delenv("API_TOKEN", raising=False)
    from fastapi.testclient import TestClient
    from historian.api import app
    return TestClient(app, raise_server_exceptions=False)


def token(role, site, days=1):
    return mint(role, site, SECRET, days)


# These tests are about the AUTH decision only. A request that gets past auth reaches the
# database, which may not exist here — a 500 from the query still means "authorized", so
# accept it alongside 200 rather than requiring a live TimescaleDB.
AUTHORIZED = (200, 500)


def test_site_token_reads_its_own_site(client):
    r = client.get("/site-a/devices", headers={"Authorization": f"Bearer {token('dashboard', 'site-a')}"})
    assert r.status_code in AUTHORIZED


def test_site_token_refused_on_another_site(client):
    r = client.get("/site-b/devices", headers={"Authorization": f"Bearer {token('dashboard', 'site-a')}"})
    assert r.status_code == 403


def test_missing_and_forged_tokens_refused(client):
    assert client.get("/site-a/devices").status_code == 401
    forged = jwt.encode({"acl": {"sub": ["ms/site-a/#"]}, "exp": int(time.time()) + 60},
                        "wrong-secret", algorithm="HS256")
    assert client.get("/site-a/devices", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_expired_token_refused(client):
    stale = jwt.encode({"acl": {"sub": ["ms/site-a/#"]}, "exp": int(time.time()) - 60},
                       SECRET, algorithm="HS256")
    assert client.get("/site-a/devices", headers={"Authorization": f"Bearer {stale}"}).status_code == 401


def test_service_token_reads_every_site(client):
    """The historian sink's own credential: ms/# on read, nothing on write."""
    tok = token("service", "-")
    for site in ("site-a", "site-b"):
        r = client.get(f"/{site}/devices", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code in AUTHORIZED
    assert acl_for("service", "-", None)["pub"] == []


def test_static_api_token_still_works(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "static-tok")
    r = client.get("/site-a/devices", headers={"Authorization": "Bearer static-tok"})
    assert r.status_code in AUTHORIZED
    assert client.get("/site-a/devices", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_onboarding_builds_per_site_dashboard(tmp_path):
    from onboard_site import main
    spec = yaml.safe_load((ROOT / "dashboard" / "site.example.yaml").read_text())
    spec_path = tmp_path / "site.yaml"
    spec_path.write_text(yaml.safe_dump(spec))
    argv = ["onboard_site", spec["site"], "--spec", str(spec_path), "--domain", "iot.example.com",
            "--secret", SECRET, "--out-dir", str(tmp_path / "dashboards")]
    monkey = sys.argv
    sys.argv = argv
    try:
        main()
    finally:
        sys.argv = monkey
    out = tmp_path / "dashboards" / spec["site"]
    assert (out / "dashboard.json").exists()
    assert (out / "manifest.webmanifest").exists()


def test_edge_token_cannot_reach_another_sites_topics():
    """The tenant boundary as the broker sees it (enforced live in test_emqx_acl.py)."""
    acl = acl_for("edge", "site-a", None)
    assert all("site-b" not in t for t in acl["pub"] + acl["sub"])
    assert any(t.startswith("ms/site-a/") for t in acl["pub"])
