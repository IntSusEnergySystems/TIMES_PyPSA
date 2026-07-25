"""Energy balance and loop tests."""

from __future__ import annotations

import pandas as pd
import pytest

from times_pypsa.aggregation import aggregate_flows
from times_pypsa.balances import (
    classify_io_ratio,
    commodity_balance_vs_comnet,
    commodity_node_residuals,
    find_loop_components,
    process_io_ratios,
)
from times_pypsa.pipeline import net_bidirectional_links


@pytest.fixture(scope="module")
def year_flows(times_model):
    years = times_model.years
    assert years, "model has no years"
    year = 2030 if 2030 in years else years[0]
    return year, times_model.energy_flows(year), times_model


def test_comnet_balance_has_columns(year_flows):
    year, flows, model = year_flows
    bal = commodity_balance_vs_comnet(model.flows, model.comnet, year=year)
    assert not bal.empty
    for col in ("commodity_code", "fout", "fin", "net_flows", "comnet", "residual", "ok"):
        assert col in bal.columns


def test_comnet_balance_only_compares_reported_commodities(year_flows):
    """A commodity absent from VAR_Comnet has no oracle — it must not be compared.

    GDX2VEDA exports VAR_Comnet only for what it was asked to; in the reference
    `.vd` that is the emission / pollutant aggregates, no energy carrier. Treating
    the missing value as 0 reported every `.DEM.` service commodity's whole FOut as
    a residual (77 PJ in 2030).
    """
    year, flows, model = year_flows
    bal = commodity_balance_vs_comnet(model.flows, model.comnet, year=year)
    if bal.empty:
        pytest.skip("No balance rows")
    reported = set(
        model.comnet.loc[model.comnet["year"] == year, "commodity_code"].astype(str)
    )
    assert set(bal["commodity_code"].astype(str)) <= reported


def test_comnet_balance_holds_where_comnet_is_reported(year_flows):
    """Where VAR_Comnet exists, ΣFOut − ΣFIn must match it."""
    year, flows, model = year_flows
    bal = commodity_balance_vs_comnet(
        model.flows, model.comnet, year=year, tolerance_pj=1e-3
    )
    if bal.empty:
        pytest.skip("No balance rows")
    failures = bal[~bal["ok"]]
    assert failures.empty, failures.to_string()


def test_node_residuals_after_netting(year_flows):
    year, flows, model = year_flows
    netted = net_bidirectional_links(
        flows[
            [
                "year",
                "region",
                "variable",
                "commodity_code",
                "commodity",
                "process_code",
                "process",
                "value",
            ]
        ]
    )
    res = commodity_node_residuals(netted, tolerance_pj=1.0)
    assert not res.empty
    # Demand sinks / primary sources residual; just ensure API works and
    # residuals are finite.
    assert res["residual"].notna().all()


def test_loop_detection_runs(year_flows):
    year, flows, model = year_flows
    netted = net_bidirectional_links(
        flows[
            [
                "year",
                "region",
                "variable",
                "commodity_code",
                "commodity",
                "process_code",
                "process",
                "value",
            ]
        ]
    )
    loops = find_loop_components(netted)
    assert isinstance(loops, list)
    for comp in loops:
        assert len(comp) > 1


def test_loop_check_ignores_material_recycles(year_flows):
    """The QA loop table is about energy: `.MAT.` recycles are not defects.

    Iron & steel genuinely loops (scrap → crude steel → finishing → scrap), but
    `MISSCR` / `MISCST` are materials in Mt. Reporting them next to energy loops
    sent readers looking for a leak that does not exist, so `qa.py` filters the
    loop input to mapped energy carriers.
    """
    from times_pypsa.qa import filter_energy_carrier_flows

    year, flows, model = year_flows
    cols = [
        "year",
        "region",
        "variable",
        "commodity_code",
        "commodity",
        "process_code",
        "process",
        "value",
    ]
    energy = filter_energy_carrier_flows(flows)
    loops = find_loop_components(net_bidirectional_links(energy[cols]))
    nodes = {node for comp in loops for node in comp}
    assert not {"MISSCR", "MISCST"} & nodes


