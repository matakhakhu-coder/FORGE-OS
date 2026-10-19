#!/usr/bin/env python3
"""Build FORGE's offline South African gazetteer (data/gazetteer/).

tools/coverage_areas.py turns the published pins into search areas from these
files, so a scheduled run never has to reach a map service (one less thing that
can hang). Rebuild only to refresh the data; the files are committed.

Sources (download them first, into one folder):
    ZA.txt              GeoNames South Africa dump, CC BY 4.0
                        https://download.geonames.org/export/dump/ZA.zip
    admin2Codes.txt     GeoNames district names
                        https://download.geonames.org/export/dump/admin2Codes.txt
    stations.geojson    SAPS station points (SAPS-generated layer, v1.2.5,
                        2021): ArcGIS 'South African Police Stations - Points'
                        services7.arcgis.com/jbdCPRrwkIrZb0KZ/.../South_African_Police_Stations
    bnd_*.geojson       SAPS station boundaries (precincts, 2019 snapshot):
                        ArcGIS 'SA Police Boundaries'
                        services8.arcgis.com/oTalEaSXAuyNT7xf/.../SA_Police_Boundaries
                        queried with maxAllowableOffset=0.005 (about 500 m)

    python tools/build_gazetteer.py --src /tmp/gaz

Outputs:
    data/gazetteer/za_places.json     populated places, suburbs, townships
    data/gazetteer/saps_precincts.json precinct outlines and station points
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "gazetteer"

PROVINCES = {"02": "KwaZulu-Natal", "03": "Free State", "05": "Eastern Cape",
             "06": "Gauteng", "07": "Mpumalanga", "08": "Northern Cape",
             "09": "Limpopo", "10": "North West", "11": "Western Cape"}
# Populated places and their sections (suburbs, townships, villages).
PLACE_CODES = {"PPL", "PPLX", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC", "PPLL", "PPLG"}


def build_places(src: pathlib.Path) -> dict:
    districts = {}
    for line in (src / "admin2Codes.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if parts[0].startswith("ZA.") and parts[0].count(".") == 2:
            districts[parts[0].split(".")[2]] = parts[1]
    rows, munis = [], {}
    lines = (src / "ZA.txt").read_text(encoding="utf-8").splitlines()
    for line in lines:
        f = line.split("\t")
        if f[7] == "ADM3" and f[12]:
            munis[f[12]] = f[1]
    for line in lines:
        f = line.split("\t")
        if f[6] != "P" or f[7] not in PLACE_CODES:
            continue
        rows.append([f[1], round(float(f[4]), 5), round(float(f[5]), 5), f[7],
                     int(f[14] or 0), PROVINCES.get(f[10], ""),
                     districts.get(f[11], f[11]), munis.get(f[12], f[12])])
    rows.sort(key=lambda r: (r[5], r[0]))
    return {
        "schema": "forge.gazetteer.places", "version": 1,
        "about": "South African populated places, suburbs, townships and villages, for "
                 "tools/coverage_areas.py. Built by tools/build_gazetteer.py.",
        "source": "GeoNames (geonames.org), CC BY 4.0. Feature class P only.",
        "fields": ["name", "lat", "lng", "feature_code", "population",
                   "province", "district", "municipality"],
        "rows": rows,
    }


def _rings(geom: dict) -> list:
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return []
    # Outer rings only: holes are rare in precincts and a hole only makes an
    # area look bigger, never smaller, which is the safe direction here.
    return [[[round(x, 4), round(y, 4)] for x, y in poly[0]] for poly in polys if poly]


def build_precincts(src: pathlib.Path) -> dict:
    stations = {}
    for feat in json.loads((src / "stations.geojson").read_text())["features"]:
        name = (feat["properties"].get("COMPNT_NM") or "").strip()
        if name and feat.get("geometry"):
            x, y = feat["geometry"]["coordinates"][:2]
            stations.setdefault(name.upper(), [round(x, 5), round(y, 5)])
    precincts, seen = [], set()
    for path in sorted(src.glob("bnd_*.geojson")):
        for feat in json.loads(path.read_text())["features"]:
            name = (feat["properties"].get("COMPNT_NM") or "").strip()
            if not name or not feat.get("geometry") or name.upper() in seen:
                continue
            seen.add(name.upper())
            precincts.append({"name": name.title(), "station": stations.get(name.upper()),
                              "rings": _rings(feat["geometry"])})
    # Stations with no outline still count as a point.
    for name, pt in stations.items():
        if name not in seen:
            precincts.append({"name": name.title(), "station": pt, "rings": []})
    precincts.sort(key=lambda p: p["name"])
    return {
        "schema": "forge.gazetteer.saps_precincts", "version": 1,
        "about": "SAPS police station precincts (outline) and station points, for "
                 "tools/coverage_areas.py. Built by tools/build_gazetteer.py.",
        "source": "SAPS-generated layers republished on ArcGIS Online: station boundaries "
                  "(2019 snapshot, simplified to about 500 m) and station points (v1.2.5, "
                  "2021). Boundaries change; a precinct named here is a search lead, "
                  "not a legal boundary.",
        "precincts": precincts,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, type=pathlib.Path)
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    places = build_places(a.src)
    (OUT / "za_places.json").write_text(json.dumps(places, ensure_ascii=False, separators=(",", ":")))
    prec = build_precincts(a.src)
    (OUT / "saps_precincts.json").write_text(json.dumps(prec, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(places['rows'])} places, {len(prec['precincts'])} precincts -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
