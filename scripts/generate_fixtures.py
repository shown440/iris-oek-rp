"""
Generate the synthetic LUWG fixture (deterministic, seed 56).

    fixtures/luwg/features.json            all features (served page by page
                                           by FixtureTransport)
    fixtures/luwg/expected_outcomes.json   expected outcome per feature
                                           (golden file used by the tests)
    fixtures/luwg/sample_page.gml          3 features as WFS 2.0 / GML 3.2,
                                           to test the GML parsing path

Sites are placed at points inside real RLP municipalities (BKG VG250
centroids, EPSG:25832). Everything else - identifiers, names, statuses,
areas, credits - is invented. Edge cases are listed in EDGE_CASES below and
documented in fixtures/README.md.

Usage:  python scripts/generate_fixtures.py
"""

import json
import math
import random
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "luwg"

TYPE_NAME = "luwg:rp_oekokonto"

CRS_URN = "urn:ogc:def:crs:EPSG::25832"

# (AGS, official name, x, y) - point inside the municipality (VG250 01.01.2026)
MUNICIPALITIES = [
    ("07315000", "Mainz", 445614, 5536039),
    ("07111000", "Koblenz", 398938, 5578346),
    ("07211000", "Trier", 331121, 5514884),
    ("07312000", "Kaiserslautern", 410145, 5476308),
    ("07314000", "Ludwigshafen am Rhein", 456306, 5481223),
    ("07316000", "Neustadt an der Weinstraße", 438420, 5465813),
    ("07133006", "Bad Kreuznach", 418627, 5521166),
    ("07138045", "Neuwied", 392625, 5590684),
    ("07319000", "Worms", 451238, 5499871),
    ("07318000", "Speyer", 458944, 5464264),
    ("07313000", "Landau in der Pfalz", 434464, 5449480),
    ("07131007", "Bad Neuenahr-Ahrweiler", 367119, 5599207),
    ("07134045", "Idar-Oberstein", 381232, 5506786),
    ("07339005", "Bingen am Rhein", 423661, 5533372),
    ("07320000", "Zweibrücken", 381036, 5456330),
    ("07317000", "Pirmasens", 397609, 5450328),
    ("07231134", "Wittlich", 350210, 5539531),
    ("07143048", "Montabaur", 415177, 5586855),
    ("07331003", "Alzey", 435391, 5510761),
]

MANNHEIM = ("08222000", "Mannheim", 463829, 5483104)

AUTHORITIES = [
    "Kreisverwaltung Mainz-Bingen - Untere Naturschutzbehörde",
    "Stadtverwaltung Koblenz - Untere Naturschutzbehörde",
    "SGD Nord - Obere Naturschutzbehörde",
    "SGD Süd - Obere Naturschutzbehörde",
    "Stiftung Natur und Umwelt Rheinland-Pfalz",
]

MEASURES = [
    "Extensivierung von Grünland",
    "Anlage einer Streuobstwiese",
    "Renaturierung eines Bachabschnitts",
    "Umwandlung von Acker in Magerrasen",
    "Pflanzung einer Feldhecke",
    "Entwicklung eines Auwalds",
]

STATUS_VARIANTS = ["umgesetzt", "Umgesetzt", " in Umsetzung ", "geplant", "IN PFLEGE", "Umsetzung abgeschlossen"]

rng = random.Random(56)


# =====================================================
# GEOMETRY HELPERS
# =====================================================


def blob(x: float, y: float, radius: float, vertices: int = 10) -> list[list[float]]:
    """
    Irregular, valid (star-shaped) polygon ring around (x, y).
    """

    ring = []

    for i in range(vertices):
        angle = 2 * math.pi * i / vertices
        r = radius * rng.uniform(0.75, 1.0)
        ring.append([round(x + r * math.cos(angle), 2), round(y + r * math.sin(angle), 2)])

    ring.append(ring[0])
    return ring


def ring_area(ring: list[list[float]]) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:], strict=False))) / 2


def german(value: float, decimals: int = 4) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def offset(x: float, y: float, spread: float = 800) -> tuple[float, float]:
    return x + rng.uniform(-spread, spread), y + rng.uniform(-spread, spread)


