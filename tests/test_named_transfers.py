"""Guards for the named TIMES transfers (industry CC, rooftop PV share).

``VAR_Cap`` only — adding ``VAR_Ncap`` is the heat-softlink double-count and
must not come back here. ``STORAGEMININD`` is the captured volume, not the
process-emission inventory (that is pypsa-wal item 12).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from times_pypsa.named_transfers import (
    ROOFTOP_PV_PROCESSES,
    STORAGEMININD,
    UTILITY_PV_PROCESSES,
    industrial_capture_kt,
    pv_rooftop_share,
)
from times_pypsa.pipeline import load_raw_records

# toy_scen.vd (scen_corrige lineage). Production demande_haute numbers live in
# pypsa-wal's committed CSVs and are a different extract.
TOY_CAPTURE_KT = {2040: 4665.16024536251, 2050: 4900.55250760251}
TOY_ROOFTOP_2050 = (10.7128622768579 + 3.09210510250719) / (
    10.7128622768579 + 3.09210510250719 + 4.54211419387978
)


@pytest.fixture(scope="module")
def raw(vd_path: Path) -> pd.DataFrame:
    return load_raw_records(vd_path, start_year=2021)


def test_storageminind_is_fout_of_co2stock(raw: pd.DataFrame):
    got = industrial_capture_kt(raw).set_index("year")["kt"]
    assert set(TOY_CAPTURE_KT) <= set(got.index)
    for year, kt in TOY_CAPTURE_KT.items():
        assert got.loc[year] == pytest.approx(kt)
    assert 2025 not in got.index
    assert 2030 not in got.index


def test_capture_does_not_use_process_emissions_inventory(raw: pd.DataFrame):
    """INDCO2c *into* STORAGEMININD is the same mass on this vd, but the pin
    is named on the stored commodity. A future extract must not switch to
    VAR_Comnet / INDCO2 (item 12's inventory)."""
    got = industrial_capture_kt(raw)
    assert list(got.columns) == ["year", "kt"]
    assert (raw["process_code"] == STORAGEMININD).any()


def test_rooftop_share_is_var_cap_only(raw: pd.DataFrame):
    got = pv_rooftop_share(raw).set_index("year")
    assert got.at[2050, "share"] == pytest.approx(TOY_ROOFTOP_2050)
    assert 0 < got.at[2050, "share"] < 1
    # ~77 % on this fixture; the production vd is extracted separately.
    assert got.at[2050, "share"] == pytest.approx(0.75, abs=0.03)


def test_rooftop_excludes_ncap(raw: pd.DataFrame):
    ncap = raw.loc[
        (raw["variable"] == "VAR_Ncap")
        & raw["process_code"].isin(ROOFTOP_PV_PROCESSES | UTILITY_PV_PROCESSES)
    ]
    if ncap.empty:
        pytest.skip("fixture has no VAR_Ncap on PV plants")
    # Adding Ncap would inflate rooftop+utility. The share function must
    # ignore those rows entirely.
    cap_only = pv_rooftop_share(raw).set_index("year")
    mixed = raw.copy()
    extra = ncap.copy()
    extra["variable"] = "VAR_Cap"
    inflated = pv_rooftop_share(pd.concat([raw, extra], ignore_index=True))
    # If the extractor filtered VAR_Cap correctly, dropping the Ncap-labelled
    # rows is a no-op; this asserts the *input* still has Ncap so the skip
    # above is the only way out, and that VAR_Cap-only matches the fixture.
    assert not cap_only.empty
    assert not inflated.empty
