# Qualified Device Catalog

The compounding Layer-2 asset: one record per device *model* that has passed the open-data gate and
been benchmarked. **Records, not code** (the code is in `adapters/`). Every engagement should deposit
at least one entry here — that discipline is what bends the delivery-cost curve.

Each `<model>.yaml` should capture:

- `model`, `class` (Cat1/Cat2/Cat3 or industrial), `interface` (mqtt / rest / modbus)
- **gate**: open/local data access — pass/fail + how (the non-negotiable filter)
- **benchmark**: measured accuracy vs a reference meter (not vendor spec), refresh, control latency
- **adapter**: the `adapters/…` id that integrates it (or the bridge family + profile)
- **procurement**: supplier, MOQ, indicative landed unit cost, lead time, certification path (SONCAP)
- **notes**: quirks, firmware/flashing path (e.g. Tasmota), local-key extraction, gotchas

Populated from the bench-eval step of each engagement (Phase 1 in `../ROADMAP.md`).
