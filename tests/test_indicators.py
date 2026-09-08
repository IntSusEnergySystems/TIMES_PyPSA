"""Scenario indicator tables extracted from a .vd.

The reference values in ``test_reproduces_icedd_2021`` come from the December-2025
``demande haute`` report tables ICEDD published next to the same ``.vd``
(``s3://intervectoriel/test/scenarios/times-pypsa__demande-haute__20251204/
strategy/report/``). 2021 is the calibrated base year, so it is the year where
the extraction and their query have to agree exactly; the later horizons drift
because the published tables were regenerated from a one-day-newer solve
(``scen_corrige_251130_0312``). See ``INDICATORS.md``.
"""

from __future__ import annotations

import pandas as pd
import pytest

from times_pypsa.indicators import (
    CATALOGUE_FACETS,
    DEMAND_SECTORS,
    _first_match,
    ELECTRICITY_OUTPUTS,
    TOTAL_EXCLUDES,
    build_indicator_tables,
    catalogue_series,
    chp_heat_shares,
    co2_capture,
    energy_demand,
    ghg_emissions,
    heat_production,
    indicator_catalogue,
    load_indicator_rules,
    power_fleet,
)


@pytest.fixture(scope="module")
def rules(mappings_dir):
    return load_indicator_rules(mappings_dir)


# --------------------------------------------------------------------------- #
# Rule tables
# --------------------------------------------------------------------------- #


def test_rule_tables_parse_and_are_self_consistent(rules):
    assert not rules.fuels.empty and not rules.heat.empty and not rules.power.empty

    # Every commodity appears once; a duplicate would silently pick one rule.
    dupes = rules.fuels["commodity"][rules.fuels["commodity"].duplicated()].tolist()
    assert dupes == [], f"duplicate commodity rules: {dupes}"

    measures = set(rules.fuels["measure"])
    assert measures <= {"input", "output", "passthrough", "upstream", "chp_fuel", "exclude"}

    # A counted row needs a fuel label; a skipped one must not have a stray label.
    counted = rules.fuels[rules.fuels["measure"].isin({"input", "output", "upstream", "chp_fuel"})]
    assert (counted["fuel"] != "").all()
    assert (rules.fuels.loc[rules.fuels["measure"] == "exclude", "fuel"] == "").all()

    assert set(rules.heat["kind"]) == {"segment", "technology"}
    assert set(rules.power["mode"]) <= {"chp", "power", "skip"}
    # heat_to_sector must name a real demand sector.
    targets = set(rules.power["heat_to_sector"]) - {""}
    assert targets <= set(DEMAND_SECTORS)


def test_heat_technology_rules_end_in_a_catch_all(rules):
    """Without a final `.*` a new process code would silently vanish."""
    techs = rules.heat[rules.heat["kind"] == "technology"]
    assert techs.iloc[-1]["pattern"] == ".*"


# --------------------------------------------------------------------------- #
# Energy demand
# --------------------------------------------------------------------------- #


def test_energy_demand_shape(times_model, rules):
    demand = energy_demand(times_model, rules)
    assert not demand.empty
    assert list(demand.columns) == ["sector", "sector_name", "fuel", "year", "gwh"]
    assert set(demand["sector"]) <= set(DEMAND_SECTORS)
    assert set(demand["year"]) <= set(times_model.years)
    assert (demand["gwh"] != 0).all()


def test_demand_never_double_counts_the_fuel_techs(times_model, rules):
    """The supply carrier and the sector carrier are the same energy.

    ``RSDOIL00`` turns ``OILDST`` into ``RSDOIL`` one-for-one. If the fuel techs
    were not skipped, residential oil would come out at roughly twice the
    delivered volume, so a plausible-looking total is the only symptom.
    """
    demand = energy_demand(times_model, rules)
    year = times_model.years[0]
    oil = demand[
        (demand["sector"] == "RSD")
        & (demand["fuel"] == "Oil products")
        & (demand["year"] == year)
    ]["gwh"].sum()

    flows = times_model.energy_flows(year)
    delivered = flows[
        (flows["variable"].str.upper() == "VAR_FIN")
        & (flows["commodity_code"].isin(["RSDOIL", "RSDGSL"]))
    ]["value"].sum() * (1000.0 / 3.6)
    assert oil == pytest.approx(delivered, rel=1e-9)


