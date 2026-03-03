"""Low-level attribute building functions.

Pure functions that construct Syndigo attribute value objects from raw
client values.  No I/O, no side effects.
"""

from typing import Any

from services.language_detector import detect_locale, group_values_by_locale


def make_value_entry(value: Any, source: str, locale: str) -> dict:
    """Build a single Syndigo value object."""
    return {"value": value, "source": source, "src": source, "locale": locale}


def build_values_attribute(
    raw_value: Any,
    source: str,
    default_locale: str,
    detect_language: bool = True,
) -> tuple[dict, list[str]]:
    """Build a Syndigo ``{"values": [...]}`` attribute from a raw client value.

    Handles both scalars and lists.  When detect_language is True each string
    value is inspected individually; values in different languages appear as
    separate entries with their own locale.

    Returns (attribute_dict, warnings).
    """
    warnings: list[str] = []
    items: list = raw_value if isinstance(raw_value, list) else [raw_value]
    items = [i for i in items if i is not None]

    if not items:
        return {"values": []}, warnings

    values: list[dict] = []

    if detect_language:
        # Group by detected locale so same-language values sit together.
        grouped = group_values_by_locale(items, fallback_locale=default_locale)
        for locale, locale_items in grouped.items():
            for item in locale_items:
                if isinstance(item, str) and len(item.strip()) < 20:
                    detected_locale, reliable = detect_locale(item, fallback=default_locale)
                    if not reliable:
                        warnings.append(
                            f"Language detection unreliable for short value "
                            f"'{item[:40]}' — using default locale '{default_locale}'"
                        )
                        locale = default_locale
                values.append(make_value_entry(item, source, locale))
    else:
        for item in items:
            values.append(make_value_entry(item, source, default_locale))

    return {"values": values}, warnings


def apply_value_transform(value: Any, lookup: dict[str, str]) -> tuple[Any, bool]:
    """Apply a lookup table transform to a value.

    Returns (transformed_value, was_matched).
    Tries exact match first, then case-insensitive match.
    """
    if not isinstance(value, str):
        return value, False
    if value in lookup:
        return lookup[value], True
    lower = value.lower()
    for k, v in lookup.items():
        if k.lower() == lower:
            return v, True
    return value, False
