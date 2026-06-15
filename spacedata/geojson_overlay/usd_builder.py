"""USD 生成: GeoFeature を Overlay レイヤー上の USD prim へ変換する。

設計方針:
  * 既存のシーン (root layer) を一切編集せず、専用のサブレイヤー
    (既定: GeoJSONOverlay.usda) を作成し、その EditTarget へだけ author する。
  * すべての overlay prim は /World/GeoJSONOverlay 配下に作成する。
  * Point      -> UsdGeom.Sphere、点数が多い場合は UsdGeom.PointInstancer
  * LineString -> UsdGeom.BasisCurves (linear)
  * Polygon    -> UsdGeom.Mesh (穴/凹対応 ear-clipping、押し出し可)
  * GeoJSON の properties は prim の custom 属性 (geojson:<key>) と customData に保持。
  * category ごとに UsdPreviewSurface マテリアルを生成し色分け (固定マップ対応)。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Vt

from .coordinate import CoordinateTransformer, make_transformer
from .geojson_loader import GeoFeature, centroid_lonlat
from . import triangulate as _tri
from .terrain import HeightSampler

_ROOT_PATH = "/World"
_OVERLAY_SCOPE = "/World/GeoJSONOverlay"
_LOOKS_SCOPE = "/World/GeoJSONOverlay/Looks"
_POINTS_PATH = "/World/GeoJSONOverlay/Points"

_DEFAULT_POINT_RADIUS = 5.0
_DEFAULT_LINE_WIDTH = 2.0
_DEFAULT_EXTRUDE_HEIGHT = 20.0
_DEFAULT_INSTANCER_THRESHOLD = 256

_PALETTE: List[Tuple[float, float, float]] = [
    (0.90, 0.30, 0.24), (0.20, 0.60, 0.86), (0.18, 0.80, 0.44),
    (0.95, 0.77, 0.06), (0.61, 0.35, 0.71), (0.90, 0.49, 0.13),
    (0.10, 0.74, 0.61), (0.55, 0.76, 0.29), (0.75, 0.22, 0.17),
    (0.16, 0.50, 0.73),
]
_DEFAULT_COLOR = (0.7, 0.7, 0.7)


def _sanitize(name: str) -> str:
    if not name:
        return "_"
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    if not re.match(r"[A-Za-z_]", s[0]):
        s = "_" + s
    return s


def parse_color(value: Any) -> Optional[Tuple[float, float, float]]:
    """色指定 ([r,g,b] 0..1 / "#RRGGBB" / "RRGGBB") を (r,g,b) へ。失敗時 None。"""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 6:
            try:
                return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore
            except ValueError:
                return None
    return None


def _value_type_for(value: Any) -> Optional[Sdf.ValueTypeName]:
    if isinstance(value, bool):
        return Sdf.ValueTypeNames.Bool
    if isinstance(value, int):
        return Sdf.ValueTypeNames.Int
    if isinstance(value, float):
        return Sdf.ValueTypeNames.Double
    if isinstance(value, str):
        return Sdf.ValueTypeNames.String
    return None


class ColorManager:
    """category ごとに UsdPreviewSurface マテリアルを生成・キャッシュする。

    color_map に明示色があればそれを優先し、無い category にはパレットを巡回割当て。
    """

    def __init__(
        self,
        stage: Usd.Stage,
        looks_path: str = _LOOKS_SCOPE,
        palette: Optional[List[Tuple[float, float, float]]] = None,
        color_map: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._stage = stage
        self._looks_path = looks_path
        self._palette = palette or _PALETTE
        self._color_map: Dict[str, Tuple[float, float, float]] = {}
        for k, v in (color_map or {}).items():
            c = parse_color(v)
            if c is not None:
                self._color_map[str(k)] = c
        self._cache: Dict[str, Tuple[Tuple[float, float, float], UsdShade.Material]] = {}
        self._palette_next = 0

    def color_only(self, category: str) -> Tuple[float, float, float]:
        """マテリアルを作らず色だけ取得 (PointInstancer プロトタイプ等)。"""
        color, _ = self.get(category)
        return color

    def get(self, category: str):
        key = category or "_default"
        if key in self._cache:
            return self._cache[key]
        if key in self._color_map:
            color = self._color_map[key]
        else:
            color = self._palette[self._palette_next % len(self._palette)]
            self._palette_next += 1
        material = self._create_material(key, color)
        self._cache[key] = (color, material)
        return self._cache[key]

    def _create_material(self, category: str, color: Tuple[float, float, float]) -> UsdShade.Material:
        UsdGeom.Scope.Define(self._stage, self._looks_path)
        mat_path = f"{self._looks_path}/mat_{_sanitize(category)}"
        material = UsdShade.Material.Define(self._stage, mat_path)
        shader = UsdShade.Shader.Define(self._stage, f"{mat_path}/Shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.6)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        return material


class UsdOverlayBuilder:
    """GeoFeature 群を USD overlay レイヤーへ書き込むビルダ。"""

    def __init__(
        self,
        stage: Usd.Stage,
        overlay_layer_name: str = "GeoJSONOverlay.usda",
        transformer: Optional[CoordinateTransformer] = None,
        point_radius: float = _DEFAULT_POINT_RADIUS,
        line_width: float = _DEFAULT_LINE_WIDTH,
        extrude: bool = False,
        extrude_height: float = _DEFAULT_EXTRUDE_HEIGHT,
        height_field: str = "height",
        enable_materials: bool = True,
        color_field: str = "category",
        color_map: Optional[Dict[str, Any]] = None,
        # --- PointInstancer ---
        point_instancer_threshold: int = _DEFAULT_INSTANCER_THRESHOLD,
        force_point_instancer: Optional[bool] = None,
        instance_primvars: bool = True,
        # --- 地形ドレープ ---
        height_sampler: Optional[HeightSampler] = None,
        drape_offset: float = 0.0,
    ) -> None:
        self._stage = stage
        self._overlay_layer_name = overlay_layer_name
        self._transformer = transformer or make_transformer("auto")
        self._point_radius = point_radius
        self._line_width = line_width
        self._extrude = extrude
        self._extrude_height = extrude_height
        self._height_field = height_field
        self._enable_materials = enable_materials
        self._color_field = color_field
        self._color_map = color_map or {}
        self._instancer_threshold = point_instancer_threshold
        self._force_instancer = force_point_instancer
        self._instance_primvars = instance_primvars
        self._sampler = height_sampler
        self._drape_offset = drape_offset
        self._overlay_layer: Optional[Sdf.Layer] = None
        self._colors: Optional[ColorManager] = None

    # 座標変換 + 地形ドレープ (sampler があれば Y を地形高さに置換)
    def _local(self, lon: float, lat: float, alt: float = 0.0) -> Tuple[float, float, float]:
        x, y, z = self._transformer.to_local(lon, lat, alt)
        if self._sampler is not None:
            sy = self._sampler.sample(x, z)
            if sy is not None:
                y = sy + self._drape_offset
        return (x, y, z)

    # ------------------------------------------------------------------ #
    # レイヤー管理
    # ------------------------------------------------------------------ #
    def _ensure_overlay_layer(self) -> Sdf.Layer:
        if self._overlay_layer is not None:
            return self._overlay_layer
        root = self._stage.GetRootLayer()
        for sub_path in root.subLayerPaths:
            if sub_path.endswith(self._overlay_layer_name):
                lyr = Sdf.Layer.FindOrOpen(root.ComputeAbsolutePath(sub_path))
                if lyr:
                    self._overlay_layer = lyr
                    return lyr
        if root.realPath:
            import os
            target = os.path.join(os.path.dirname(root.realPath), self._overlay_layer_name)
            lyr = Sdf.Layer.FindOrOpen(target) or Sdf.Layer.CreateNew(target)
        else:
            lyr = Sdf.Layer.CreateAnonymous(self._overlay_layer_name)
        root.subLayerPaths.insert(0, lyr.identifier)
        self._overlay_layer = lyr
        return lyr

    def clear_overlay(self) -> None:
        lyr = self._ensure_overlay_layer()
        with Usd.EditContext(self._stage, Usd.EditTarget(lyr)):
            if self._stage.GetPrimAtPath(_OVERLAY_SCOPE):
                self._stage.RemovePrim(_OVERLAY_SCOPE)

    # ------------------------------------------------------------------ #
    # メイン処理
    # ------------------------------------------------------------------ #
    def build(
        self,
        features: List[GeoFeature],
        origin: Optional[Tuple[float, float]] = None,
        clear: bool = True,
    ) -> Dict[str, int]:
        if not features:
            return {"Point": 0, "LineString": 0, "Polygon": 0}

        if origin is None:
            origin = centroid_lonlat(features)
        self._transformer.set_origin(origin[0], origin[1], 0.0)

        lyr = self._ensure_overlay_layer()
        counts = {"Point": 0, "LineString": 0, "Polygon": 0}

        point_feats = [f for f in features if f.is_point]
        use_instancer = (
            self._force_instancer
            if self._force_instancer is not None
            else len(point_feats) >= self._instancer_threshold
        )

        with Usd.EditContext(self._stage, Usd.EditTarget(lyr)):
            if clear:
                self.clear_overlay()
            UsdGeom.Xform.Define(self._stage, _ROOT_PATH)
            scope = UsdGeom.Scope.Define(self._stage, _OVERLAY_SCOPE)
            scope.GetPrim().SetMetadata("kind", "group")
            self._colors = (
                ColorManager(self._stage, color_map=self._color_map)
                if self._enable_materials else None
            )

            used_names: Dict[str, int] = {}

            # 多数の Point はまとめて PointInstancer 化
            if use_instancer and point_feats:
                self._build_point_instancer(point_feats)
                counts["Point"] += len(point_feats)

            for idx, feat in enumerate(features):
                if feat.is_point:
                    if use_instancer:
                        continue  # 既に instancer で処理
                    name = self._unique_name(feat, idx, used_names)
                    gprim = self._build_point(f"{_OVERLAY_SCOPE}/{name}", feat)
                    counts["Point"] += 1
                elif feat.is_line:
                    name = self._unique_name(feat, idx, used_names)
                    gprim = self._build_line(f"{_OVERLAY_SCOPE}/{name}", feat)
                    counts["LineString"] += 1
                elif feat.is_polygon:
                    name = self._unique_name(feat, idx, used_names)
                    gprim = self._build_polygon(f"{_OVERLAY_SCOPE}/{name}", feat)
                    counts["Polygon"] += 1
                else:
                    gprim = None
                if gprim is not None:
                    self._apply_style(gprim, feat)

        if lyr.realPath:
            lyr.Save()
        return counts

    # ------------------------------------------------------------------ #
    # 名前
    # ------------------------------------------------------------------ #
    def _unique_name(self, feat: GeoFeature, idx: int, used: Dict[str, int]) -> str:
        base = feat.source_id or feat.properties.get("name") or f"{feat.geom_type}_{idx}"
        base = _sanitize(str(base))
        if base in used:
            used[base] += 1
            return f"{base}_{used[base]}"
        used[base] = 0
        return base

    # ------------------------------------------------------------------ #
    # Point (単体 Sphere)
    # ------------------------------------------------------------------ #
    def _build_point(self, path: str, feat: GeoFeature):
        lon, lat, alt = feat.coordinates[0]
        x, y, z = self._local(lon, lat, alt)
        xform = UsdGeom.Xform.Define(self._stage, path)
        xform.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
        sphere = UsdGeom.Sphere.Define(self._stage, f"{path}/marker")
        r = float(self._point_radius)
        sphere.GetRadiusAttr().Set(r)
        sphere.GetExtentAttr().Set(Vt.Vec3fArray([Gf.Vec3f(-r, -r, -r), Gf.Vec3f(r, r, r)]))
        self._write_properties(xform.GetPrim(), feat)
        return sphere

    # ------------------------------------------------------------------ #
    # Point (PointInstancer: 大量データ向け)
    # ------------------------------------------------------------------ #
    def _build_point_instancer(self, point_feats: List[GeoFeature]) -> None:
        instancer = UsdGeom.PointInstancer.Define(self._stage, _POINTS_PATH)
        proto_path = f"{_POINTS_PATH}/Prototypes"
        UsdGeom.Scope.Define(self._stage, proto_path)
        r = float(self._point_radius)

        # category -> プロトタイプ index
        cat_to_proto: Dict[str, int] = {}
        proto_targets: List[str] = []

        def _proto_for(category: str) -> int:
            if category in cat_to_proto:
                return cat_to_proto[category]
            i = len(proto_targets)
            p = f"{proto_path}/proto_{_sanitize(category) if category else 'default'}_{i}"
            sphere = UsdGeom.Sphere.Define(self._stage, p)
            sphere.GetRadiusAttr().Set(r)
            sphere.GetExtentAttr().Set(
                Vt.Vec3fArray([Gf.Vec3f(-r, -r, -r), Gf.Vec3f(r, r, r)]))
            # 色分け
            if self._colors is not None:
                color, material = self._colors.get(category)
                try:
                    UsdShade.MaterialBindingAPI(sphere.GetPrim()).Bind(material)
                except Exception:
                    pass
            else:
                color = _DEFAULT_COLOR
            try:
                sphere.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
            except Exception:
                pass
            cat_to_proto[category] = i
            proto_targets.append(p)
            return i

        positions: List[Gf.Vec3f] = []
        proto_indices: List[int] = []
        props_list: List[Dict[str, Any]] = []
        for feat in point_feats:
            lon, lat, alt = feat.coordinates[0]
            positions.append(Gf.Vec3f(*self._local(lon, lat, alt)))
            category = str(feat.properties.get(self._color_field, "") or "")
            proto_indices.append(_proto_for(category))
            props_list.append(dict(feat.properties))

        instancer.CreatePositionsAttr(Vt.Vec3fArray(positions))
        instancer.CreateProtoIndicesAttr(Vt.IntArray(proto_indices))
        instancer.CreatePrototypesRel().SetTargets([Sdf.Path(p) for p in proto_targets])
        # per-point properties を varying primvars として公開
        self._author_instance_primvars(instancer.GetPrim(), point_feats)
        # 全点の properties は instancer の customData にもまとめて保持
        try:
            instancer.GetPrim().SetCustomDataByKey("geojson:propertiesList", props_list)
            instancer.GetPrim().SetCustomDataByKey("geojson:geomType", "PointInstancer")
        except Exception:
            pass

    @staticmethod
    def _infer_array_type(values: List[Any]):
        """値リストから (Sdf配列型, 変換関数, 既定値) を推定。対象外なら (None,..)。"""
        present = [v for v in values if v is not None]
        if not present:
            return None, None, None
        if all(isinstance(v, bool) for v in present):
            return Sdf.ValueTypeNames.IntArray, (lambda v: int(bool(v))), 0
        if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
            return Sdf.ValueTypeNames.IntArray, int, 0
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
            return Sdf.ValueTypeNames.FloatArray, float, 0.0
        return Sdf.ValueTypeNames.StringArray, (lambda v: str(v)), ""

    @staticmethod
    def _vt_array(arr_type, data):
        if arr_type == Sdf.ValueTypeNames.IntArray:
            return Vt.IntArray(data)
        if arr_type == Sdf.ValueTypeNames.FloatArray:
            return Vt.FloatArray([float(x) for x in data])
        return Vt.StringArray([str(x) for x in data])

    def _author_instance_primvars(self, prim, point_feats: List[GeoFeature]) -> None:
        """各点の properties を per-instance primvars (interpolation=varying) として付与。"""
        if not self._instance_primvars:
            return
        # キーの出現順を保持
        keys: List[str] = []
        seen = set()
        for f in point_feats:
            for k in f.properties:
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        if not keys:
            return
        try:
            api = UsdGeom.PrimvarsAPI(prim)
        except Exception:
            return
        for key in keys:
            vals = [f.properties.get(key) for f in point_feats]
            arr_type, conv, default = self._infer_array_type(vals)
            if arr_type is None:
                continue
            data = [conv(v) if v is not None else default for v in vals]
            try:
                pv = api.CreatePrimvar(
                    f"geojson:{_sanitize(key)}", arr_type, UsdGeom.Tokens.varying)
                pv.Set(self._vt_array(arr_type, data))
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # LineString
    # ------------------------------------------------------------------ #
    def _build_line(self, path: str, feat: GeoFeature):
        pts = [Gf.Vec3f(*self._local(lon, lat, alt))
               for (lon, lat, alt) in feat.coordinates]
        if len(pts) < 2:
            return None
        curves = UsdGeom.BasisCurves.Define(self._stage, path)
        curves.GetTypeAttr().Set(UsdGeom.Tokens.linear)
        curves.GetCurveVertexCountsAttr().Set([len(pts)])
        curves.GetPointsAttr().Set(Vt.Vec3fArray(pts))
        curves.GetWidthsAttr().Set(Vt.FloatArray([float(self._line_width)] * len(pts)))
        curves.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
        self._write_properties(curves.GetPrim(), feat)
        return curves

    # ------------------------------------------------------------------ #
    # Polygon (穴/凹 + 押し出し)
    # ------------------------------------------------------------------ #
    def _feature_height(self, feat: GeoFeature) -> float:
        val = feat.properties.get(self._height_field)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        return float(self._extrude_height)

    def _build_polygon(self, path: str, feat: GeoFeature):
        # feat.coordinates = [outer_ring, hole1, ...] (各 ring は (lon,lat,alt))
        rings_3d: List[List[Tuple[float, float, float]]] = []
        for ring in feat.coordinates:
            rings_3d.append([self._local(lon, lat, alt)
                             for (lon, lat, alt) in ring])
        if not rings_3d or len(rings_3d[0]) < 3:
            return None

        # 穴/凹対応の三角形分割 (キャップ用)
        cap_pts, cap_tris = _tri.triangulate(rings_3d)
        if not cap_tris:
            return None

        mesh = UsdGeom.Mesh.Define(self._stage, path)

        if not self._extrude:
            points = [Gf.Vec3f(*p) for p in cap_pts]
            face_counts = [3] * len(cap_tris)
            face_indices: List[int] = []
            for (a, b, c) in cap_tris:
                face_indices += [a, b, c]
            mesh.GetPointsAttr().Set(Vt.Vec3fArray(points))
            mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(face_counts))
            mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(face_indices))
        else:
            height = self._feature_height(feat)
            points: List[Gf.Vec3f] = []
            face_counts: List[int] = []
            face_indices: List[int] = []
            C = len(cap_pts)
            # 底面キャップ (下向き: 反転) と 上面キャップ (上向き)
            for p in cap_pts:
                points.append(Gf.Vec3f(p[0], p[1], p[2]))            # 0..C-1 底
            for p in cap_pts:
                points.append(Gf.Vec3f(p[0], p[1] + height, p[2]))  # C..2C-1 上
            for (a, b, c) in cap_tris:
                face_counts.append(3)
                face_indices += [a, c, b]                # 底面 (反転)
            for (a, b, c) in cap_tris:
                face_counts.append(3)
                face_indices += [C + a, C + b, C + c]    # 上面
            # 側壁: 各リングの境界辺ごとに四角形を追加 (専用頂点を append)
            for ring in rings_3d:
                rr = ring[:-1] if (len(ring) >= 2 and _tri._same_xz(ring[0], ring[-1])) else ring
                m = len(rr)
                if m < 3:
                    continue
                base_idx = len(points)
                for p in rr:
                    points.append(Gf.Vec3f(p[0], p[1], p[2]))             # 底
                for p in rr:
                    points.append(Gf.Vec3f(p[0], p[1] + height, p[2]))   # 上
                for i in range(m):
                    j = (i + 1) % m
                    bi, bj = base_idx + i, base_idx + j
                    ti, tj = base_idx + m + i, base_idx + m + j
                    face_counts.append(4)
                    face_indices += [bi, bj, tj, ti]
            mesh.GetPointsAttr().Set(Vt.Vec3fArray(points))
            mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(face_counts))
            mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(face_indices))

        mesh.GetSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        mesh.GetDoubleSidedAttr().Set(True)
        self._write_properties(mesh.GetPrim(), feat)
        return mesh

    # ------------------------------------------------------------------ #
    # スタイル
    # ------------------------------------------------------------------ #
    def _apply_style(self, gprim, feat: GeoFeature) -> None:
        category = str(feat.properties.get(self._color_field, "") or "")
        if self._colors is not None:
            color, material = self._colors.get(category)
            try:
                UsdShade.MaterialBindingAPI(gprim.GetPrim()).Bind(material)
            except Exception:
                pass
        else:
            color = _DEFAULT_COLOR
        try:
            gprim.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 属性
    # ------------------------------------------------------------------ #
    def _write_properties(self, prim: Usd.Prim, feat: GeoFeature) -> None:
        if not prim or not prim.IsValid():
            return
        for key, value in feat.properties.items():
            vt = _value_type_for(value)
            if vt is not None:
                prim.CreateAttribute(f"geojson:{_sanitize(key)}", vt, custom=True).Set(value)
        try:
            prim.SetCustomDataByKey("geojson:properties", dict(feat.properties))
        except Exception:
            pass
        if feat.source_id:
            prim.SetCustomDataByKey("geojson:id", feat.source_id)
        prim.SetCustomDataByKey("geojson:geomType", feat.geom_type)
