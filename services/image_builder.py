"""Image entity builder.

Reads client image fields (image01, image02, …) and produces separate
Syndigo entities of type "image".

The MD5 of the source URL is used as the entity ID and damchecksum,
matching the pattern observed in Syndigo-generated output.
"""

import hashlib
import re
from typing import Any


def _md5_of_url(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def _scalar_attr(value: Any, source: str, locale: str) -> dict:
    """Build a minimal Syndigo values attribute for a single scalar."""
    return {
        "values": [{"value": value, "source": source, "src": source, "locale": locale}]
    }


def _extract_image_fields(
    client_data: dict, pattern: str
) -> dict[str, dict]:
    """Return {number_str: image_dict} for all image fields matching the pattern."""
    regex = re.compile(pattern.replace("{n}", r"(\d+)") + r"$")
    found: dict[str, dict] = {}
    for key, value in client_data.items():
        m = regex.fullmatch(key)
        if m and isinstance(value, dict):
            found[m.group(1)] = value
    return dict(sorted(found.items(), key=lambda x: int(x[0])))


def build_image_entities(
    client_record: dict,
    config: dict,
    source: str,
    default_locale: str,
) -> tuple[list[dict], set[str], list[str]]:
    """Build a list of Syndigo image entities from client image fields.

    Returns (image_entities, consumed_client_data_keys, warnings).
    """
    img_cfg = config.get("images", {})
    pattern = img_cfg.get("client_pattern", "image{n}")
    main_number = img_cfg.get("main_image_number", "01")
    media_main = img_cfg.get("media_type_main", "MainImage")
    media_additional = img_cfg.get("media_type_additional", "AdditionalImage")
    url_field = img_cfg.get("url_field", "source")
    defaults = img_cfg.get("defaults", {})

    client_data: dict = client_record.get("data", {})
    images = _extract_image_fields(client_data, pattern)

    consumed_keys: set[str] = set(f"image{n}" for n in images)
    warnings: list[str] = []
    entities: list[dict] = []

    # Resolve linking fields from the client record.
    gtin_raw = client_data.get("gtin", [])
    gtin = (gtin_raw[0] if isinstance(gtin_raw, list) and gtin_raw
            else str(gtin_raw) if gtin_raw else "")
    productnr = client_data.get("productnr", "")

    for n, img in images.items():
        url = img.get(url_field, "")
        if not url:
            warnings.append(
                f"image{n}: field '{url_field}' is missing or empty — skipped"
            )
            continue

        # Report original_url as unmapped (it is not used in Syndigo output).
        if "original_url" in img:
            warnings.append(
                f"image{n}.original_url: no Syndigo target — not included in output"
            )

        checksum = _md5_of_url(url)
        media_type = media_main if n == main_number else media_additional

        def v(val: Any) -> dict:  # noqa: E306
            return _scalar_attr(val, source, default_locale)

        attributes: dict[str, Any] = {
            "dammediaid": v(n),
            "dammediatype": v(media_type),
            "productnr": v(productnr),
            "damgtin": v(gtin),
            "damsourceurl": v(url),
            "rsInternalDigitalAssetOriginalAssetUrl": v(url),
            "rsInternalDigitalAssetPreviewAssetUrl": v(url),
            "damchannel": v(defaults.get("damchannel", "")),
            "damresolutionkey": v(defaults.get("damresolutionkey", "")),
            "damcontenttype": v(defaults.get("damcontenttype", "image/jpeg")),
            "damchecksum": v(checksum),
            "sysmdmid": v(checksum),
            "sysmdmname": v(url),
            "thingDataSource": v(source),
        }

        entities.append({
            "entity": {
                "properties": {"src": source},
                "id": checksum,
                "type": "image",
                "name": checksum,
                "data": {
                    "attributes": attributes,
                    "contexts": [],
                },
            }
        })

    return entities, consumed_keys, warnings
