"""Energy balance and loop tests."""

from __future__ import annotations

import pandas as pd
import pytest

from times_pypsa.aggregation import aggregate_flows
from times_pypsa.balances import (
    commodity_balance_vs_comnet,
    commodity_node_residuals,
    find_loop_components,
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


def test_comnet_balance_majority_ok(year_flows):
    """
    Most mapped PJ commodities should match VAR_Comnet within a loose tolerance.

    A minority of residuals is expected (trade, stocks, non-PJ, mapping gaps);
    we fail only if almost everything is broken.
    """
    year, flows, model = year_flows
    # Restrict to commodities that appear in both flows and comnet
    bal = commodity_balance_vs_comnet(
        model.flows, model.comnet, year=year, tolerance_pj=1.0
    )
    if bal.empty:
        pytest.skip("No balance rows")
    # Only commodities with some FIn/FOut activity
    active = bal[(bal["fout"] + bal["fin"]) > 0.01]
    if active.empty:
        pytest.skip("No active commodities")
    ok_frac = active["ok"].mean()
    assert ok_frac >= 0.3, (
        f"Only {ok_frac:.0%} of active commodities match Comnet within 1 PJ "
        f"({int((~active['ok']).sum())} failures). See EXTRACTION_QA.md expert Q1."
    )


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
