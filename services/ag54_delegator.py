"""P8.A3 — Delegate MDM mapping to AG54 SchemaMapper.

Optional ``use_ag54: true`` on a /map request (or env ``MDM_USE_AG54=1``)
routes the simple+transformed-attribute subset through AG54's engine.
Legacy MDM services still own:

  * The Syndigo ``{"values": [...]}`` envelope (language detection,
    locale grouping).
  * Numbered/single groups, derived attributes, defaults, images.

The delegator is a thin glue layer: AG54 does routing+lookup transforms;
the legacy ``build_values_attribute`` wraps each emitted scalar in the
Syndigo attribute shape.

Cross-repo dep honesty: ``gaxl_mapper_agent`` + ``gaxl_mapping_engine``
are not yet pip-installable from a public registry (RFC P9 evaluates
foundation promotion). :func:`available` reports importability so tests
can ``skipif`` gracefully.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:                                                       # pragma: no cover
    from gaxl_mapping_engine import (
        CanonicalRef,
        MapperIngress,
        MappingEngine,
        MappingRule,
        PrivacyMode,
        SourceRef,
    )
    from gaxl_mapping_engine.canonical import load_canonical_dir

    _IMPORT_ERROR: Exception | None = None
except ImportError as exc:                                  # pragma: no cover
    MappingEngine = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


def available() -> bool:
    """True iff AG54 (mapping engine) is importable."""
    return MappingEngine is not None


def use_ag54_enabled(request_flag: bool | None) -> bool:
    """Activation: per-request flag wins over env override.
    Env ``MDM_USE_AG54=1`` activates by default."""
    if request_flag is True:
        return True
    if request_flag is False:
        return False
    return os.environ.get("MDM_USE_AG54", "").strip().lower() in (
        "1", "true", "yes",
    )


def load_ag54_mapping(path: Path) -> dict[str, Any]:
    """Read the auto-converted AG54 mapping YAML from disk. Returns the
    parsed body (the inner ``mapping`` dict)."""
    if not path.exists():
        raise FileNotFoundError(
            f"AG54 mapping not found at {path}; run "
            "scripts/convert_to_ag54_mapping.py to regenerate."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw.get("mapping", raw)


def delegate(
    *,
    client_record: dict[str, Any],
    syndigo_config: dict[str, Any],
    ag54_mapping: dict[str, Any],
    canonical_root: Path,
    correlation_id: str,
) -> dict[str, dict]:
    """Run AG54 over one client record and return the attributes dict
    keyed by syndigo_key. Each value is the Syndigo ``{"values": [...]}``
    envelope (legacy ``build_values_attribute`` is what produces it).

    The delegator is intentionally SCOPED — it only emits attributes
    AG54 actually mapped. The caller (``mapper.map_client_to_syndigo``
    under the use_ag54 branch) is still responsible for merging
    numbered_groups / single_groups / derived / defaults / images from
    the legacy pipeline.
    """
    if not available():
        raise RuntimeError(
            "gaxl_mapping_engine not importable; cannot delegate. "
            f"ImportError: {_IMPORT_ERROR}"
        )

    # Legacy import is delayed so we don't have to load the language
    # detector unless the delegator actually runs.
    from services.attribute_mapper import build_values_attribute  # noqa: PLC0415

    metadata = syndigo_config.get("metadata") or {}
    source = metadata.get("default_source", "mkl")
    default_locale = metadata.get("default_locale", "de-DE")

    client_data: dict[str, Any] = client_record.get("data") or {}
    if not client_data:
        return {}

    # Build engine override rules from the converted mapping YAML.
    overrides: list = []
    for fm in ag54_mapping.get("field_mappings", []):
        overrides.append(MappingRule(
            canonical_path=fm["canonical_path"],
            source_path=fm["source_path"],
            transform=fm.get("transform"),
        ))
    lookup_tables = ag54_mapping.get("lookup_tables") or {}

    # P8 calls MappingEngine directly — the agent's MapperAgent.map()
    # doesn't yet forward lookup_tables (would land in a separate agent
    # PR). Direct invocation also skips the agent's overlay/proposer
    # paths which aren't needed here. Tenant overlays + P6 fallback
    # remain agent-level concerns.
    canonical_models = load_canonical_dir(canonical_root)
    canon_ref = ag54_mapping["canonical"]
    canonical = canonical_models.get(canon_ref["entity"])
    if canonical is None:
        # Missing canonical — same shape as P6 fallback: return empty
        # so the legacy path is responsible for the full output.
        return {}
    ingress = MapperIngress(
        source=SourceRef(
            system=ag54_mapping.get("source", {}).get("system", "mkl"),
            object=ag54_mapping.get("source", {}).get("object", "client_product"),
        ),
        source_format="json",
        source_records=[client_data],
        canonical_ref=CanonicalRef(
            entity=canon_ref["entity"],
            version=canon_ref["version"],
        ),
        privacy_mode=PrivacyMode.INTERNAL,
        deterministic_only=True,
        correlation_id=correlation_id,
    )
    engine = MappingEngine()
    egress = engine.run(
        ingress, canonical, overrides=overrides, lookup_tables=lookup_tables,
    )

    # If AG54 emitted no record (canonical missing → P6 fallback,
    # or empty source), bail out — caller's legacy path will handle.
    if not egress.mapped_records:
        return {}

    record = egress.mapped_records[0]
    attributes: dict[str, dict] = {}
    for syndigo_key, raw_value in record.items():
        attribute, _warnings = build_values_attribute(
            raw_value, source, default_locale, detect_language=True,
        )
        attributes[syndigo_key] = attribute
    return attributes


__all__ = ["available", "delegate", "load_ag54_mapping", "use_ag54_enabled"]
