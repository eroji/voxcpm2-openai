"""The 30 languages from the ``openbmb/VoxCPM2`` model card (SPEC v0.2.0 §8).

VoxCPM2 needs no language tag in the request — the text determines the language.
This list only drives Studio presets, filters and voice metadata.
"""

from __future__ import annotations

_LANGS: tuple[tuple[str, str], ...] = (
    ("ar", "Arabic"),
    ("my", "Burmese"),
    ("zh", "Chinese"),
    ("da", "Danish"),
    ("nl", "Dutch"),
    ("en", "English"),
    ("fi", "Finnish"),
    ("fr", "French"),
    ("de", "German"),
    ("el", "Greek"),
    ("he", "Hebrew"),
    ("hi", "Hindi"),
    ("id", "Indonesian"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("km", "Khmer"),
    ("ko", "Korean"),
    ("lo", "Lao"),
    ("ms", "Malay"),
    ("no", "Norwegian"),
    ("pl", "Polish"),
    ("pt", "Portuguese"),
    ("ru", "Russian"),
    ("es", "Spanish"),
    ("sw", "Swahili"),
    ("sv", "Swedish"),
    ("tl", "Tagalog"),
    ("th", "Thai"),
    ("tr", "Turkish"),
    ("vi", "Vietnamese"),
)

LANGUAGES: tuple[dict[str, str], ...] = tuple({"code": c, "name": n} for c, n in _LANGS)

LANGUAGE_NAMES: dict[str, str] = dict(_LANGS)


def language_name(code: str) -> str:
    """English name for an ISO code; falls back to the code itself."""
    return LANGUAGE_NAMES.get(code, code)