# =====================================================
# FEATURE FACTORY
# =====================================================

counter = 0


def next_id() -> str:
    global counter
    counter += 1
    return f"RP-OEK-2026-{counter:04d}"


def base_feature(municipality: tuple, object_type: str = "Ökokonto", **overrides) -> dict:
    ags, name, mx, my = municipality
    x, y = offset(mx, my)
    ring = blob(x, y, rng.uniform(45, 160))
    area_ha = ring_area(ring) / 10_000
    site_id = next_id()
    eco = object_type.lower().startswith(("ö", "oe"))
    total = round(area_ha * rng.uniform(80_000, 140_000)) if eco else None

    properties = {
        "objekt_id": site_id,
        "objektart": object_type,
        "bezeichnung": f"{rng.choice(MEASURES)} {name}",
        "aktenzeichen": f"UNB-{ags[2:5]}-{rng.randint(100, 999)}/{rng.randint(2015, 2025)}",
        "gemeinde": name,
        "gemeinde_schluessel": ags,
        "gemarkung": f"{name.split(' ')[0]}-{rng.choice(['Nord', 'Süd', 'Ost', 'West', 'Mitte'])}",
        "gemarkung_schluessel": f"07{rng.randint(1000, 9999)}",
        "flurstuecke": f"Flur {rng.randint(1, 30)}, Nr. {rng.randint(1, 400)}/{rng.randint(1, 9)}",
        "status": rng.choice(STATUS_VARIANTS),
        "flaeche_ha": german(area_ha) if rng.random() < 0.6 else round(area_ha, 4),
        "oekopunkte_gesamt": total,
        "oekopunkte_verfuegbar": None if total is None else round(total * rng.uniform(0.1, 0.9)),
        "massnahme": rng.choice(MEASURES),
        "zielzustand": rng.choice(["Magerrasen", "Streuobstwiese", "Auwald", "Feldgehölz", "Extensivgrünland"]),
        "eintragungsstelle": rng.choice(AUTHORITIES),
        "datum_eintragung": (
            f"{rng.randint(2016, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            if rng.random() < 0.7
            else f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(2016, 2025)}"
        ),
    }
    properties.update(overrides.pop("properties", {}))

    feature = {
        "type": "Feature",
        "id": f"rp_oekokonto.{1000 + counter}",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": properties,
    }
    feature.update(overrides)
    return feature


