import numpy as np
import pytest

from server.geo import LocalProjection, adjacency, ring_area, ring_centroid, voronoi_cells


def rect(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


def test_area_and_centroid():
    r = rect(0, 0, 100, 30)
    assert ring_area(r) == pytest.approx(3000.0)
    assert ring_centroid(r) == pytest.approx([50.0, 15.0])


def test_projection_round_trip():
    proj = LocalProjection(129.34, 36.10)
    xy = np.array([[0.0, 0.0], [500.0, -250.0]])
    back = proj.to_xy(proj.to_lonlat(xy))
    assert back == pytest.approx(xy, abs=1e-6)


def test_touching_parcels_share_bund():
    edges = adjacency([rect(0, 0, 100, 30), rect(0, 30, 100, 60)])
    assert len(edges) == 1
    i, j, length, touch = edges[0]
    assert (i, j) == (0, 1)
    assert touch is True
    assert length == pytest.approx(100.0, rel=0.02)


def test_parcels_across_farm_road_are_near_not_touching():
    edges = adjacency([rect(0, 0, 100, 30), rect(106, 0, 206, 30)])
    assert len(edges) == 1
    _, _, length, touch = edges[0]
    assert touch is False
    assert length == pytest.approx(30.0, rel=0.05)


def test_diagonal_corner_is_not_adjacent():
    # 수로(2m)를 사이에 둔 대각선 필지: 모서리만 가깝다.
    edges = adjacency([rect(0, 0, 100, 30), rect(102, 30, 202, 60)])
    assert edges == []


def test_far_parcels_not_adjacent():
    assert adjacency([rect(0, 0, 10, 10), rect(50, 0, 60, 10)]) == []


def test_voronoi_cells_tile_rectangle():
    rng = np.random.default_rng(0)
    pts = rng.uniform([0, 0], [100, 80], size=(12, 2))
    cells = voronoi_cells(pts, (0, 0, 100, 80))
    assert sum(ring_area(c) for c in cells) == pytest.approx(8000.0, rel=1e-6)
