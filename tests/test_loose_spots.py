"""Metaforge loose-spot clustering + decode helpers."""

from __future__ import annotations

from arc_helper.map_ingest import cluster_loose_spots


def test_cluster_loose_spots_merges_nearby():
    # Three tight points + one far away → 2 clusters
    spots = [
        (100.0, 100.0),
        (105.0, 102.0),
        (110.0, 108.0),
        (5000.0, 5000.0),
    ]
    clusters = cluster_loose_spots(spots, radius=50.0)
    assert len(clusters) == 2
    counts = sorted(c[2] for c in clusters)
    assert counts == [1, 3]


def test_cluster_empty():
    assert cluster_loose_spots([]) == []
