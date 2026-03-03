"""Group attribute builders.

Handles two Syndigo group patterns:

1. numbered_groups — client fields named prefix01/prefix02/… where the
   trailing number indexes into a repeating group entry (e.g. materials,
   noticeable features).

2. single_groups — several flat client keys that all belong to a single
   group entry (e.g. care labels, target group).
"""

import re
from typing import Any

from services.attribute_mapper import build_values_attribute


# ---------------------------------------------------------------------------
# Numbered groups
# ---------------------------------------------------------------------------

def _extract_numbered_fields(client_data: dict, client_pattern: str) -> dict[str, Any]:
    """Return {number_str: value} for all client keys matching a numbered pattern.

    client_pattern uses ``{n}`` as the placeholder, e.g. ``"materialfashionmaterial{n}"``.
    """
    regex = re.compile(client_pattern.replace("{n}", r"(\d+)") + r"$")
    matches: dict[str, Any] = {}
    for key, value in client_data.items():
        m = regex.fullmatch(key)
        if m:
            matches[m.group(1)] = value
    return dict(sorted(matches.items(), key=lambda x: int(x[0])))


def build_numbered_groups(
    client_data: dict,
    group_config: dict,
    source: str,
    default_locale: str,
) -> tuple[dict | None, list[str], set[str]]:
    """Build a ``{"group": [...]}`` attribute from numbered client fields.

    Each distinct number produces one group entry.  Sub-attributes sharing
    the same number are placed together in that entry.

    Returns (attribute_dict | None, warnings, consumed_client_keys).
    None is returned when no matching client keys are found.
    """
    syndigo_parent: str = group_config["syndigo_parent"]
    field_configs: list[dict] = group_config["fields"]
    group_defaults: dict = group_config.get("defaults", {})

    consumed_keys: set[str] = set()
    warnings: list[str] = []

    # Collect {syndigo_sub_key: {number: value}} for every field pattern.
    extracted: dict[str, dict[str, Any]] = {}
    all_numbers: set[str] = set()

    for field_cfg in field_configs:
        pattern = field_cfg["client_pattern"]
        sub_key = field_cfg["syndigo_sub_key"]
        found = _extract_numbered_fields(client_data, pattern)
        extracted[sub_key] = found
        all_numbers.update(found.keys())

        # Mark the original client keys as consumed.
        regex = re.compile(pattern.replace("{n}", r"(\d+)") + r"$")
        for key in client_data:
            if regex.fullmatch(key):
                consumed_keys.add(key)

    if not all_numbers:
        return None, warnings, consumed_keys

    groups: list[dict] = []
    for n in sorted(all_numbers, key=int):
        group_entry: dict[str, Any] = {"locale": default_locale, "source": source}

        for sub_key, by_number in extracted.items():
            value = by_number.get(n)
            if value is not None:
                attr, w = build_values_attribute(
                    value, source, default_locale, detect_language=True
                )
                group_entry[sub_key] = attr
                warnings.extend(w)

        # Inject config-level defaults for sub-attributes absent from client.
        for default_key, default_value in group_defaults.items():
            if default_key not in group_entry:
                attr, w = build_values_attribute(
                    default_value, source, default_locale, detect_language=False
                )
                group_entry[default_key] = attr
                warnings.extend(w)

        groups.append(group_entry)

    return {"group": groups}, warnings, consumed_keys


# ---------------------------------------------------------------------------
# Single groups
# ---------------------------------------------------------------------------

def build_single_group(
    client_data: dict,
    group_config: dict,
    source: str,
    default_locale: str,
) -> tuple[dict | None, list[str], set[str]]:
    """Build a single-entry ``{"group": [...]}`` attribute from flat client keys.

    All matched client keys become sub-attributes within one group entry.

    Returns (attribute_dict | None, warnings, consumed_client_keys).
    None is returned when none of the expected client keys are present.
    """
    field_map: dict[str, str] = group_config["fields"]  # client_key → syndigo_sub_key
    consumed_keys: set[str] = set()
    warnings: list[str] = []
    group_entry: dict[str, Any] = {"locale": default_locale, "source": source}
    any_found = False

    for client_key, syndigo_sub_key in field_map.items():
        if client_key in client_data:
            value = client_data[client_key]
            attr, w = build_values_attribute(
                value, source, default_locale, detect_language=True
            )
            group_entry[syndigo_sub_key] = attr
            consumed_keys.add(client_key)
            warnings.extend(w)
            any_found = True

    if not any_found:
        return None, warnings, consumed_keys

    return {"group": [group_entry]}, warnings, consumed_keys
