"""座標変換: WGS84 (lon, lat, alt) -> USD ローカル座標 (X, Y, Z)。

USD は Y-up を前提とする。水平面を X-Z に取り、
  X = 東方向 (経度方向)
  Z = 北方向 (緯度方向)
  Y = 高さ (alt をそのままメートルとして利用)

2 つの実装を切替可能:
  * SimpleEquirectangular : 依存ライブラリ不要の等距円筒近似 (MVP 既定)
  * PyprojTransformer      : pyproj による正式な投影 (UTM / Web Mercator 等)

`make_transformer()` は pyproj の有無と引数に応じて適切な実装を返す。
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

# 地球半径などの定数 (WGS84 近似)
_EARTH_RADIUS_M = 6378137.0
_METERS_PER_DEG_LAT = 111320.0  # 緯度1度あたりの距離 (近似)


class CoordinateTransformer:
    """座標変換の抽象基底クラス。"""

    #: 人が読める方式名 (UI 表示・デバッグ用)
    name: str = "base"

    def set_origin(self, lon: float, lat: float, alt: float = 0.0) -> None:
        """変換の原点 (この点が USD 原点 0,0,0 になる) を設定する。"""
        raise NotImplementedError

    def to_local(self, lon: float, lat: float, alt: float = 0.0) -> Tuple[float, float, float]:
        """(lon, lat, alt) を USD ローカル座標 (x, y, z) [メートル] へ変換する。"""
        raise NotImplementedError


class SimpleEquirectangular(CoordinateTransformer):
    """等距円筒近似による軽量変換 (依存ライブラリ不要)。

    原点緯度における経度方向のスケールを cos 補正する。
    数百 m〜数十 km 程度の局所領域であれば十分実用的。
    """

    name = "simple-equirectangular"

    def __init__(self) -> None:
        self._origin_lon = 0.0
        self._origin_lat = 0.0
        self._origin_alt = 0.0
        self._m_per_deg_lon = _METERS_PER_DEG_LAT  # set_origin で更新

    def set_origin(self, lon: float, lat: float, alt: float = 0.0) -> None:
        self._origin_lon = lon
        self._origin_lat = lat
        self._origin_alt = alt
        # 原点緯度での経度1度あたり距離
        self._m_per_deg_lon = _METERS_PER_DEG_LAT * math.cos(math.radians(lat))

    def to_local(self, lon: float, lat: float, alt: float = 0.0) -> Tuple[float, float, float]:
        x = (lon - self._origin_lon) * self._m_per_deg_lon
        z = (lat - self._origin_lat) * _METERS_PER_DEG_LAT
        y = alt - self._origin_alt
        return (x, y, z)


class PyprojTransformer(CoordinateTransformer):
    """pyproj による正式な投影変換。

    既定では原点経度に基づく UTM ゾーンを自動選択する。
    `epsg` を渡せば任意の投影座標系 (例: 3857 = Web Mercator) を使用できる。
    """

    name = "pyproj"

    def __init__(self, epsg: Optional[int] = None) -> None:
        # import は遅延させ、pyproj が無い環境でも本モジュールの import 自体は通す
        from pyproj import Transformer  # noqa: F401  (存在確認)

        self._Transformer = Transformer
        self._epsg = epsg
        self._fwd = None
        self._ox = 0.0
        self._oy = 0.0
        self._origin_alt = 0.0

    @staticmethod
    def _auto_utm_epsg(lon: float, lat: float) -> int:
        """経度・緯度から UTM ゾーンの EPSG コードを求める。"""
        zone = int(math.floor((lon + 180.0) / 6.0)) + 1
        # 北半球: 326xx / 南半球: 327xx
        return (32600 if lat >= 0 else 32700) + zone

    def set_origin(self, lon: float, lat: float, alt: float = 0.0) -> None:
        epsg = self._epsg or self._auto_utm_epsg(lon, lat)
        # WGS84 (lon, lat) -> 投影座標 (x, y) [m]
        self._fwd = self._Transformer.from_crs(
            "EPSG:4326", f"EPSG:{epsg}", always_xy=True
        )
        self._ox, self._oy = self._fwd.transform(lon, lat)
        self._origin_alt = alt

    def to_local(self, lon: float, lat: float, alt: float = 0.0) -> Tuple[float, float, float]:
        px, py = self._fwd.transform(lon, lat)
        x = px - self._ox          # 東方向
        z = py - self._oy          # 北方向
        y = alt - self._origin_alt  # 高さ
        return (x, y, z)


def pyproj_available() -> bool:
    """pyproj が import 可能かどうか。"""
    try:
        import pyproj  # noqa: F401
        return True
    except Exception:
        return False


def ensure_pyproj(auto_install: bool = True) -> bool:
    """pyproj を利用可能にする。無ければ omni.kit.pipapi で取得を試みる。

    Returns:
        利用可能になったら True。
    """
    if pyproj_available():
        return True
    if not auto_install:
        return False
    # Kit 環境であれば pipapi 経由で取得を試みる (失敗しても例外は出さない)
    try:
        import omni.kit.pipapi

        omni.kit.pipapi.install("pyproj>=3.4")
        return pyproj_available()
    except Exception:
        return False


def make_transformer(
    prefer: str = "auto",
    epsg: Optional[int] = None,
    auto_install: bool = True,
) -> CoordinateTransformer:
    """変換器を生成する。

    Args:
        prefer: "simple" | "pyproj" | "auto"
                "auto" は pyproj があれば pyproj、無ければ simple を選ぶ。
        epsg:   pyproj 使用時の投影 EPSG (None なら原点から UTM 自動選択)。
        auto_install: pyproj が無いとき pipapi での自動取得を試みるか。
    """
    prefer = (prefer or "auto").lower()
    if prefer == "simple":
        return SimpleEquirectangular()
    if prefer == "pyproj":
        # 明示指定なら取得を試み、それでも無ければ simple へフォールバック
        if ensure_pyproj(auto_install):
            return PyprojTransformer(epsg=epsg)
        return SimpleEquirectangular()
    # auto: 既に使えるなら pyproj、無ければ simple (auto では自動インストールしない)
    if pyproj_available():
        return PyprojTransformer(epsg=epsg)
    return SimpleEquirectangular()