def test_blended_road_fuel_is_split_into_fossil_and_bio(times_model, rules):
    """TRADST is a blend; the fossil / biofuel split only exists upstream of it."""
    demand = energy_demand(times_model, rules)
    tra = demand[demand["sector"] == "TRA"]
    fuels = set(tra["fuel"])
    assert {"Oil products", "Biofuel"} <= fuels
    # The blended carrier itself must never surface as a fuel label.
    assert "TRADST" not in fuels

    year = times_model.years[0]
    flows = times_model.energy_flows(year)
    blend = flows[
        (flows["variable"].str.upper() == "VAR_FIN")
        & (flows["commodity_code"] == "TRADST")
    ]["value"].sum()
    if blend > 0:
        producer_in = flows[
            (flows["variable"].str.upper() == "VAR_FIN")
            & (flows["process_code"] == "TRADST00")
        ]["value"].sum()
        # The pass-through conserves energy: what the blender took in is what
        # the vehicles burn, just relabelled by origin.
        assert producer_in == pytest.approx(blend, rel=1e-6)


def test_kerosene_is_reported_but_left_out_of_the_transport_total(times_model, rules):
    tables = {t.key: t for t in build_indicator_tables(times_model, rules)}
    tra = tables["demand_tra"]
    if "Kerosene" not in tra.frame.index:
        pytest.skip("no aviation kerosene in this .vd")
    counted = [f for f in tra.frame.index if f not in TOTAL_EXCLUDES]
    expected = tra.frame.loc[counted].sum()
    pd.testing.assert_series_equal(tra.total, expected, check_names=False)
    assert (tra.frame.loc["Kerosene"] > 0).any()


def test_totals_are_the_sum_of_the_stacked_rows(times_model, rules):
    """The safeguard behind the transport bar being taller than its own total.

    Every chart draws `frame` as a stack and `total` as a line on top of it, so
    the two have to be the same number. They were not for the demand sectors:
    aviation kerosene is reported on its own row and left out of the total, and
    the transport stack overshot its total line by the whole 8.3 TWh bunker.
    A row may still sit outside the total — it has to say so in `excluded_rows`,
    which is what makes the report draw it beside the stack instead of in it.
    """
    tables = build_indicator_tables(times_model, rules)
    assert tables
    for table in tables:
        if table.total is None:
            continue
        assert set(table.excluded_rows) <= set(table.frame.index), table.key
        stacked = table.frame.loc[table.stacked_rows].sum()
        pd.testing.assert_series_equal(
            table.total.reindex(table.frame.columns),
            stacked.reindex(table.frame.columns),
            check_names=False,
            rtol=1e-9,
            obj=f"total of {table.key}",
        )


def test_only_the_documented_rows_sit_outside_a_total(times_model, rules):
    """`excluded_rows` is a licence to draw outside the stack; keep it narrow."""
    tables = build_indicator_tables(times_model, rules)
    excluded = {r for t in tables for r in t.excluded_rows}
    assert excluded <= set(TOTAL_EXCLUDES)


def test_chp_fuel_is_split_between_heat_and_electricity(times_model, rules):
    shares = chp_heat_shares(times_model, rules)
    if shares.empty:
        pytest.skip("no CHP processes in this .vd")
    assert ((shares >= 0) & (shares <= 1)).all()
    # At least one unit must actually be split, otherwise the weighting is a
    # no-op and this whole branch is untested.
    assert (shares < 1).any()


def test_ambient_heat_is_not_final_energy(times_model, rules):
    """Heat pumps harvest ambient heat; counting it inflates the sector."""
    demand = energy_demand(times_model, rules)
    excluded = set(rules.fuels.loc[rules.fuels["measure"] == "exclude", "commodity"])
    assert {"RSDAHT", "COMAHT"} <= excluded
    assert "Ambient heat" not in set(demand["fuel"])


# --------------------------------------------------------------------------- #
# Emissions
# --------------------------------------------------------------------------- #


