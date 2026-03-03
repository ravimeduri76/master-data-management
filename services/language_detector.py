"""Language detection utilities.

Detects the BCP-47 locale of a string value using langdetect.
Short strings below MIN_DETECTION_LENGTH are flagged as unreliable
to avoid false positives on product codes, single words, etc.
"""

from langdetect import detect, LangDetectException

# Strings shorter than this are too ambiguous for reliable detection.
MIN_DETECTION_LENGTH = 20

# ISO 639-1 → BCP-47 locale map.  Extend here for new languages.
_LANG_TO_LOCALE: dict[str, str] = {
    "de": "de-DE",
    "en": "en-US",
    "fr": "fr-FR",
    "es": "es-ES",
    "it": "it-IT",
    "nl": "nl-NL",
    "pt": "pt-PT",
    "pl": "pl-PL",
    "cs": "cs-CZ",
    "hu": "hu-HU",
    "ro": "ro-RO",
    "sk": "sk-SK",
    "da": "da-DK",
    "sv": "sv-SE",
    "no": "nb-NO",
    "fi": "fi-FI",
}


def detect_locale(text: str, fallback: str = "und") -> tuple[str, bool]:
    """Return (bcp47_locale, is_reliable) for the given text.

    is_reliable is False when the string is too short for confident
    detection; in that case the fallback locale is returned.
    """
    if not isinstance(text, str) or len(text.strip()) < MIN_DETECTION_LENGTH:
        return fallback, False
    try:
        lang_code = detect(text)
        locale = _LANG_TO_LOCALE.get(lang_code, f"{lang_code}-{lang_code.upper()}")
        return locale, True
    except LangDetectException:
        return fallback, False


def group_values_by_locale(
    items: list,
    fallback_locale: str,
) -> dict[str, list]:
    """Group a list of string values by their detected locale.

    Returns { locale: [value, ...] }.
    Values whose locale cannot be reliably detected are placed under
    fallback_locale.
    """
    grouped: dict[str, list] = {}
    for item in items:
        if isinstance(item, str):
            locale, _ = detect_locale(item, fallback=fallback_locale)
        else:
            locale = fallback_locale
        grouped.setdefault(locale, []).append(item)
    return grouped
