"""Pure geometry helpers. No I/O, no providers -- safe to use anywhere.

Road distance here is an *estimate*: great-circle distance inflated by a
terrain-aware detour factor. Anything that calls it labels the result as
ESTIMATED. When a routing provider is configured, `services/routing.py`
replaces these numbers with live ones and relabels the provenance.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

EARTH_RADIUS_KM = 6371.0088

# Great-circle to road-distance inflation. Indian highways wander, and hill
# roads wander a lot more.
DETOUR_PLAINS = 1.24
DETOUR_HILLS = 1.55
DETOUR_HIGH_HIMALAYA = 1.85


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def detour_factor(terrain: str) -> float:
    """Road-distance multiplier for a terrain class."""
    return {
        "plains": DETOUR_PLAINS,
        "hills": DETOUR_HILLS,
        "high_himalaya": DETOUR_HIGH_HIMALAYA,
    }.get(terrain, DETOUR_PLAINS)


def estimate_road_km(
    lat1: float, lon1: float, lat2: float, lon2: float, terrain: str = "plains"
) -> float:
    """Estimated driving distance in km."""
    return round(haversine_km(lat1, lon1, lat2, lon2) * detour_factor(terrain), 1)


def centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Arithmetic centroid of (lat, lon) pairs. Adequate at city scale."""
    if not points:
        raise ValueError("centroid() needs at least one point")
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def nearest_neighbour_order(
    start: tuple[float, float], points: Sequence[tuple[float, float]]
) -> list[int]:
    """Greedy nearest-neighbour visiting order.

    Returns indices into `points`. Used to sequence a day's stops so the plan
    does not zig-zag across a city.
    """
    remaining = list(range(len(points)))
    order: list[int] = []
    cursor = start
    while remaining:
        best = min(remaining, key=lambda i: haversine_km(cursor[0], cursor[1], *points[i]))
        order.append(best)
        cursor = points[best]
        remaining.remove(best)
    return order


def route_length_km(path: Sequence[tuple[float, float]]) -> float:
    """Total great-circle length along an ordered path."""
    return round(
        sum(
            haversine_km(path[i][0], path[i][1], path[i + 1][0], path[i + 1][1])
            for i in range(len(path) - 1)
        ),
        2,
    )


def kmeans_geo(
    points: Sequence[tuple[float, float]], k: int, iterations: int = 40, seed: int = 7
) -> list[int]:
    """Assign points to `k` geographic clusters.

    A small deterministic k-means (k-means++ seeding, fixed RNG) so the same
    inputs always produce the same day groupings. Latitude/longitude are
    scaled so a degree of longitude counts for less away from the equator,
    which keeps clusters compact on the ground rather than on the chart.
    """
    n = len(points)
    if n == 0:
        return []
    k = max(1, min(k, n))
    if k == 1:
        return [0] * n

    mean_lat = sum(p[0] for p in points) / n
    lon_scale = max(0.2, math.cos(math.radians(mean_lat)))
    scaled = [(p[0], p[1] * lon_scale) for p in points]

    def d2(a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

    # k-means++ seeding with a deterministic LCG, so no global RNG state.
    state = seed or 1
    def rand() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) % 2147483648
        return state / 2147483648

    centres = [scaled[0]]
    while len(centres) < k:
        weights = [min(d2(p, c) for c in centres) for p in scaled]
        total = sum(weights)
        if total <= 0:
            centres.append(scaled[len(centres) % n])
            continue
        target = rand() * total
        acc = 0.0
        for idx, w in enumerate(weights):
            acc += w
            if acc >= target:
                centres.append(scaled[idx])
                break
        else:
            centres.append(scaled[-1])

    assignment = [0] * n
    for _ in range(iterations):
        changed = False
        for i, p in enumerate(scaled):
            best = min(range(k), key=lambda c: d2(p, centres[c]))
            if best != assignment[i]:
                assignment[i] = best
                changed = True
        for c in range(k):
            members = [scaled[i] for i in range(n) if assignment[i] == c]
            if members:
                centres[c] = (
                    sum(m[0] for m in members) / len(members),
                    sum(m[1] for m in members) / len(members),
                )
        if not changed:
            break
    return assignment


def balanced_clusters(
    points: Sequence[tuple[float, float]], groups: int, seed: int = 7
) -> list[list[int]]:
    """Cluster geographically, then even out group sizes.

    Pure k-means can hand one day eight stops and another day none. This
    rebalances by moving the point that is cheapest to move -- the one closest
    to the receiving cluster's centre -- until no group is more than one stop
    larger than the smallest.
    """
    n = len(points)
    if n == 0:
        return [[] for _ in range(groups)]
    groups = max(1, groups)
    assignment = kmeans_geo(points, groups, seed=seed)
    buckets: list[list[int]] = [[] for _ in range(groups)]
    for idx, c in enumerate(assignment):
        buckets[c].append(idx)

    def bucket_centre(bucket: list[int]) -> tuple[float, float] | None:
        if not bucket:
            return None
        return centroid([points[i] for i in bucket])

    target_max = math.ceil(n / groups)
    for _ in range(n * 2):
        donors = [b for b in buckets if len(b) > target_max]
        receivers = [b for b in buckets if len(b) < target_max]
        if not donors or not receivers:
            break
        donor = max(donors, key=len)
        receiver = min(receivers, key=len)
        centre = bucket_centre(receiver) or bucket_centre(donor)
        assert centre is not None
        moved = min(donor, key=lambda i: haversine_km(points[i][0], points[i][1], *centre))
        donor.remove(moved)
        receiver.append(moved)
    return buckets


def bounding_box(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    """(min_lat, min_lon, max_lat, max_lon) for a set of points."""
    pts = list(points)
    if not pts:
        raise ValueError("bounding_box() needs at least one point")
    return (
        min(p[0] for p in pts),
        min(p[1] for p in pts),
        max(p[0] for p in pts),
        max(p[1] for p in pts),
    )