def test_ghg_matches_the_times_ghg_aggregate_where_nothing_is_captured(times_model):
    """`<SEC>GHG` is CO2N + CO2P + 28*CH4 + 265*N2O, which is what we rebuild.

    Rebuilding it from the gases rather than reading the aggregate is what lets
    captured CO2 be netted out; where no CCS runs the two must be identical, and
    that identity is the check that the GWPs and the gas list are right.
    """
    ghg = ghg_emissions(times_model)
    capture = co2_capture(times_model)
    flows = times_model.flows
    agg = flows[
        (flows["variable"].str.upper() == "VAR_FOUT")
        & (flows["commodity_code"].astype(str).str.match(r"^(AGR|COM|ELC|IND|RSD|SUP|TRA)GHG$"))
    ].copy()
    if agg.empty:
        pytest.skip("no <SEC>GHG aggregate in this .vd")
    agg["sector"] = agg["commodity_code"].astype(str).str[:3]
    agg = agg.groupby(["sector", "year"], as_index=False, observed=True)["value"].sum()

    captured = {(r.sector, r.year) for r in capture.itertuples() if r.kt > 1e-6}
    merged = ghg.merge(agg, on=["sector", "year"], suffixes=("", "_agg"))
    clean = merged[~merged.apply(lambda r: (r["sector"], r["year"]) in captured, axis=1)]
    assert not clean.empty
    for row in clean.itertuples():
        assert row.ktco2eq == pytest.approx(row.value, rel=1e-6), (
            f"{row.sector} {row.year}: rebuilt {row.ktco2eq} vs <SEC>GHG {row.value}"
        )


def test_ghg_ignores_the_region_aggregate_rows(times_model):
    """TIMES writes each emission twice: per process and once for the region."""
    ghg = ghg_emissions(times_model)
    flows = times_model.flows
    region_rows = flows[
        (flows["variable"].str.upper() == "VAR_FOUT")
        & (flows["process_code"].astype(str) == "-")
        & (flows["commodity_code"].astype(str).str.endswith("CO2N"))
    ]
    if region_rows.empty:
        pytest.skip("no region-level emission rows in this .vd")
    # Summing both would roughly double the answer; a factor-two check is enough
    # to catch it without pinning the exact GWP arithmetic.
    year = ghg["year"].iloc[0]
    total = ghg[ghg["year"] == year]["ktco2eq"].sum()
    per_process = flows[
        (flows["variable"].str.upper() == "VAR_FOUT")
        & (flows["process_code"].astype(str) != "-")
        & (flows["commodity_code"].astype(str).str.endswith("CO2N"))
        & (flows["year"] == year)
    ]["value"].sum()
    assert total < 1.9 * per_process


# --------------------------------------------------------------------------- #
# Heat
# --------------------------------------------------------------------------- #


def test_heat_production_shape(times_model, rules):
    heat = heat_production(times_model, rules)
    assert not heat.empty
    assert "skip" not in set(heat["technology"])
    assert set(heat["segment"]) <= {
        "Residential space heating",
        "Residential water heating",
        "Tertiary space heating",
        "Tertiary water heating",
        "Industry process heat",
    }


def test_retrofit_savings_are_not_counted_as_heat_production(times_model, rules):
    """A Retrofit-* process 'produces' avoided demand out of a dummy input."""
    heat = heat_production(times_model, rules)
    flows = times_model.energy_flows()
    retrofit = flows[
        (flows["variable"].str.upper() == "VAR_FOUT")
        & (flows["process_code"].astype(str).str.startswith("Retrofit"))
    ]
    if retrofit.empty:
        pytest.skip("no retrofit processes in this .vd")
    year = int(retrofit["year"].max())
    served = flows[
        (flows["variable"].str.upper() == "VAR_FOUT") & (flows["year"] == year)
    ].copy()
    served["segment"] = served["commodity_code"].astype(str).map(
        lambda c: _first_match(rules.heat[rules.heat["kind"] == "segment"], c, "label")
    )
    served = served[served["segment"] != ""]
    saved = served[served["process_code"].astype(str).str.startswith("Retrofit")]
    assert saved["value"].sum() > 0, "fixture has retrofit rows outside every heat segment"

    reported = heat[heat["year"] == year]["gwh"].sum()
    expected = (served["value"].sum() - saved["value"].sum()) * (1000.0 / 3.6)
    assert reported == pytest.approx(expected, rel=1e-9)


# --------------------------------------------------------------------------- #
# Power
# --------------------------------------------------------------------------- #


def test_power_fleet_excludes_transformers_and_storage(times_model, rules):
    generation, capacity = power_fleet(times_model, rules)
    assert not generation.empty
    assert set(generation["mode"]) <= {"chp", "power"}
    assert "skip" not in set(generation["mode"])
    assert set(capacity["mode"]) <= {"chp", "power"}
    # Grid transformers move far more electricity than any plant generates, so
    # letting one through would dominate the chart.
    flows = times_model.energy_flows()
    year = times_model.years[0]
    transformer = flows[
        (flows["variable"].str.upper() == "VAR_FOUT")
        & (flows["process_code"].astype(str).str.startswith("EVTRANS"))
        & (flows["year"] == year)
    ]["value"].sum() * (1000.0 / 3.6)
    if transformer > 0:
        assert generation[generation["year"] == year]["value"].sum() < transformer * 1.5


