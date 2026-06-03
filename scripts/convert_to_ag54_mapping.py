"""P8.A2 — Convert Syndigo ``mapping_config.yaml`` to an AG54 Mapping YAML.

Reads the in-tree ``config/mapping_config.yaml`` and emits an
ontology-builder-shaped Mapping YAML covering the subset AG54 can
faithfully replay:

  * ``simple_attributes``     → ``field_mappings`` (no transform)
  * ``transformed_attributes`` → ``field_mappings`` with
                                 ``transform: lookup:<table_name>``
                                 (uses the new P8.A1 engine transform)

Out-of-scope (Syndigo-only — stays in the legacy mapping pipeline):

  * ``numbered_groups`` / ``single_groups`` — assemble-time collapse of
    ``prefix01``, ``prefix02`` columns into ``list<structured>``
    elements. Requires engine extensions beyond what P8 ships.
  * ``derived_attributes`` — computed fields (concatenate / template
    expansion). Out of AG54's transform vocabulary.
  * ``defaults`` — channel-level literal injections. The delegator
    handles these post-engine (legacy code untouched).
  * ``images`` — domain-specific URL transforms.

Usage:
    python scripts/convert_to_ag54_mapping.py
       [--source-system mkl] [--out config/ag54_mapping.yaml]

The output mapping YAML uses ``canonical.thing.product`` as the
canonical reference — adjust via ``--canonical`` if your spine uses
a different ukey.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_syndigo_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Syndigo config not found at {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_simple_field_mappings(simple: dict[str, str]) -> list[dict]:
    """Each ``client_key: syndigo_key`` entry becomes one field mapping
    with no transform. Both keys land verbatim (AG54's snake_case
    normalisation happens at match time; we're authoring explicit
    overrides, so the canonical_path is exactly what we declare)."""
    out: list[dict] = []
    for client_key, syndigo_key in sorted(simple.items()):
        out.append({
            "canonical_path": syndigo_key,
            "source_path": client_key,
            "notes": (
                "Auto-converted by convert_to_ag54_mapping.py from "
                "mapping_config.yaml::simple_attributes"
            ),
        })
    return out


def _build_transformed_field_mappings(
    transformed: dict[str, dict],
) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """Each transformed_attributes entry becomes a field mapping with
    ``transform: lookup:<table_name>``. The lookup tables are returned
    separately — the engine accepts them via ``lookup_tables=`` on run().
    """
    field_mappings: list[dict] = []
    lookup_tables: dict[str, dict[str, str]] = {}
    for client_key, cfg in sorted(transformed.items()):
        syndigo_key = cfg.get("syndigo_key", client_key)
        lookup = cfg.get("lookup") or {}
        if not lookup:
            print(
                f"warn: transformed_attributes[{client_key!r}] has no lookup table; "
                "skipping",
                file=sys.stderr,
            )
            continue
        table_name = f"{client_key}_lookup"
        lookup_tables[table_name] = {str(k): str(v) for k, v in lookup.items()}
        field_mappings.append({
            "canonical_path": syndigo_key,
            "source_path": client_key,
            "transform": f"lookup:{table_name}",
            "notes": (
                "Auto-converted from mapping_config.yaml::transformed_attributes. "
                "Lookup table written alongside in the lookup_tables: block."
            ),
        })
    return field_mappings, lookup_tables


def convert(
    *,
    syndigo_config: dict,
    source_system: str = "mkl",
    source_object: str = "client_product",
    canonical_entity: str = "canonical.thing.product",
    canonical_version: str = "1.0.0",
) -> dict:
    """Produce the AG54 Mapping YAML body. Returns the parsed dict the
    caller can yaml-dump to disk."""
    simple = syndigo_config.get("simple_attributes") or {}
    transformed = syndigo_config.get("transformed_attributes") or {}

    simple_fm = _build_simple_field_mappings(simple)
    transformed_fm, lookup_tables = _build_transformed_field_mappings(transformed)
    all_field_mappings = simple_fm + transformed_fm

    # Surface what we deliberately didn't convert so the reviewer sees
    # the scope cut.
    out_of_scope: list[str] = []
    for key in ("numbered_groups", "single_groups", "derived_attributes",
                "defaults", "images"):
        if syndigo_config.get(key):
            out_of_scope.append(key)

    body: dict = {
        "ukey": f"map.global.{source_system}.{source_object}.thing_product",
        "version": "0.1.0",
        "scope": "global",
        "tenant_ukey": None,
        "canonical": {
            "entity": canonical_entity,
            "version": canonical_version,
        },
        "source": {
            "system": source_system,
            "object": source_object,
        },
        "authored_by": "convert_to_ag54_mapping.py",
        "description": (
            "Auto-converted subset of master-data-management/config/"
            "mapping_config.yaml for AG54 replay. Covers simple_attributes "
            "+ transformed_attributes (lookup). Out-of-scope sections "
            "remain handled by the legacy MDM pipeline: "
            f"{', '.join(out_of_scope) if out_of_scope else '(none)'}."
        ),
        "cardinality": "one_to_one",
        "direction": "forward_only",
        "field_mappings": all_field_mappings,
        "lookup_tables": lookup_tables,
    }
    return {"mapping": body}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config", type=Path,
        default=_REPO_ROOT / "config" / "mapping_config.yaml",
        help="Path to Syndigo mapping_config.yaml",
    )
    parser.add_argument(
        "--out", type=Path,
        default=_REPO_ROOT / "config" / "ag54_mapping.yaml",
        help="Output path for the AG54 Mapping YAML",
    )
    parser.add_argument("--source-system", default="mkl")
    parser.add_argument("--source-object", default="client_product")
    parser.add_argument("--canonical", default="canonical.thing.product")
    parser.add_argument("--canonical-version", default="1.0.0")
    args = parser.parse_args(argv)

    syndigo_config = _load_syndigo_config(args.config)
    body = convert(
        syndigo_config=syndigo_config,
        source_system=args.source_system,
        source_object=args.source_object,
        canonical_entity=args.canonical,
        canonical_version=args.canonical_version,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        yaml.safe_dump(body, sort_keys=False, default_flow_style=False,
                       allow_unicode=True),
        encoding="utf-8",
    )

    mapping = body["mapping"]
    print(f"  [ok] wrote {args.out.relative_to(_REPO_ROOT)}")
    print(f"        field_mappings:  {len(mapping['field_mappings'])}")
    print(f"        lookup_tables:   {len(mapping['lookup_tables'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
