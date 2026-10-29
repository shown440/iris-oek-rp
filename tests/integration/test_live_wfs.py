"""
Opt-in smoke test of the live HTTP path against a REAL public RLP WFS
(LVermGeo "Umlegung", dl-de/by-2-0). Proves discovery, schema parsing,
hits and one paged GeoJSON request work against a genuine MapServer.

    pytest -m live
"""

from dataclasses import replace

import pytest

from app.config.mapping import load_mapping
from app.runners.compensation_runner import build_transport
from tests.conftest import harvest_features
from tests.conftest import make_settings

pytestmark = pytest.mark.live


def test_discover_and_page_real_rlp_wfs(tmp_path):
    settings = make_settings(
        tmp_path,
        source_mode="live",
        wfs_url="https://www.geoportal.rlp.de/registry/wfs/588",
        wfs_layer="umlegung_rlp",
        page_size=2,
        max_features=2,
        request_delay_s=1.0,
    )
    # This layer is not a compensation layer: use its own identifier ("gid")
    # so the verify-before-use schema gate passes; the HTTP path is what is tested.
    mapping = replace(load_mapping(settings.mapping_file), id_field="gid", sort_field="gid")

    _, discovery, pages = harvest_features(settings, mapping, build_transport(settings))

    assert discovery.blockers == ()
    assert discovery.feature_type.name == "vermkv:umlegung_rlp"
    assert discovery.capabilities.implements_paging
    assert discovery.schema.geometry_property
    assert pages and pages[0][1].number_returned == 2
    assert pages[0][1].features[0].source_srid == 25832