def test_industrial_chp_electricity_is_counted(times_model, rules):
    """It never reaches a grid bus — it lands on INDELC — so a grid-only filter
    would report the autoproducer fleet as producing nothing."""
    assert "INDELC" in ELECTRICITY_OUTPUTS
    generation, _ = power_fleet(times_model, rules)
    chp = generation[generation["mode"] == "chp"]
    if chp.empty:
        pytest.skip("no cogeneration in this .vd")
    assert chp["value"].sum() > 0


# --------------------------------------------------------------------------- #
# Assembled tables
# --------------------------------------------------------------------------- #


def test_tables_cover_every_group_and_carry_a_total(times_model, rules):
    tables = build_indicator_tables(times_model, rules)
    assert {t.group for t in tables} == {"demand", "emissions", "heat", "power"}
    keys = [t.key for t in tables]
    assert len(keys) == len(set(keys))
    for table in tables:
        assert not table.frame.empty
        assert table.total is not None
        assert list(table.frame.columns) == sorted(table.frame.columns)
        csv = table.to_csv_frame()
        assert csv.index[0] == "Total"


def test_years_filter_is_honoured(times_model, rules):
    year = times_model.years[0]
    tables = build_indicator_tables(times_model, rules, years=[year])
    for table in tables:
        assert table.years == [year]


def test_demand_total_is_the_sum_of_the_sector_tables(times_model, rules):
    tables = {t.key: t for t in build_indicator_tables(times_model, rules)}
    total = tables["demand_total"].total
    per_sector = sum(
        tables[f"demand_{code.lower()}"].total
        for code in DEMAND_SECTORS
        if f"demand_{code.lower()}" in tables
    )
    pd.testing.assert_series_equal(
        total.astype(float), per_sector.astype(float), check_names=False, rtol=1e-9
    )


# --------------------------------------------------------------------------- #
# Flat catalogue
# --------------------------------------------------------------------------- #


def test_catalogue_is_faceted_and_complete(times_model, rules):
    cat = indicator_catalogue(times_model, rules)
    assert not cat.empty
    assert list(cat.columns) == [*CATALOGUE_FACETS, "unite", "year", "value"]
    for facet in CATALOGUE_FACETS:
        assert (cat[facet].astype(str).str.strip() != "").all(), f"{facet} has blanks"
    # Every chartable table must be reachable through the facets.
    charted = {t.indicateur for t in build_indicator_tables(times_model, rules) if t.in_catalogue}
    assert charted == set(cat["indicateur"])


def test_catalogue_rows_are_leaves_that_sum_to_the_sector_totals(times_model, rules):
    """A row that is the sum of other rows would double count any selection."""
    tables = {t.key: t for t in build_indicator_tables(times_model, rules)}
    assert tables["demand_total"].in_catalogue is False

    cat = indicator_catalogue(times_model, rules, tables=list(tables.values()))
    demand = cat[cat["categorie"] == "Consommation d'énergie"]
    # Aviation kerosene is a series in its own right in the catalogue but is not
    # part of a sector's final energy, exactly as on the demand page.
    demand = demand[~demand["vecteur"].isin(TOTAL_EXCLUDES)]
    got = demand.pivot_table(
        index="technologies", columns="year", values="value", aggfunc="sum", observed=True
    )
    expected = tables["demand_total"].frame
    pd.testing.assert_frame_equal(
        got.reindex(index=expected.index, columns=expected.columns).astype(float),
        expected.astype(float),
        check_names=False,
        rtol=1e-9,
    )


def test_catalogue_series_aligns_values_with_years(times_model, rules):
    cat = indicator_catalogue(times_model, rules)
    years, series = catalogue_series(cat)
    assert years == sorted(set(cat["year"]))
    assert series
    for entry in series:
        assert len(entry["values"]) == len(years)
        assert set(entry) == {*CATALOGUE_FACETS, "unite", "values"}
    # One entry per unique facet tuple, no duplicates collapsed or invented.
    keys = {tuple(e[f] for f in CATALOGUE_FACETS) for e in series}
    assert len(keys) == len(series)
    assert len(series) == len(cat.groupby(list(CATALOGUE_FACETS), observed=True))


