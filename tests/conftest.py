"""Shared fixtures for TIMES_PyPSA tests.

Integration tests prefer the toy fixtures under ``tests/fixtures/`` (pre-aggregated
soft-link years). Set ``TIMES_PYPSA_FULL_DATA=1`` to exercise the full scenario
``.vd`` instead.

``generate_qa_report`` tests use the leaner ``toy_qa.*`` pair (two years, top
export-category neighbourhood) unless full data mode is enabled.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_MAPPINGS = REPO_ROOT / "data"
TOY_VD = REPO_ROOT / "tests" / "fixtures" / "toy_scen.vd"
TOY_VDT = REPO_ROOT / "tests" / "fixtures" / "toy_scen.vdt"
TOY_QA_VD = REPO_ROOT / "tests" / "fixtures" / "toy_qa.vd"
TOY_QA_VDT = REPO_ROOT / "tests" / "fixtures" / "toy_qa.vdt"

# Full scenario candidates (sibling pypsa-wal checkout or local data/)
CANDIDATE_VD = [
    Path("/home/sylvain/svn/pypsa-wal/TIMES_data/scen_corrige_251129_0112.vd"),
    REPO_ROOT.parent / "pypsa-wal" / "TIMES_data" / "scen_corrige_251129_0112.vd",
    REPO_ROOT / "data" / "scen_corrige_251129_0112.vd",
]


def _use_full_data() -> bool:
    return os.environ.get("TIMES_PYPSA_FULL_DATA", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _find_full_vd() -> Path | None:
    for p in CANDIDATE_VD:
        if p.exists():
            return p
    data = REPO_ROOT / "data"
    if data.exists():
        vds = sorted(p for p in data.glob("*.vd") if p.name != "demos_004_0209.vd")
        if vds:
            return vds[0]
    return None


def _find_vd() -> Path | None:
    if not _use_full_data() and TOY_VD.exists():
        return TOY_VD
    return _find_full_vd()


def _vdt_for(vd: Path) -> Path | None:
    if vd == TOY_VD and TOY_VDT.exists():
        return TOY_VDT
    if vd == TOY_QA_VD and TOY_QA_VDT.exists():
        return TOY_QA_VDT
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
def qa_vd_path(vd_path: Path) -> Path:
    """Lean fixture for generate_qa_report tests (falls back to vd_path)."""
    if not _use_full_data() and TOY_QA_VD.exists():
        return TOY_QA_VD
    return vd_path


@pytest.fixture(scope="session")
def qa_vdt_path(qa_vd_path: Path) -> Path | None:
    return _vdt_for(qa_vd_path)


@pytest.fixture(scope="session")
def mappings_dir() -> Path:
    return DATA_MAPPINGS


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


@pytest.fixture(scope="session")
def qa_model(qa_vd_path: Path, qa_vdt_path: Path | None, mappings_dir: Path):
    from times_pypsa.model import load_times_annual_flows
    from times_pypsa.pipeline import PipelineConfig

    return load_times_annual_flows(
        qa_vd_path,
        mappings_dir,
        vdt_file=qa_vdt_path,
        config=PipelineConfig(start_year=2025),
    )
