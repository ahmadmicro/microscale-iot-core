"""Generate the firmware metric registry from metrics.yaml.

MicroPython can't parse YAML on-device, so the canonical registry compiles to a frozen
dict the firmware imports. Re-run after any registry change (a schema MINOR bump);
tests/test_firmware.py fails if the committed file drifts from metrics.yaml.

    python tools/gen_fw_registry.py            # rewrites firmware/micropython/iotcore_registry.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "firmware" / "micropython" / "iotcore_registry.py"


def generate() -> str:
    doc = yaml.safe_load((ROOT / "schema" / "canonical" / "metrics.yaml").read_text())
    lines = [
        "# GENERATED from schema/canonical/metrics.yaml v%s by tools/gen_fw_registry.py" % doc["version"],
        "# Do not edit — re-run the generator after a registry change.",
        'VERSION = "%s"' % doc["version"],
        "# metric: (canonical unit, writable)",
        "METRICS = {",
    ]
    for metric, m in doc["metrics"].items():
        lines.append('    "%s": ("%s", %s),' % (metric, m["unit"], bool(m.get("writable", False))))
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    text = generate()
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        if OUT.read_text() != text:
            raise SystemExit(f"{OUT} is out of date — run: python tools/gen_fw_registry.py")
        print("registry in sync")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
