"""
Pure attribute normalisers.

Rule for every function here: return (value, issue). When the input
cannot be interpreted unambiguously the value is None and the issue code
explains why - nothing is guessed or imputed.
"""

import math
import re
import unicodedata
from datetime import date
from datetime import datetime

_WHITESPACE = re.compile(r"\s+")

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# "12.5", "-3", "1250"
_PLAIN_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")

# "1,25", "1.250,5", "12.500,00"  (German: '.' thousands, ',' decimal)
_GERMAN_DECIMAL = re.compile(r"^-?(\d{1,3}(\.\d{3})+|\d+),\d+$|^-?\d{1,3}(\.\d{3})+$")

# "12.500" - either 12.5 (decimal point) or 12500 (German thousands)
_AMBIGUOUS_DECIMAL = re.compile(r"^-?\d{1,3}\.\d{3}$")

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

_GERMAN_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")

_MOJIBAKE_MARKERS = ("Ã", "Â")


# =====================================================
# TEXT
# =====================================================


def clean_text(value: object) -> str | None:
    """
    Unicode NFC, collapse internal whitespace, trim. Empty -> None.
    """

    if value is None:
        return None

    text = unicodedata.normalize("NFC", str(value))
    text = _WHITESPACE.sub(" ", text).strip()

    return text or None


def repair_mojibake(text: str) -> tuple[str, bool]:
    """
    Undo UTF-8 bytes that were decoded as cp1252/Latin-1
    ("WeinstraÃŸe" -> "Weinstraße").

    Applied only when the text contains typical marker characters AND the
    byte round-trip decodes cleanly; otherwise the text is returned as-is.
    """

    if not any(marker in text for marker in _MOJIBAKE_MARKERS):
        return text, False

    for encoding in ("cp1252", "latin-1"):
        try:
            fixed = text.encode(encoding).decode("utf-8")
        except UnicodeEncodeError, UnicodeDecodeError:
            continue

        if fixed != text:
            return unicodedata.normalize("NFC", fixed), True

    return text, False


def clean_name(value: object) -> tuple[str | None, bool]:
    """
    clean_text + deterministic mojibake repair. Casing is preserved:
    title-casing German place names is lossy ("an der", "am Rhein").
    """

    text = clean_text(value)

    if text is None:
        return None, False

    return repair_mojibake(text)


def vocabulary_key(value: object) -> str:
    """
    Matching key for controlled vocabularies: casefold + umlaut
    transliteration, so "Ökokonto", "OEKOKONTO" and "oekokonto" match.
    """

    return (clean_text(value) or "").casefold().translate(_UMLAUTS)


def map_vocabulary(value: object, vocabulary: dict[str, str]) -> tuple[str | None, str | None]:
    """
    Returns (mapped_value, cleaned_source_value). Unmapped -> (None, source).
    """

    source = clean_text(value)

    if source is None:
        return None, None

    return vocabulary.get(vocabulary_key(source)), source


# =====================================================
# NUMBERS
# =====================================================


def parse_decimal(value: object) -> tuple[float | None, str | None]:
    """
    Parse numbers from typed values or German/plain strings.

    "1,25"     -> 1.25          (German decimal comma)
    "1.250,5"  -> 1250.5
    "12.5"     -> 12.5
    "12.500"   -> None, "ambiguous_number"   (12.5 or 12500?)
    """

    if value is None:
        return None, None

    if isinstance(value, bool):
        return None, "invalid_number"

    if isinstance(value, int | float):
        number = float(value)
        return (number, None) if math.isfinite(number) else (None, "invalid_number")

    text = clean_text(value)

    if text is None:
        return None, None

    text = text.replace(" ", "")

    if _AMBIGUOUS_DECIMAL.match(text):
        return None, "ambiguous_number"

    if _PLAIN_DECIMAL.match(text):
        return float(text), None

    if _GERMAN_DECIMAL.match(text):
        return float(text.replace(".", "").replace(",", ".")), None

    return None, "invalid_number"


# =====================================================
# KEYS
# =====================================================


def normalize_ags(value: object) -> tuple[str | None, str | None]:
    """
    Amtlicher Gemeindeschlüssel: fixed-width 8 digits.

    A 7-digit value is the same key with its leading zero lost to a numeric
    column type (all RLP keys start with "07"); it is restored and flagged.
    """

    text = clean_text(value)

    if text is None:
        return None, None

    text = text.replace(" ", "")

    if not text.isdigit():
        return None, "invalid_municipality_key"

    if len(text) == 8:
        return text, None

    if len(text) == 7:
        return f"0{text}", "municipality_key_zero_padded"

    return None, "invalid_municipality_key"


def normalize_numeric_key(value: object) -> tuple[str | None, str | None]:
    """
    Generic numeric key (e.g. Gemarkungsschlüssel): kept verbatim if digits.
    """

    text = clean_text(value)

    if text is None:
        return None, None

    text = text.replace(" ", "")

    return (text, None) if text.isdigit() else (None, "invalid_cadastral_key")


# =====================================================
# DATES
# =====================================================


def parse_date(value: object) -> tuple[date | None, str | None]:
    """
    ISO-8601 (YYYY-MM-DD[...]) or German DD.MM.YYYY. Anything else is
    rejected rather than guessed (e.g. "03/04/2021" is ambiguous).
    """

    if value is None:
        return None, None

    if isinstance(value, datetime):
        return value.date(), None

    if isinstance(value, date):
        return value, None

    text = clean_text(value)

    if text is None:
        return None, None

    try:
        if match := _ISO_DATE.match(text):
            year, month, day = match.groups()
            return date(int(year), int(month), int(day)), None

        if match := _GERMAN_DATE.match(text):
            day, month, year = match.groups()
            return date(int(year), int(month), int(day)), None

    except ValueError:
        return None, "invalid_date"

    return None, "invalid_date"
