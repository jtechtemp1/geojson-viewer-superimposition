"""多角形の三角形分割 (穴あり・凹対応)。

USD 非依存の純ロジック。Polygon は XZ 平面 (USD の水平面) 上にあるとみなし、
geometry 判定は 2D (x, z) で行いつつ、出力頂点は元の 3D 座標を保持する。

手順:
  1. 外周リングを CCW、穴リングを CW に揃える。
  2. 各穴を外周へブリッジ (hole bridging) して 1 本の単純多角形へ統合。
  3. ear-clipping で三角形分割。

ブリッジは O'Rourke の可視頂点法 (右方向レイキャスト + reflex 頂点チェック) を用いる。
計算量は O(n^2) 程度で、MVP 規模の地物には十分。
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

# 3D 頂点 (x, y, z)
Pt3 = Tuple[float, float, float]

_EPS = 1e-9


def _x(p: Pt3) -> float:
    return p[0]


def _z(p: Pt3) -> float:
    return p[2]


def signed_area_xz(ring: Sequence[Pt3]) -> float:
    """XZ 平面での符号付き面積 (CCW で正)。"""
    s = 0.0
    n = len(ring)
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        s += a[0] * b[2] - b[0] * a[2]
    return 0.5 * s


def _area2(a: Pt3, b: Pt3, c: Pt3) -> float:
    """三角形 abc の 2 倍符号付き面積 (XZ, CCW で正)。"""
    return (b[0] - a[0]) * (c[2] - a[2]) - (c[0] - a[0]) * (b[2] - a[2])


def _same_xz(a: Pt3, b: Pt3) -> bool:
    return abs(a[0] - b[0]) <= _EPS and abs(a[2] - b[2]) <= _EPS


def _point_in_tri(p: Pt3, a: Pt3, b: Pt3, c: Pt3) -> bool:
    """p が三角形 abc の内部 (辺上含む) にあるか。"""
    d1 = _area2(a, b, p)
    d2 = _area2(b, c, p)
    d3 = _area2(c, a, p)
    has_neg = (d1 < -_EPS) or (d2 < -_EPS) or (d3 < -_EPS)
    has_pos = (d1 > _EPS) or (d2 > _EPS) or (d3 > _EPS)
    return not (has_neg and has_pos)


def _strip_closing(ring: Sequence[Pt3]) -> List[Pt3]:
    r = list(ring)
    if len(r) >= 2 and _same_xz(r[0], r[-1]):
        r = r[:-1]
    return r


def _clean_ring(ring: Sequence[Pt3]) -> List[Pt3]:
    """閉じ重複・連続重複点を除去する (退化ケースの前処理)。"""
    r = _strip_closing(ring)
    out: List[Pt3] = []
    for p in r:
        if not out or not _same_xz(out[-1], p):
            out.append(p)
    # 先頭と末尾が一致する場合も除去
    while len(out) >= 2 and _same_xz(out[0], out[-1]):
        out.pop()
    return out


# --------------------------------------------------------------------------- #
# 穴ブリッジ
# --------------------------------------------------------------------------- #
def _find_bridge_index(poly: List[Pt3], m: Pt3) -> int:
    """穴の最右頂点 m から見える外周(poly)頂点のインデックスを返す。"""
    n = len(poly)
    mx, mz = m[0], m[2]

    # 右方向レイ (z=mz, x>mx) と交差する辺を探し、最も近い交点を持つ辺を選ぶ
    best_x = float("inf")
    best_edge = -1
    best_ix = 0.0
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        az, bz = a[2], b[2]
        # 辺が水平線 z=mz を跨ぐか (端点の扱いを片側だけ含める)
        if (az <= mz < bz) or (bz <= mz < az):
            t = (mz - az) / (bz - az)
            ix = a[0] + t * (b[0] - a[0])
            if ix >= mx - _EPS and ix < best_x:
                best_x = ix
                best_edge = i
                best_ix = ix
    if best_edge < 0:
        # フォールバック: 最も x が大きい頂点
        return max(range(n), key=lambda i: poly[i][0])

    a = poly[best_edge]
    b = poly[(best_edge + 1) % n]
    # 交点が頂点に一致するなら、その頂点が可視
    intersection: Pt3 = (best_ix, m[1], mz)
    # 候補 P: 辺の端点のうち x が大きい方
    p_idx = best_edge if a[0] >= b[0] else (best_edge + 1) % n
    p = poly[p_idx]

    # 三角形 (m, intersection, p) 内の reflex 頂点をチェックし、
    # あればそのうち m から見て角度最小のものへブリッジ
    best_reflex = -1
    best_angle = float("inf")
    best_dist = float("inf")
    for i in range(n):
        if i == p_idx:
            continue
        q = poly[i]
        prev = poly[(i - 1) % n]
        nxt = poly[(i + 1) % n]
        # reflex 判定 (CCW 外周で内角 > 180°)
        if _area2(prev, q, nxt) >= -_EPS:
            continue
        if not _point_in_tri(q, m, intersection, p):
            continue
        dx = q[0] - mx
        dz = q[2] - mz
        # +x 軸からの角度 (|tan|)。x>0 前提
        angle = abs(dz) / dx if dx > _EPS else float("inf")
        dist = dx * dx + dz * dz
        if angle < best_angle - _EPS or (abs(angle - best_angle) <= _EPS and dist < best_dist):
            best_angle = angle
            best_dist = dist
            best_reflex = i
    if best_reflex >= 0:
        return best_reflex
    return p_idx


def eliminate_holes(outer: List[Pt3], holes: List[List[Pt3]]) -> List[Pt3]:
    """穴を外周へブリッジし、1 本の単純多角形 (頂点列) を返す。"""
    poly = list(outer)
    # 穴を最右頂点の x 降順で処理
    holes_sorted = sorted(
        holes, key=lambda h: max(p[0] for p in h), reverse=True
    )
    for hole in holes_sorted:
        # 最右頂点
        m_local = max(range(len(hole)), key=lambda i: hole[i][0])
        m = hole[m_local]
        t = _find_bridge_index(poly, m)
        hole_rot = hole[m_local:] + hole[:m_local]      # M から一周
        insertion = hole_rot + [hole_rot[0]] + [poly[t]]  # ...M, (M複製), (P複製)
        poly = poly[:t + 1] + insertion + poly[t + 1:]
    return poly


# --------------------------------------------------------------------------- #
# ear clipping
# --------------------------------------------------------------------------- #
def _earclip(poly: List[Pt3]) -> List[Tuple[int, int, int]]:
    """単純多角形 (CCW) を三角形分割。poly のインデックスで三角形を返す。"""
    n = len(poly)
    if n < 3:
        return []
    # CCW 前提に整える
    idx = list(range(n))
    if signed_area_xz(poly) < 0:
        idx.reverse()

    tris: List[Tuple[int, int, int]] = []
    guard = 0
    max_guard = 3 * n + 10
    while len(idx) > 3 and guard < max_guard:
        guard += 1
        ear_i = -1
        m = len(idx)
        for i in range(m):
            ip = idx[(i - 1) % m]
            ic = idx[i]
            inx = idx[(i + 1) % m]
            a, b, c = poly[ip], poly[ic], poly[inx]
            # 凸頂点か (CCW)
            if _area2(a, b, c) <= _EPS:
                continue
            # 他頂点を含まないか
            is_ear = True
            for j in idx:
                if j in (ip, ic, inx):
                    continue
                q = poly[j]
                if _same_xz(q, a) or _same_xz(q, b) or _same_xz(q, c):
                    continue
                if _point_in_tri(q, a, b, c):
                    is_ear = False
                    break
            if is_ear:
                ear_i = i
                break

        if ear_i < 0:
            # フォールバック: 耳が見つからない (自己交差/退化)。
            # 面積が最大の凸頂点を強制的にクリップして前進する。
            best = -1
            best_area = _EPS
            for i in range(m):
                a = poly[idx[(i - 1) % m]]
                b = poly[idx[i]]
                c = poly[idx[(i + 1) % m]]
                ar = _area2(a, b, c)
                if ar > best_area:
                    best_area = ar
                    best = i
            if best < 0:
                # 凸頂点も無い (ほぼ退化) → 中断
                break
            ear_i = best

        ip = idx[(ear_i - 1) % len(idx)]
        ic = idx[ear_i]
        inx = idx[(ear_i + 1) % len(idx)]
        tris.append((ip, ic, inx))
        del idx[ear_i]

    if len(idx) == 3:
        # 残り三角形 (面積があれば採用)
        if abs(_area2(poly[idx[0]], poly[idx[1]], poly[idx[2]])) > _EPS:
            tris.append((idx[0], idx[1], idx[2]))
    return tris


def triangulate(rings: List[Sequence[Pt3]]) -> Tuple[List[Pt3], List[Tuple[int, int, int]]]:
    """穴あり多角形を三角形分割する。

    Args:
        rings: [外周, 穴1, 穴2, ...]。各リングは (x,y,z) の頂点列
               (末尾の閉じ重複は自動除去)。
    Returns:
        (points, triangles):
            points    = 三角形分割に用いた頂点列 (ブリッジで複製を含む)
            triangles = points へのインデックス三つ組のリスト
    """
    if not rings:
        return [], []
    outer = _clean_ring(rings[0])
    # 外周が退化 (3 頂点未満 / 面積ほぼ 0) なら何も出さない
    if len(outer) < 3 or abs(signed_area_xz(outer)) <= _EPS:
        return [], []
    # 外周は CCW
    if signed_area_xz(outer) < 0:
        outer = list(reversed(outer))

    holes: List[List[Pt3]] = []
    for h in rings[1:]:
        hh = _clean_ring(h)
        if len(hh) < 3 or abs(signed_area_xz(hh)) <= _EPS:
            continue  # 退化した穴は無視
        # 穴は CW
        if signed_area_xz(hh) > 0:
            hh = list(reversed(hh))
        holes.append(hh)

    poly = eliminate_holes(outer, holes) if holes else outer
    tris = _earclip(poly)
    return poly, tris
