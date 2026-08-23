# SPDX-License-Identifier: MIT
"""TIMES → PyPSA road-transport soft-link: fleet and activity by drivetrain.

PyPSA-Wal imposes ``land_transport_{electric,fuel_cell,ice}_share`` exogenously
from ``config.default.yaml`` for every node, and for the Walloon node derives the
electric share from the **energy** ratio ``electricity road / total road`` in
``wallon_demands_{year}.csv``. That ratio is the right number for the *load* and
the wrong number for the *fleet*: a BEV converts more of its energy into km than
an ICE and ``total road`` also meters freight the car count excludes, so the same
TIMES answer is a 14.2 % energy share and a 52.9 % car stock share in 2030. PyPSA
scales the BEV charger ``p_nom`` and the EV-battery ``e_nom`` on
``number_cars * electric_share``, so feeding those the energy share understated
the flexible fleet 3.7× at 2030.

TIMES-WAL carries the fleet directly. Its road-vehicle processes are declared in
**thousands of vehicles** (``Capacity unit = 000VEH``) and **billion vehicle-km**
(``Activity unit = BVKM``), so:

* ``VAR_Cap`` → vehicle **stock**, thousand units;
* ``VAR_Act`` → vehicle **activity**, billion vehicle-km.

Both are exported here, per vehicle class and drivetrain, together with the
shares PyPSA consumes.

**pypsa-wal reads the ``_shares`` file.** ``prepare_sector_network`` takes
``stock_share`` — the share **by count** — for the BEV-charger ``p_nom`` and the
EV-battery ``e_nom``, and ``stock_kveh`` for the vehicle count itself, replacing a
population-scaled figure that was frozen across horizons. The *load* still uses
the energy ratio, which is what makes the EV grid draw equal the transferred
``electricity road`` exactly. Two consequences for anyone changing this module:

* **Both numbers must keep coming from the same vehicle classes.** pypsa-wal reads
  the count and the share from one class list (``cars``) precisely so their
  product is a BEV count. Renaming a class in
  ``transport_softlink_groups.csv`` raises there rather than silently sizing the
  chargers at 0 — that file is now declared as a Snakemake input, so an edit
  invalidates the export.
* **``activity_share`` is the documented alternative, not dead weight.** It is the
  right share for a per-km quantity; pypsa-wal uses ``stock_share`` because a
  charger rating and a battery capacity are per-vehicle. The two differ ~6 %.

See ``pypsa-wal/docs/ev-charging-softlink.md`` §2 for the full reasoning and the
decision record (E1–E3).

Two conventions to know:

* ``VAR_Cap`` only, never ``VAR_Cap + VAR_Ncap``. ``VAR_Ncap`` (capacity built in
  the period) is already inside ``VAR_Cap``; adding them double-counts. Same
  finding as the heating soft-link — see :mod:`times_pypsa.heat_softlink`.
* The vehicle class comes from the process **description**, never from the
  process-code prefix: fourteen ``TCAR…`` codes in this ``.vd`` are heavy-duty
  trucks and light commercial vehicles.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from times_pypsa.pipeline import default_mappings_dir

logger = logging.getLogger(__name__)

GROUPS_FILE_NAME = "transport_softlink_groups.csv"

#: TIMES declares road-vehicle processes in these units. The pair is the selector
#: — it is what separates a vehicle from the fuel-supply technology feeding it,
#: both of which can carry the same ``Aggregation Level 2`` label.
VEHICLE_CAPACITY_UNIT = "000VEH"
VEHICLE_ACTIVITY_UNIT = "BVKM"

#: The three shares PyPSA-Wal reads (``sector.land_transport_*_share``).
PYPSA_ENGINE_TYPES = ("electric", "fuel_cell", "ice")

_REQUIRED_GROUP_COLUMNS = ("kind", "times_match", "key", "pypsa_engine_type")
_CATCH_ALL = "*"


def default_groups_file() -> Path:
    """Path of the bundled group definition."""
    return default_mappings_dir() / GROUPS_FILE_NAME


def resolve_groups_file(mappings_dir: Path | str | None = None) -> Path:
    """Group definition inside ``mappings_dir``, else the bundled one."""
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


def load_transport_groups(groups_file: Path | str | None = None) -> pd.DataFrame:
    """Read and validate ``transport_softlink_groups.csv``.

    Row order is significant for ``kind=drivetrain``: the first ``times_match``
    found in a process description wins, and the ``*`` row is the catch-all. That
    is why ``PHEV`` precedes ``Hybrid`` and ``Hybrid`` precedes ``Electric``.
    """
    path = Path(groups_file) if groups_file else default_groups_file()
    df = pd.read_csv(path)
    missing = [c for c in _REQUIRED_GROUP_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    for col in _REQUIRED_GROUP_COLUMNS:
        df[col] = df[col].fillna("").astype(str).str.strip()

    bad_kind = sorted(set(df["kind"]) - {"class", "drivetrain"})
    if bad_kind:
        raise ValueError(
            f"{path}: unknown kind(s) {bad_kind}; valid values are class, drivetrain"
        )

    drivetrains = df[df["kind"] == "drivetrain"]
    bad_engine = sorted(
        {e for e in drivetrains["pypsa_engine_type"] if e not in PYPSA_ENGINE_TYPES}
    )
    if bad_engine:
        raise ValueError(
            f"{path}: pypsa_engine_type {bad_engine} is not one of "
            f"{list(PYPSA_ENGINE_TYPES)}"
        )
    if list(drivetrains["times_match"]).count(_CATCH_ALL) != 1:
        raise ValueError(
            f"{path}: exactly one kind=drivetrain row must carry "
            f"times_match={_CATCH_ALL!r} as the catch-all"
        )
    if drivetrains["times_match"].iloc[-1] != _CATCH_ALL:
        raise ValueError(
            f"{path}: the {_CATCH_ALL!r} drivetrain row must come last — row order "
            "is the matching priority"
        )
    if df[df["kind"] == "class"].empty:
        raise ValueError(f"{path}: no kind=class rows")
    return df


def _classify(description: str, groups: pd.DataFrame) -> tuple[str, str, str]:
    """``(vehicle_class, drivetrain, pypsa_engine_type)`` for one description."""
    low = str(description).lower()

    vehicle_class = ""
    for _, row in groups[groups["kind"] == "class"].iterrows():
        if row["times_match"].lower() in low:
            vehicle_class = row["key"]
            break

    for _, row in groups[groups["kind"] == "drivetrain"].iterrows():
        match = row["times_match"]
        if match == _CATCH_ALL or match.lower() in low:
            return vehicle_class, row["key"], row["pypsa_engine_type"]
    raise AssertionError("unreachable: the catch-all row is validated to exist")


def road_vehicle_fleet(
    raw_flows_df: pd.DataFrame,
    processes_df: pd.DataFrame,
    horizon: int,
    groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Road-vehicle stock and activity for one horizon, by class and drivetrain.

    Columns: ``year``, ``vehicle_class``, ``drivetrain``, ``pypsa_engine_type``,
    ``stock_kveh`` (thousand vehicles), ``activity_bvkm`` (billion vehicle-km),
    ``stock_share`` and ``activity_share`` (within the vehicle class).

    The region column is not filtered: TIMES-WAL is a single-region model
    (``RW``), as the heating soft-link also assumes.
    """
    groups = load_transport_groups() if groups is None else groups

    mapping = processes_df.copy()
    tech_col = (
        "Technology (Process)"
        if "Technology (Process)" in mapping.columns
        else "Process"
    )
    for col in ("Capacity unit", "Activity unit", "Description"):
        if col not in mapping.columns:
            raise ValueError(f"mapping_processes is missing the {col!r} column")

    vehicles = mapping[
        (mapping["Capacity unit"] == VEHICLE_CAPACITY_UNIT)
        & (mapping["Activity unit"] == VEHICLE_ACTIVITY_UNIT)
    ]
    if vehicles.empty:
        raise ValueError(
            f"no process declares {VEHICLE_CAPACITY_UNIT}/{VEHICLE_ACTIVITY_UNIT}; "
            "the road-vehicle selector no longer matches this mapping file"
        )
    classified = {
        code: _classify(desc, groups)
        for code, desc in zip(vehicles[tech_col], vehicles["Description"])
    }
    unclassed = sorted(c for c, (cls, _, _) in classified.items() if not cls)
    if unclassed:
        logger.warning(
            "%d road-vehicle process(es) match no kind=class row and are dropped: %s. "
            "Add a class row to %s.",
            len(unclassed),
            unclassed,
            GROUPS_FILE_NAME,
        )

    rows = raw_flows_df.loc[
        (raw_flows_df["year"] == horizon)
        & (raw_flows_df["variable"].isin(("VAR_Cap", "VAR_Act")))
        & (raw_flows_df["process_code"].isin(classified))
    ].copy()
    keys = rows["process_code"].map(classified)
    rows["vehicle_class"] = [k[0] for k in keys]
    rows["drivetrain"] = [k[1] for k in keys]
    rows["pypsa_engine_type"] = [k[2] for k in keys]
    rows = rows[rows["vehicle_class"] != ""]

    index = ["vehicle_class", "drivetrain", "pypsa_engine_type"]
    agg = (
        rows.pivot_table(
            index=index, columns="variable", values="value", aggfunc="sum"
        )
        .reindex(columns=["VAR_Cap", "VAR_Act"])
        .fillna(0.0)
        .rename(columns={"VAR_Cap": "stock_kveh", "VAR_Act": "activity_bvkm"})
        .reset_index()
    )
    agg.insert(0, "year", horizon)
    for value, share in (("stock_kveh", "stock_share"), ("activity_bvkm", "activity_share")):
        total = agg.groupby("vehicle_class")[value].transform("sum")
        agg[share] = (agg[value] / total).where(total > 0, 0.0)
    return agg.sort_values(index).reset_index(drop=True)


