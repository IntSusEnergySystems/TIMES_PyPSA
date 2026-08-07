"""Guards for the heating soft-link payload (Option C).

Three properties matter and none of them is checked anywhere else:

1. **Closure.** The per-technology energy targets must sum to exactly the same
   number the demand transfer already gives pypsa-wal. If they do not, the
   energy-mix constraint and the heat load disagree and the LP is either
   infeasible or quietly vents heat.
2. **No double count on the capacity axis.** ``VAR_Cap + VAR_Ncap`` inflated the
   old export by 36-65 %; ``test_capacity_excludes_ncap`` fails if anyone adds
   ``VAR_Ncap`` back.
3. **Scope.** Only building-heat labels may appear. The old regex admitted an
   industrial geothermal process and a 1 740 MW CCGT-CCS power plant.

The group definition itself is data (``data/heat_softlink_groups.csv``), so the
structural tests are data tests: they fail on a typo in the CSV, not only on a
code change.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from times_pypsa.heat_softlink import (
    PYPSA_STOCK_TECHNOLOGIES,
    SENSE_NONE,
    extract_heating_capacities,
    extract_heating_targets,
    heat_group_targets,
    heating_capacities,
    load_heat_groups,
    resolve_groups_file,
)
from times_pypsa.pipeline import (
    PJ_TO_TWH,
    extract_demands_for_horizon,
    load_extraction_rules,
    load_metadata,
    load_raw_records,
    prepare_annual_values,
    PipelineConfig,
)

#: The three parent categories `write_wallon_heat_demands` rescales the decentral
#: heat loads onto. The constrained groups must reproduce their sum exactly.
DECENTRAL_PARENTS = [
    "BEWAL residential urban decentral heat",
    "BEWAL residential rural heat",
    "BEWAL services urban decentral heat",
]

#: Labels that must never reach the capacity export. Both were let in by the old
#: `boiler|heat pump|stove|thermal|heater` regex.
FORBIDDEN_CAPACITY_LABELS = {
    "Geothermal (IND)",
    "Thermal Public - Retrofitting CCGT CCS",
}


# --------------------------------------------------------------------------- #
# Group definition (data tests — no .vd needed)
# --------------------------------------------------------------------------- #


def test_groups_file_is_bundled_and_loads():
    groups = load_heat_groups()
    assert not groups.empty
    assert set(groups["group"]) >= {
        "heat pump",
        "gas boiler",
        "oil boiler",
        "biomass boiler",
        "resistive heater",
        "solar thermal",
        "district heating",
    }


def test_district_heating_is_out_of_scope():
    """Diagnosis §6 *Scope*: the constraint must stop at the decentral buses."""
    groups = load_heat_groups()
    dh = groups[groups["group"] == "district heating"]
    assert not dh.empty
    assert set(dh["sense"]) == {SENSE_NONE}
    assert set(dh["scope"]) == {"urban central"}


def test_every_decentral_group_declares_a_sense_and_carriers():
    groups = load_heat_groups()
    decentral = groups[groups["scope"] == "decentral"]
    assert not decentral.empty
    assert (decentral["sense"] != SENSE_NONE).all()
    assert decentral["carriers"].map(len).gt(0).all()
    assert set(decentral["pypsa_component"]) <= {"Link", "Generator"}


def test_stock_technologies_are_valid_pypsa_columns():
    groups = load_heat_groups()
    declared = {t for t in groups["pypsa_stock_technology"] if t}
    assert declared <= set(PYPSA_STOCK_TECHNOLOGIES)


def test_arbitrary_mappings_carry_a_note():
    """A row that folds a TIMES technology into a different PyPSA one must say why.

    The coal → oil boiler, geothermal → ground heat pump and tertiary-CHP → gas
    boiler assignments are judgement calls. An undocumented one is a silent
    modelling assumption.
    """
    groups = load_heat_groups()
    for group, sub in groups.groupby("group"):
        if len(sub) == 1:
            continue
        notes = sub["note"].fillna("").astype(str)
        assert (notes.str.len() > 0).sum() >= len(sub) - 1, (
            f"group {group!r} splits over {len(sub)} rows but documents only "
            f"{(notes.str.len() > 0).sum()} of them"
        )


def test_resolve_groups_file_prefers_a_local_copy(tmp_path: Path):
    bundled = resolve_groups_file(None)
    assert bundled.exists()
    local = tmp_path / "heat_softlink_groups.csv"
    pd.read_csv(bundled).to_csv(local, index=False)
    assert resolve_groups_file(tmp_path) == local
    # A directory without the file falls back instead of raising.
    assert resolve_groups_file(tmp_path / "empty") == bundled


def test_duplicate_category_is_rejected(tmp_path: Path):
    groups = pd.read_csv(resolve_groups_file(None))
    groups.loc[groups.index[0], "times_categories"] = (
        str(groups.loc[groups.index[0], "times_categories"])
        + ";residential rural gas boiler"
    )
    path = tmp_path / "heat_softlink_groups.csv"
    groups.to_csv(path, index=False)
    with pytest.raises(ValueError, match="claimed by both"):
        load_heat_groups(path)


def test_unknown_sense_is_rejected(tmp_path: Path):
    groups = pd.read_csv(resolve_groups_file(None))
    groups.loc[groups.index[0], "sense"] = "~="
    path = tmp_path / "heat_softlink_groups.csv"
    groups.to_csv(path, index=False)
    with pytest.raises(ValueError, match="unknown constraint sense"):
        load_heat_groups(path)


# --------------------------------------------------------------------------- #
# Energy targets against a real .vd
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def demands_by_year(vd_path: Path, mappings_dir: Path, tmp_path_factory):
    """``wallon_demands`` for every horizon in the fixture."""
    metadata = load_metadata(mappings_dir)
    rules = load_extraction_rules(metadata.extraction_rules_file)
    config = PipelineConfig(start_year=2025)
    _raw, annual = prepare_annual_values(vd_path, metadata, config)
    out_dir = tmp_path_factory.mktemp("demands")
    years = sorted(int(y) for y in annual["year"].unique())
    return {
        year: extract_demands_for_horizon(
            annual,
            metadata.processes_df,
            metadata.mapping_df,
            rules,
            metadata.commodity_mapping_file,
            year,
            out_dir / f"wallon_demands_{year}.csv",
            apply_netting=config.apply_netting,
        )
        for year in years
    }


def test_targets_close_against_the_transferred_demand(demands_by_year):
    """Σ constrained group targets == Σ the three decentral parent categories.

    This is the property the whole option rests on: pypsa-wal scales its
    decentral heat loads to those parents, so a group total that does not add up
    to the same number over-determines the LP.
    """
    groups = load_heat_groups()
    for year, demands in demands_by_year.items():
        targets = heat_group_targets(demands, year, groups)
        got = targets.loc[targets["constrained"], "TWh"].sum()
        want = (
            demands.set_index("category")["TWh"].reindex(DECENTRAL_PARENTS).fillna(0.0)
        ).sum()
        assert got == pytest.approx(want, abs=1e-9), (
            f"{year}: groups sum to {got:.6f} TWh but the transferred decentral "
            f"demand is {want:.6f} TWh"
        )


def test_shares_sum_to_one(demands_by_year):
    groups = load_heat_groups()
    for year, demands in demands_by_year.items():
        targets = heat_group_targets(demands, year, groups)
        total = targets.loc[targets["constrained"], "share"].sum()
        assert total == pytest.approx(1.0, abs=1e-9), f"{year}: shares sum to {total}"
        assert (targets.loc[~targets["constrained"], "share"] == 0).all()


def test_pj_and_twh_agree(demands_by_year):
    groups = load_heat_groups()
    year, demands = next(iter(demands_by_year.items()))
    targets = heat_group_targets(demands, year, groups)
    assert targets["TWh"].to_numpy() == pytest.approx(
        (targets["PJ"] * PJ_TO_TWH).to_numpy()
    )


def test_district_heating_target_is_exported_but_not_constrained(demands_by_year):
    groups = load_heat_groups()
    year, demands = next(iter(demands_by_year.items()))
    targets = heat_group_targets(demands, year, groups)
    dh = targets[targets["group"] == "district heating"].iloc[0]
    assert not dh["constrained"]
    assert dh["sense"] == SENSE_NONE
    want = (
        demands.set_index("category")["TWh"]
        .reindex(["residential district heating", "services district heating"])
        .fillna(0.0)
        .sum()
    )
    assert dh["TWh"] == pytest.approx(want)


def test_missing_category_is_a_warning_not_a_crash(demands_by_year, caplog):
    """An older export, or a scenario whose rules dropped a category."""
    year, demands = next(iter(demands_by_year.items()))
    trimmed = demands[demands["category"] != "residential rural gas boiler"]
    with caplog.at_level("WARNING"):
        targets = heat_group_targets(trimmed, year, load_heat_groups())
    assert "absent from the export" in caplog.text
    assert not targets.empty


def test_extract_heating_targets_writes_a_readable_csv(demands_by_year, tmp_path: Path):
    year, demands = next(iter(demands_by_year.items()))
    path = tmp_path / f"heating_targets_{year}.csv"
    written = extract_heating_targets(demands, year, path)
    reread = pd.read_csv(path)
    assert list(reread.columns) == list(written.columns)
    assert reread["TWh"].to_numpy() == pytest.approx(written["TWh"].to_numpy())


# --------------------------------------------------------------------------- #
# Capacities
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def raw_flows(vd_path: Path):
    return load_raw_records(vd_path, start_year=2021)


@pytest.fixture(scope="module")
def processes_df(mappings_dir: Path):
    return load_metadata(mappings_dir).processes_df


def _capacity_years(raw_flows: pd.DataFrame) -> list[int]:
    caps = raw_flows[raw_flows["variable"] == "VAR_Cap"]
    if caps.empty:
        pytest.skip("fixture carries no VAR_Cap rows")
    return sorted(int(y) for y in caps["year"].unique())


def test_capacity_excludes_ncap(raw_flows, processes_df):
    """``VAR_Ncap`` is inside ``VAR_Cap``; adding it double-counts.

    Verified structurally rather than by a magic number: for every process-year
    the fixture reports, ``VAR_Ncap <= VAR_Cap``, so a sum of the two must exceed
    the export — and the export must equal ``VAR_Cap`` alone.
    """
    year = _capacity_years(raw_flows)[0]
    caps = heating_capacities(raw_flows, processes_df, year)
    label_map = processes_df.set_index("Process")["Aggregation Level 2"].to_dict()
    cap_only = raw_flows[
        (raw_flows["year"] == year) & (raw_flows["variable"] == "VAR_Cap")
    ].copy()
    cap_only["label"] = cap_only["process_code"].map(label_map)
    by_label = cap_only.groupby("label")["value"].sum() * 1e3
    for _, row in caps.iterrows():
        assert row["MW_th"] == pytest.approx(float(by_label.get(row["times_label"], 0.0)))


def test_capacity_scope_excludes_non_building_heat(raw_flows, processes_df):
    for year in _capacity_years(raw_flows):
        caps = heating_capacities(raw_flows, processes_df, year)
        assert not (set(caps["times_label"]) & FORBIDDEN_CAPACITY_LABELS)


def test_district_heating_capacity_is_reported(raw_flows, processes_df):
    """The old regex silently dropped ``District heating`` — the substations."""
    year = _capacity_years(raw_flows)[0]
    caps = heating_capacities(raw_flows, processes_df, year)
    dh = caps[caps["group"] == "district heating"]
    assert not dh.empty
    assert (dh["times_heat_system"] == "urban central").all()
    assert not dh["transferable"].any()


def test_capacity_placement_columns_are_well_formed(raw_flows, processes_df):
    year = _capacity_years(raw_flows)[0]
    caps = heating_capacities(raw_flows, processes_df, year)
    assert set(caps["sector"]) <= {"residential", "services", "district"}
    assert set(caps["times_heat_system"]) <= {"rural", "urban decentral", "urban central"}
    # Every services row lands on urban decentral or urban central: pypsa-wal
    # deletes the `BEWAL services rural` sub-system, so a services-rural row
    # would be dropped on the floor.
    services = caps[caps["sector"] == "services"]
    assert set(services["times_heat_system"]) <= {"urban decentral", "urban central"}
    assert (caps["MW_th"] >= 0).all()
    transferable = caps[caps["transferable"]]
    assert (transferable["pypsa_stock_technology"] != "").all()


def test_solar_thermal_capacity_is_reported_as_non_transferable(raw_flows, processes_df):
    """PyPSA's solar thermal Generator has no base-year stock column."""
    year = _capacity_years(raw_flows)[0]
    caps = heating_capacities(raw_flows, processes_df, year)
    st = caps[caps["group"] == "solar thermal"]
    assert not st.empty
    assert not st["transferable"].any()


def test_extract_heating_capacities_writes_a_readable_csv(
    raw_flows, processes_df, tmp_path: Path
):
    year = _capacity_years(raw_flows)[0]
    path = tmp_path / f"heating_capacities_{year}.csv"
    written = extract_heating_capacities(raw_flows, processes_df, year, path)
    reread = pd.read_csv(path)
    assert list(reread.columns) == list(written.columns)
    assert reread["MW_th"].to_numpy() == pytest.approx(written["MW_th"].to_numpy())
