"""Named TIMES → PyPSA transfers that are not demands or heat/transport.

Two numbers the Walloon model pins rather than rediscovering:

* **Industrial CO₂ capture** — ``STORAGEMININD`` ``VAR_FOut`` of ``CO2STOCK``
  (kt/a). TIMES names this a *minimum* storage process; pypsa-wal imposes it as
  a floor on BEWAL industry-CC (process + biomass + gas), not DAC.
* **Rooftop PV share** — ``VAR_Cap`` of the ERNW rooftop plants over rooftop +
  greenfield. ``VAR_Ncap`` is already inside ``VAR_Cap`` and must not be added
  (same finding as :mod:`times_pypsa.heat_softlink`).

Demand-side PV stock (``RSDPVELC`` / ``COMPVELC`` / ``INDPVELC``) and the solar
resource process ``ELCSOL00`` are excluded: the former overlaps the ERNW plants,
the latter is not a plant.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

ROOFTOP_PV_PROCESSES = frozenset(
    {
        "ERNW_PV-Buildings_SOL_N",
        "ERNW_PV-Large_Roof_SOL_N",
        "ERNW_PV-RES_Homes_SOL_N",
    }
)
UTILITY_PV_PROCESSES = frozenset({"ERNW_PV-GreenField_SOL_N"})

STORAGEMININD = "STORAGEMININD"
CO2STOCK = "CO2STOCK"


def _var_cap(raw: pd.DataFrame, processes: frozenset[str]) -> pd.Series:
    """Annual ``VAR_Cap`` (GW) per year for the given process codes."""
    cap = raw.loc[
        (raw["variable"] == "VAR_Cap") & raw["process_code"].isin(processes)
    ]
    if cap.empty:
        return pd.Series(dtype=float)
    return cap.groupby("year")["value"].sum()


def industrial_capture_kt(raw: pd.DataFrame) -> pd.DataFrame:
    """``STORAGEMININD`` captured CO₂, kt/a, by TIMES period.

    Sums ``VAR_FOut`` of ``CO2STOCK``. Multiple vintages in one period are
    added; timeslices, if any, are added too. Years with no row are omitted
    (the pin is then a no-op), not filled with zero.
    """
    rows = raw.loc[
        (raw["process_code"] == STORAGEMININD)
        & (raw["variable"] == "VAR_FOut")
        & (raw["commodity_code"] == CO2STOCK)
    ]
    if rows.empty:
        return pd.DataFrame(columns=["year", "kt"])
    out = (
        rows.groupby("year", as_index=False)["value"]
        .sum()
        .rename(columns={"value": "kt"})
    )
    out["year"] = out["year"].astype(int)
    return out.sort_values("year").reset_index(drop=True)


def pv_rooftop_share(raw: pd.DataFrame) -> pd.DataFrame:
    """Rooftop share of TIMES PV plant capacity (``VAR_Cap`` only).

    ``share = rooftop / (rooftop + utility)``. Years where both sides are
    zero are omitted.
    """
    rooftop = _var_cap(raw, ROOFTOP_PV_PROCESSES)
    utility = _var_cap(raw, UTILITY_PV_PROCESSES)
    years = sorted(set(rooftop.index) | set(utility.index))
    rows = []
    for year in years:
        r = float(rooftop.get(year, 0.0))
        u = float(utility.get(year, 0.0))
        total = r + u
        if total <= 0:
            continue
        rows.append(
            {
                "year": int(year),
                "share": r / total,
                "rooftop_gw": r,
                "utility_gw": u,
            }
        )
    return pd.DataFrame(rows)


def extract_industrial_capture(
    raw: pd.DataFrame, path: Path | str | None = None
) -> pd.DataFrame:
    """:func:`industrial_capture_kt`, optionally written to CSV."""
    out = industrial_capture_kt(raw)
    if path is not None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(path, index=False)
        logger.info("Wrote industrial capture to %s", path)
    return out


def extract_pv_rooftop_share(
    raw: pd.DataFrame, path: Path | str | None = None
) -> pd.DataFrame:
    """:func:`pv_rooftop_share`, optionally written to CSV."""
    out = pv_rooftop_share(raw)
    if path is not None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(path, index=False)
        logger.info("Wrote PV rooftop share to %s", path)
    return out
