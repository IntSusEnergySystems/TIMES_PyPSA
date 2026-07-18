"""Tests for .vdt topology parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from times_pypsa.topology import load_topology


def test_load_topology_basic(vdt_path: Path | None):
    if vdt_path is None:
        pytest.skip("No .vdt alongside reference .vd")
    topo = load_topology(vdt_path)
    # Toy fixture keeps thousands of links; full .vdt is larger still.
    assert topo.n_links > 500
    assert set(topo.links["direction"].unique()) <= {"IN", "OUT"}
    assert "RW" in set(topo.links["region"].unique())
    assert topo.links["process"].nunique() > 50
    assert topo.links["commodity"].nunique() > 50


def test_topology_key_set(vdt_path: Path | None):
    if vdt_path is None:
        pytest.skip("No .vdt")
    topo = load_topology(vdt_path)
    keys = topo.as_key_set()
    assert len(keys) == topo.n_links or len(keys) <= topo.n_links  # duplicates possible
    sample = topo.links.iloc[0]
    assert (
        str(sample["region"]),
        str(sample["process"]),
        str(sample["commodity"]),
        str(sample["direction"]),
    ) in keys


def test_direction_for_variable(vdt_path: Path | None):
    if vdt_path is None:
        pytest.skip("No .vdt")
    topo = load_topology(vdt_path)
    assert topo.direction_for_variable("VAR_FIn") == "IN"
    assert topo.direction_for_variable("VAR_FOut") == "OUT"
    assert topo.direction_for_variable("VAR_Cap") is None
