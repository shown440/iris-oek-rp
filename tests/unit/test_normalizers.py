from datetime import date

import pytest

from app.transformers.normalizers import clean_name
from app.transformers.normalizers import clean_text
from app.transformers.normalizers import map_vocabulary
from app.transformers.normalizers import normalize_ags
from app.transformers.normalizers import normalize_numeric_key
from app.transformers.normalizers import parse_date
from app.transformers.normalizers import parse_decimal
from app.transformers.normalizers import vocabulary_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("   ", None),
        ("  Bad   Kreuznach ", "Bad Kreuznach"),
        ("Zweibrücken", "Zweibrücken"),  # decomposed umlaut -> NFC
        (7315000, "7315000"),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_mojibake_repaired_only_when_detectable():
    assert clean_name("Neustadt an der WeinstraÃŸe") == ("Neustadt an der Weinstraße", True)
    assert clean_name("Neustadt an der Weinstraße") == ("Neustadt an der Weinstraße", False)
    assert clean_name("Ludwigshafen am Rhein") == ("Ludwigshafen am Rhein", False)


def test_casing_is_never_changed():
    assert clean_name("NEUSTADT AN DER WEINSTRASSE")[0] == "NEUSTADT AN DER WEINSTRASSE"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ökokonto", "oekokonto"),
        ("OEKOKONTO", "oekokonto"),
        ("Ersatzzahlungsmaßnahme", "ersatzzahlungsmassnahme"),
        ("  in   Umsetzung ", "in umsetzung"),
    ],
)
def test_vocabulary_key(raw, expected):
    assert vocabulary_key(raw) == expected


def test_map_vocabulary_keeps_source_when_unmapped():
    vocabulary = {"umgesetzt": "implemented"}
    assert map_vocabulary(" Umgesetzt ", vocabulary) == ("implemented", "Umgesetzt")
    assert map_vocabulary("abgeschlossen", vocabulary) == (None, "abgeschlossen")
    assert map_vocabulary(None, vocabulary) == (None, None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, (None, None)),
        ("", (None, None)),
        (1.25, (1.25, None)),
        (3, (3.0, None)),
        ("1,25", (1.25, None)),
        ("1.250,5", (1250.5, None)),
        ("1.250.000", (1250000.0, None)),
        ("12.5", (12.5, None)),
        ("12.5000", (12.5, None)),
        ("12.500", (None, "ambiguous_number")),  # 12.5 or 12500 - never guessed
        ("abc", (None, "invalid_number")),
        (float("nan"), (None, "invalid_number")),
        (True, (None, "invalid_number")),
    ],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("07315000", ("07315000", None)),
        (" 0731 5000 ", ("07315000", None)),
        (7211000, ("07211000", "municipality_key_zero_padded")),
        ("731500", (None, "invalid_municipality_key")),
        ("07A15000", (None, "invalid_municipality_key")),
        (None, (None, None)),
    ],
)
def test_normalize_ags(raw, expected):
    assert normalize_ags(raw) == expected


def test_normalize_numeric_key():
    assert normalize_numeric_key("071234") == ("071234", None)
    assert normalize_numeric_key("07-12") == (None, "invalid_cadastral_key")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2021-03-04", (date(2021, 3, 4), None)),
        ("2021-03-04T10:00:00Z", (date(2021, 3, 4), None)),
        ("04.03.2021", (date(2021, 3, 4), None)),
        ("03/04/2021", (None, "invalid_date")),  # ambiguous US/EU order
        ("2021/13/45", (None, "invalid_date")),
        ("31.02.2021", (None, "invalid_date")),
        (date(2020, 1, 1), (date(2020, 1, 1), None)),
        (None, (None, None)),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected
