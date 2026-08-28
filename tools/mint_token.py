"""
Mint JWT credentials for the EMQX production broker (deploy/emqx/).

The token IS the credential: EMQX validates the signature (shared HMAC secret) and
enforces the embedded `acl` claim as a per-client topic whitelist — no broker-side
user database to manage. Anything not in the whitelist is denied (authorization
no_match=deny).

Roles:
  device    — a field device/adapter: publishes only its own subtree, subscribes only
              its own /cmd topics.
  edge      — an edge-gateway bridge process: full ms/<site>/# plus the native device
              namespaces it translates (tele/stat/cmnd for Tasmota, zigbee2mqtt/#).
  dashboard — a viewer: reads the whole site, writes only /cmd topics.

Usage:
  python tools/mint_token.py device    --site acme-lagos --device plug-01 --secret $S
  python tools/mint_token.py edge      --site acme-lagos --secret $S
  python tools/mint_token.py dashboard --site acme-lagos --secret $S [--days 30]

Pass the output as the MQTT *password* (username: anything readable, e.g. the role).
"""
from __future__ import annotations

import argparse
import time

import jwt


def acl_for(role: str, site: str, device: str | None) -> dict:
    if role == "device":
        if not device:
            raise SystemExit("--device is required for role 'device'")
        return {
            "pub": [f"ms/{site}/{device}/#"],
            "sub": [f"ms/{site}/{device}/+/+/cmd"],
        }
    if role == "edge":
        return {
            "pub": [f"ms/{site}/#", "cmnd/#", "zigbee2mqtt/+/set"],
            "sub": [f"ms/{site}/#", "tele/#", "stat/#", "zigbee2mqtt/#"],
        }
    if role == "dashboard":
        return {
            "pub": [f"ms/{site}/+/+/+/cmd"],
            "sub": [f"ms/{site}/#"],
        }
    raise SystemExit(f"unknown role {role!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mint an EMQX JWT credential")
    ap.add_argument("role", choices=["device", "edge", "dashboard"])
    ap.add_argument("--site", required=True)
    ap.add_argument("--device", help="device_id (device role only)")
    ap.add_argument("--secret", required=True, help="the broker's JWT HMAC secret")
    ap.add_argument("--days", type=int, default=365, help="token lifetime (default 365)")
    args = ap.parse_args()

    name = f"{args.site}.{args.device}" if args.device else f"{args.role}.{args.site}"
    claims = {
        "username": name,
        "acl": acl_for(args.role, args.site, args.device),
        "iat": int(time.time()),
        "exp": int(time.time()) + args.days * 86400,
    }
    print(jwt.encode(claims, args.secret, algorithm="HS256"))


if __name__ == "__main__":
    main()
