"""Heating soft-link payload: TIMES annual heat output and stock per technology.

This module carries the *technology* axis of the Walloon heating soft-link, the
one the demand transfer throws away. ``wallon_demands_{year}.csv`` already holds
the per-appliance useful-heat totals as 23 child categories, but nothing tells
pypsa-wal which PyPSA carrier each of them corresponds to, and the raw TIMES
labels are not a taxonomy PyPSA can match on. Two artefacts fix that:

.. note::

   **This branch is `heat-softlink-option-b`, and it is identical to
   `heat-softlink-option-c`.** The two pypsa-wal branches implement different
   *mechanisms* for imposing the mix — option C an annual energy constraint,
   option B' a pinned hourly profile — but they consume the **same payload**: the
   ``share`` column of ``heating_targets_{year}.csv``. Nothing in this library
   had to change for option B'. The branch exists so that each pypsa-wal branch
   has a matching library branch to pin against, and so a future divergence has
   somewhere to land. See ``docs/heat_softlink_option_comparison.md`` in
   pypsa-wal.

``heating_targets_{year}.csv``
    Annual heat output per **constraint group** — the right-hand side of the
    Option-C energy-mix constraint (``docs/times-heating-softlink-options.md``
    §6 in pypsa-wal). Groups are defined on the technology axis only and summed
    over ``rural`` + ``urban decentral`` + ``services``, because the TIMES
    urban/rural label is a per-process labelling convention rather than a TIMES
    result (§1.2(d)) and the residential/services split does not survive
    ``cluster_heat_buses``. Summing over both cancels the artefact.

``heating_capacities_{year}.csv``
    Installed heat-generation capacity in **MW thermal output**, keyed so that
    pypsa-wal can drop it straight into ``existing_heating_distribution``, whose
    cells are in the same unit.

The group definition — including every arbitrary mapping choice and its
justification — lives in ``data/heat_softlink_groups.csv`` so that it is
reviewable data rather than buried code.

Two defects of the previous ``extract_heating_capacities`` are fixed here:

* it summed ``VAR_Cap + VAR_Ncap``, and ``VAR_Ncap`` (new capacity built in the
  period) is already inside ``VAR_Cap`` — verified on this ``.vd``:
  ``VAR_Ncap <= VAR_Cap`` for all 100 % of (process, year) pairs, and
  ``VAR_Cap`` grows by exactly ``VAR_Ncap`` less retirements. The double count
  was +36 % to +65 % on the 2050 gas and heat-pump rows.
* it selected rows with the regex ``boiler|heat pump|stove|thermal|heater``,
  which admitted ``Geothermal (IND)`` (an industrial process) and
  ``Thermal Public - Retrofitting CCGT CCS`` (a 1 740 MW power plant) while
  dropping ``District heating``. Selection is now by explicit label list.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from times_pypsa.pipeline import PJ_TO_TWH, default_mappings_dir

logger = logging.getLogger(__name__)

#: ``sense`` value marking a group that is exported for accounting but must not
#: be turned into a constraint (district heating — see the diagnosis §6 *Scope*).
SENSE_NONE = "none"

_VALID_SENSES = {">=", "<=", "==", SENSE_NONE}

GROUPS_FILE_NAME = "heat_softlink_groups.csv"

_REQUIRED_GROUP_COLUMNS = (
    "group",
    "scope",
    "pypsa_carriers",
    "pypsa_component",
    "sense",
    "times_categories",
    "times_process_labels",
    "pypsa_stock_technology",
)

#: PyPSA-Eur ``existing_heating_distribution`` technology columns. A
#: ``pypsa_stock_technology`` outside this set is a typo, not a modelling choice.
PYPSA_STOCK_TECHNOLOGIES = frozenset(
    {
        "gas boiler",
        "oil boiler",
        "resistive heater",
        "air heat pump",
        "ground heat pump",
        "biomass boiler",
    }
)


def default_groups_file() -> Path:
    """Path of the bundled group definition."""
    return default_mappings_dir() / GROUPS_FILE_NAME


def resolve_groups_file(mappings_dir: Path | str | None = None) -> Path:
    """Group definition inside ``mappings_dir``, else the bundled one.

    A coupling bundle carries its own copy of the mapping CSVs so a run is
    reproducible from the bundle alone; an older bundle predating this file falls
    back to the installed default rather than failing.
    """
    if mappings_dir:
        candidate = Path(mappings_dir)
        if candidate.is_dir():
            candidate = candidate / GROUPS_FILE_NAME
        if candidate.exists():
            return candidate
        logger.info(
            "No %s in %s; using the bundled group definition.",
            GROUPS_FILE_NAME,
            mappings_dir,
        )
    return default_groups_file()


def _split_cell(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return [v.strip() for v in str(value).split(";") if v.strip()]


def load_heat_groups(groups_file: Path | str | None = None) -> pd.DataFrame:
    """Read and validate ``heat_softlink_groups.csv``.

    Returns one row per (group, mapping-decision) with the semicolon-separated
    cells parsed into lists. A group may appear on several rows so that each
    arbitrary assignment carries its own ``note``; the energy target is the sum
    over its rows.
    """
    path = Path(groups_file) if groups_file else default_groups_file()
    df = pd.read_csv(path)
    missing = [c for c in _REQUIRED_GROUP_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    df["group"] = df["group"].astype(str).str.strip()
    df["scope"] = df["scope"].astype(str).str.strip()
    df["sense"] = df["sense"].astype(str).str.strip()
    df["pypsa_component"] = (
        df["pypsa_component"].fillna("").astype(str).str.strip()
    )
    df["pypsa_stock_technology"] = (
        df["pypsa_stock_technology"].fillna("").astype(str).str.strip()
    )
    df["carriers"] = df["pypsa_carriers"].map(_split_cell)
    df["categories"] = df["times_categories"].map(_split_cell)
    df["labels"] = df["times_process_labels"].map(_split_cell)

    bad_sense = sorted(set(df["sense"]) - _VALID_SENSES)
    if bad_sense:
        raise ValueError(
            f"{path}: unknown constraint sense(s) {bad_sense}; "
            f"valid values are {sorted(_VALID_SENSES)}"
        )
    bad_tech = sorted(
        {
            t
            for t in df["pypsa_stock_technology"]
            if t and t not in PYPSA_STOCK_TECHNOLOGIES
        }
    )
    if bad_tech:
        raise ValueError(
            f"{path}: pypsa_stock_technology {bad_tech} is not an "
            f"existing_heating_distribution column {sorted(PYPSA_STOCK_TECHNOLOGIES)}"
        )
    # A category counted under two groups would double-count the mix.
    seen: dict[str, str] = {}
    for _, row in df.iterrows():
        for cat in row["categories"]:
            if cat in seen and seen[cat] != row["group"]:
                raise ValueError(
                    f"{path}: TIMES category {cat!r} is claimed by both "
                    f"{seen[cat]!r} and {row['group']!r}"
                )
            seen[cat] = row["group"]
    # Ditto for the capacity axis.
    seen_labels: dict[str, str] = {}
    for _, row in df.iterrows():
        for label in row["labels"]:
            if label in seen_labels:
                raise ValueError(
                    f"{path}: TIMES process label {label!r} appears twice "
                    f"({seen_labels[label]!r} and {row['group']!r})"
                )
            seen_labels[label] = row["group"]
    # One sense / component / carrier set per group, whatever the row count.
    for group, sub in df.groupby("group"):
        for col in ("sense", "scope", "pypsa_carriers", "pypsa_component"):
            values = set(sub[col].fillna(""))
            if len(values) > 1:
                raise ValueError(
                    f"{path}: group {group!r} declares conflicting {col}: {sorted(values)}"
                )
    return df


def heat_group_targets(
    demands_df: pd.DataFrame,
    horizon: int,
    groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Aggregate the ``wallon_demands`` child categories into constraint groups.

    ``demands_df`` is the frame written by
    :func:`times_pypsa.pipeline.extract_demands_for_horizon` (columns
    ``category``, ``TWh``, ``PJ``). A category the extraction did not produce is
    treated as 0 TWh with a warning, the same convention as pypsa-wal's
    ``times_demand_twh``: an older export must not break the build.
    """
    groups = load_heat_groups() if groups is None else groups
    demands = demands_df.set_index("category") if "category" in demands_df else demands_df
    known = set(demands.index)

    rows = []
    for group, sub in groups.groupby("group", sort=False):
        categories = [c for row in sub["categories"] for c in row]
        missing = [c for c in categories if c not in known]
        if missing:
            logger.warning(
                "Heat group %r: TIMES categories %s absent from the export; "
                "treated as 0 TWh.",
                group,
                missing,
            )
        twh = float(demands["TWh"].reindex(categories).fillna(0.0).sum())
        first = sub.iloc[0]
        rows.append(
            {
                "year": horizon,
                "group": group,
                "scope": first["scope"],
                "pypsa_component": first["pypsa_component"],
                "pypsa_carriers": first["pypsa_carriers"],
                "sense": first["sense"],
                "TWh": twh,
                "PJ": twh / PJ_TO_TWH,
                "times_categories": ";".join(categories),
            }
        )

    out = pd.DataFrame(rows)
    constrained = (out["scope"] == "decentral") & (out["sense"] != SENSE_NONE)
    total = out.loc[constrained, "TWh"].sum()
    out["share"] = 0.0
    if total > 0:
        out.loc[constrained, "share"] = out.loc[constrained, "TWh"] / total
    out["constrained"] = constrained
    return out[
        [
            "year",
            "group",
            "scope",
            "constrained",
            "pypsa_component",
            "pypsa_carriers",
            "sense",
            "TWh",
            "PJ",
            "share",
            "times_categories",
        ]
    ]