def test_l0_aggregation_reduces_nodes(year_flows):
    year, flows, model = year_flows
    enriched = model.flows_for_year(year)
    enriched = enriched[
        enriched["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])
    ]
    if "sector" not in enriched.columns:
        pytest.skip("sector column missing")
    l0 = aggregate_flows(enriched, level="L0", apply_netting=True)
    assert l0["process_code"].nunique() <= 20
    assert len(l0) < len(enriched)


def test_mapping_aggregation_uses_csv_labels(year_flows):
    year, flows, model = year_flows
    enriched = model.flows_for_year(year)
    enriched = enriched[
        enriched["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])
    ]
    mapped = aggregate_flows(enriched.head(500), level="mapping", apply_netting=False)
    # No invented "Unmapped:" display prefix (CSV labels / TIMES names only)
    labels = set(mapped["process_code"].astype(str)) | set(
        mapped["commodity_code"].astype(str)
    )
    assert not any(str(x).startswith("Unmapped:") for x in labels)


def _io_fixture() -> pd.DataFrame:
    """Heat pump (COP 3), boiler (90%), import with an unmapped-only input."""
    return pd.DataFrame(
        {
            "variable": ["VAR_FIn", "VAR_FOut", "VAR_FIn", "VAR_FOut", "VAR_FIn", "VAR_FOut"],
            "process_code": ["HP", "HP", "BOIL", "BOIL", "IMP", "IMP"],
            "process": ["heat pump", "heat pump", "boiler", "boiler", "import", "import"],
            "sector": ["RSD"] * 4 + ["IMP", "IMP"],
            "commodity_code": ["ELC", "HEAT", "GAS", "HEAT", "MT_ORE", "GAS"],
            "value": [10.0, 30.0, 10.0, 9.0, 5.0, 4.0],
            "exported": [False, True, False, True, False, False],
        }
    )


def test_process_io_ratios_reports_cop_efficiency_and_balance():
    df = _io_fixture()
    carrier = df["commodity_code"].ne("MT_ORE")  # ore has no pypsa_carrier
    out = process_io_ratios(df, carrier_mask=carrier).set_index("process_code")

    assert out.loc["HP", "ratio"] == pytest.approx(3.0)
    assert out.loc["HP", "balance"] == pytest.approx(20.0)
    assert out.loc["HP", "reading"] == "COP / service accounting"
    # Only the FOut side is soft-linked, and the table says so.
    assert out.loc["HP", "exported_out"] == pytest.approx(30.0)
    assert out.loc["HP", "exported_in"] == pytest.approx(0.0)

    assert out.loc["BOIL", "ratio"] == pytest.approx(0.9)
    assert out.loc["BOIL", "balance"] == pytest.approx(-1.0)
    assert out.loc["BOIL", "reading"] == "conversion"

    # Non-carrier rows never enter inflow/outflow, but must be visible as unmapped
    # so a ratio distorted by a missing commodity mapping is diagnosable.
    assert out.loc["IMP", "inflow"] == pytest.approx(0.0)
    assert out.loc["IMP", "unmapped_in"] == pytest.approx(5.0)
    assert out.loc["IMP", "outflow"] == pytest.approx(4.0)
    assert out.loc["IMP", "reading"] == "no energy input (unmapped FIn)"


def test_process_io_ratios_groups_by_aggregated_label():
    df = _io_fixture()
    df["process_code"] = ["Heating"] * 4 + ["Supply"] * 2
    out = process_io_ratios(df).set_index("process_code")
    # Both heaters pooled: 20 in / 39 out
    assert out.loc["Heating", "inflow"] == pytest.approx(20.0)
    assert out.loc["Heating", "outflow"] == pytest.approx(39.0)
    assert out.loc["Heating", "ratio"] == pytest.approx(1.95)


def test_process_io_ratios_sorted_by_throughput_and_empty_safe():
    out = process_io_ratios(_io_fixture())
    assert list(out["process_code"])[0] == "HP"  # largest throughput first
    empty = process_io_ratios(pd.DataFrame())
    assert empty.empty and "ratio" in empty.columns


def test_classify_io_ratio_readings():
    assert classify_io_ratio(10.0, 0.0) == "final demand / sink"
    assert classify_io_ratio(0.0, 10.0) == "source (no energy input)"
    assert classify_io_ratio(10.0, 2.0) == "high losses (check outputs)"
    assert classify_io_ratio(10.0, 9.5) == "conversion"
    # A large unmapped input is flagged rather than reported as a fake COP.
    assert classify_io_ratio(1.0, 5.0, unmapped_in=10.0) == "ratio distorted (unmapped FIn)"


def test_classify_io_ratio_separates_sources_from_cops():
    # Imports / mining: output dwarfs input — a source, not a COP.
    assert classify_io_ratio(1.0, 36.0) == "mostly source (input ≪ output)"
    # A heat pump stays a COP.
    assert classify_io_ratio(10.0, 30.0) == "COP / service accounting"
    # Non-energy activity outputs (Mvkm, Mm²) are never read as efficiencies.
    assert classify_io_ratio(10.0, 500.0, activity_unit="MVKM") == "non-PJ activity output"
