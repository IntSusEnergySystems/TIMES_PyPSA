#!/usr/bin/env python3
"""Backward-compatible wrapper around the times_pypsa package."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_VD = REPO_ROOT / "data" / "scen_corrige_251129_0112.vd"
DEFAULT_OUT = REPO_ROOT / "output"
DEFAULT_YEAR = 2030


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    from times_pypsa import default_mappings_dir, export_all_horizons, generate_sankey

    mappings_dir = default_mappings_dir()
    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)

    export_all_horizons(
        vd_file=DEFAULT_VD,
        mappings_dir=mappings_dir,
        horizons=range(2021, 2051),
        out_dir=DEFAULT_OUT,
        emit="demands",
    )
    generate_sankey(
        vd_file=DEFAULT_VD,
        mappings_dir=mappings_dir,
        year=DEFAULT_YEAR,
        out_dir=DEFAULT_OUT,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
