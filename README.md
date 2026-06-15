# GeoJSON Overlay for USD Composer

GeoJSON の **Point / LineString / Polygon** を、NVIDIA Omniverse の **USD Composer**
（Kit 106 / 107 系）のシーンへ **別レイヤー**（`GeoJSONOverlay.usda`）で重畳表示する Kit 拡張です。
既存の USD シーンを一切編集せず、地理データだけを独立したレイヤーに author します。

```
GeoJSON ─▶ 座標変換 ─▶ USD Overlay Layer ─▶ USD Composer
```

| ドキュメント | 内容 |
|---|---|
| 本 README | 概要・機能一覧・構成・クイックスタート |
| [docs/仕様書.md](docs/仕様書.md) | アーキテクチャ、各機能の詳細仕様、API リファレンス |
| [docs/導入手順書.md](docs/導入手順書.md) | インストール、有効化、pyproj 導入、使い方、トラブルシュート |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 変更履歴 |

## 主な機能

GeoJSON を読み込み、地物の種類に応じた USD ジオメトリへ変換します。

- **座標変換** — WGS84 (lon, lat, alt) → USD ローカル座標 (X=東, Z=北, Y=高さ)。
  依存ライブラリ不要の簡易方式（等距円筒近似）と、`pyproj` による正式投影（UTM / Web Mercator）を切替。
- **重畳表示** — Point→`UsdGeom.Sphere`、LineString→`UsdGeom.BasisCurves`、Polygon→`UsdGeom.Mesh`。
- **属性保持** — GeoJSON の `properties` を `geojson:<key>` の custom 属性および `customData` として保持。
- **レイヤー分離** — `GeoJSONOverlay.usda` を root のサブレイヤー先頭に挿入し、その EditTarget にのみ author。
- **押し出し** — Polygon を高さ付き立体メッシュへ。高さは UI 値または `height` 属性から。
- **category 色分け** — `category` ごとに `UsdPreviewSurface` を生成・バインド。固定色マップ指定も可能。
- **穴 / 凹対応** — ear-clipping ＋ hole bridging により、穴あき・凹多角形を正しく三角形分割。
- **大量 Point の集約** — 点数が閾値超で `UsdGeom.PointInstancer` 化。per-point の `primvars` も付与。
- **地形ドレープ** — 既存メッシュの高さをサンプリングし、地物の Y を地形面へ吸着。

各機能の詳細は [docs/仕様書.md](docs/仕様書.md) を参照してください。

## ディレクトリ構成

```
spacedata.geojson_overlay/
├─ config/
│  └─ extension.toml          # 依存・設定・python.module・pip 宣言
├─ spacedata/geojson_overlay/
│  ├─ __init__.py
│  ├─ extension.py            # omni.ext.IExt / メニュー登録
│  ├─ window.py               # omni.ui 操作ウィンドウ
│  ├─ coordinate.py           # 座標変換 (simple / pyproj)
│  ├─ geojson_loader.py       # GeoJSON 解析・正規化 (USD 非依存)
│  ├─ triangulate.py          # 穴/凹対応 ear-clipping (USD 非依存)
│  ├─ terrain.py              # 地形ドレープ用 高さサンプラ
│  ├─ usd_builder.py          # USD prim 生成・レイヤー管理・色分け
│  └─ tests/                  # omni.kit.test 用テスト
├─ data/
│  └─ sample.geojson          # 動作確認用サンプル (東京駅周辺)
└─ docs/
   ├─ 仕様書.md
   ├─ 導入手順書.md
   ├─ README.md               # 詳細リファレンス (旧 README)
   └─ CHANGELOG.md
```

`geojson_loader.py` / `coordinate.py` / `triangulate.py` / `terrain.py` は Kit・USD に依存しないため、
CI 上で単体テスト可能です。

## クイックスタート

1. 本フォルダ（`spacedata.geojson_overlay`）を kit-app-template の `source/extensions/` 配下へ配置。
2. USD Composer を起動し、**Extensions** ウィンドウで `spacedata.geojson_overlay` を有効化。
3. メニュー **Window → GeoJSON Overlay** でウィンドウを開く。
4. `data/sample.geojson` を選択して **重畳表示** を押すと、`/World/GeoJSONOverlay` 配下に
   prim が生成され、`GeoJSONOverlay.usda` レイヤーへ保存されます。

詳しい手順は [docs/導入手順書.md](docs/導入手順書.md) を参照してください。

## 動作要件

- NVIDIA Omniverse Kit 106 / 107 系（USD Composer）
- Python 3.10 系（Kit 同梱）
- 正式投影を使う場合のみ `pyproj>=3.4`（任意。未導入時は簡易方式へ自動フォールバック）

## ライセンス / 作者

- 作者: SpaceData
- バージョン: 0.4.0
