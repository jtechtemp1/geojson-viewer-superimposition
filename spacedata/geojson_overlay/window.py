"""omni.ui ウィンドウ: GeoJSON ファイルの選択とロード実行 UI。"""

from __future__ import annotations

import traceback
from typing import Optional

import omni.ui as ui
import omni.usd

from .coordinate import make_transformer, pyproj_available
from .geojson_loader import load_geojson
from .usd_builder import UsdOverlayBuilder

WINDOW_TITLE = "GeoJSON Overlay"


class GeoJSONOverlayWindow(ui.Window):
    """重畳ロード操作用のドッキング可能なウィンドウ。"""

    def __init__(self, overlay_layer_name: str = "GeoJSONOverlay.usda", **kwargs):
        super().__init__(WINDOW_TITLE, width=420, height=320, **kwargs)
        self._overlay_layer_name = overlay_layer_name

        # モデル (UI 状態)
        self._path_model = ui.SimpleStringModel("")
        self._origin_lon_model = ui.SimpleFloatModel(0.0)
        self._origin_lat_model = ui.SimpleFloatModel(0.0)
        self._auto_origin_model = ui.SimpleBoolModel(True)
        self._radius_model = ui.SimpleFloatModel(5.0)
        self._line_width_model = ui.SimpleFloatModel(2.0)
        # 押し出し
        self._extrude_model = ui.SimpleBoolModel(False)
        self._extrude_height_model = ui.SimpleFloatModel(20.0)
        self._height_field_model = ui.SimpleStringModel("height")
        # 色分け
        self._materials_model = ui.SimpleBoolModel(True)
        self._color_field_model = ui.SimpleStringModel("category")
        # category -> 色 の固定マップ (JSON)。例: {"zone": "#E04C3D", "route": [0.2,0.6,0.86]}
        self._color_map_model = ui.SimpleStringModel("")
        # PointInstancer: 0=auto, 1=常に, 2=使わない
        self._instancer_index = ui.SimpleIntModel(0)
        # 地形ドレープ
        self._drape_model = ui.SimpleBoolModel(False)
        self._terrain_path_model = ui.SimpleStringModel("/World/Terrain")
        self._drape_offset_model = ui.SimpleFloatModel(0.0)
        # 0: auto, 1: simple, 2: pyproj
        self._mode_index = ui.SimpleIntModel(0)
        self._status = ui.SimpleStringModel("GeoJSON ファイルを選択してください。")

        self.frame.set_build_fn(self._build_ui)

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        with ui.VStack(spacing=8, height=0):
            ui.Label("GeoJSON → USD Composer 重畳表示", height=24,
                     style={"font_size": 18})

            # --- ファイル選択 ---
            with ui.HStack(spacing=6, height=26):
                ui.Label("GeoJSON:", width=70)
                ui.StringField(self._path_model)
                ui.Button("参照…", width=70, clicked_fn=self._pick_file)

            # --- 座標変換方式 ---
            with ui.HStack(spacing=6, height=26):
                ui.Label("変換方式:", width=70)
                options = ["auto", "simple (依存なし)", "pyproj (正式投影)"]
                if not pyproj_available():
                    options[2] += " ※未インストール"
                ui.ComboBox(self._mode_index, *options)

            # --- 原点 ---
            with ui.HStack(spacing=6, height=26):
                ui.Label("原点自動:", width=70)
                ui.CheckBox(self._auto_origin_model, width=20)
                ui.Label("(全フィーチャの重心を原点に)", width=0)
            with ui.HStack(spacing=6, height=26):
                ui.Label("原点 lon:", width=70)
                ui.FloatField(self._origin_lon_model)
                ui.Label("lat:", width=30)
                ui.FloatField(self._origin_lat_model)

            # --- 見た目 ---
            with ui.HStack(spacing=6, height=26):
                ui.Label("Point半径:", width=70)
                ui.FloatField(self._radius_model)
                ui.Label("線幅:", width=40)
                ui.FloatField(self._line_width_model)

            # --- 押し出し (Polygon) ---
            with ui.HStack(spacing=6, height=26):
                ui.Label("押し出し:", width=70)
                ui.CheckBox(self._extrude_model, width=20)
                ui.Label("高さ:", width=40)
                ui.FloatField(self._extrude_height_model)
                ui.Label("高さ属性:", width=60)
                ui.StringField(self._height_field_model)

            # --- 色分け ---
            with ui.HStack(spacing=6, height=26):
                ui.Label("色分け:", width=70)
                ui.CheckBox(self._materials_model, width=20)
                ui.Label("属性キー:", width=60)
                ui.StringField(self._color_field_model)
            with ui.HStack(spacing=6, height=26):
                ui.Label("色マップ:", width=70)
                ui.StringField(self._color_map_model)  # JSON: {"zone":"#E04C3D"}

            # --- PointInstancer ---
            with ui.HStack(spacing=6, height=26):
                ui.Label("点の集約:", width=70)
                ui.ComboBox(self._instancer_index,
                            "auto (閾値で自動)", "常に PointInstancer", "個別 prim")

            # --- 地形ドレープ ---
            with ui.HStack(spacing=6, height=26):
                ui.Label("地形ドレープ:", width=80)
                ui.CheckBox(self._drape_model, width=20)
                ui.Label("地形prim:", width=60)
                ui.StringField(self._terrain_path_model)
                ui.Label("オフセット:", width=60)
                ui.FloatField(self._drape_offset_model)

            ui.Spacer(height=4)
            with ui.HStack(spacing=6, height=30):
                ui.Button("重畳表示", clicked_fn=self._on_load)
                ui.Button("Overlay をクリア", clicked_fn=self._on_clear)

            ui.Spacer(height=4)
            ui.Label("ステータス:", height=18)
            ui.StringField(self._status, multiline=True, read_only=True, height=60)

    # ------------------------------------------------------------------ #
    def _pick_file(self) -> None:
        """ファイルピッカーを開く (無い環境では何もしない)。"""
        try:
            from omni.kit.window.filepicker import FilePickerDialog

            def _on_apply(filename: str, dirname: str):
                import os
                full = os.path.join(dirname or "", filename or "")
                self._path_model.set_value(full)
                dialog.hide()

            dialog = FilePickerDialog(
                "GeoJSON を選択",
                apply_button_label="選択",
                click_apply_handler=_on_apply,
                item_filter_options=["*.geojson", "*.json", "*.*"],
            )
            dialog.show()
        except Exception:
            self._status.set_value(
                "ファイルピッカーが使えません。パスを直接入力してください。"
            )

    def _make_builder(self) -> Optional[UsdOverlayBuilder]:
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            self._status.set_value("ステージが開かれていません。")
            return None
        prefer = ["auto", "simple", "pyproj"][self._mode_index.get_value_as_int()]
        transformer = make_transformer(prefer)

        # 色マップ (JSON) のパース。不正なら無視。
        color_map = None
        raw = self._color_map_model.get_value_as_string().strip()
        if raw:
            try:
                import json
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    color_map = parsed
            except Exception:
                self._status.set_value("色マップの JSON が不正です。無視します。")

        # PointInstancer: 0=auto(None), 1=常に(True), 2=個別(False)
        force = [None, True, False][self._instancer_index.get_value_as_int()]

        # 地形ドレープ: 指定 prim のメッシュから高さサンプラを構築
        height_sampler = None
        if self._drape_model.get_value_as_bool():
            from .terrain import MeshHeightSampler
            terrain_path = self._terrain_path_model.get_value_as_string().strip()
            height_sampler = MeshHeightSampler.from_prim(stage, terrain_path)
            if height_sampler is None:
                self._status.set_value(
                    f"地形メッシュが見つかりません: {terrain_path}。ドレープを無効化します。")

        return UsdOverlayBuilder(
            stage,
            overlay_layer_name=self._overlay_layer_name,
            transformer=transformer,
            point_radius=self._radius_model.get_value_as_float(),
            line_width=self._line_width_model.get_value_as_float(),
            extrude=self._extrude_model.get_value_as_bool(),
            extrude_height=self._extrude_height_model.get_value_as_float(),
            height_field=self._height_field_model.get_value_as_string().strip() or "height",
            enable_materials=self._materials_model.get_value_as_bool(),
            color_field=self._color_field_model.get_value_as_string().strip() or "category",
            color_map=color_map,
            force_point_instancer=force,
            height_sampler=height_sampler,
            drape_offset=self._drape_offset_model.get_value_as_float(),
        )

    def _on_load(self) -> None:
        path = self._path_model.get_value_as_string().strip()
        if not path:
            self._status.set_value("GeoJSON のパスを入力してください。")
            return
        try:
            features = load_geojson(path)
            if not features:
                self._status.set_value("有効なフィーチャが見つかりませんでした。")
                return
            builder = self._make_builder()
            if builder is None:
                return
            origin = None
            if not self._auto_origin_model.get_value_as_bool():
                origin = (
                    self._origin_lon_model.get_value_as_float(),
                    self._origin_lat_model.get_value_as_float(),
                )
            counts = builder.build(features, origin=origin, clear=True)
            self._status.set_value(
                "重畳表示しました ({}方式)\n"
                "Point: {Point} / LineString: {LineString} / Polygon: {Polygon}\n"
                "レイヤー: {layer}".format(
                    builder._transformer.name,
                    layer=self._overlay_layer_name,
                    **counts,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._status.set_value(f"エラー: {exc}\n{traceback.format_exc()}")

    def _on_clear(self) -> None:
        try:
            builder = self._make_builder()
            if builder is None:
                return
            builder.clear_overlay()
            self._status.set_value("Overlay レイヤーをクリアしました。")
        except Exception as exc:  # noqa: BLE001
            self._status.set_value(f"エラー: {exc}")

    def destroy(self) -> None:
        super().destroy()
