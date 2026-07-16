"""Compile a dashboard authoring spec (YAML) → runtime dashboard.json.

The YAML is the human authoring format (see design/dashboard/example-site.dashboard.yaml);
the browser loads compiled JSON so the PWA needs no YAML parser. Trivial now; the place to add
validation against the metric registry and topic-namespace prefixing later.

    python tools/build_dashboard.py <in.yaml> [out.json]
"""
import json
import sys
from pathlib import Path

import yaml


def main() -> None:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("dashboard/dashboard.json")
    spec = yaml.safe_load(src.read_text())
    out.write_text(json.dumps(spec, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
