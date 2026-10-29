import geopandas as gpd
import pytest
import shapely
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.geometry import Point
from shapely.geometry import Polygon

from app.reference import load_state_boundary
from app.transformers.geometry import check_state
from app.transformers.geometry import prepare_geometries
from app.transformers.geometry import prepare_geometry

MAINZ = (445614, 5536039)
MANNHEIM = (463829, 5483104)


def square(x, y, size=100):
    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)])


def test_valid_polygon_becomes_multipolygon_unchanged():
    result = prepare_geometry(square(*MAINZ), 25832, 1.0)
    assert result.issue is None
    assert result.geom.geom_type == "MultiPolygon"
    assert result.geom.area == pytest.approx(10_000)
    assert result.flags == ()


def test_bow_tie_is_repaired_and_flagged():
    x, y = MAINZ
    bow_tie = Polygon([(x, y), (x + 200, y + 200), (x + 200, y), (x, y + 200), (x, y)])
    result = prepare_geometry(bow_tie, 25832, 1.0)

    assert result.issue is None
    assert result.geom.is_valid
    assert len(result.geom.geoms) == 2
    assert result.geom.area == pytest.approx(20_000)
    assert "geometry_repaired" in result.flags
    assert "Self-intersection" in result.repair_note


def test_collapsed_sliver_is_rejected_not_invented():
    x, y = MAINZ
    sliver = Polygon([(x, y), (x + 100, y), (x + 200, y), (x, y)])
    assert prepare_geometry(sliver, 25832, 1.0).issue == "unrepairable_geometry"


@pytest.mark.parametrize(
    ("geom", "issue"),
    [
        (None, "missing_geometry"),
        (Polygon(), "empty_geometry"),
        (Point(MAINZ), "unsupported_geometry_type"),
        (LineString([MAINZ, (MAINZ[0] + 10, MAINZ[1])]), "unsupported_geometry_type"),
        (square(*MAINZ, size=0.5), "degenerate_geometry"),
    ],
)
def test_rejections(geom, issue):
    assert prepare_geometry(geom, 25832, 1.0).issue == issue


def test_z_is_dropped():
    polygon_z = shapely.force_3d(square(*MAINZ), 140.0)
    result = prepare_geometry(polygon_z, 25832, 1.0)
    assert not shapely.has_z(result.geom)
    assert "z_dropped" in result.flags


def test_wgs84_is_projected_to_25832():
    # 100 x 100 m-ish square around Mainz cathedral, in lon/lat.
    lon, lat = 8.2741, 49.9990
    d = 0.0007
    polygon = Polygon([(lon, lat), (lon + d, lat), (lon + d, lat + d), (lon, lat + d), (lon, lat)])
    result = prepare_geometry(polygon, 4326, 1.0)

    x, y = result.geom.centroid.coords[0]
    assert x == pytest.approx(448_001, abs=2)
    assert y == pytest.approx(5_538_811, abs=2)
    assert 3_000 < result.geom.area < 7_000


def test_state_check_uses_municipality_key_as_authority():
    boundary = load_state_boundary()
    mainz = prepare_geometry(square(*MAINZ), 25832, 1).geom
    mannheim = prepare_geometry(square(*MANNHEIM), 25832, 1).geom

    assert check_state("07315000", mainz, boundary, "07", 2000) == (None, None)
    assert check_state("08222000", mannheim, boundary, "07", 2000) == ("outside_state", None)
    # No key: geometry decides. Mannheim lies ~1 km east of the RLP border.
    assert check_state(None, mannheim, boundary, "07", 2000) == ("geometry_outside_state", None)


def test_state_check_catches_swapped_axes():
    boundary = load_state_boundary()
    swapped = Polygon([(y, x) for x, y in square(*MAINZ).exterior.coords])
    geom = prepare_geometry(swapped, 25832, 1).geom
    assert check_state("07315000", geom, boundary, "07", 2000) == ("geometry_outside_state", None)


def test_state_check_tolerates_generalised_border():
    boundary = load_state_boundary()
    # RLP key, ~670 m outside the 1:2.5M generalised boundary: accepted with a flag.
    near = prepare_geometry(square(461_000, 5_482_000, 50), 25832, 1).geom
    assert check_state("07314000", near, boundary, "07", 2000) == (None, "near_state_border")


def test_vectorised_batch_equals_single_geometry_results():
    x, y = MAINZ
    batch = [
        square(x, y),
        Polygon([(x, y), (x + 200, y + 200), (x + 200, y), (x, y + 200), (x, y)]),  # bow-tie
        None,
        Point(MAINZ),
        Polygon([(x, y), (x + 100, y), (x + 200, y), (x, y)]),  # sliver
        square(x, y, size=0.5),
    ]
    vectorised = prepare_geometries(gpd.GeoSeries(batch, crs="EPSG:25832"), 1.0)
    single = [prepare_geometry(g, 25832, 1.0) for g in batch]

    assert [(r.issue, r.flags) for r in vectorised] == [(r.issue, r.flags) for r in single]
    assert [r.issue for r in vectorised] == [
        None,
        None,
        "missing_geometry",
        "unsupported_geometry_type",
        "unrepairable_geometry",
        "degenerate_geometry",
    ]


def test_transformer_groups_mixed_source_crs(settings, mapping):
    from app.parsers.compensation_parser import RawFeature
    from app.transformers.compensation_transformer import CompensationTransformer

    lon, lat, d = 8.2741, 49.9990, 0.001
    wgs84 = Polygon([(lon, lat), (lon + d, lat), (lon + d, lat + d), (lon, lat + d), (lon, lat)])
    props = {"objektart": "Ökokonto", "gemeinde_schluessel": "07315000"}
    features = [
        RawFeature("f.1", {**props, "objekt_id": "A"}, square(*MAINZ), 25832, 1),
        RawFeature("f.2", {**props, "objekt_id": "B"}, wgs84, 4326, 1),
    ]
    transformer = CompensationTransformer(settings, mapping, None, "luwg:rp_oekokonto", "test://")
    result = transformer.transform(features)
    by_id = {r.site_id: r for r in result.records}

    assert by_id["A"].source_crs == "EPSG:25832" and by_id["B"].source_crs == "EPSG:4326"
    # Expected centroid computed independently with pyproj.
    expected_x, expected_y = Transformer.from_crs(4326, 25832, always_xy=True).transform(lon + d / 2, lat + d / 2)
    assert by_id["B"].geom.centroid.x == pytest.approx(expected_x, abs=1)
    assert by_id["B"].geom.centroid.y == pytest.approx(expected_y, abs=1)
    assert by_id["B"].geom_source.equals(wgs84)  # original CRS geometry kept untouched
