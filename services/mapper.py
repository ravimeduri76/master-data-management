"""Core mapping orchestrator.

map_client_to_syndigo() is the single entry point.  It applies all rule
categories from mapping_config.yaml in order and returns the list of
Syndigo entities plus a detailed mapping report.

All functions are pure (no I/O, no global state mutation).
"""

import re
from datetime import datetime, timezone
from typing import Any

from services.attribute_mapper import build_values_attribute, apply_value_transform
from services.group_builder import build_numbered_groups, build_single_group
from services.image_builder import build_image_entities


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def _resolve_path(record: dict, path: str) -> Any:
    """Resolve a dot-notation path into a nested structure.

    Supports list index access as an integer segment, e.g.
    ``"sources.0.provider_code"`` → ``record["sources"][0]["provider_code"]``.
    """
    current: Any = record
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _apply_transform(value: Any, transform: str) -> Any:
    """Apply a named structural transform to a resolved value."""
    if transform == "date_only" and isinstance(value, str):
        # "2026-01-07T09:58:56.391Z" → "2026-01-07"
        return value[:10]
    return value


def _format_template(template: str, resolved: dict[str, str]) -> str:
    """Substitute ``{key}`` placeholders in a template string."""
    result = template
    for key, value in resolved.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


# ---------------------------------------------------------------------------
# Main mapping function
# ---------------------------------------------------------------------------

def map_client_to_syndigo(
    client_record: dict,
    config: dict,
) -> tuple[list[dict], dict]:
    """Map one client product record to a list of Syndigo entity objects.

    Returns (entities, mapping_report).

    entities[0] is always the ``requestitem`` entity.
    entities[1:] are ``image`` entities (one per client image field found).
    """
    metadata = config.get("metadata", {})
    source: str = metadata.get("default_source", "mkl")
    default_locale: str = metadata.get("default_locale", "de-DE")

    client_data: dict = client_record.get("data", {})
    all_client_data_keys: set[str] = set(client_data.keys())
    consumed_keys: set[str] = set()
    all_warnings: list[str] = []
    attributes: dict[str, Any] = {}

    # ── 1. Simple 1-to-1 attributes ──────────────────────────────────────────
    for client_key, syndigo_key in config.get("simple_attributes", {}).items():
        if client_key in client_data:
            attr, w = build_values_attribute(
                client_data[client_key], source, default_locale, detect_language=True
            )
            attributes[syndigo_key] = attr
            consumed_keys.add(client_key)
            all_warnings.extend(w)

    # ── 2. Transformed attributes ─────────────────────────────────────────────
    for client_key, transform_cfg in config.get("transformed_attributes", {}).items():
        if client_key in client_data:
            raw = client_data[client_key]
            lookup: dict = transform_cfg.get("lookup", {})
            transformed, matched = apply_value_transform(raw, lookup)
            if not matched:
                all_warnings.append(
                    f"transform_warning: no lookup match for "
                    f"'{client_key}' = '{raw}' — raw value passed through"
                )
            syndigo_key = transform_cfg.get("syndigo_key", client_key)
            attr, w = build_values_attribute(
                transformed, source, default_locale, detect_language=False
            )
            attributes[syndigo_key] = attr
            consumed_keys.add(client_key)
            all_warnings.extend(w)

    # ── 3. Numbered groups ────────────────────────────────────────────────────
    for group_cfg in config.get("numbered_groups", []):
        attr, w, ck = build_numbered_groups(
            client_data, group_cfg, source, default_locale
        )
        consumed_keys.update(ck)
        all_warnings.extend(w)
        if attr is not None:
            attributes[group_cfg["syndigo_parent"]] = attr

    # ── 4. Single groups ──────────────────────────────────────────────────────
    for group_cfg in config.get("single_groups", []):
        attr, w, ck = build_single_group(
            client_data, group_cfg, source, default_locale
        )
        consumed_keys.update(ck)
        all_warnings.extend(w)
        if attr is not None:
            attributes[group_cfg["syndigo_parent"]] = attr

    # ── 5. Derived attributes ─────────────────────────────────────────────────
    # First pass: resolve source_path entries.
    resolved_derived: dict[str, str] = {}

    for syndigo_key, derive_cfg in config.get("derived_attributes", {}).items():
        value: Any = None

        if "source_path" in derive_cfg:
            path: str = derive_cfg["source_path"]
            value = _resolve_path(client_record, path)
            if value is None:
                all_warnings.append(
                    f"derived_warning: '{syndigo_key}' source_path "
                    f"'{path}' resolved to nothing"
                )
            # Mark the corresponding client_data key as consumed.
            if path.startswith("data."):
                client_key = path[len("data."):]
                if "." not in client_key:
                    consumed_keys.add(client_key)

        elif derive_cfg.get("transform") == "processing_timestamp":
            value = datetime.now(timezone.utc).isoformat()

        # Apply extract / structural transforms.
        extract = derive_cfg.get("extract")
        if extract == "first_from_array" and isinstance(value, list):
            value = value[0] if value else None

        transform = derive_cfg.get("transform")
        if transform and transform != "processing_timestamp" and value is not None:
            value = _apply_transform(value, transform)

        if value is not None:
            resolved_derived[syndigo_key] = str(value)

    # Second pass: resolve template entries (depend on first-pass values).
    for syndigo_key, derive_cfg in config.get("derived_attributes", {}).items():
        if "template" in derive_cfg:
            value = _format_template(derive_cfg["template"], resolved_derived)
            resolved_derived[syndigo_key] = value

    # Build attribute objects for all derived values.
    for syndigo_key, value in resolved_derived.items():
        attr, w = build_values_attribute(
            value, source, default_locale, detect_language=False
        )
        attributes[syndigo_key] = attr
        all_warnings.extend(w)

    # ── 6. Channel/customer defaults ─────────────────────────────────────────
    for syndigo_key, value in config.get("defaults", {}).items():
        attr, w = build_values_attribute(
            value, source, default_locale, detect_language=False
        )
        attributes[syndigo_key] = attr
        all_warnings.extend(w)

    # ── 7. Image entities ─────────────────────────────────────────────────────
    image_entities, image_keys, image_warnings = build_image_entities(
        client_record, config, source, default_locale
    )
    consumed_keys.update(image_keys)
    all_warnings.extend(image_warnings)

    # ── 8. Assemble requestitem entity ────────────────────────────────────────
    sysmdmid = resolved_derived.get("sysmdmid", "")
    requestitem: dict = {
        "entity": {
            "properties": {"src": source},
            "id": sysmdmid,
            "type": "requestitem",
            "name": sysmdmid,
            "data": {
                "attributes": attributes,
                "contexts": [],
            },
        }
    }

    # ── 9. Build mapping report ───────────────────────────────────────────────
    unmapped_client_keys = sorted(all_client_data_keys - consumed_keys)
    system_fields_omitted: list = config.get("system_fields_omitted", [])

    lang_warnings = [w for w in all_warnings if "Language detection" in w or "locale" in w]
    transform_warnings = [w for w in all_warnings if "transform_warning" in w]
    derived_warnings = [w for w in all_warnings if "derived_warning" in w]
    image_report = [w for w in all_warnings if w.startswith("image")]

    report: dict = {
        "unmapped_client_keys": unmapped_client_keys,
        "system_fields_omitted": system_fields_omitted,
        "language_detection_warnings": lang_warnings,
        "transform_warnings": transform_warnings,
        "derived_warnings": derived_warnings,
        "image_warnings": image_report,
    }

    return [requestitem] + image_entities, report
