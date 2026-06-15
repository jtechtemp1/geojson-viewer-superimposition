# GeoJSON Overlay for USD Composer

GeoJSON の **Point / LineString / Polygon** を、USD Composer のシーンへ
**別レイヤー (`GeoJSONOverlay.usda`)** で重畳表示する MVP 拡張です。
既存の USD シーンを一切編集せず、地理データだけを独立レイヤーに author します。

```
GeoJSON ─▶ 座標変換 ─▶ USD Overlay Layer ─▶ USD Composer
```

## 機能 (MVP)

| # | 機能 | 内容 |
|---|------|------|
| 1 | GeoJSON 読込 | `FeatureCollection` / `Feature` / 生 geometry を解析。`Multi*` と `GeometryCollection` は単一ジオメトリへ平坦化 |
| 2 | 座標変換 | WGS84 (lon, lat, alt) → USD ローカル (X=東, Z=北, Y=高さ)。簡易/正式の 2 方式を切替 |
| 3 | 重畳表示 | Point→`UsdGeom.Sphere`、LineString→`UsdGeom.BasisCurves`、Polygon→`UsdGeom.Mesh` |
| 4 | 属性表示 | `properties` を `geojson:<key>` の custom 属性 + `customData` として保持 |
| 5 | Layer 分離 | `GeoJSONOverlay.usda` を root のサブレイヤー先頭に挿入し、その EditTarget へのみ author |
| 6 | Polygon 押し出し | `extrude` で平面→立体メッシュ化。高さは UI 値または `height` 属性（建物の高さ表現） |
| 7 | category 色分け | `category` ごとに `UsdPreviewSurface` マテリアルを生成・バインド（+ `displayColor`） |
| 8 | 穴/凹対応 | ear-clipping ＋ hole bridging で穴あき・凹多角形を正しく三角形分割 |
| 9 | 色の固定マップ | `color_map` で category→色（`#RRGGBB` / `[r,g,b]`）を明示指定。未定義はパレット巡回 |
| 10 | 大量 Point の集約 | 点数が閾値超で `UsdGeom.PointInstancer` 化（category 別プロトタイプで色分け、per-point `primvars`） |
| 11 | 地形ドレープ | 既存メッシュの高さをサンプリングし、地物 Y を地形面へ吸着（建物は地形上に押し出し） |

## 座標変換の方式

`make_transformer(prefer)` で切替えます。

- **simple** (`SimpleEquirectangular`): 等距円筒近似。依存ライブラリ不要。原点緯度で経度方向を cos 補正。局所領域 (数百 m〜数十 km) に最適。
- **pyproj** (`PyprojTransformer`): pyproj による正式投影。既定は原点経度からの UTM ゾーン自動選択。`epsg=3857` で Web Mercator など任意指定可。
- **auto** (既定): pyproj があれば pyproj、無ければ simple。

原点は UI の「原点自動」で全フィーチャの重心が選ばれます。明示指定も可能です。

## 構成

```
spacedata.geojson_overlay/
├─ config/
│  └─ extension.toml          # 依存・設定・python.module 宣言 (Kit 106/107)
├─ spacedata/geojson_overlay/
│  ├─ __init__.py
│  ├─ extension.py            # omni.ext.IExt / メニュー登録
│  ├─ window.py               # omni.ui 操作ウィンドウ
│  ├─ coordinate.py           # 座標変換 (simple / pyproj)
│  ├─ geojson_loader.py       # GeoJSON 解析・正規化 (USD 非依存)
│  └─ usd_builder.py          # USD prim 生成・レイヤー管理
├─ data/
│  └─ sample.geojson          # 動作確認用サンプル (東京駅周辺)
└─ docs/
   └─ README.md
```

`geojson_loader.py` と `coordinate.py` は Kit/USD に依存しないため、CI で単体テスト可能です。

## インストール

1. このフォルダを kit-app-template の `source/extensions/` 配下へ配置。
   （または `.kit` の `[settings] exts.folders` に本フォルダの親を追加）
2. USD Composer を起動し、**Extensions** ウィンドウで `spacedata.geojson_overlay` を有効化。
3. メニュー **Window → GeoJSON Overlay** でウィンドウを開く。

### pyproj の導入

正式投影（UTM / Web Mercator 等）には `pyproj` が必要です。3 通りの導入方法があります。

1. **拡張の自動取得（推奨）**: `config/extension.toml` の `[python.pipapi]` に
   `pyproj>=3.4` を宣言済み。オンライン接続があれば拡張有効化時に Kit が解決します。
   さらに変換方式で **pyproj** を明示選択すると、未導入時に `omni.kit.pipapi` 経由で
   ランタイム取得を試みます（`coordinate.ensure_pyproj()`）。
