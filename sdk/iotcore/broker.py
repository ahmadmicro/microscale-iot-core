"""
Broker connection URLs — one string carries transport, TLS, host, port and path.

The dashboard already configures its broker as a URL (`wss://host/mqtt`); this gives
adapters, bridges and the historian the same vocabulary, so an edge gateway can reach a
cloud broker through the TLS front door instead of needing a plaintext MQTT port opened:

    mqtt://broker.lan              plain TCP, 1883        (LAN / behind a VPN)
    mqtts://iot.example.com        TCP + TLS, 8883
    ws://localhost:8083/mqtt       WebSocket, no TLS
    wss://iot.example.com/mqtt     WebSocket + TLS, 443   (through an HTTPS reverse proxy)

Anything not given falls back to the scheme's default port and `/mqtt` for websockets.
"""
from __future__ import annotations

from typing import Any, NamedTuple
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

# scheme -> (paho transport, tls, default port)
_SCHEMES: dict[str, tuple[str, bool, int]] = {
    "mqtt": ("tcp", False, 1883),
    "mqtts": ("tcp", True, 8883),
    "ws": ("websockets", False, 80),
    "wss": ("websockets", True, 443),
}


class Broker(NamedTuple):
    host: str
    port: int
    transport: str
    tls: bool
    path: str


def parse_broker_url(url: str) -> Broker:
    parts = urlparse(url)
    if parts.scheme not in _SCHEMES:
        raise ValueError(f"unsupported broker scheme {parts.scheme!r} in {url!r} "
                         f"(use one of: {', '.join(sorted(_SCHEMES))})")
    transport, tls, default_port = _SCHEMES[parts.scheme]
    if not parts.hostname:
        raise ValueError(f"no host in broker url {url!r}")
    return Broker(
        host=parts.hostname,
        port=parts.port or default_port,
        transport=transport,
        tls=tls,
        path=parts.path or "/mqtt",
    )


def connect_kwargs(url: str | None, host: str, port: int) -> Broker:
    """Resolve either a URL or a legacy host/port pair to one Broker."""
    if url:
        return parse_broker_url(url)
    return Broker(host=host, port=port, transport="tcp", tls=False, path="/mqtt")


def make_client(client_id: str, broker: Broker, *, username: str | None = None,
                password: str | None = None, **kw: Any) -> mqtt.Client:
    """A paho client wired for the transport the Broker describes."""
    client = mqtt.Client(client_id=client_id, transport=broker.transport, **kw)
    if broker.transport == "websockets":
        client.ws_set_options(path=broker.path)
    if broker.tls:
        client.tls_set()          # system CA bundle — Let's Encrypt chains verify as-is
    if username or password:
        client.username_pw_set(username, password)
    return client
