"""地形ドレープ用の高さサンプラ。

USD のローカル座標 (X=東, Z=北, Y=高さ) において、(x, z) から地形面の Y を返す。
サンプリングのコアは USD 非依存の純ロジックで、単体テスト可能。

実装:
  * ConstantHeightSampler : 一定高さ (既定の振る舞い)
  * CallableHeightSampler : 任意の関数 f(x, z) -> y
  * MeshHeightSampler     : 三角形群に対し XZ 平面で内包判定 + バリセントリック補間。
                            多数三角形向けに XZ 一様グリッドで空間インデックス化。
`MeshHeightSampler.from_prim()` は USD メッシュ prim からワールド座標で三角形を構築する
(こちらのみ pxr 依存)。
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

Pt3 = Tuple[float, float, float]
Tri = Tuple[Pt3, Pt3, Pt3]

_EPS = 1e-9


class HeightSampler:
    """(x, z) -> 高さ Y。範囲外などで不明な場合は None を返す。"""

    def sample(self, x: float, z: float) -> Optional[float]:
        raise NotImplementedError


class ConstantHeightSampler(HeightSampler):
    def __init__(self, y: float = 0.0) -> None:
        self._y = y

    def sample(self, x: float, z: float) -> Optional[float]:
        return self._y


class CallableHeightSampler(HeightSampler):
    def __init__(self, fn: Callable[[float, float], Optional[float]]) -> None:
        self._fn = fn

    def sample(self, x: float, z: float) -> Optional[float]:
        return self._fn(x, z)


def _bary_y(px: float, pz: float, a: Pt3, b: Pt3, c: Pt3) -> Optional[float]:
    """三角形 abc の XZ 内に (px,pz) があれば Y をバリセントリック補間して返す。"""
    det = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
    if abs(det) < _EPS:
        return None  # 退化三角形
    u = ((b[2] - c[2]) * (px - c[0]) + (c[0] - b[0]) * (pz - c[2])) / det
    v = ((c[2] - a[2]) * (px - c[0]) + (a[0] - c[0]) * (pz - c[2])) / det
    w = 1.0 - u - v
    tol = 1e-7
    if u < -tol or v < -tol or w < -tol:
        return None  # 三角形外
    return u * a[1] + v * b[1] + w * c[1]


class MeshHeightSampler(HeightSampler):
    """三角形メッシュへの鉛直サンプリング。"""

    def __init__(self, triangles: Sequence[Tri], grid: int = 64) -> None:
        self._tris: List[Tri] = [t for t in triangles if _tri_valid(t)]
        self._grid = max(1, int(grid))
        self._build_index()

    def _build_index(self) -> None:
        if not self._tris:
            self._minx = self._minz = 0.0
            self._cellx = self._cellz = 1.0
            self._buckets = {}
            return
        xs = [p[0] for t in self._tris for p in t]
        zs = [p[2] for t in self._tris for p in t]
        self._minx, maxx = min(xs), max(xs)
        self._minz, maxz = min(zs), max(zs)
        self._cellx = max((maxx - self._minx) / self._grid, _EPS)
        self._cellz = max((maxz - self._minz) / self._grid, _EPS)
        self._buckets: dict = {}
        for ti, t in enumerate(self._tris):
            tx = [p[0] for p in t]
            tz = [p[2] for p in t]
            i0 = self._cx(min(tx))
            i1 = self._cx(max(tx))
            j0 = self._cz(min(tz))
            j1 = self._cz(max(tz))
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    self._buckets.setdefault((i, j), []).append(ti)

    def _cx(self, x: float) -> int:
        return min(self._grid - 1, max(0, int((x - self._minx) / self._cellx)))

    def _cz(self, z: float) -> int:
        return min(self._grid - 1, max(0, int((z - self._minz) / self._cellz)))

    def sample(self, x: float, z: float) -> Optional[float]:
        if not self._tris:
            return None
        cell = (self._cx(x), self._cz(z))
        candidates = self._buckets.get(cell)
        if not candidates:
            return None
        # 複数三角形に該当した場合は最大 Y (上面) を採用
        best: Optional[float] = None
        for ti in candidates:
            a, b, c = self._tris[ti]
            y = _bary_y(x, z, a, b, c)
            if y is not None and (best is None or y > best):
                best = y
        return best

    @property
    def triangle_count(self) -> int:
        return len(self._tris)

    # ------------------------------------------------------------------ #
    @classmethod
    def from_points_indices(
        cls,
        points: Sequence[Pt3],
        face_vertex_counts: Sequence[int],
        face_vertex_indices: Sequence[int],
        grid: int = 64,
    ) -> "MeshHeightSampler":
        """USD メッシュの points / faceVertexCounts / faceVertexIndices から構築。

        n>3 の面は扇状に三角形へ分割する。
        """
        tris: List[Tri] = []
        k = 0
        for cnt in face_vertex_counts:
            face = [points[face_vertex_indices[k + m]] for m in range(cnt)]
            k += cnt
            for i in range(1, cnt - 1):
                tris.append((face[0], face[i], face[i + 1]))
        return cls(tris, grid=grid)

    @classmethod
    def from_prim(cls, stage, prim_path: str, grid: int = 64) -> Optional["MeshHeightSampler"]:
        """USD ステージ上のメッシュ prim からワールド座標で構築 (pxr 依存)。"""
        from pxr import UsdGeom, Usd

        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid() or not prim.IsA(UsdGeom.Mesh):
            return None
        mesh = UsdGeom.Mesh(prim)
        pts = mesh.GetPointsAttr().Get()
        fvc = mesh.GetFaceVertexCountsAttr().Get()
        fvi = mesh.GetFaceVertexIndicesAttr().Get()
        if not pts or not fvc or not fvi:
            return None
        # ローカル -> ワールド変換
        xf = UsdGeom.Xformable(prim)
        m = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world = []
        for p in pts:
            wp = m.Transform(p)
            world.append((float(wp[0]), float(wp[1]), float(wp[2])))
        return cls.from_points_indices(world, list(fvc), list(fvi), grid=grid)


def _tri_valid(t: Tri) -> bool:
    det = (t[1][2] - t[2][2]) * (t[0][0] - t[2][0]) + \
          (t[2][0] - t[1][0]) * (t[0][2] - t[2][2])
    return abs(det) > _EPS
