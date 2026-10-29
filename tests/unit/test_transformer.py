import json

import pytest

from app.database.base import STAGING_SCHEMA
from app.database.base import Base
from app.scrapers.wfs.transport import FixtureTransport
from app.transformers.compensation_transformer import CompensationTransformer
from tests.conftest import harvest_features


def transform(settings, mapping, transport=None):
    transport, discovery, pages = harvest_features(settings, mapping, transport)
    transformer = CompensationTransformer(
        settings, mapping, discovery.schema, discovery.feature_type.name, transport.endpoint
    )
    return transformer.transform([f for _, page in pages for f in page.features])


@pytest.fixture
def result(settings, mapping):
    return transform(settings, mapping)


def test_every_fixture_record_has_its_expected_outcome(result, expected_outcomes):
    """
    Golden test: fixtures/luwg/expected_outcomes.json documents the expected
    outcome of every feature (incl. all edge cases E01-E29).
    """

    accepted = {r.site_id: r for r in result.records}
    rejected = {}

    for issue in result.issues:
        if issue.severity == "rejected":
            rejected.setdefault(issue.site_id or issue.feature_id, issue.code)

    for key, expected in expected_outcomes.items():
        if expected["outcome"] == "rejected":
            assert rejected.get(key) == expected["code"], key
            assert key not in accepted, key
        else:
            assert key in accepted, key
            assert accepted[key].quality_flags == expected["flags"], key


def test_counts_reconcile(result):
    counts = result.counts
    assert counts["fetched"] == 55
    assert counts["fetched"] == counts["accepted"] + counts["rejected"] + counts["duplicates_collapsed"]
    assert (counts["accepted"], counts["rejected"], counts["duplicates_collapsed"]) == (42, 12, 1)


def test_normalised_values(result):
    by_flag = {flag: r for r in result.records for flag in r.quality_flags}

    neustadt = by_flag["name_encoding_repaired"]
    assert neustadt.municipality_name == "Neustadt an der Weinstraße"
    assert neustadt.municipality_name_source == "Neustadt an der Weinstraße"

    mismatch = by_flag["municipality_name_mismatch"]
    assert (mismatch.municipality_key, mismatch.municipality_name) == ("07111000", "Koblenz")
    assert mismatch.municipality_name_source == "Mainz"  # source kept verbatim for audit

    padded = by_flag["municipality_key_zero_padded"]
    assert padded.municipality_key == "07211000"
    assert padded.district_name == "Trier"

    assert by_flag["ambiguous_number"].area_source_m2 is None
    assert by_flag["invalid_eco_credits"].eco_credits_available is None
    assert by_flag["unmapped_status"].compensation_status is None
    assert by_flag["unmapped_status"].compensation_status_source in ("vorläufig gesichert", "abgeschlossen")

    unknown = by_flag["municipality_key_unknown"]
    assert unknown.municipality_name == "Unbekannt"
    assert unknown.district_key == unknown.district_name is None


def test_hectares_converted_to_square_metres(result):
    clean = [r for r in result.records if not r.quality_flags and r.area_source_m2]
    assert clean

    for record in clean:
        assert record.area_source_m2 == pytest.approx(record.area_geom_m2, rel=0.01)


def test_provenance_and_geometry_on_every_record(result):
    for record in result.records:
        assert record.state_code == "RP"
        assert record.source_layer == "luwg:rp_oekokonto"
        assert record.source_crs == "EPSG:25832"
        assert record.geom.geom_type == "MultiPolygon"
        assert record.geom.is_valid
        assert record.geom_source is not None
        assert record.source_attributes["objekt_id"] == record.site_id


def test_record_hash_is_deterministic(settings, mapping, result):
    again = transform(settings, mapping)
    assert {r.site_id: r.record_hash for r in result.records} == {r.site_id: r.record_hash for r in again.records}


def test_record_hash_changes_with_content(settings, mapping, result, fixture_copy):
    collection = json.loads((fixture_copy / "features.json").read_text(encoding="utf-8"))
    collection["features"][0]["properties"]["status"] = "aufgehoben"
    (fixture_copy / "features.json").write_text(json.dumps(collection), encoding="utf-8")

    changed = transform(settings, mapping, FixtureTransport(fixture_copy))
    before = {r.site_id: r.record_hash for r in result.records}
    after = {r.site_id: r.record_hash for r in changed.records}

    assert [k for k in before if before[k] != after[k]] == ["RP-OEK-2026-0001"]


def test_renumbered_server_feature_ids_do_not_count_as_changes(settings, mapping, result, fixture_copy):
    collection = json.loads((fixture_copy / "features.json").read_text(encoding="utf-8"))

    for index, feature in enumerate(collection["features"]):
        feature["id"] = f"rp_oekokonto.{9000 + index}"

    (fixture_copy / "features.json").write_text(json.dumps(collection), encoding="utf-8")
    renumbered = transform(settings, mapping, FixtureTransport(fixture_copy))

    assert {r.site_id: r.record_hash for r in renumbered.records} == {r.site_id: r.record_hash for r in result.records}


def test_layer_without_object_type_field_is_not_rejected(settings, mapping, fixture_copy):
    xsd = (fixture_copy / "describe_feature_type.xsd").read_text(encoding="utf-8")
    xsd = xsd.replace('<element name="objektart" type="string" minOccurs="0" maxOccurs="1"/>', "")
    (fixture_copy / "describe_feature_type.xsd").write_text(xsd, encoding="utf-8")

    result = transform(settings, mapping, FixtureTransport(fixture_copy))
    rejected = {i.code for i in result.issues if i.severity == "rejected"}

    assert not rejected & {"missing_object_type", "out_of_domain", "unknown_object_type"}
    assert all(r.object_type is None for r in result.records)


def test_absent_source_field_is_null_not_guessed(settings, mapping, fixture_copy):
    xsd = (fixture_copy / "describe_feature_type.xsd").read_text(encoding="utf-8")
    xsd = xsd.replace('<element name="oekopunkte_verfuegbar" type="decimal" minOccurs="0" maxOccurs="1"/>', "")
    (fixture_copy / "describe_feature_type.xsd").write_text(xsd, encoding="utf-8")

    result = transform(settings, mapping, FixtureTransport(fixture_copy))
    assert all(r.eco_credits_available is None for r in result.records)


def test_all_tables_live_in_iris_staging():
    import app.database.models  # noqa: F401

    assert {t.schema for t in Base.metadata.tables.values()} == {STAGING_SCHEMA}
    assert "iris_core" not in STAGING_SCHEMA
