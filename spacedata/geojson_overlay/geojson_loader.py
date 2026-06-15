"""GeoJSON の読み込みと正規化。

FeatureCollection / Feature / Geometry のいずれかを受け取り、
扱いやすい GeoFeature のリストへ正規化する。USD 非依存の純粋ロジックなので
Kit の外 (CI など) でも単体テスト可能。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Union

# (lon, lat[, alt]) のタプル
Coord = Tuple[float, ...]

_SUPPORTED = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}


@dataclass
class GeoFeature:
    """正規化済みフィーチャ 1 件。"""

    geom_type: str                       # "Point" | "LineString" | "Polygon"
    # geom_type に応じた座標構造:
    #   Point      : [ (lon,lat,alt) ]                          (1 点)
    #   LineString : [ (lon,lat,alt), ... ]                     (頂点列)
    #   Polygon    : [ outer_ring, hole1, ... ]  各 ring = [ (lon,lat,alt), ... ]
    coordinates: List[Any]
    properties: Dict[str, Any] = field(default_factory=dict)
    source_id: str = ""                  # GeoJSON 上の id (あれば)

    @property
    def is_point(self) -> bool:
        return self.geom_type == "Point"

    @property
    def is_line(self) -> bool:
        return self.geom_type == "LineString"

    @property
    def is_polygon(self) -> bool:
        return self.geom_type == "Polygon"


def _to_coord(c: List[float]) -> Coord:
    """[lon, lat] または [lon, lat, alt] をタプル化。alt 既定 0。"""
    if len(c) >= 3:
        return (float(c[0]), float(c[1]), float(c[2]))
    return (float(c[0]), float(c[1]), 0.0)


def _explode_geometry(geom: Dict[str, Any]) -> List[Tuple[str, List[Any]]]:
    """1 つの geometry を (単一型, coords) の列に分解する。

    Multi* と GeometryCollection を単一ジオメトリへ平坦化することで、
    後段 (USD 生成) を Point / LineString / Polygon の 3 種だけで扱える。
    """
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    out: List[Tuple[str, List[Any]]] = []

    if gtype == "Point":
        out.append(("Point", [_to_coord(coords)]))
    elif gtype == "MultiPoint":
        for c in coords:
            out.append(("Point", [_to_coord(c)]))
    elif gtype == "LineString":
        out.append(("LineString", [_to_coord(c) for c in coords]))
    elif gtype == "MultiLineString":
        for line in coords:
            out.append(("LineString", [_to_coord(c) for c in line]))
    elif gtype == "Polygon":
        rings = [[_to_coord(c) for c in ring] for ring in coords]
        out.append(("Polygon", rings))
    elif gtype == "MultiPolygon":
        for poly in coords:
            rings = [[_to_coord(c) for c in ring] for ring in poly]
            out.append(("Polygon", rings))
    elif gtype == "GeometryCollection":
        for sub in geom.get("geometries", []):
            out.extend(_explode_geometry(sub))
    else:
        # 未対応タイプは黙って無視 (MVP)
        pass
    return out


def normalize(geojson: Dict[str, Any]) -> List[GeoFeature]:
    """GeoJSON dict を GeoFeature のリストへ正規化する。"""
    features: List[GeoFeature] = []

    gtype = geojson.get("type")
    if gtype == "FeatureCollection":
        raw_features = geojson.get("features", [])
    elif gtype == "Feature":
        raw_features = [geojson]
    elif gtype in _SUPPORTED or gtype == "GeometryCollection":
        # 生の geometry のみ
        raw_features = [{"type": "Feature", "geometry": geojson, "properties": {}}]
    else:
        raise ValueError(f"未対応の GeoJSON type です: {gtype!r}")

    for feat in raw_features:
        geom = feat.get("geometry")
        if not geom:
            continue
        props = feat.get("properties") or {}
        fid = str(feat.get("id", ""))
        for single_type, coords in _explode_geometry(geom):
            features.append(
                GeoFeature(
                    geom_type=single_type,
                    coordinates=coords,
                    properties=dict(props),
                    source_id=fid,
                )
            )
    return features


def load_geojson(source: Union[str, bytes, Dict[str, Any]]) -> List[GeoFeature]:
    """ファイルパス / JSON 文字列 / dict のいずれかから GeoFeature を読み込む。"""
    if isinstance(source, dict):
        data = source
    elif isinstance(source, (bytes, bytearray)):
        data = json.loads(source.decode("utf-8"))
    elif isinstance(source, str):
        s = source.strip()
        if s.startswith("{"):
            data = json.loads(s)            # JSON 文字列
        else:
            with open(source, "r", encoding="utf-8") as f:  # ファイルパス
                data = json.load(f)
    else:
        raise TypeError(f"未対応の source 型: {type(source)!r}")

    return normalize(data)


def all_coords(features: List[GeoFeature]) -> List[Coord]:
    """全フィーチャの座標を平坦化して返す (原点自動決定などに使う)。"""
    out: List[Coord] = []
    for f in features:
        if f.is_point:
            out.extend(f.coordinates)
        elif f.is_line:
            out.extend(f.coordinates)
        elif f.is_polygon:
            for ring in f.coordinates:
                out.extend(ring)
    return out


def centroid_lonlat(features: List[GeoFeature]) -> Tuple[float, float]:
    """全座標の単純平均 (重心) を原点候補として返す。"""
    coords = all_coords(features)
    if not coords:
        return (0.0, 0.0)
    n = len(coords)
    return (sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n)
