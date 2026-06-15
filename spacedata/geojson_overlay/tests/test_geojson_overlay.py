"""omni.kit.test 用テスト。Composer/Kit 環境内で実行される。"""

import omni.kit.test
import omni.usd
from pxr import Usd

from ..geojson_loader import load_geojson, centroid_lonlat
from ..coordinate import SimpleEquirectangular
from ..usd_builder import UsdOverlayBuilder
from ..terrain import ConstantHeightSampler

_SAMPLE = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "id": "p1",
         "geometry": {"type": "Point", "coordinates": [139.767, 35.681, 10.0]},
         "properties": {"name": "p", "value": 1.5}},
        {"type": "Feature", "id": "l1",
         "geometry": {"type": "LineString",
                      "coordinates": [[139.764, 35.679], [139.769, 35.682]]},
         "properties": {}},
        {"type": "Feature", "id": "z1",
         "geometry": {"type": "Polygon",
                      "coordinates": [[[139.765, 35.680], [139.770, 35.680],
                                       [139.770, 35.683], [139.765, 35.680]]]},
         "properties": {"level": 2}},
    ],
}


class TestGeoJSONOverlay(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._ctx = omni.usd.get_context()
        await self._ctx.new_stage_async()

    async def test_loader_normalizes(self):
        feats = load_geojson(_SAMPLE)
        self.assertEqual([f.geom_type for f in feats],
                         ["Point", "LineString", "Polygon"])

    async def test_build_creates_overlay_layer(self):
        stage = self._ctx.get_stage()
        feats = load_geojson(_SAMPLE)
        builder = UsdOverlayBuilder(stage, transformer=SimpleEquirectangular())
        counts = builder.build(feats)
        self.assertEqual(counts,
                         {"Point": 1, "LineString": 1, "Polygon": 1})

        # 別レイヤーに author され、元 root には overlay prim が無い
        root = stage.GetRootLayer()
        self.assertTrue(any("GeoJSONOverlay" in p for p in root.subLayerPaths))
        self.assertFalse(root.GetPrimAtPath("/World/GeoJSONOverlay"))

        # prim とジオメトリ確認
        self.assertTrue(stage.GetPrimAtPath("/World/GeoJSONOverlay/p1").IsValid())
        self.assertTrue(
            stage.GetPrimAtPath("/World/GeoJSONOverlay/p1/marker").IsValid())

        # 属性
        p = stage.GetPrimAtPath("/World/GeoJSONOverlay/p1")
        self.assertEqual(p.GetAttribute("geojson:value").Get(), 1.5)
        self.assertEqual(p.GetCustomDataByKey("geojson:geomType"), "Point")

    async def test_clear(self):
        stage = self._ctx.get_stage()
        builder = UsdOverlayBuilder(stage, transformer=SimpleEquirectangular())
        builder.build(load_geojson(_SAMPLE))
        builder.clear_overlay()
        self.assertFalse(
            stage.GetPrimAtPath("/World/GeoJSONOverlay").IsValid())

    async def test_extrude_creates_prism(self):
        from pxr import UsdGeom
        stage = self._ctx.get_stage()
        builder = UsdOverlayBuilder(
            stage, transformer=SimpleEquirectangular(),
            extrude=True, extrude_height=30.0, enable_materials=False)
        builder.build(load_geojson(_SAMPLE))
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/World/GeoJSONOverlay/z1"))
        pts = mesh.GetPointsAttr().Get()
        # 底面 4 + 上面 4 = 8 頂点 (ring 4 点を押し出し)
        self.assertEqual(len(pts), 8)
        # 上面が底面 +30 の高さ
        for i in range(4):
            self.assertAlmostEqual(pts[4 + i][1], pts[i][1] + 30.0, places=3)

    async def test_materials_bound_by_category(self):
        from pxr import UsdShade
        stage = self._ctx.get_stage()
        builder = UsdOverlayBuilder(
            stage, transformer=SimpleEquirectangular(), enable_materials=True)
        builder.build(load_geojson(_SAMPLE))
        mesh_prim = stage.GetPrimAtPath("/World/GeoJSONOverlay/z1")
        binding = UsdShade.MaterialBindingAPI(mesh_prim).GetDirectBinding()
        self.assertTrue(binding.GetMaterial())

    async def test_polygon_with_hole(self):
        from pxr import UsdGeom
        stage = self._ctx.get_stage()
        holed = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "id": "plaza",
                "geometry": {"type": "Polygon", "coordinates": [
                    [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001], [0, 0]],
                    [[0.0003, 0.0003], [0.0007, 0.0003],
                     [0.0007, 0.0007], [0.0003, 0.0007], [0.0003, 0.0003]],
                ]},
                "properties": {"category": "plaza"},
            }],
        }
        builder = UsdOverlayBuilder(
            stage, transformer=SimpleEquirectangular(), enable_materials=False)
        builder.build(load_geojson(holed), origin=(0.0, 0.0))
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/World/GeoJSONOverlay/plaza"))
        fvc = mesh.GetFaceVertexCountsAttr().Get()
        self.assertTrue(len(fvc) > 0 and all(c == 3 for c in fvc))

    async def test_color_map(self):
        from pxr import UsdShade, Gf
        stage = self._ctx.get_stage()
        builder = UsdOverlayBuilder(
            stage, transformer=SimpleEquirectangular(),
            enable_materials=True, color_map={"zone": "#FF0000"})
        builder.build(load_geojson(_SAMPLE))
        prim = stage.GetPrimAtPath("/World/GeoJSONOverlay/z1")
        dc = prim.GetAttribute("primvars:displayColor").Get()
        self.assertEqual(tuple(dc[0]), (1.0, 0.0, 0.0))

    async def test_point_instancer_forced(self):
        from pxr import UsdGeom
        stage = self._ctx.get_stage()
        many = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": f"p{i}",
             "geometry": {"type": "Point", "coordinates": [0.0001 * i, 0.0]},
             "properties": {"category": "a" if i % 2 else "b"}}
            for i in range(10)]}
        builder = UsdOverlayBuilder(
            stage, transformer=SimpleEquirectangular(),
            force_point_instancer=True)
        counts = builder.build(load_geojson(many), origin=(0.0, 0.0))
        self.assertEqual(counts["Point"], 10)
        inst = UsdGeom.PointInstancer(
            stage.GetPrimAtPath("/World/GeoJSONOverlay/Points"))
        self.assertTrue(inst.GetPrim().IsValid())
        self.assertEqual(len(inst.GetPositionsAttr().Get()), 10)
        self.assertEqual(len(inst.GetProtoIndicesAttr().Get()), 10)

    async def test_instance_primvars(self):
        from pxr import UsdGeom
        stage = self._ctx.get_stage()
        many = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": f"p{i}",
             "geometry": {"type": "Point", "coordinates": [0.0001 * i, 0.0]},
             "properties": {"temp": 20.0 + i, "label": f"n{i}"}}
            for i in range(5)]}
        builder = UsdOverlayBuilder(
            stage, transformer=SimpleEquirectangular(),
            force_point_instancer=True, instance_primvars=True,
            enable_materials=False)
        builder.build(load_geojson(many), origin=(0.0, 0.0))
        prim = stage.GetPrimAtPath("/World/GeoJSONOverlay/Points")
        api = UsdGeom.PrimvarsAPI(prim)
        temp = api.GetPrimvar("geojson:temp")
        self.assertTrue(temp.IsDefined())
        self.assertEqual(len(temp.Get()), 5)

    async def test_terrain_drape(self):
        from pxr import UsdGeom
        stage = self._ctx.get_stage()
        builder = UsdOverlayBuilder(
            stage, transformer=SimpleEquirectangular(), enable_materials=False,
            height_sampler=ConstantHeightSampler(100.0), drape_offset=2.0)
        builder.build(load_geojson(_SAMPLE), origin=(139.767, 35.681))
        # LineString の全頂点 Y が 102 に吸着
        curves = UsdGeom.BasisCurves(
            stage.GetPrimAtPath("/World/GeoJSONOverlay/l1"))
        for p in curves.GetPointsAttr().Get():
            self.assertAlmostEqual(p[1], 102.0, places=3)
