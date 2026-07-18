"""Shared fixtures for TIMES_PyPSA tests."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_MAPPINGS = REPO_ROOT / "times_pypsa" / "mappings"

# Preferred example scenario (sibling pypsa-wal checkout)
CANDIDATE_VD = [
    Path("/home/sylvain/svn/pypsa-wal/TIMES_data/scen_corrige_251129_0112.vd"),
    REPO_ROOT.parent / "pypsa-wal" / "TIMES_data" / "scen_corrige_251129_0112.vd",
    REPO_ROOT / "data" / "scen_corrige_251129_0112.vd",
]


def _find_vd() -> Path | None:
    for p in CANDIDATE_VD:
        if p.exists():
            return p
    # Any .vd under data/
    data = REPO_ROOT / "data"
    if data.exists():
        vds = sorted(data.glob("*.vd"))
        if vds:
            return vds[0]
    return None


def _vdt_for(vd: Path) -> Path | None:
    cand = vd.with_suffix(".vdt")
    return cand if cand.exists() else None


@pytest.fixture(scope="session")
def vd_path() -> Path:
    path = _find_vd()
    if path is None:
        pytest.skip("No TIMES .vd file found for integration tests")
    return path


@pytest.fixture(scope="session")
def vdt_path(vd_path: Path) -> Path | None:
    return _vdt_for(vd_path)


@pytest.fixture(scope="session")
def mappings_dir() -> Path:
    return PACKAGE_MAPPINGS


@pytest.fixture(scope="session")
def times_model(vd_path: Path, vdt_path: Path | None, mappings_dir: Path):
    from times_pypsa.model import load_times_annual_flows
    from times_pypsa.pipeline import PipelineConfig

    return load_times_annual_flows(
        vd_path,
        mappings_dir,
        vdt_file=vdt_path,
        config=PipelineConfig(start_year=2025),
    )
