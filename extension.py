"""Kit Extension エントリポイント (omni.ext.IExt)。

USD Composer のメニュー [Window] に "GeoJSON Overlay" を追加し、
クリックで操作ウィンドウを開く。
"""

from __future__ import annotations

import carb
import omni.ext
import omni.ui as ui

from .window import GeoJSONOverlayWindow, WINDOW_TITLE

_MENU_PATH = f"Window/{WINDOW_TITLE}"
_SETTING_PREFIX = "exts/spacedata.geojson_overlay"


class GeoJSONOverlayExtension(omni.ext.IExt):
    """拡張のライフサイクル管理。"""

    def on_startup(self, ext_id: str) -> None:
        carb.log_info(f"[spacedata.geojson_overlay] startup: {ext_id}")

        settings = carb.settings.get_settings()
        self._overlay_layer_name = (
            settings.get(f"{_SETTING_PREFIX}/overlay_layer_name")
            or "GeoJSONOverlay.usda"
        )

        self._window: GeoJSONOverlayWindow | None = None
        self._menu = None

        # メニュー登録 (omni.kit.menu.utils があれば使う)
        try:
            from omni.kit.menu.utils import MenuItemDescription, add_menu_items
            self._menu_items = [
                MenuItemDescription(
                    name=WINDOW_TITLE,
                    onclick_fn=self._toggle_window,
                )
            ]
            add_menu_items(self._menu_items, "Window")
        except Exception:
            self._menu_items = None
            # フォールバック: 旧 editor_menu
            try:
                editor_menu = omni.kit.ui.get_editor_menu()
                if editor_menu:
                    self._menu = editor_menu.add_item(
                        _MENU_PATH, self._on_menu_click, toggle=True, value=False
                    )
            except Exception:
                carb.log_warn(
                    "[spacedata.geojson_overlay] メニュー登録に失敗 (UI 無し環境?)"
                )

        # 起動時はウィンドウを生成しておく (非表示)
        self._window = GeoJSONOverlayWindow(
            overlay_layer_name=self._overlay_layer_name
        )
        self._window.visible = False
        self._window.set_visibility_changed_fn(self._on_visibility_changed)

    def on_shutdown(self) -> None:
        carb.log_info("[spacedata.geojson_overlay] shutdown")
        # メニュー解除
        try:
            if self._menu_items:
                from omni.kit.menu.utils import remove_menu_items
                remove_menu_items(self._menu_items, "Window")
        except Exception:
            pass
        self._menu_items = None
        self._menu = None

        if self._window is not None:
            self._window.destroy()
            self._window = None

    # ------------------------------------------------------------------ #
    def _toggle_window(self, *args) -> None:
        if self._window is not None:
            self._window.visible = not self._window.visible

    def _on_menu_click(self, menu_path: str, value: bool) -> None:
        if self._window is not None:
            self._window.visible = value

    def _on_visibility_changed(self, visible: bool) -> None:
        # メニューのチェック状態を同期
        try:
            if self._menu_items:
                from omni.kit.menu.utils import set_menu_item_value
                set_menu_item_value(_MENU_PATH, visible)
        except Exception:
            pass
