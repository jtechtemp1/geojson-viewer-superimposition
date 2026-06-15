"""GeoJSON Overlay extension for USD Composer.

GeoJSON (Point / LineString / Polygon) を USD Composer の
シーンへ別レイヤー (GeoJSONOverlay.usda) で重畳表示する MVP 拡張。
"""

from .extension import GeoJSONOverlayExtension  # noqa: F401
from .coordinate import CoordinateTransformer, make_transformer  # noqa: F401
from .geojson_loader import load_geojson, GeoFeature  # noqa: F401
from .usd_builder import UsdOverlayBuilder, ColorManager, parse_color  # noqa: F401
from . import triangulate  # noqa: F401
from .terrain import (  # noqa: F401
    HeightSampler, ConstantHeightSampler, CallableHeightSampler, MeshHeightSampler,
)
