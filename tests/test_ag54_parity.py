"""P8.A3 — AG54 delegation parity on the simple+transformed subset.

For a synthetic client record covering ``simple_attributes`` +
``transformed_attributes``, the AG54-delegated path produces the same
Syndigo attributes as the legacy ``map_client_to_syndigo``.

Out-of-scope sections (numbered_groups / single_groups / derived /
defaults / images) are NOT exercised — they stay on the legacy path.
The parity assertion is per-attribute equality on the subset AG54
covers, not whole-response equality.

Skip-if-not-importable: ``gaxl_mapper_agent`` isn't pip-installable
from a public registry yet (RFC P9). When the GrowthAXL repos are
bootstrapped + installed, this test starts running.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

ag54 = pytest.importorskip("gaxl_mapper_agent")
_engine = pytest.importorskip("gaxl_mapping_engine")


@pytest.fixture(scope="module")
def syndigo_config() -> dict:
    """The in-tree mapping_config.yaml (the canonical Syndigo config)."""
    with (_REPO_ROOT / "config" / "mapping_config.yaml").open(
        encoding="utf-8",
    ) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def ag54_mapping() -> dict:
    """The auto-converted AG54 mapping YAML."""
    from services.ag54_delegator import load_ag54_mapping  # noqa: PLC0415
    return load_ag54_mapping(_REPO_ROOT / "config" / "ag54_mapping.yaml")


@pytest.fixture
def canonical_root(tmp_path: Path, ag54_mapping: dict) -> Path:
    """Synthesise canonical.thing.product with every field AG54's
    converted mapping needs. Real production uses the ontology-builder
    canonical_models tree; here we inline so the test is hermetic."""
    root = tmp_path / "_canonical_models"
    (root / "thing").mkdir(parents=True)

    fields = []
    seen: set[str] = set()
    for fm in ag54_mapping.get("field_mappings", []):
        name = fm["canonical_path"]
        if name in seen:
            continue
        seen.add(name)
        fields.append({
            "name": name,
            "type": "string",
            "required": False,
            "sensitivity": "low",
        })
    (root / "thing" / "product.yaml").write_text(yaml.safe_dump({
        "canonical_model": {
            "ukey": "canonical.thing.product",
            "version": "1.0.0",
            "kind": "thing",
            "fields": fields,
        },
    }), encoding="utf-8")
    return root


@pytest.fixture
def synthetic_client_record() -> dict:
    """A client record exercising simple + transformed attributes.

    Uses values that survive AG54's tier-1 (exact match on
    canonical_path) deterministic routing. The transformed-attribute
    values exercise the lookup table path."""
    return {
        "data": {
            # simple_attributes (subset)
            "genericbrand": "Acme",
            "genericcolor": "blue",
            "genericstyle": "regular",
            "fastenerposition": "front",
            # transformed_attributes
            "steuerklassifikationartikel": "1",   # → "Voll (19%)"
            "nontextilepartsofanimalorigin": "nein",   # → "False"
        }
    }


# ── Smoke tests ───────────────────────────────────────────────────────────


def test_delegator_available_when_ag54_imported():
    from services.ag54_delegator import available
    assert available() is True


def test_use_ag54_enabled_via_request_flag():
    from services.ag54_delegator import use_ag54_enabled
    assert use_ag54_enabled(True) is True
    assert use_ag54_enabled(False) is False
    assert use_ag54_enabled(None) is False   # default off when no env


def test_use_ag54_enabled_via_env(monkeypatch):
    from services.ag54_delegator import use_ag54_enabled
    monkeypatch.setenv("MDM_USE_AG54", "1")
    assert use_ag54_enabled(None) is True
    monkeypatch.setenv("MDM_USE_AG54", "no")
    assert use_ag54_enabled(None) is False


# ── Parity (headline P8 exit gate test) ───────────────────────────────────


def test_simple_attributes_parity(
    syndigo_config: dict,
    ag54_mapping: dict,
    canonical_root: Path,
    synthetic_client_record: dict,
):
    """Per-attribute parity on simple_attributes between legacy and
    AG54-delegated paths."""
    from services.ag54_delegator import delegate  # noqa: PLC0415
    from services.attribute_mapper import build_values_attribute  # noqa: PLC0415

    metadata = syndigo_config.get("metadata") or {}
    source = metadata.get("default_source", "mkl")
    default_locale = metadata.get("default_locale", "de-DE")
    client_data = synthetic_client_record["data"]

    # Build the legacy attributes for the simple_attribute subset.
    legacy_attrs: dict = {}
    for client_key, syndigo_key in syndigo_config["simple_attributes"].items():
        if client_key in client_data:
            attr, _ = build_values_attribute(
                client_data[client_key], source, default_locale,
                detect_language=True,
            )
            legacy_attrs[syndigo_key] = attr

    # AG54-delegated path
    ag54_attrs = delegate(
        client_record=synthetic_client_record,
        syndigo_config=syndigo_config,
        ag54_mapping=ag54_mapping,
        canonical_root=canonical_root,
        correlation_id="parity-simple-001",
    )

    # Every simple attribute the legacy path produced is present in
    # AG54's output with the SAME shape.
    for key, legacy_attr in legacy_attrs.items():
        assert key in ag54_attrs, f"AG54 missing attribute {key!r}"
        assert ag54_attrs[key] == legacy_attr, (
            f"Attribute {key!r} diverged.\n"
            f"  legacy: {legacy_attr}\n"
            f"  ag54:   {ag54_attrs[key]}"
        )


def test_transformed_attributes_apply_lookup(
    syndigo_config: dict,
    ag54_mapping: dict,
    canonical_root: Path,
    synthetic_client_record: dict,
):
    """Transformed attributes route through the new ``lookup:<table>``
    engine transform. ``steuerklassifikationartikel`` ``"1"`` becomes
    ``"Voll (19%)"`` via the lookup table converted from mapping_config."""
    from services.ag54_delegator import delegate  # noqa: PLC0415

    ag54_attrs = delegate(
        client_record=synthetic_client_record,
        syndigo_config=syndigo_config,
        ag54_mapping=ag54_mapping,
        canonical_root=canonical_root,
        correlation_id="parity-transform-001",
    )

    # Tax-class lookup applied
    tax_attr = ag54_attrs.get("steuerklassifikationartikel")
    assert tax_attr is not None
    assert tax_attr["values"], "tax-class attribute should have at least one value"
    assert tax_attr["values"][0]["value"] == "Voll (19%)"

    # Boolean coercion lookup applied
    bool_attr = ag54_attrs.get("nontextilepartsofanimalorigin")
    assert bool_attr is not None
    assert bool_attr["values"][0]["value"] == "False"


def test_transformed_attributes_match_legacy(
    syndigo_config: dict,
    ag54_mapping: dict,
    canonical_root: Path,
    synthetic_client_record: dict,
):
    """Per-attribute equality on the transformed_attributes subset."""
    from services.ag54_delegator import delegate  # noqa: PLC0415
    from services.attribute_mapper import (  # noqa: PLC0415
        apply_value_transform,
        build_values_attribute,
    )

    metadata = syndigo_config.get("metadata") or {}
    source = metadata.get("default_source", "mkl")
    default_locale = metadata.get("default_locale", "de-DE")
    client_data = synthetic_client_record["data"]

    legacy_attrs: dict = {}
    for client_key, cfg in syndigo_config["transformed_attributes"].items():
        if client_key not in client_data:
            continue
        syndigo_key = cfg["syndigo_key"]
        lookup = cfg.get("lookup") or {}
        raw_value = client_data[client_key]
        transformed_value, _matched = apply_value_transform(raw_value, lookup)
        attr, _ = build_values_attribute(
            transformed_value, source, default_locale, detect_language=True,
        )
        legacy_attrs[syndigo_key] = attr

    ag54_attrs = delegate(
        client_record=synthetic_client_record,
        syndigo_config=syndigo_config,
        ag54_mapping=ag54_mapping,
        canonical_root=canonical_root,
        correlation_id="parity-transform-match-001",
    )

    for key, legacy_attr in legacy_attrs.items():
        assert key in ag54_attrs, f"AG54 missing transformed attribute {key!r}"
        assert ag54_attrs[key] == legacy_attr, (
            f"Transformed attribute {key!r} diverged.\n"
            f"  legacy: {legacy_attr}\n"
            f"  ag54:   {ag54_attrs[key]}"
        )


# ── Out-of-scope sections are NOT touched by the delegator ────────────────


def test_delegator_ignores_out_of_scope_sections(
    syndigo_config: dict,
    ag54_mapping: dict,
    canonical_root: Path,
):
    """If a client record only has numbered_group / derived /
    defaults / images-relevant fields, the delegator returns an empty
    dict — the legacy path stays responsible for those."""
    from services.ag54_delegator import delegate  # noqa: PLC0415
    record = {"data": {"unknown_field_only_in_groups": "x"}}
    ag54_attrs = delegate(
        client_record=record,
        syndigo_config=syndigo_config,
        ag54_mapping=ag54_mapping,
        canonical_root=canonical_root,
        correlation_id="oos-001",
    )
    assert ag54_attrs == {}
