"""Live verification of the production broker's security model (EMQX JWT + acl claim).

Skips unless a test EMQX listens on localhost:1884 with JWT secret "test-secret-123".
Start one with:

    docker run -d --rm --name emqx-acl-test -p 1884:1883 \
      -e EMQX_AUTHENTICATION__1__MECHANISM=jwt \
      -e EMQX_AUTHENTICATION__1__FROM=password \
      -e EMQX_AUTHENTICATION__1__USE_JWKS=false \
      -e EMQX_AUTHENTICATION__1__ALGORITHM=hmac-based \
      -e EMQX_AUTHENTICATION__1__SECRET=test-secret-123 \
      -e EMQX_AUTHENTICATION__1__SECRET_BASE64_ENCODED=false \
      -e EMQX_AUTHENTICATION__1__ACL_CLAIM_NAME=acl \
      -e EMQX_AUTHORIZATION__NO_MATCH=deny \
      -e EMQX_AUTHORIZATION__DENY_ACTION=ignore \
      emqx/emqx:5.8
"""
import json
import socket
import time
import uuid

import paho.mqtt.client as mqtt
import pytest

from conftest import wait_for

jwt = pytest.importorskip("jwt")

SECRET = "test-secret-123"
PORT = 1884


def _emqx_up() -> bool:
    try:
        socket.create_connection(("localhost", PORT), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _emqx_up(), reason="no test EMQX on localhost:1884")


def mint(role, site, device=None, secret=SECRET):
    import sys
    sys.path.insert(0, "tools")
    from mint_token import acl_for
    name = f"{site}.{device}" if device else f"{role}.{site}"
    return jwt.encode({"username": name, "acl": acl_for(role, site, device),
                       "exp": int(time.time()) + 3600}, secret, algorithm="HS256")


def connect(token, on_message=None):
    c = mqtt.Client(client_id=f"acl-{uuid.uuid4().hex[:6]}")
    c.username_pw_set("test", password=token)
    state = {"rc": None, "granted": {}}
    c.on_connect = lambda cl, u, f, rc: state.__setitem__("rc", rc)
    c.on_subscribe = lambda cl, u, mid, qos: state["granted"].__setitem__(mid, qos)
    if on_message:
        c.on_message = on_message
    c.connect("localhost", PORT, keepalive=10)
    c.loop_start()
    assert wait_for(lambda: state["rc"] is not None, timeout=5)
    return c, state


def test_security_model_end_to_end():
    clients = []
    try:
        # forged signature refused
        c_bad, s_bad = connect(mint("dashboard", "site-a", secret="wrong-secret-000"))
        clients.append(c_bad)
        assert s_bad["rc"] != 0

        seen_a, seen_b, got_cmd = {}, {}, []
        dash_a, sa = connect(mint("dashboard", "site-a"),
                             lambda c, u, m: seen_a.__setitem__(m.topic, m.payload.decode()))
        dash_b, sb = connect(mint("dashboard", "site-b"),
                             lambda c, u, m: seen_b.__setitem__(m.topic, m.payload.decode()))
        dev, sd = connect(mint("device", "site-a", "plug-01"),
                          lambda c, u, m: got_cmd.append(m.topic))
        clients += [dash_a, dash_b, dev]
        assert sa["rc"] == sb["rc"] == sd["rc"] == 0

        # dashboard: own site granted, other site denied (SUBACK 0x80)
        r_own = dash_a.subscribe("ms/site-a/#", qos=1)
        r_other = dash_a.subscribe("ms/site-b/#", qos=1)
        dash_b.subscribe("ms/site-b/#", qos=1)
        assert wait_for(lambda: sa["granted"].get(r_other[1]) is not None)
        assert sa["granted"][r_own[1]] == (1,)
        assert sa["granted"][r_other[1]] == (128,)

        # device publishes its own subtree -> delivered; spoofs -> silently dropped
        own_topic = "ms/site-a/plug-01/main/electrical.active_power"
        dev.publish(own_topic, json.dumps({"t": 1, "v": 60}), qos=1)
        dev.publish("ms/site-b/plug-01/main/electrical.active_power", "{}", qos=1)
        dev.publish("ms/site-a/main-meter/main/electrical.active_power", "{}", qos=1)
        assert wait_for(lambda: own_topic in seen_a)
        time.sleep(1)
        assert not seen_b, "cross-site publish must be blocked"
        assert "ms/site-a/main-meter/main/electrical.active_power" not in seen_a

        # cmd path: device hears its own /cmd; dashboard may write cmd but not readings
        dev.subscribe("ms/site-a/plug-01/+/+/cmd", qos=1)
        time.sleep(0.5)
        dash_a.publish("ms/site-a/plug-01/main/device.relay/cmd", '"off"', qos=1)
        assert wait_for(lambda: "ms/site-a/plug-01/main/device.relay/cmd" in got_cmd)
        dash_a.publish(own_topic, json.dumps({"t": 1, "v": 0}), qos=1)
        time.sleep(1)
        assert seen_a[own_topic] == json.dumps({"t": 1, "v": 60}), \
            "dashboard reading-spoof must be blocked"
    finally:
        for c in clients:
            c.loop_stop()
            c.disconnect()