def main() -> None:
    features: list[dict] = []
    expected: dict[str, dict] = {}

    def add(feature: dict, outcome: str, code: str | None = None, flags: list[str] | None = None, key=None):
        features.append(feature)
        key = key or feature["properties"]["objekt_id"]
        expected[key] = {"outcome": outcome, "code": code, "flags": sorted(flags or [])}

    object_types = ["Ökokonto", "Kompensationsfläche", "OEKOKONTO", "Kompensationsflaeche", "Ersatzzahlungsmaßnahme"]

    # ---- 8 clean sites ------------------------------------------------------
    # With SORTBY objekt_id the whitespace id of E06 sorts first, so the
    # duplicate pair below lands on sorted positions 10 and 11, i.e. split
    # across page 1 and page 2 (CountDefault = 10).
    for i in range(8):
        add(base_feature(MUNICIPALITIES[i % len(MUNICIPALITIES)], object_types[i % len(object_types)]), "accepted")

    # ---- E07 identical duplicate straddling the page-1/page-2 boundary ----
    duplicate = base_feature(MUNICIPALITIES[9])
    features.append(duplicate)
    features.append(json.loads(json.dumps(duplicate)))
    expected[duplicate["properties"]["objekt_id"]] = {
        "outcome": "accepted",
        "code": None,
        "flags": [],
        "note": "E07 two identical copies -> loaded once, duplicate_in_source warning",
    }

    # ---- more clean sites --------------------------------------------------
    for i in range(10, 28):
        add(base_feature(MUNICIPALITIES[i % len(MUNICIPALITIES)], object_types[i % len(object_types)]), "accepted")

    m = MUNICIPALITIES

    # ---- geometry edge cases ----------------------------------------------
    f = base_feature(m[0], properties={"flaeche_ha": None})
    x, y = f["geometry"]["coordinates"][0][0]
    f["geometry"]["coordinates"] = [[[x, y], [x + 200, y + 200], [x + 200, y], [x, y + 200], [x, y]]]
    add(f, "accepted", flags=["geometry_repaired"])  # E01 bow-tie

    f = base_feature(m[1])
    x, y = m[1][2] + 300, m[1][3] - 300
    shell = [[x - 150, y - 150], [x + 150, y - 150], [x + 150, y + 150], [x - 150, y + 150], [x - 150, y - 150]]
    hole = [[x - 50, y - 50], [x - 50, y + 50], [x + 50, y + 50], [x + 50, y - 50], [x - 50, y - 50]]
    f["geometry"]["coordinates"] = [shell, hole]
    f["properties"]["flaeche_ha"] = german((300 * 300 - 100 * 100) / 10_000)
    add(f, "accepted")  # E02 polygon with hole

    f = base_feature(m[2])
    f["geometry"]["coordinates"] = [[[cx, cy, 142.5] for cx, cy in f["geometry"]["coordinates"][0]]]
    add(f, "accepted", flags=["z_dropped"])  # E03 3D coordinates

    add(base_feature(m[3], geometry=None), "rejected", "missing_geometry")  # E04

    f = base_feature(m[4])
    f["geometry"] = {"type": "Point", "coordinates": [m[4][2], m[4][3]]}
    add(f, "rejected", "unsupported_geometry_type")  # E05

    f = base_feature(m[5])
    x, y = m[5][2], m[5][3]
    f["geometry"]["coordinates"] = [[[x, y], [x + 100, y], [x + 200, y], [x, y]]]
    add(f, "rejected", "unrepairable_geometry")  # E21 collapsed sliver

    f = base_feature(m[6])
    x, y = m[6][2], m[6][3]
    f["geometry"]["coordinates"] = [[[x, y], [x + 0.5, y], [x + 0.5, y + 0.5], [x, y + 0.5], [x, y]]]
    add(f, "rejected", "degenerate_geometry")  # E29 0.25 m2

    f = base_feature(m[7])
    f["geometry"] = {"type": "Polygon", "coordinates": [[[m[7][2], m[7][3]]]]}
    add(f, "rejected", "geometry_unparseable")  # E27 one-point ring

    # ---- identity / domain / state ----------------------------------------
    f = base_feature(m[8], properties={"objekt_id": "   "})
    add(f, "rejected", "missing_site_id", key=f["id"])  # E06

    f1 = base_feature(m[9], properties={"status": "umgesetzt"})
    f2 = json.loads(json.dumps(f1))
    f2["properties"]["status"] = "in Pflege"
    features.extend([f1, f2])
    expected[f1["properties"]["objekt_id"]] = {"outcome": "rejected", "code": "conflicting_duplicate", "flags": []}

    add(base_feature(MANNHEIM), "rejected", "outside_state")  # E09

    f = base_feature(m[0])
    f["geometry"]["coordinates"] = [[[cy, cx] for cx, cy in f["geometry"]["coordinates"][0]]]
    add(f, "rejected", "geometry_outside_state")  # E10 axis swapped

    add(base_feature(m[1], "Eingriffsverfahren"), "rejected", "out_of_domain")  # E11
    add(base_feature(m[2], "Biotopverbundfläche"), "rejected", "unknown_object_type")  # E26

    # ---- attribute edge cases (accepted with warnings) --------------------
    add(base_feature(m[3], properties={"status": "vorläufig gesichert"}), "accepted", flags=["unmapped_status"])  # E12
    add(base_feature(m[4], properties={"status": "abgeschlossen"}), "accepted", flags=["unmapped_status"])  # E13
    add(base_feature(m[5], properties={"flaeche_ha": "12.500"}), "accepted", flags=["ambiguous_number"])  # E14

    add(
        base_feature(m[5], properties={"gemeinde": "Neustadt an der WeinstraÃŸe"}),
        "accepted",
        flags=["name_encoding_repaired"],
    )  # E15

    add(
        base_feature(m[1], properties={"gemeinde": "Mainz"}),
        "accepted",
        flags=["municipality_name_mismatch"],
    )  # E16 name contradicts key

    add(
        base_feature(m[2], properties={"gemeinde_schluessel": 7211000}),
        "accepted",
        flags=["municipality_key_zero_padded"],
    )  # E17

    f = base_feature(m[6])
    f["properties"]["flaeche_ha"] = german(3 * ring_area(f["geometry"]["coordinates"][0]) / 10_000)
    add(f, "accepted", flags=["area_mismatch"])  # E18 declared area 3x the geometry

    add(
        base_feature(m[7], properties={"oekopunkte_gesamt": 40000, "oekopunkte_verfuegbar": -500}),
        "accepted",
        flags=["invalid_eco_credits"],
    )  # E19

    add(
        base_feature(m[8], properties={"oekopunkte_gesamt": 10000, "oekopunkte_verfuegbar": 25000}),
        "accepted",
        flags=["eco_credits_inconsistent"],
    )  # E20

    add(
        base_feature(m[10], properties={"gemeinde_schluessel": "07999999", "gemeinde": "Unbekannt"}),
        "accepted",
        flags=["municipality_key_unknown"],
    )  # E22

    add(base_feature(m[11], properties={"datum_eintragung": "2021/13/45"}), "accepted", flags=["invalid_date"])  # E23

    add(
        base_feature(m[12], properties={"gemeinde_schluessel": None, "gemarkung": None, "gemarkung_schluessel": None}),
        "accepted",
    )  # E25 no key: state decided by geometry, nothing guessed

    collection = {
        "type": "FeatureCollection",
        "name": TYPE_NAME,
        "timeStamp": "2026-10-28T00:00:00Z",
        "crs": {"type": "name", "properties": {"name": CRS_URN}},
        "features": features,
    }

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURE_DIR / "features.json").write_text(json.dumps(collection, ensure_ascii=False, indent=1), encoding="utf-8")
    (FIXTURE_DIR / "expected_outcomes.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    write_gml_sample(features)

    print(f"{len(features)} features, {len(expected)} expected outcomes -> {FIXTURE_DIR}")


# =====================================================
# GML SAMPLE
# =====================================================


def _pos_list(ring: list[list[float]]) -> str:
    return " ".join(f"{c[0]} {c[1]}" for c in ring)


def write_gml_sample(features: list[dict]) -> None:
    picks = [features[0], next(f for f in features if len(f["geometry"]["coordinates"]) == 2)]
    members = []

    for index, feature in enumerate(picks, start=1):
        rings = feature["geometry"]["coordinates"]
        interior = "".join(
            f"<gml:interior><gml:LinearRing><gml:posList>{_pos_list(r)}</gml:posList></gml:LinearRing></gml:interior>"
            for r in rings[1:]
        )
        properties = "".join(f"<luwg:{k}>{v}</luwg:{k}>" for k, v in feature["properties"].items() if v is not None)
        members.append(
            f'<wfs:member><luwg:rp_oekokonto gml:id="{feature["id"]}">'
            f'<luwg:geom><gml:MultiSurface gml:id="ms.{index}" srsName="{CRS_URN}"><gml:surfaceMember>'
            f'<gml:Polygon gml:id="p.{index}"><gml:exterior><gml:LinearRing><gml:posList>{_pos_list(rings[0])}'
            f"</gml:posList></gml:LinearRing></gml:exterior>{interior}</gml:Polygon>"
            f"</gml:surfaceMember></gml:MultiSurface></luwg:geom>{properties}</luwg:rp_oekokonto></wfs:member>"
        )

    gml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- SYNTHETIC FIXTURE: GetFeature response as WFS 2.0 / GML 3.2 -->\n"
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:luwg="https://geodaten-wasser.rlp.de/luwg" '
        f'numberMatched="{len(picks)}" numberReturned="{len(picks)}" timeStamp="2026-10-28T00:00:00Z">\n'
        + "\n".join(members)
        + "\n</wfs:FeatureCollection>\n"
    )
    (FIXTURE_DIR / "sample_page.gml").write_text(gml, encoding="utf-8")


if __name__ == "__main__":
    main()
