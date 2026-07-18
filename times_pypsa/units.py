"""Energy-unit helpers for QA / report output."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from times_pypsa.pipeline import PJ_TO_TWH

EnergyUnit = Literal["twh", "pj"]

DEFAULT_FLOW_THRESHOLD_TWH = PJ_TO_TWH  # ≈ 1 PJ
DEFAULT_FLOW_THRESHOLD_PJ = 1.0


def unit_label(units: EnergyUnit) -> str:
    return "TWh" if units == "twh" else "PJ"


def default_flow_threshold(units: EnergyUnit) -> float:
    return DEFAULT_FLOW_THRESHOLD_TWH if units == "twh" else DEFAULT_FLOW_THRESHOLD_PJ


def pj_to_display(value_pj: float, units: EnergyUnit) -> float:
    if units == "twh":
        return float(value_pj) * PJ_TO_TWH
    return float(value_pj)


def display_to_pj(value: float, units: EnergyUnit) -> float:
    if units == "twh":
        return float(value) / PJ_TO_TWH
    return float(value)


def convert_series_pj_to_display(series: pd.Series, units: EnergyUnit) -> pd.Series:
    if units == "twh":
        return series.astype(float) * PJ_TO_TWH
    return series.astype(float)


def convert_dataframe_energy(
    df: pd.DataFrame,
    columns: list[str],
    units: EnergyUnit,
) -> pd.DataFrame:
    """Convert selected PJ columns to the requested display unit."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = convert_series_pj_to_display(out[col], units)
    return out


def rename_energy_columns(
    df: pd.DataFrame,
    columns: list[str],
    units: EnergyUnit,
) -> pd.DataFrame:
    """Rename energy columns to the display unit label (e.g. value → TWh)."""
    out = df.copy()
    label = unit_label(units)
    rename: dict[str, str] = {}
    for col in columns:
        if col not in out.columns:
            continue
        if col == "value":
            rename[col] = label
        elif col == "pj":
            rename[col] = label
        elif col.endswith("_pj"):
            rename[col] = col[: -len("_pj")] + f"_{label.lower()}"
    return out.rename(columns=rename)


def prepare_energy_output(
    df: pd.DataFrame,
    columns: list[str],
    units: EnergyUnit,
) -> pd.DataFrame:
    """Convert PJ values and rename columns for CSV / HTML tables."""
    return rename_energy_columns(convert_dataframe_energy(df, columns, units), columns, units)


def format_energy(pj: float, units: EnergyUnit, *, decimals: int = 2) -> str:
    return f"{pj_to_display(pj, units):.{decimals}f} {unit_label(units)}"