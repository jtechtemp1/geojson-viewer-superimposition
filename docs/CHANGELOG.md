# Changelog

## [0.4.0]
- 地形ドレープ (`terrain.py`): Constant / Callable / Mesh の高さサンプラ、builder へ統合
- 三角形分割の退化ケース頑健化 (連続重複・共線・穴の境界接触・複数穴・退化リスキップ・耳なしフォールバック)
- PointInstancer の per-point `primvars:geojson:*` (varying, 型自動判別) 公開
- UI に地形ドレープ (prim パス / オフセット) を追加

## [0.3.0]
- 穴 (holes)・凹多角形対応の ear-clipping 三角形分割 (`triangulate.py`)、押し出しも穴対応
- category→色の固定マップ (`color_map`、`#RRGGBB` / `[r,g,b]`) 対応
- 大量 Point の `UsdGeom.PointInstancer` 化 (category 別プロトタイプで色分け)
- UI に色マップ JSON 欄・点の集約モードを追加

## [0.2.0]
- Polygon 押し出し（`extrude` / `extrude_height` / `height_field`）で立体メッシュ化
- category 別 `UsdPreviewSurface` マテリアル生成・バインド（+ `displayColor`）
- pyproj 導入整備: `[python.pipapi]` 宣言 + `ensure_pyproj()` ランタイム取得
- UI に押し出し・高さ・高さ属性・色分け・色属性キーを追加

## [0.1.0] - MVP
- GeoJSON (Point / LineString / Polygon, Multi* / GeometryCollection 含む) の読込
- 座標変換 (simple 等距円筒近似 / pyproj 正式投影 切替)
- USD 重畳生成: Sphere / BasisCurves / Mesh
- properties を `geojson:*` 属性 + customData として保持
- 専用サブレイヤー `GeoJSONOverlay.usda` への分離 author / クリア機能
- omni.ui 操作ウィンドウ、Window メニュー登録
