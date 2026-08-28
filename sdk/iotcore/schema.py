"""
Canonical schema access — the single source of truth for units/kinds and envelope construction.

Everything an adapter publishes goes through `build_envelope`, so the canonical unit is enforced
in one place. Loads the metric registry from schema/canonical/metrics.yaml.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "schema" / "canonical" / "metrics.yaml"

_VALID_QUALITY = {"good", "stale", "estimated", "bad", "simulated"}
_VALID_SOURCE = {"grid", "generator", "solar", "battery", "unknown"}
_VALID_MEDIUM = {"water", "gas", "steam", "air"}


class SchemaError(ValueError):
    pass


def _load_registry() -> dict[str, Any]:
    with _REGISTRY_PATH.open() as fh:
        doc = yaml.safe_load(fh)
    return dict(doc["metrics"])


_METRICS: dict[str, Any] = _load_registry()
SCHEMA_VERSION: str = "1.0"


def is_metric(metric: str) -> bool:
    return metric in _METRICS


def canonical_unit(metric: str) -> str:
    _require(metric)
    return str(_METRICS[metric]["unit"])


def kind(metric: str) -> str:
    _require(metric)
    return str(_METRICS[metric]["kind"])


def is_writable(metric: str) -> bool:
    _require(metric)
    return bool(_METRICS[metric].get("writable", False))


def _require(metric: str) -> None:
    if metric not in _METRICS:
        raise SchemaError(f"unknown canonical metric: {metric!r} (not in metrics.yaml)")


def build_envelope(
    metric: str,
    value: Any,
    *,
    t_ms: int | None = None,
    quality: str = "good",
    source: str | None = None,
    medium: str | None = None,
    seq: int | None = None,
    include_unit: bool = True,
) -> dict[str, Any]:
    """Build a reading-envelope dict (validated against reading.envelope.schema.json shape)."""
    _require(metric)
    if quality not in _VALID_QUALITY:
        raise SchemaError(f"invalid quality {quality!r}")
    if source is not None and source not in _VALID_SOURCE:
        raise SchemaError(f"invalid source {source!r}")
    if medium is not None and medium not in _VALID_MEDIUM:
        raise SchemaError(f"invalid medium {medium!r}")

    env: dict[str, Any] = {
        "t": t_ms if t_ms is not None else int(time.time() * 1000),
        "v": value,
    }
    if include_unit:
        env["u"] = canonical_unit(metric)
    if quality != "good":
        env["q"] = quality
    if source is not None:
        env["src"] = source
    if medium is not None:
        env["medium"] = medium
    if seq is not None:
        env["seq"] = seq
    return env
