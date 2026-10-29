"""
Build the static reference data shipped in app/reference/.

Inputs are the official BKG administrative boundary products
(Datenlizenz Deutschland - Namensnennung - Version 2.0):

    VG2500  (state boundary, 1:2,500,000)
        https://daten.gdz.bkg.bund.de/produkte/vg/vg2500/aktuell/vg2500_12-31.utm32s.shape.zip
    VG250   (municipalities + districts, 1:250,000)
        https://daten.gdz.bkg.bund.de/produkte/vg/vg250_ebenen_0101/aktuell/vg250_01-01.utm32s.shape.ebenen.zip

Usage:

    python scripts/build_reference_data.py \
        --vg2500-dir /path/to/vg2500 \
        --vg250-dir /path/to/vg250_ebenen_0101

Outputs (committed, so the harvester never downloads at runtime):

    app/reference/rp_boundary_vg2500.geojson   EPSG:25832, coords rounded to 1 m
    app/reference/rp_municipalities.csv        AGS -> official name + district
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pyogrio
import shapely

STATE_ARS = "07"

# GF = 4: land areas incl. structure (BKG "Geofaktor"); excludes water-only parts.
GF_LAND = 4

OUT_DIR = Path(__file__).resolve().parents[1] / "app" / "reference"


def build_boundary(vg2500_dir: Path) -> None:
    gdf = pyogrio.read_dataframe(vg2500_dir / "VG2500_LAN.shp")
    state = gdf[gdf["ARS"] == STATE_ARS]

    if state.empty:
        raise SystemExit("Rhineland-Palatinate (ARS 07) not found in VG2500_LAN")

    geom = shapely.union_all(state.geometry.values)
    geom = shapely.set_precision(geom, 1.0)

    feature_collection = {
        "type": "FeatureCollection",
        "name": "rp_boundary_vg2500",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:EPSG::25832"},
        },
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "ars": STATE_ARS,
                    "name": "Rheinland-Pfalz",
                    "source": "BKG VG2500 (31.12.2025), dl-de/by-2-0",
                },
                "geometry": json.loads(shapely.to_geojson(geom)),
            }
        ],
    }

    out = OUT_DIR / "rp_boundary_vg2500.geojson"
    out.write_text(json.dumps(feature_collection, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {out} ({geom.area / 1e6:,.0f} km2)")


def build_municipalities(vg250_dir: Path) -> None:
    columns = ["AGS", "GEN", "BEZ", "GF"]

    gem = pyogrio.read_dataframe(vg250_dir / "VG250_GEM.dbf", read_geometry=False, columns=columns)
    krs = pyogrio.read_dataframe(vg250_dir / "VG250_KRS.dbf", read_geometry=False, columns=columns)

    gem = gem[gem["AGS"].str.startswith(STATE_ARS) & (gem["GF"] == GF_LAND)]
    krs = krs[krs["AGS"].str.startswith(STATE_ARS) & (krs["GF"] == GF_LAND)]

    if not gem["AGS"].is_unique:
        raise SystemExit("Duplicate AGS in VG250_GEM for RLP")

    districts = dict(zip(krs["AGS"], krs["GEN"], strict=True))

    out = OUT_DIR / "rp_municipalities.csv"

    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ags", "name", "type", "district_key", "district_name"])

        for row in gem.sort_values("AGS").itertuples(index=False):
            district_key = row.AGS[:5]
            writer.writerow([row.AGS, row.GEN, row.BEZ, district_key, districts[district_key]])

    print(f"Wrote {out} ({len(gem):,} municipalities, {len(districts)} districts)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vg2500-dir", type=Path, required=True)
    parser.add_argument("--vg250-dir", type=Path, required=True)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_boundary(args.vg2500_dir)
    build_municipalities(args.vg250_dir)


if __name__ == "__main__":
    main()