def road_transport_shares(
    raw_flows_df: pd.DataFrame,
    processes_df: pd.DataFrame,
    horizon: int,
    groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Fleet collapsed onto the three PyPSA engine types, per vehicle class.

    Columns: ``year``, ``vehicle_class``, ``pypsa_engine_type``, ``stock_kveh``,
    ``activity_bvkm``, ``stock_share``, ``activity_share``. Every class carries
    all three engine types, zero-filled, so a consumer never has to guess whether
    a missing row means zero or means the class was not modelled.

    ``stock_share`` is what pypsa-wal applies to a **fleet** quantity (charger
    power, battery energy), because those are per-vehicle; ``activity_share`` is
    the one for a **per-km** quantity. Neither equals the energy ratio, which
    pypsa-wal keeps for the *load* — see the module docstring.

    Consumers must take ``stock_kveh`` and ``stock_share`` from the **same**
    ``vehicle_class`` rows: their product is the BEV count, and crossing the
    boundaries gets it wrong in either direction -- a car count against a
    cars+vans share is 14 % low at 2030, a cars+vans count against a car share
    17 % high.
    """
    fleet = road_vehicle_fleet(raw_flows_df, processes_df, horizon, groups)
    out = (
        fleet.groupby(["year", "vehicle_class", "pypsa_engine_type"])[
            ["stock_kveh", "activity_bvkm"]
        ]
        .sum()
        .reset_index()
    )
    full = pd.MultiIndex.from_product(
        [[horizon], sorted(out["vehicle_class"].unique()), list(PYPSA_ENGINE_TYPES)],
        names=["year", "vehicle_class", "pypsa_engine_type"],
    )
    out = (
        out.set_index(["year", "vehicle_class", "pypsa_engine_type"])
        .reindex(full)
        .fillna(0.0)
        .reset_index()
    )
    for value, share in (("stock_kveh", "stock_share"), ("activity_bvkm", "activity_share")):
        total = out.groupby("vehicle_class")[value].transform("sum")
        out[share] = (out[value] / total).where(total > 0, 0.0)
    return out


def extract_road_transport(
    raw_flows_df: pd.DataFrame,
    processes_df: pd.DataFrame,
    horizon: int,
    road_transport_path: Path | str,
    groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """:func:`road_vehicle_fleet` and :func:`road_transport_shares`, written to CSV.

    Writes ``road_transport_path`` (per class × drivetrain) and, alongside it,
    ``<stem>_shares.csv`` (per class × PyPSA engine type).
    """
    road_transport_path = Path(road_transport_path)
    road_transport_path.parent.mkdir(parents=True, exist_ok=True)
    groups = load_transport_groups() if groups is None else groups

    fleet = road_vehicle_fleet(raw_flows_df, processes_df, horizon, groups)
    fleet.to_csv(road_transport_path, index=False)

    shares_path = road_transport_path.with_name(
        f"{road_transport_path.stem}_shares{road_transport_path.suffix}"
    )
    shares = road_transport_shares(raw_flows_df, processes_df, horizon, groups)
    shares.to_csv(shares_path, index=False)

    cars = shares[shares["vehicle_class"] == "cars"].set_index("pypsa_engine_type")
    if not cars.empty:
        logger.info(
            "TIMES %d passenger cars: %.0f k vehicles, electric stock share %.1f %% "
            "(activity share %.1f %%).",
            horizon,
            shares.loc[shares["vehicle_class"] == "cars", "stock_kveh"].sum(),
            100 * cars.at["electric", "stock_share"],
            100 * cars.at["electric", "activity_share"],
        )
    logger.info("Saved road-transport fleet to %s and %s", road_transport_path, shares_path)
    return fleet