def test_catalogue_units_are_consistent_per_indicator(times_model, rules):
    """A filter that mixes GWh and GW rows under one indicator is unreadable."""
    cat = indicator_catalogue(times_model, rules)
    per_indicator = cat.groupby("indicateur", observed=True)["unite"].nunique()
    assert (per_indicator == 1).all(), per_indicator[per_indicator > 1].to_dict()


# --------------------------------------------------------------------------- #
# Regression against the published December-2025 tables
# --------------------------------------------------------------------------- #

#: 2021 values from ICEDD's own report CSVs for `scen_corrige_251129_0112.vd`
#: (GWh unless noted), with the tolerance each row is expected to hold to and
#: why it is not zero. INDICATORS.md § *Reconciliation* explains each one.
ICEDD_2021 = {
    "demand_total": {
        # Wood chips: ICEDD's demande_agriculture.csv has no wood row at all.
        "Agriculture": (1480.28, 5e-3),
        # Industrial CHP burning INDGAS: ICEDD drops that fuel entirely while
        # applying the heat-share split to every other fuel in the same units.
        "Industry": (39230.56, 3e-2),
        "Residential": (31457.57, 1e-6),
        "Tertiary": (14255.00, 1e-6),
        "Transport": (29347.28, 1e-6),
    },
    "demand_rsd": {
        "Electricity": (6780.49, 1e-6),
        "Gas mix": (9839.49, 1e-6),
        "Geothermal": (2.41, 1e-3),
        "LPG": (1032.52, 1e-5),
        "Oil products": (10396.23, 1e-6),
        "Solar": (90.99, 1e-4),
        "Solid fuels": (128.284736, 1e-6),
        "Wood": (3187.151506, 1e-6),
    },
    "emissions_sectors": {
        # ICEDD's published series leaves the N2O term out of the GWP sum.
        "Agriculture": (338.266282, 3e-3),
        "Electricity": (2697.201091, 1e-2),
        "Industry": (10456.998466, 1e-2),
        "Residential": (5144.207235, 1e-2),
        "Tertiary": (1608.631644, 1e-2),
        "Transport": (6990.584761, 1e-2),
    },
    "heat_residential": {
        # 18962.5710 (RH BOILER) + 1907.4657 (RW BOILER)
        "Boiler": (20870.036648, 1e-6),
        # 1626.3808 (RH ELC_JOULE) + 893.6589 (RW ELC_JOULE)
        "Direct electric heating": (2520.039708, 1e-6),
        "Geothermal": (2.167102, 1e-5),
        "Solar thermal": (72.793440, 1e-5),
    },
    "heat_industry": {
        "Cogeneration": (5804.941142, 1e-6),
        # ICEDD's single IND-HTH BOILER row is 7043.4083. It also holds the
        # 261.75 GWh of purchased network heat this table breaks out as
        # District heat, and the 360.68 GWh `INDHTH` sector aggregate, which is
        # the same joules again: INDHTH is redistributed as INDHET and shows up
        # a second time as branch process heat. 7043.4083 - 261.7474 - 360.6818.
        "Boiler": (6420.979077, 1e-6),
        "District heat": (261.747393, 1e-6),
    },
}


def test_reproduces_icedd_2021(vd_path, mappings_dir, rules):
    """The calibrated base year has to land on ICEDD's published numbers."""
    if vd_path.name != "scen_corrige_251129_0112.vd":
        pytest.skip(
            "reference values are for scen_corrige_251129_0112.vd "
            "(run with TIMES_PYPSA_FULL_DATA=1 and that .vd in data/)"
        )
    from times_pypsa.model import load_times_annual_flows
    from times_pypsa.pipeline import PipelineConfig

    # The shared `times_model` fixture starts at 2025; 2021 is the calibrated
    # year these references were taken from, so this test reads its own.
    model = load_times_annual_flows(
        vd_path, mappings_dir, config=PipelineConfig(start_year=2021)
    )
    tables = {t.key: t for t in build_indicator_tables(model, rules)}
    problems: list[str] = []
    for key, rows in ICEDD_2021.items():
        frame = tables[key].frame
        for row, (expected, tol) in rows.items():
            got = float(frame.loc[row, 2021]) if row in frame.index else 0.0
            if abs(got - expected) > tol * abs(expected):
                problems.append(
                    f"{key}/{row}: got {got:.4f}, ICEDD {expected:.4f} "
                    f"({abs(got - expected) / abs(expected):.2%} > {tol:.2%})"
                )
    assert not problems, "\n".join(problems)
