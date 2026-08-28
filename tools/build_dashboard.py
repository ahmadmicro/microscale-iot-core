"""Compile + VALIDATE a dashboard authoring spec (YAML) → runtime dashboard.json,
with optional per-client theming and a themed PWA manifest.

    python tools/build_dashboard.py dashboard/site.example.yaml
    python tools/build_dashboard.py site.yaml --theme dashboard/themes/client.yaml --out-dir build/

Author-time validation (against the FROZEN v1.0 registry — errors fail the build):
  - every card type is known; bind/write topics have the canonical shape
    ms/<site>/<device>/<channel>/<metric>[/cmd] and match the spec's site
  - every metric exists in metrics.yaml; write targets must be writable metrics
  - chart cards name a registry metric and an explicit device

A theme file scopes branding per client: {name, brand, logo, mode} — merged into
spec.theme (+ title), and stamped into the emitted manifest.webmanifest.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from iotcore import schema  # noqa: E402

CARD_TYPES = {"metric", "gauge", "source-split", "switch", "chart"}


def check_topic(topic: str, site: str, *, cmd: bool = False) -> str | None:
    parts = topic.split("/")
    if len(parts) != (6 if cmd else 5) or parts[0] != "ms":
        return f"bad topic shape {topic!r}"
    if parts[1] != site:
        return f"topic site {parts[1]!r} != spec site {site!r}"
    if not schema.is_metric(parts[4]):
        return f"unknown metric {parts[4]!r}"
    if cmd:
        if parts[5] != "cmd":
            return f"write topic must end in /cmd: {topic!r}"
        if not schema.is_writable(parts[4]):
            return f"metric {parts[4]!r} is not writable in the registry"
    return None


def validate(spec: dict) -> list[str]:
    errors: list[str] = []
    site = spec.get("site")
    if not site:
        errors.append("spec is missing `site`")
    for page in spec.get("pages", []):
        for card in page.get("cards", []):
            where = f"{page.get('name', '?')} / {card.get('title', '?')}"
            ctype = card.get("type")
            if ctype not in CARD_TYPES:
                errors.append(f"{where}: unknown card type {ctype!r}")
                continue
            if ctype == "chart":
                metric = card.get("metric")
                if not metric or not schema.is_metric(metric):
                    errors.append(f"{where}: unknown metric {metric!r}")
                if not card.get("device"):
                    errors.append(f"{where}: chart cards need an explicit `device`")
                continue
            bind = card.get("bind")
            if not bind:
                errors.append(f"{where}: missing `bind`")
            elif site:
                err = check_topic(bind, site)
                if err:
                    errors.append(f"{where}: {err}")
            if card.get("write") and site:
                err = check_topic(card["write"], site, cmd=True)
                if err:
                    errors.append(f"{where}: {err}")
    return errors


def apply_theme(spec: dict, theme: dict) -> None:
    spec.setdefault("theme", {}).update(
        {k: v for k, v in theme.items() if k in ("brand", "logo", "mode", "accent")})
    if theme.get("name"):
        spec["title"] = theme["name"]


def build_manifest(spec: dict, out_dir: Path) -> None:
    manifest = json.loads((ROOT / "dashboard" / "manifest.webmanifest").read_text())
    manifest["name"] = spec.get("title", manifest["name"])
    manifest["short_name"] = spec.get("title", manifest["short_name"])[:24]
    brand = (spec.get("theme") or {}).get("brand")
    if brand:
        manifest["theme_color"] = brand
    (out_dir / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2))


def build(src: Path, theme_path: Path | None, out_dir: Path) -> Path:
    spec = yaml.safe_load(src.read_text())
    if theme_path is not None:
        apply_theme(spec, yaml.safe_load(theme_path.read_text()))
    errors = validate(spec)
    if errors:
        raise SystemExit("dashboard spec INVALID:\n  - " + "\n  - ".join(errors))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "dashboard.json"
    out.write_text(json.dumps(spec, indent=2) + "\n")
    build_manifest(spec, out_dir)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate + compile a dashboard spec")
    ap.add_argument("src", type=Path, help="authoring spec (YAML)")
    ap.add_argument("--theme", type=Path, help="per-client theme YAML")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "dashboard")
    args = ap.parse_args()
    out = build(args.src, args.theme, args.out_dir)
    print(f"wrote {out} (+ manifest.webmanifest)")


if __name__ == "__main__":
    main()
