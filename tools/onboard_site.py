"""Onboard a client site to the cloud service: mint its credentials and build its dashboard.

One command per new client — the repeatable half of docs/cloud-hosting.md.

    python tools/onboard_site.py acme-lagos --spec sites/acme-lagos.yaml \
        --theme dashboard/themes/acme.yaml --domain iot.example.com

Writes deploy/cloud/dashboards/<site>/ (served at https://<domain>/s/<site>/) and prints the
credentials to hand over: the edge-gateway token, the operator link, and the viewer link.
Nothing is stored — re-run to reissue. Requires JWT_SECRET in the environment or --secret.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_dashboard import build  # noqa: E402
from mint_token import acl_for  # noqa: E402


def mint(role: str, site: str, secret: str, days: int, device: str | None = None) -> str:
    import time

    import jwt
    name = f"{site}.{device}" if device else f"{role}.{site}"
    return jwt.encode({"username": name, "acl": acl_for(role, site, device),
                       "iat": int(time.time()), "exp": int(time.time()) + days * 86400},
                      secret, algorithm="HS256")


def main() -> None:
    ap = argparse.ArgumentParser(description="Onboard a site to the Microscale cloud")
    ap.add_argument("site")
    ap.add_argument("--spec", type=Path, required=True, help="dashboard authoring YAML")
    ap.add_argument("--theme", type=Path, help="client branding YAML")
    ap.add_argument("--domain", required=True, help="the service domain, e.g. iot.example.com")
    ap.add_argument("--base-url", help="public base URL if it isn't https://<domain> "
                                       "(e.g. http://localhost:8880 during local evaluation)")
    ap.add_argument("--secret", default=os.environ.get("JWT_SECRET"),
                    help="broker JWT secret (default: $JWT_SECRET)")
    ap.add_argument("--edge-days", type=int, default=365)
    ap.add_argument("--dash-days", type=int, default=30)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "deploy" / "cloud" / "dashboards")
    args = ap.parse_args()
    if not args.secret:
        raise SystemExit("no JWT secret: pass --secret or set JWT_SECRET")

    out = args.out_dir / args.site
    build(args.spec, args.theme, out)          # validates against the frozen registry

    edge = mint("edge", args.site, args.secret, args.edge_days)
    dash = mint("dashboard", args.site, args.secret, args.dash_days)
    base = f"{args.base_url or f'https://{args.domain}'}/s/{args.site}/"

    print(f"\n=== {args.site} onboarded ===")
    print(f"dashboard built:  {out}")
    print(f"\nEDGE GATEWAY token (expires in {args.edge_days}d) — MQTT password on the site box:")
    print(f"  {edge}")
    print(f"\nOPERATOR link (expires in {args.dash_days}d):\n  {base}?token={dash}")
    print(f"\nVIEWER link (read-only):\n  {base}?role=viewer&token={dash}")
    print("\nThe token is remembered by the browser after first open — send the link once, "
          "over a private channel. Reissue by re-running this command.")


if __name__ == "__main__":
    main()