2. **Kit の Python へ手動 pip**:
   `<kit>/python.sh -m pip install "pyproj>=3.4"`（Windows は `python.bat`）。
3. **社内ミラー**: `extension.toml` の `[python.pipapi] extra_args` に
   `--index-url https://your-mirror/simple` を追加。

いずれも失敗した場合、`coordinate.py` は自動的に **simple 方式へフォールバック**するため
拡張自体は常に動作します。

## 使い方

1. ステージ（既存の USD シーン）を開く。
2. **GeoJSON Overlay** ウィンドウで GeoJSON を選択（`data/sample.geojson` で動作確認可）。
3. 変換方式・原点・Point 半径・線幅を必要に応じて調整。
4. **重畳表示** を押すと `/World/GeoJSONOverlay` 配下に prim が生成され、
   `GeoJSONOverlay.usda` レイヤーへ保存されます。
5. **Overlay をクリア** で重畳分のみ削除（元シーンは無傷）。

## 属性の格納先

各 prim に以下を付与します。

- typed custom 属性: `geojson:<key>`（bool/int/double/string を自動判別）
- `customData["geojson:properties"]`: properties 全体（ラウンドトリップ用）
- `customData["geojson:id"]`, `customData["geojson:geomType"]`

## 押し出しと色分け

UI（または `UsdOverlayBuilder` の引数）で制御します。

- **押し出し** (`extrude`, `extrude_height`, `height_field`): ON で Polygon を立体メッシュ化。
  高さは `properties[height_field]`（既定 `height`）に数値があればそれを優先し、無ければ
  UI の高さ値を使用。底面・上面（扇分割）＋側壁（四角形）で構成、`doubleSided=True`。
- **色分け** (`enable_materials`, `color_field`): `properties[color_field]`（既定 `category`）
  ごとに `UsdPreviewSurface` マテリアルを `/World/GeoJSONOverlay/Looks` 配下へ生成し、
  10 色パレットを巡回割り当て・バインド。`primvars:displayColor` も併せて設定。

## 穴/凹・色マップ・PointInstancer

- **三角形分割** (`triangulate.py`): 外周＋穴リングを受け取り、穴を外周へブリッジ
  （O'Rourke 可視頂点法）した上で ear-clipping。凹多角形・複数穴に対応。
  押し出し時は上下キャップをこの分割で作り、側壁は外周＋各穴の境界辺から生成。
- **色の固定マップ** (`color_map`): `{"zone": "#E04C3D", "route": [0.2,0.6,0.86]}` の形式。
  UI の「色マップ」欄に JSON を入力。マップにない category はパレットを巡回割当て。
- **PointInstancer** (`force_point_instancer`, `point_instancer_threshold`):
  点数が閾値（既定 256）以上で自動的に `UsdGeom.PointInstancer` を 1 つ生成。
  category ごとに Sphere プロトタイプを作り `protoIndices` で割当てて色分け。
  UI の「点の集約」で auto / 常に / 個別 prim を選択。
  `instance_primvars=True` で各点の `properties` を `primvars:geojson:<key>`
  （interpolation=`varying`、型は float/int/string を自動判別）として公開する。

## 地形ドレープ (`terrain.py`)

`height_sampler` を渡すと、各地物の Y 座標を地形面の高さに置換します（`drape_offset` で浮かせる）。

- `ConstantHeightSampler(y)` … 一定高さ。
- `CallableHeightSampler(fn)` … 任意関数 `f(x, z) -> y`。
- `MeshHeightSampler(triangles)` … 三角形群に対し XZ 内包判定 + バリセントリック補間。
  XZ 一様グリッドで空間インデックス化し、多数三角形でも高速。
  `MeshHeightSampler.from_prim(stage, "/World/Terrain")` で USD メッシュ prim から
  ワールド座標で構築できます（UI の「地形ドレープ」で prim パスを指定）。

押し出しと併用すると、底面が地形に沿い、上面は `base + height` になります（建物が地形に乗る）。

## MVP の制限・今後

- 三角形分割は自己交差ポリゴンや穴同士の接触など退化ケースを想定しない。
- 押し出しは鉛直一様。傾斜地形へのドレープは未対応。
- PointInstancer 化時は per-point の typed 属性を持たず、properties は instancer の
  `customData["geojson:propertiesList"]` に一括保持する。

## 検証

`geojson_loader` / `coordinate` の純ロジック、および `usd_builder` の
ジオメトリ生成（三角形分割インデックス・属性型・レイヤー挿入・クリア）を
単体検証済みです。`omni.kit.test` 用のテストフックは `extension.toml` に宣言済み。