def extract_heating_targets(
    demands_df: pd.DataFrame,
    horizon: int,
    heating_targets_path: Path | str,
    groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """:func:`heat_group_targets`, written to CSV."""
    heating_targets_path = Path(heating_targets_path)
    heating_targets_path.parent.mkdir(parents=True, exist_ok=True)
    out = heat_group_targets(demands_df, horizon, groups)
    out.to_csv(heating_targets_path, index=False)
    logger.info("Saved heating targets to %s", heating_targets_path)
    return out


def _stock_placement(label: str) -> tuple[str, str]:
    """(sector, TIMES urban/rural label) of an ``Aggregation Level 2`` heat label.

    The TIMES urban/rural label is *reported*, never *used* to place capacity:
    pypsa-wal decides the split (see ``docs/heat_soft_linking.md``). Reporting it
    keeps the arbitrary convention visible in the artefact instead of hiding it
    inside an aggregation.
    """
    low = label.lower()
    if low.startswith("residential"):
        sector = "residential"
    elif low.startswith("commercial"):
        sector = "services"
    else:
        sector = "district"
    if "district heating" in low or "heat exchanger" in low:
        return sector, "urban central"
    if " rural" in low:
        return sector, "rural"
    return sector, "urban decentral"


def heating_capacities(
    raw_flows_df: pd.DataFrame,
    processes_df: pd.DataFrame,
    horizon: int,
    groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Installed building-heat capacity for one horizon, in MW thermal output.

    ``VAR_Cap`` only — see the module docstring for why ``VAR_Ncap`` must not be
    added — and restricted to the ``Aggregation Level 2`` labels the group file
    lists, so no industrial or power-sector process can leak in.
    """
    groups = load_heat_groups() if groups is None else groups

    mapping = processes_df.copy()
    tech_col = (
        "Technology (Process)"
        if "Technology (Process)" in mapping.columns
        else "Process"
    )
    map_dict = mapping.set_index(tech_col)["Aggregation Level 2"].to_dict()

    caps = raw_flows_df.loc[
        (raw_flows_df["year"] == horizon) & (raw_flows_df["variable"] == "VAR_Cap")
    ].copy()
    caps["label"] = caps["process_code"].map(map_dict)
    # TIMES capacity is in GW; existing_heating_distribution is in MW.
    by_label = caps.groupby("label")["value"].sum() * 1e3

    rows = []
    for _, row in groups.iterrows():
        for label in row["labels"]:
            sector, heat_system = _stock_placement(label)
            rows.append(
                {
                    "year": horizon,
                    "group": row["group"],
                    "times_label": label,
                    "sector": sector,
                    "times_heat_system": heat_system,
                    "pypsa_stock_technology": row["pypsa_stock_technology"],
                    "MW_th": float(by_label.get(label, 0.0)),
                }
            )
    out = pd.DataFrame(rows)
    out["transferable"] = out["pypsa_stock_technology"] != ""
    dropped = out.loc[~out["transferable"] & (out["MW_th"] > 0), "MW_th"].sum()
    if dropped > 0:
        logger.info(
            "%.1f MW_th of TIMES %d heat capacity has no existing_heating_distribution "
            "column and is reported but not transferable.",
            dropped,
            horizon,
        )
    return out.sort_values(["group", "sector", "times_heat_system"]).reset_index(
        drop=True
    )


def extract_heating_capacities(
    raw_flows_df: pd.DataFrame,
    processes_df: pd.DataFrame,
    horizon: int,
    heating_capacities_path: Path | str,
    groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """:func:`heating_capacities`, written to CSV."""
    heating_capacities_path = Path(heating_capacities_path)
    heating_capacities_path.parent.mkdir(parents=True, exist_ok=True)
    out = heating_capacities(raw_flows_df, processes_df, horizon, groups)
    out.to_csv(heating_capacities_path, index=False)
    logger.info("Saved heating capacities to %s", heating_capacities_path)
    return out
