"""Guards for the road-transport soft-link payload.

Four properties matter and none is checked anywhere else:

1. **The selector is the unit pair, not the label.** ``Cars`` and ``Road
   Freight`` label both the vehicle processes (``000VEH``/``BVKM``) and the fuel
   technologies feeding them (``PJA``/``PJ``). Selecting on the label would sum
   PJ into a vehicle count.
2. **The vehicle class comes from the description.** Fourteen ``TCAR…`` process
   codes in the reference ``.vd`` are heavy-duty trucks or vans, so a code-prefix
   classifier silently reports trucks as cars.
3. **Row order in the group CSV is the drivetrain matching priority**, with a
   single ``*`` catch-all last. A reordered CSV changes results silently, so the
   loader rejects it.
4. **Stock share ≠ activity share ≠ energy share.** The whole point of the export
   is that PyPSA-Wal currently derives a *fleet* quantity from an *energy* ratio.
   The three must be reported separately and never conflated.

The group definition is data (``data/transport_softlink_groups.csv``), so the
structural tests are data tests: they fail on a typo in the CSV, not only on a
code change.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from times_pypsa.pipeline import load_raw_records, load_metadata
from times_pypsa.transport_softlink import (
    GROUPS_FILE_NAME,
    PYPSA_ENGINE_TYPES,
    VEHICLE_ACTIVITY_UNIT,
    VEHICLE_CAPACITY_UNIT,
    extract_road_transport,
    load_transport_groups,
    resolve_groups_file,
    road_transport_shares,
    road_vehicle_fleet,
)

HORIZON = 2030


@pytest.fixture(scope="module")
def groups() -> pd.DataFrame:
    return load_transport_groups()


@pytest.fixture(scope="module")
def raw_and_processes(vd_path: Path, mappings_dir: Path):
    raw = load_raw_records(vd_path, start_year=2020)
    metadata = load_metadata(mappings_dir)
    return raw, metadata.processes_df


@pytest.fixture(scope="module")
def fleet(raw_and_processes, groups) -> pd.DataFrame:
    raw, processes = raw_and_processes
    return road_vehicle_fleet(raw, processes, HORIZON, groups)


def _has_activity(raw: pd.DataFrame) -> bool:
    """Fixtures committed before 2026-08-21 carry no ``VAR_Act``."""
    return (raw["variable"] == "VAR_Act").any()


# --------------------------------------------------------------------------- #
# Group definition (data tests)
# --------------------------------------------------------------------------- #


def test_groups_file_is_resolvable(mappings_dir: Path):
    assert resolve_groups_file(mappings_dir).name == GROUPS_FILE_NAME
    assert resolve_groups_file(mappings_dir).exists()


def test_engine_types_are_the_pypsa_three(groups: pd.DataFrame):
    drivetrains = groups[groups["kind"] == "drivetrain"]
    assert set(drivetrains["pypsa_engine_type"]) <= set(PYPSA_ENGINE_TYPES)
    # All three must be reachable, or a share PyPSA reads can never be non-zero.
    assert set(drivetrains["pypsa_engine_type"]) == set(PYPSA_ENGINE_TYPES)


def test_catch_all_must_be_last(groups: pd.DataFrame, tmp_path: Path):
    """Row order is the matching priority, so a moved ``*`` row is an error."""
    drivetrains = groups[groups["kind"] == "drivetrain"]
    assert drivetrains["times_match"].iloc[-1] == "*"

    shuffled = pd.concat([drivetrains.iloc[[-1]], drivetrains.iloc[:-1]])
    path = tmp_path / GROUPS_FILE_NAME
    pd.concat([groups[groups["kind"] == "class"], shuffled]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="must come last"):
        load_transport_groups(path)


def test_missing_catch_all_is_rejected(groups: pd.DataFrame, tmp_path: Path):
    path = tmp_path / GROUPS_FILE_NAME
    groups[groups["times_match"] != "*"].to_csv(path, index=False)
    with pytest.raises(ValueError, match="catch-all"):
        load_transport_groups(path)


def test_unknown_engine_type_is_rejected(groups: pd.DataFrame, tmp_path: Path):
    bad = groups.copy()
    bad.loc[bad["key"] == "BEV", "pypsa_engine_type"] = "battery"
    path = tmp_path / GROUPS_FILE_NAME
    bad.to_csv(path, index=False)
    with pytest.raises(ValueError, match="pypsa_engine_type"):
        load_transport_groups(path)


# --------------------------------------------------------------------------- #
# Selection and classification
# --------------------------------------------------------------------------- #


def test_selector_is_the_unit_pair_not_the_label(raw_and_processes):
    """A PJA/PJ fuel technology must never enter the vehicle count."""
    _, processes = raw_and_processes
    tech_col = (
        "Technology (Process)"
        if "Technology (Process)" in processes.columns
        else "Process"
    )
    label_selected = processes[processes["Aggregation Level 2"] == "Cars"]
    unit_selected = label_selected[
        (label_selected["Capacity unit"] == VEHICLE_CAPACITY_UNIT)
        & (label_selected["Activity unit"] == VEHICLE_ACTIVITY_UNIT)
    ]
    # If these ever coincide the guard is vacuous and the test should be revisited.
    assert len(unit_selected) < len(label_selected), (
        "the `Cars` label no longer mixes vehicle and fuel-supply processes; "
        "re-check whether the unit selector is still the right one"
    )
    assert not unit_selected.empty
    assert set(unit_selected[tech_col]) < set(label_selected[tech_col])


def test_trucks_labelled_with_a_car_process_code_are_not_cars(
    raw_and_processes, groups: pd.DataFrame
):
    """Regression: `TCARGASEX1x` are heavy-duty trucks and vans, not cars."""
    from times_pypsa.transport_softlink import _classify

    _, processes = raw_and_processes
    tech_col = (
        "Technology (Process)"
        if "Technology (Process)" in processes.columns
        else "Process"
    )
    tcar_trucks = processes[
        processes[tech_col].astype(str).str.startswith("TCARGAS")
        & processes["Description"].astype(str).str.contains("Trucks|Commercial")
    ]
    assert not tcar_trucks.empty, "fixture no longer contains the mislabelled codes"
    for desc in tcar_trucks["Description"]:
        vehicle_class, _, _ = _classify(desc, groups)
        assert vehicle_class in ("heavy duty trucks", "light commercial vehicles")


def test_bicycle_electric_is_matched_case_insensitively(groups: pd.DataFrame):
    from times_pypsa.transport_softlink import _classify

    vehicle_class, drivetrain, engine = _classify("Bicycle electric  Existing", groups)
    assert (vehicle_class, drivetrain, engine) == (
        "two and three wheelers",
        "BEV",
        "electric",
    )


def test_phev_and_hybrid_do_not_collapse_into_bev(groups: pd.DataFrame):
    from times_pypsa.transport_softlink import _classify

    assert _classify("Cars PHEV Gasoline Executive Existing", groups)[1] == "PHEV"
    assert _classify("Cars Hybrid Gasoline Medium-Lower New", groups)[1] == "HEV"
    assert _classify("Cars Electric Small New", groups)[1] == "BEV"
    # PHEV and HEV are `ice` for PyPSA: there is no PHEV component.
    assert _classify("Cars PHEV Gasoline Executive Existing", groups)[2] == "ice"
    assert _classify("Cars Hybrid Gasoline Medium-Lower New", groups)[2] == "ice"


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def test_fleet_has_cars_and_a_plausible_stock(fleet: pd.DataFrame):
    cars = fleet[fleet["vehicle_class"] == "cars"]
    assert not cars.empty
    total_kveh = cars["stock_kveh"].sum()
    # Wallonia holds ~1.9-2.2 million passenger cars across the horizons.
    assert 1_000 < total_kveh < 3_500, total_kveh


def test_shares_sum_to_one_per_class(raw_and_processes, groups: pd.DataFrame):
    raw, processes = raw_and_processes
    shares = road_transport_shares(raw, processes, HORIZON, groups)
    by_class = shares.groupby("vehicle_class")["stock_share"].sum()
    assert ((by_class - 1.0).abs() < 1e-9).all(), by_class.to_dict()


def test_every_class_carries_all_three_engine_types(
    raw_and_processes, groups: pd.DataFrame
):
    """A missing row must never be ambiguous between "zero" and "not modelled"."""
    raw, processes = raw_and_processes
    shares = road_transport_shares(raw, processes, HORIZON, groups)
    for vehicle_class, sub in shares.groupby("vehicle_class"):
        assert set(sub["pypsa_engine_type"]) == set(PYPSA_ENGINE_TYPES), vehicle_class


def test_stock_share_differs_from_activity_share(
    raw_and_processes, groups: pd.DataFrame
):
    """The two are not interchangeable — that is the point of exporting both."""
    raw, processes = raw_and_processes
    if not _has_activity(raw):
        pytest.skip("fixture carries no VAR_Act; regenerate with build_toy_fixtures.py")
    shares = road_transport_shares(raw, processes, HORIZON, groups)
    cars = shares[
        (shares["vehicle_class"] == "cars")
        & (shares["pypsa_engine_type"] == "electric")
    ].iloc[0]
    assert cars["stock_share"] > 0
    assert cars["activity_share"] > 0
    assert cars["stock_share"] != cars["activity_share"]


def test_capacity_excludes_ncap(raw_and_processes, groups: pd.DataFrame):
    """``VAR_Ncap`` is inside ``VAR_Cap``; adding it would inflate the fleet."""
    raw, processes = raw_and_processes
    ncap = raw[
        (raw["variable"] == "VAR_Ncap") & (raw["year"] == HORIZON)
    ]
    if ncap.empty:
        pytest.skip("fixture carries no VAR_Ncap, so the double count is unreachable")
    fleet = road_vehicle_fleet(raw, processes, HORIZON, groups)
    with_ncap = road_vehicle_fleet(
        pd.concat([raw, ncap.assign(variable="VAR_Cap")]), processes, HORIZON, groups
    )
    assert with_ncap["stock_kveh"].sum() > fleet["stock_kveh"].sum(), (
        "adding VAR_Ncap as VAR_Cap did not change the total — the extraction is "
        "probably not reading VAR_Cap at all"
    )


def test_extract_writes_both_files(raw_and_processes, groups, tmp_path: Path):
    raw, processes = raw_and_processes
    out = tmp_path / f"road_transport_{HORIZON}.csv"
    extract_road_transport(raw, processes, HORIZON, out, groups)
    shares = out.with_name(f"road_transport_{HORIZON}_shares.csv")
    assert out.exists() and shares.exists()
    fleet_csv = pd.read_csv(out)
    shares_csv = pd.read_csv(shares)
    assert {"stock_kveh", "activity_bvkm", "stock_share", "activity_share"} <= set(
        fleet_csv.columns
    )
    # The collapse onto engine types must conserve the stock.
    assert abs(fleet_csv["stock_kveh"].sum() - shares_csv["stock_kveh"].sum()) < 1e-6


# --------------------------------------------------------------------------- #
# mapping_processes.csv hygiene
# --------------------------------------------------------------------------- #


def test_no_duplicate_process_codes(mappings_dir: Path):
    """`prepare_annual_values` merges on process_code — a duplicate multiplies rows.

    Seven codes used to appear twice, once as a vehicle (000VEH) and once as a
    fuel technology (PJA). All seven were zero in the reference .vd so nothing was
    double-counted, but LNG trucks are a plausible future and the failure would be
    silent.
    """
    mapping = pd.read_csv(mappings_dir / "mapping_processes.csv")
    col = "Technology (Process)"
    dups = sorted(mapping.loc[mapping[col].duplicated(), col].unique())
    if dups:
        pytest.fail(
            f"mapping_processes.csv has duplicate process code(s): {dups}. "
            "prepare_annual_values merges on this column, so each duplicate "
            "multiplies every flow row for that process. Keep the row whose "
            "units match data/AllProcesses.csv."
        )


def test_every_road_vehicle_is_classified(raw_and_processes, groups: pd.DataFrame):
    """A vehicle the group file cannot place is silently dropped from the export."""
    from times_pypsa.transport_softlink import _classify

    _, processes = raw_and_processes
    tech_col = (
        "Technology (Process)"
        if "Technology (Process)" in processes.columns
        else "Process"
    )
    vehicles = processes[
        (processes["Capacity unit"] == VEHICLE_CAPACITY_UNIT)
        & (processes["Activity unit"] == VEHICLE_ACTIVITY_UNIT)
    ]
    unclassed = [
        code
        for code, desc in zip(vehicles[tech_col], vehicles["Description"])
        if not _classify(desc, groups)[0]
    ]
    if unclassed:
        pytest.fail(
            f"{len(unclassed)} road-vehicle process(es) match no kind=class row and "
            f"are dropped from the fleet export: {unclassed}. Either add a class "
            f"row to {GROUPS_FILE_NAME} or fix the process description in "
            "mapping_processes.csv."
        )
